"""MiMo TTS 插件测试套件的共享工具。

设计目标：
- 只依赖标准库 + 正在测试的 KiraAI 框架源码（真实对象，不打桩框架逻辑）
- 框架定位：优先环境变量 KIRA_FRAMEWORK_DIR，其次常见目录结构自动发现
- 隔离：每个测试文件使用自己的临时工作目录（data/ 基于 cwd 生成），互不污染
- 假客户端：替换 httpx.AsyncClient，模拟 MiMo API 的成功/失败响应
"""
import base64
import importlib.util
import os
import struct
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLUGIN = REPO_ROOT / "main.py"
PLUGIN_ID = "kira-ai-plugin-mimo-tts"

_RESULTS = []


# ---------------------------------------------------------------- 框架定位 --
def setup_framework() -> None:
    """把 KiraAI 框架源码加入 sys.path（只做路径处理，不导入框架模块）。"""
    candidates = []
    env = os.environ.get("KIRA_FRAMEWORK_DIR", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(REPO_ROOT.parent / "KiraAI")  # 与框架仓库并列
    try:
        candidates.append(REPO_ROOT.parents[2])  # data/plugins/<plugin> 安装形态
    except IndexError:
        pass
    for cand in candidates:
        if (cand / "core").is_dir():
            sys.path.insert(0, str(cand))
            break


def make_scratch_dir(prefix: str = "mimo_tts_test_") -> Path:
    """创建独立临时工作目录并切换过去。

    同时先建好 data/：框架部分模块（如日志）在导入时就会写入 data/log.log，
    需要一个已存在的数据目录（与框架启动时创建数据目录的行为一致）。
    """
    scratch = Path(tempfile.mkdtemp(prefix=prefix))
    os.chdir(scratch)
    (scratch / "data").mkdir(exist_ok=True)
    return scratch


def bootstrap() -> Path:
    """测试入口引导：定位框架 -> 切换到独立临时目录 -> 验证 core 可导入。"""
    setup_framework()
    scratch = make_scratch_dir()
    try:
        import core.plugin  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "无法导入 KiraAI 框架（core.*）。请设置 KIRA_FRAMEWORK_DIR 指向框架源码目录后重试，例如：\n"
            "  KIRA_FRAMEWORK_DIR=/path/to/KiraAI python3 tests/run_all.py\n"
            f"原始错误：{exc}"
        )
    return scratch


# ------------------------------------------------------------------ 插件加载 --
def load_plugin_module(name: str = "mimo_tts_plugin"):
    """按文件路径加载插件 main.py（与框架加载方式一致；manifest.json 需同目录）。

    可用环境变量 MIMO_TTS_PLUGIN_PATH 指定其它副本，默认测试仓库根目录的 main.py。
    """
    path = Path(os.environ.get("MIMO_TTS_PLUGIN_PATH", str(DEFAULT_PLUGIN))).resolve()
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# -------------------------------------------------------------- 假 MiMo 客户端 --
def make_wav(seconds: float = 0.3, rate: int = 24000) -> bytes:
    """生成一段合法的最小 WAV（静音），用于验证字节级一致性。"""
    n = int(rate * seconds)
    data = b"\x00\x00" * n
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " +
           struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
           b"data" + struct.pack("<I", len(data)))
    return hdr + data


WAV_BYTES = make_wav()
WAV_B64 = base64.b64encode(WAV_BYTES).decode()


class FakeResp:
    status_code = 200
    text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"audio": {"data": WAV_B64}}}]}


class FakeClient:
    """成功路径：返回携带 WAV 的合法响应。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        return FakeResp()


class BoomClient:
    """网络异常路径。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        raise RuntimeError("boom")


class HttpErrResp:
    status_code = 500
    text = "X" * 1000

    def raise_for_status(self):
        import httpx

        request = httpx.Request("POST", "https://api.xiaomimimo.com")
        raise httpx.HTTPStatusError("server error", request=request, response=self)

    def json(self):
        return {}


class HttpErrClient:
    """HTTP 500 且正文超长（验证日志截断）。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return HttpErrResp()


class LogRec:
    """替换插件的 logger，收集日志用于断言。"""

    def __init__(self):
        self.msgs = []

    def info(self, m):
        self.msgs.append(("INFO", str(m)))

    def warning(self, m):
        self.msgs.append(("WARN", str(m)))

    def error(self, m):
        self.msgs.append(("ERROR", str(m)))

    def debug(self, m):
        self.msgs.append(("DEBUG", str(m)))

    def exception(self, m):
        self.msgs.append(("EXC", str(m)))


def install_mocks(mod, client_cls=FakeClient) -> LogRec:
    """替换插件的 httpx 客户端与 logger，返回 LogRec。"""
    mod.httpx.AsyncClient = client_cls
    rec = LogRec()
    mod.logger = rec
    return rec


class FakeConfig:
    """给 AsyncTempMonitor 用的最小配置对象。"""

    def __init__(self, cache=None):
        self.cache = cache or {"max_size_mb": 50, "max_files": 50,
                               "max_age_hours": 24, "check_interval_minutes": 5}

    def get_config(self, key, default=None):
        if key == "bot_config.cache":
            return self.cache
        return default


# ---------------------------------------------------------------- QQ 出站 --
_QA = None


async def qq_payload(chain):
    """用真实 QQAdapter 把消息链转换为出站载荷（base64:// 等），与线上发送一致。"""
    global _QA
    if _QA is None:
        from core.adapter.src.qq.qq import QQAdapter
        from core.logging_manager import get_logger

        _QA = QQAdapter.__new__(QQAdapter)
        _QA.emoji_dict = {}
        _QA.logger = get_logger("qq-test", "blue")
    from core.adapter.src.qq.napcat_client.utils import QQMessageChain

    segments = await _QA._process_outgoing_message(chain)
    return QQMessageChain(segments).to_list()


# ------------------------------------------------------------ 发送管线脚手架 --
class PipelineHarness:
    """真实 MessageProcessor + PluginManager 注册链，复刻框架实际运行方式。"""

    def __init__(self, plugin, plugin_id: str = PLUGIN_ID):
        from core.plugin.plugin_handlers import event_handler_reg, EventType
        from core.plugin.plugin_registry import PluginManager
        from core.chat import Session
        from core.adapter.adapter_info import AdapterInfo
        from core.message_manager import MessageProcessor
        from core.chat.message_utils import KiraIMSentResult

        self.event_handler_reg = event_handler_reg
        self.EventType = EventType

        pm = PluginManager.__new__(PluginManager)
        pm.plugin_instances = {plugin_id: plugin}
        pm._register_plugin_hooks_for(plugin_id)
        pm._register_plugin_tags_for(plugin_id)

        self.adapter_info = AdapterInfo(enabled=True, adapter_id="a1", name="qq", platform="QQ")
        self.session = Session(adapter_name="qq", session_type="gm", session_id="10001")

        self.sent = []
        mp = MessageProcessor.__new__(MessageProcessor)
        mp.min_message_delay = 0.0
        mp.max_message_delay = 0.0

        async def recorder(sid, chain):
            self.sent.append(chain)
            return KiraIMSentResult(message_id=f"mid{len(self.sent)}")

        mp.send_message_chain = recorder
        self.mp = mp

    def make_event(self):
        from core.chat.message_utils import KiraMessageBatchEvent

        return KiraMessageBatchEvent(adapter=self.adapter_info, session=self.session,
                                     messages=[], message_types=[], timestamp=0)

    def build_tag_set(self):
        from importlib import import_module
        from core.tag import tag_registry, TagSet

        ktags = import_module("core.plugin.builtin_plugins.kira-ai.tags")
        ts = TagSet()
        ts.register(ktags.TextTag, ktags.AtTag, ktags.ReplyTag, ktags.PokeTag)
        ts.register(*tag_registry.get_all())
        ts.register(*tag_registry.get_all_root())
        return ts

    async def send(self, xml: str, dispatch_llm_response: bool = False):
        """走一遍真实 send_xml_messages；返回 (sent_chains, results, flattened_text)。"""
        from core.provider import LLMResponse

        self.sent.clear()
        event = self.make_event()
        text = xml
        if dispatch_llm_response:
            resp = LLMResponse(text_response=xml)
            for handler in self.event_handler_reg.get_handlers(self.EventType.ON_LLM_RESPONSE):
                await handler.exec_handler(event, resp)
            text = resp.text_response
        results = await self.mp.send_xml_messages(event, text.strip(), self.build_tag_set())
        return self.sent, (results or []), text


def chain_repr(chain) -> str:
    """可读的消息链摘要（Record 不打印完整 base64）。"""
    from core.chat.message_elements import At, Record, Reply, Text

    parts = []
    for e in chain.message_list:
        if isinstance(e, Record):
            if e.file_type == "path":
                parts.append(f"Record({os.path.basename(e.file)})")
            else:
                parts.append(f"Record(<{e.file_type} len={len(e.file or '')}>)")
        elif isinstance(e, Text):
            parts.append(f"Text({e.text!r})")
        elif isinstance(e, At):
            parts.append(f"At({e.pid})")
        elif isinstance(e, Reply):
            parts.append(f"Reply({e.message_id})")
        else:
            parts.append(type(e).__name__)
    return " + ".join(parts) if parts else "<empty>"


def count_records(chains) -> int:
    from core.chat.message_elements import Record

    return sum(1 for c in chains for e in c.message_list if isinstance(e, Record))


# ------------------------------------------------------------------ 校验器 --
def check(name: str, cond, detail: str = "") -> bool:
    ok = bool(cond)
    _RESULTS.append((name, ok, detail))
    line = f"  {'PASS' if ok else 'FAIL'}  {name}"
    if detail and not ok:
        line += f"  [{detail}]"
    print(line, flush=True)
    return ok


def finish() -> None:
    total = len(_RESULTS)
    failed = [(n, d) for n, ok, d in _RESULTS if not ok]
    print(f"\n{'=' * 64}\n{total - len(failed)}/{total} checks passed")
    if failed:
        for n, d in failed:
            print("  FAILED:", n, d)
        sys.exit(1)
