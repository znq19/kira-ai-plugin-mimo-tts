"""验证插件不依赖 data/temp。

背景（v1.4.0 语音发不出的根因）：框架 AsyncTempMonitor 会静默删除
data/temp 下的空子目录；旧版把音频落在这里且仅在加载时创建一次目录，
目录被删后写入必然失败。当前版本的默认路径（base64 直传）与 data/temp
完全无关——本测试锁定这个不变量，并顺带验证框架按需物化文件的能力。
"""
import base64
import asyncio
import os
import time

import _harness as H

H.bootstrap()  # 定位框架 + 切换独立工作目录（含 data/）

from core.chat import MessageChain  # noqa: E402


async def main():
    from core.temp_monitor import AsyncTempMonitor

    # 1) 背景事实：框架清理器会删除 data/temp 下的空子目录（>60s 保护期）
    hazard_dir = os.path.join(os.getcwd(), "data", "temp", "mimo_tts")
    os.makedirs(hazard_dir, exist_ok=True)
    old = time.time() - 600
    os.utime(hazard_dir, (old, old))
    monitor = AsyncTempMonitor(
        folder_path=os.path.join(os.getcwd(), "data", "temp"),
        kira_config=H.FakeConfig(),
        check_interval=300,
        batch_size=20,
    )
    await monitor.cleanup()
    H.check("背景事实：框架清理器会删除 data/temp 下的空子目录", not os.path.exists(hazard_dir))

    # 2) 当前插件与该目录完全解耦：目录不存在也能正常合成与发送
    mod = H.load_plugin_module()
    H.install_mocks(mod)
    plugin = mod.MiMoTTSPlugin(None, {"api_key": "k", "default_voice": "v"})
    result = await plugin.mimo_tts_tag("目录不存在也能正常发送", voice="")
    record = result[0]
    H.check("目录被清理后仍返回 base64 Record（零磁盘依赖）",
            getattr(record, "file_type", None) == "base64")
    H.check("插件没有重建 data/temp/mimo_tts", not os.path.exists(hazard_dir))

    payload = await H.qq_payload(MessageChain([record]))
    b64 = payload[0]["data"]["file"].split("base64://", 1)[1]
    H.check("QQ 出站载荷为语音 record 且解码与原始 WAV 一致",
            payload[0]["type"] == "record" and base64.b64decode(b64) == H.WAV_BYTES)

    # 3) 需要真实文件时由框架按需物化（唯一名，不依赖子目录）
    path = await record.to_path()
    H.check("框架可按需物化文件（to_path，唯一命名）",
            os.path.exists(path) and os.path.getsize(path) == len(H.WAV_BYTES))

    H.finish()


if __name__ == "__main__":
    asyncio.run(main())
