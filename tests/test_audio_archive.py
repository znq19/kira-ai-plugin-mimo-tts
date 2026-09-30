"""save_audio（可选落盘留存）专项测试。

覆盖：开关关闭时不落盘 / 开启后路径 Record 与日志 / QQ 出站转换 /
定量与超龄清理 / 写后轻检 / 任务生命周期（terminate 可重入）/
保存失败自动回退 base64。
"""
import asyncio
import base64
import os
import shutil
import time

import _harness as H

H.bootstrap()  # 定位框架 + 切换独立工作目录（含 data/）

from core.chat import MessageChain  # noqa: E402


def wav_files(audio_dir):
    try:
        return sorted(n for n in os.listdir(audio_dir)
                      if n.startswith("mimo_tts_") and n.endswith(".wav"))
    except OSError:
        return []


def seed_old_files(audio_dir, count, age_seconds=7200, tag="9000"):
    os.makedirs(audio_dir, exist_ok=True)
    now = time.time()
    for i in range(count):
        path = os.path.join(audio_dir, f"mimo_tts_{tag}{i:04d}.wav")
        with open(path, "wb") as f:
            f.write(b"x" * 100)
        os.utime(path, (now - age_seconds, now - age_seconds))


async def main():
    mod = H.load_plugin_module()
    H.install_mocks(mod)

    # A0. 默认关闭：纯 base64，且不会创建目录
    p_off = mod.MiMoTTSPlugin(None, {"api_key": "k", "default_voice": "v"})
    audio_dir = p_off.audio_dir
    shutil.rmtree(audio_dir, ignore_errors=True)
    result = await p_off.mimo_tts_tag("直传模式", voice="")
    H.check("A0 开关关闭：base64 Record 且不创建目录",
            result[0].file_type == "base64" and not os.path.isdir(audio_dir))

    # A1. 开启：路径 Record 落在 data/files/mimo_tts，日志打印完整路径
    plugin = mod.MiMoTTSPlugin(None, {"api_key": "k", "default_voice": "v", "save_audio": True})
    log = H.install_mocks(mod)  # 重新绑定日志收集
    await plugin.initialize()
    H.check("A1 清理任务已启动", plugin._cleanup_task is not None and not plugin._cleanup_task.done())

    result = await plugin.mimo_tts_tag("落盘模式测试", voice="")
    record = result[0]
    H.check("A1 返回路径 Record 且位于 data/files/mimo_tts",
            record.file_type == "path" and os.path.dirname(record.file) == audio_dir)
    H.check("A1 文件真实存在", os.path.exists(record.file))
    H.check("A1 日志打印了完整保存路径",
            any("音频已保存" in m and record.file in m for _, m in log.msgs))

    # A2. 路径 Record 的 QQ 出站转换（与旧版行为一致）
    payload = await H.qq_payload(MessageChain([record]))
    b64 = payload[0]["data"]["file"].split("base64://", 1)[1]
    H.check("A2 QQ 载荷 record 解码与原始 WAV 一致",
            payload[0]["type"] == "record" and base64.b64decode(b64) == H.WAV_BYTES)

    # B1. 定量清理：121 -> 100，且新文件受保护
    seed_old_files(audio_dir, 120)
    before = len(wav_files(audio_dir))
    await plugin._cleanup_audio_files()
    after = wav_files(audio_dir)
    fresh_alive = any(os.path.getmtime(os.path.join(audio_dir, n)) > time.time() - 300 for n in after)
    H.check(f"B1 定量清理 {before} -> {len(after)}（上限 100）且新文件受保护",
            before == 121 and len(after) == 100 and fresh_alive)

    # B2. 超龄清理：把 60 个文件改成 49 小时前
    now = time.time()
    for name in after[:60]:
        os.utime(os.path.join(audio_dir, name), (now - 49 * 3600, now - 49 * 3600))
    await plugin._cleanup_audio_files()
    remaining = len(wav_files(audio_dir))
    H.check(f"B2 超龄清理(49h) {len(after)} -> {remaining}（保留 48h 内）", remaining == 40)

    # B3. 写后轻检：上限 5 个时，写入第 7 个会立即触发清理
    shutil.rmtree(audio_dir, ignore_errors=True)
    p3 = mod.MiMoTTSPlugin(None, {"api_key": "k", "default_voice": "v",
                                  "save_audio": True, "save_audio_max_files": 5})
    await p3.initialize()
    seed_old_files(audio_dir, 6)
    r3 = await p3.mimo_tts_tag("小配额测试", voice="")
    H.check("B3 写后轻检把小配额压回上限（7 -> 5）且新文件保留",
            len(wav_files(audio_dir)) == 5 and os.path.exists(r3[0].file))
    await p3.terminate()

    # C1. 任务生命周期：terminate 可重入、任务正确取消
    task = plugin._cleanup_task
    await plugin.terminate()
    cancelled = task.cancelled()
    await plugin.terminate()
    H.check("C1 terminate 可重入、任务被取消、引用清空",
            cancelled and plugin._cleanup_task is None)

    # D1. 保存失败 -> 自动回退 base64 直发
    shutil.rmtree(audio_dir, ignore_errors=True)
    p4 = mod.MiMoTTSPlugin(None, {"api_key": "k", "default_voice": "v", "save_audio": True})
    await p4.initialize()
    with open(audio_dir, "w") as f:  # 用文件占住目录路径，令 makedirs 失败
        f.write("block")
    r4 = await p4.mimo_tts_tag("保存失败应回退", voice="")
    H.check("D1 保存失败回退 base64（语音仍可正常发出）", r4[0].file_type == "base64")
    H.check("D1 回退有明确错误日志", any("音频保存失败" in m for _, m in log.msgs))
    await p4.terminate()
    os.unlink(audio_dir)

    H.finish()


if __name__ == "__main__":
    asyncio.run(main())
