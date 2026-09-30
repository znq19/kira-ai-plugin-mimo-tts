import asyncio
import base64
import os
import re
import stat
import time
from typing import Optional

import httpx

from core.plugin import BasePlugin, logger, on, register
from core.chat import MessageChain
from core.chat.message_elements import At, Record, Reply, Text
from core.provider import LLMResponse
from core.utils.path_utils import get_data_path

MIMO_TTS_ENDPOINT = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_TTS_MODEL = "mimo-v2.5-tts-voicedesign"

_AUDIO_FILE_PREFIX = "mimo_tts_"
_AUDIO_FILE_PROTECT_SECONDS = 60


class MiMoTTSPlugin(BasePlugin):

    def __init__(self, ctx, cfg: dict):
        super().__init__(ctx, cfg)
        self._cfg = cfg or {}
        self.api_key: str = self._cfg.get("api_key", "") or ""
        self.default_voice: str = self._cfg.get("default_voice", "一个年轻温柔的女性声音，语速适中，语调自然亲切")
        self.auto_format_fix: bool = self._cfg.get("auto_format_fix", True)
        # 可选留存的音频目录：data/files 不在框架临时清理器管辖内，由插件自己定量+定时清理
        self.audio_dir = os.path.join(str(get_data_path()), "files", "mimo_tts")
        self._cleanup_task: Optional[asyncio.Task] = None

    async def initialize(self):
        if self.api_key:
            logger.info("MiMo TTS 插件已加载（标签模式：<mimo_tts>）")
        else:
            logger.warning("MiMo TTS: API Key 未配置，请在插件设置中填写")
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._audio_cleanup_loop())

    async def terminate(self):
        task = self._cleanup_task
        self._cleanup_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"MiMo TTS: 清理任务结束于异常: {e}")
        logger.info("MiMo TTS 插件已卸载")

    # ========== 落盘音频的定量 + 定时清理 ==========

    def _cfg_bool(self, key: str, default: bool) -> bool:
        value = self._cfg.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    async def _audio_cleanup_loop(self):
        """后台清理循环：启动先清一遍，之后按配置间隔定期清理（每轮读取最新配置）。"""
        try:
            await self._cleanup_audio_files()
            while True:
                interval_minutes = max(5, self._cfg_int("save_audio_cleanup_minutes", 180))
                await asyncio.sleep(interval_minutes * 60)
                try:
                    await self._cleanup_audio_files()
                except Exception:
                    logger.exception("MiMo TTS: 音频清理失败，将在下个周期重试")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MiMo TTS: 音频清理任务异常退出")

    async def _cleanup_audio_files(self):
        """按「保留数量 + 保留时长」清理 data/files/mimo_tts 下本插件产生的 wav。"""
        folder = self.audio_dir
        max_files = max(1, self._cfg_int("save_audio_max_files", 100))
        max_age_seconds = max(1, self._cfg_int("save_audio_max_age_hours", 48)) * 3600
        protect_seconds = _AUDIO_FILE_PROTECT_SECONDS

        def sweep():
            if not os.path.isdir(folder):
                return None
            now = time.time()
            entries = []
            for name in os.listdir(folder):
                if not name.startswith(_AUDIO_FILE_PREFIX) or not name.endswith(".wav"):
                    continue
                path = os.path.join(folder, name)
                try:
                    entry_stat = os.stat(path)
                except OSError:
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    continue
                entries.append((path, entry_stat.st_mtime))
            entries.sort(key=lambda item: item[1])  # 最旧在前

            deleted = 0
            remaining = []
            for path, mtime in entries:
                age = now - mtime
                if age > max_age_seconds and age > protect_seconds:
                    try:
                        os.unlink(path)
                        deleted += 1
                        continue
                    except OSError as e:
                        logger.debug(f"MiMo TTS: 音频清理失败 {path}: {e}")
                remaining.append((path, mtime))

            overflow = len(remaining) - max_files
            if overflow > 0:
                kept = []
                for path, mtime in remaining:  # 最旧在前，优先删除超量的旧文件
                    if overflow > 0 and (now - mtime) > protect_seconds:
                        try:
                            os.unlink(path)
                            deleted += 1
                            overflow -= 1
                            continue
                        except OSError as e:
                            logger.debug(f"MiMo TTS: 音频清理失败 {path}: {e}")
                    kept.append((path, mtime))
                remaining = kept
            return deleted, len(remaining)

        result = await asyncio.to_thread(sweep)
        if result and result[0] > 0:
            logger.info(f"MiMo TTS: 音频清理完成，删除 {result[0]} 个，保留 {result[1]} 个")

    async def _audio_post_write_check(self):
        """落盘后的轻量检查：数量超限时立即清理，避免扎堆等到下个周期。"""
        folder = self.audio_dir
        max_files = max(1, self._cfg_int("save_audio_max_files", 100))

        def count():
            try:
                return sum(
                    1 for name in os.listdir(folder)
                    if name.startswith(_AUDIO_FILE_PREFIX) and name.endswith(".wav")
                )
            except OSError:
                return 0

        if await asyncio.to_thread(count) > max_files:
            await self._cleanup_audio_files()

    async def _save_audio_file(self, audio_bytes: bytes) -> Optional[str]:
        """把合成音频写入 data/files/mimo_tts；失败返回 None（调用方回退为直传）。"""
        folder = self.audio_dir
        file_path = os.path.join(folder, f"{_AUDIO_FILE_PREFIX}{int(time.time() * 1000)}.wav")

        def write():
            os.makedirs(folder, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(audio_bytes)

        try:
            await asyncio.to_thread(write)
        except Exception as e:
            logger.error(f"MiMo TTS: 音频保存失败，改为直传: {e}")
            return None
        logger.info(f"MiMo TTS: 音频已保存 → {file_path}")
        return file_path

    async def _synthesize(self, voice_description: str, text: str) -> bytes:
        """调用 MiMo TTS API 合成语音，返回 WAV 音频字节"""
        payload = {
            "model": MIMO_TTS_MODEL,
            "messages": [
                {"role": "user", "content": voice_description},
                {"role": "assistant", "content": text}
            ],
            "audio": {
                "format": "wav"
            },
            "stream": False
        }

        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(MIMO_TTS_ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        audio_b64 = data["choices"][0]["message"]["audio"]["data"]
        return base64.b64decode(audio_b64)

    @register.tag(
        name="mimo_tts",
        description='<mimo_tts>voice_text</mimo_tts> # 用 MiMo TTS 发送语音，voice_text 直接写要合成语音的文本，'
                    '不要在标签内嵌套 <text> 等其他标签；'
                    '可用 voice 属性自定义音色，如 <mimo_tts voice="低沉磁性的男声，缓慢沉稳">文本</mimo_tts>，'
                    '省略 voice 则使用默认音色。需要发语音且想自定义音色时使用（普通语音用 <record>），'
                    '同一条消息中最多使用一次',
        parent="msg",
    )
    async def mimo_tts_tag(self, value: str, voice: str = "", **kwargs):
        """<mimo_tts> 标签处理器：在 XML 解析阶段合成语音并作为 Record 随本条消息一起发出。

        与官方 <record> 标签同机制：LLM 决策、合成、发送在同一个 LLM 步骤内完成，
        不产生额外的工具调用步骤，也不会出现工具结果后 LLM 输出空 <msg/> 的问题。

        默认（save_audio 关闭）：音频以 base64 直接交给框架（与框架自带 TTS
        客户端一致），发送时按适配器需要由框架物化临时文件，插件自身不落盘。
        开启 save_audio 后：音频保存到 data/files/mimo_tts 并以路径形式交给
        框架（同旧版行为，位置可查、日志可见），文件由插件定量+定时清理。
        """
        text = self._clean_voice_text(value)
        if not text:
            return []

        if not self.api_key:
            logger.warning("MiMo TTS: API Key 未配置，语音内容降级为文本发送")
            return [Text(text)]

        voice_desc = (voice or "").strip() or self.default_voice

        try:
            logger.info(f"MiMo TTS: 开始合成，音色='{voice_desc[:50]}...'，文本长度={len(text)}")
            audio_bytes = await self._synthesize(voice_desc, text)
            logger.info(f"MiMo TTS: 合成完成，文件大小={len(audio_bytes)} bytes")

            if self._cfg_bool("save_audio", False):
                saved_path = await self._save_audio_file(audio_bytes)
                if saved_path:
                    await self._audio_post_write_check()
                    return [Record(record=saved_path, mime="audio/wav", name=os.path.basename(saved_path))]

            filename = f"{_AUDIO_FILE_PREFIX}{int(time.time())}.wav"
            return [Record(
                record=base64.b64encode(audio_bytes).decode("ascii"),
                mime="audio/wav",
                name=filename,
            )]
        except httpx.HTTPStatusError as e:
            logger.error(f"MiMo TTS API 错误: {e.response.status_code} - {str(e.response.text)[:300]}")
        except Exception as e:
            logger.error(f"MiMo TTS 合成异常: {e}")

        # 合成失败：降级为文本发送，保证内容不丢
        return [Text(text)]

    # ========== 语音消息格式自动修复 ==========

    _MIMO_BLOCK_RE = re.compile(r"<mimo_tts(\s[^>]*)?>(.*?)</mimo_tts>", re.DOTALL)

    @on.llm_response()
    async def flatten_nested_mimo_tags(self, event, resp: LLMResponse, *_):
        """XML 解析前的兜底：摊平 <mimo_tts><text>…</text></mimo_tts> 这类嵌套写法。

        框架解析器只取标签的直接文本（child.text），标签内再嵌套子标签时
        内容会在解析阶段静默丢失（处理器收到空串，语音发不出去），
        所以必须在 llm_response 阶段先把 text_response 里的嵌套剥掉。

        该兜底始终生效，不受 auto_format_fix 开关影响（该开关只控制发送前的拆分整理）。
        """
        if resp.tool_calls:
            return
        text = resp.text_response or ""
        if "<mimo_tts" not in text:
            return
        new_text = self._MIMO_BLOCK_RE.sub(self._flatten_block, text)
        if new_text != text:
            resp.text_response = new_text
            logger.info("MiMo TTS: 已自动摊平 <mimo_tts> 内的嵌套标签")

    @staticmethod
    def _flatten_block(m) -> str:
        attrs = m.group(1) or ""
        inner = re.sub(r"</?[a-zA-Z][^>]*>", "", m.group(2))
        return f"<mimo_tts{attrs}>{inner}</mimo_tts>"

    @staticmethod
    def _clean_voice_text(value: str) -> str:
        """清洗标签内容：剥掉残留的 <text> 等 XML 标签，只保留纯文本。

        常见错误输出：<mimo_tts><text>要说的话</text></mimo_tts>
        不清洗的话 TTS 会把 "<text>" 也念出来。
        （llm_response 阶段的摊平是主防线，这里作为第二道兜底）
        """
        if not value:
            return ""
        text = re.sub(r"</?[a-zA-Z][^>]*>", "", value)
        return text.strip()

    @on.after_xml_parse()
    async def fix_voice_format(self, event, actions, *_):
        """发送前兜底：把混在消息里的语音拆成单条干净消息（不带 @ 和回复）。

        QQ 上语音与 @/回复等内容混在同一条消息里会无法正常显示，
        这里在 after_xml_parse 阶段直接改写待发送的 actions 列表：
        每个 Record 各自单独成链，@/回复迁移到首个有实际内容的文字链。
        对官方 <record> 标签产生的语音同样生效。
        """
        if not self.auto_format_fix:
            return
        try:
            new_actions = []
            changed = False
            for action in actions:
                if not isinstance(action, MessageChain):
                    new_actions.append(action)
                    continue
                if not any(isinstance(e, Record) for e in action.message_list):
                    new_actions.append(action)
                    continue
                changed = True
                new_actions.extend(self._split_voice_chain(action))
            if changed:
                actions[:] = new_actions
                logger.info("MiMo TTS: 已自动修复语音消息格式（语音单条发送，不带@/回复）")
        except Exception:
            logger.exception("MiMo TTS: 语音格式修复异常")

    @staticmethod
    def _split_voice_chain(chain: MessageChain) -> list:
        """把含 Record 的消息链按顺序拆分：每个 Record 单独成链，其余内容保持原顺序成链。

        仅含 @/回复的碎片链会并入首个有实际内容的链（回复保持最前）；
        若整条消息只有语音，则 @/回复直接丢弃，保证语音消息绝对干净。
        """
        runs = []  # 按原始顺序的元素分组：语音单独一组，其余连续成组
        run = []
        for e in chain.message_list:
            if isinstance(e, Record):
                if run:
                    runs.append(run)
                    run = []
                runs.append([e])
            else:
                run.append(e)
        if run:
            runs.append(run)

        def has_real_content(elems) -> bool:
            return any(not isinstance(x, (At, Reply)) for x in elems)

        real_runs = [r for r in runs if not (len(r) == 1 and isinstance(r[0], Record)) and has_real_content(r)]
        stray = [e for r in runs
                 if not (len(r) == 1 and isinstance(r[0], Record)) and not has_real_content(r)
                 for e in r]

        if real_runs and stray:
            # 回复需保持在消息最前，@ 其次，其余按原相对顺序
            stray.sort(key=lambda x: 0 if isinstance(x, Reply) else 1)
            real_runs[0] = stray + real_runs[0]
        # 没有文字内容时 stray 直接丢弃

        # 按原顺序重组：文字链和语音链的先后关系保持不变
        ordered = []
        for r in runs:
            if len(r) == 1 and isinstance(r[0], Record):
                ordered.append(r)
            elif has_real_content(r):
                # 首个内容链可能已并入 stray，用 real_runs 里对应版本
                ordered.append(real_runs.pop(0) if real_runs else r)
        # real_runs 若有剩余（理论上不会），追加到末尾
        ordered.extend(real_runs)

        return [MessageChain(list(r)) for r in ordered]
