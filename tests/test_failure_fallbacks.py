"""失败路径与降级行为。

覆盖：网络异常 -> 文本降级 / HTTP 500 + 超长正文 -> 降级与日志截断 /
空内容与空白内容不发送 / 未配置密钥 -> 文本降级并告警。
"""
import asyncio

import _harness as H

H.bootstrap()  # 定位框架 + 切换独立工作目录（含 data/）


async def main():
    from core.chat.message_elements import Text

    mod = H.load_plugin_module()
    H.install_mocks(mod)
    plugin = mod.MiMoTTSPlugin(None, {"api_key": "k", "default_voice": "v"})

    # T1 正常路径
    result = await plugin.mimo_tts_tag("正常", voice="")
    H.check("T1 正常路径返回 base64 Record", result and result[0].file_type == "base64")

    # T2 网络异常 -> 文本降级（内容不丢）
    H.install_mocks(mod, H.BoomClient)
    result = await plugin.mimo_tts_tag("网络失败", voice="")
    H.check("T2 网络异常降级为文本",
            result and isinstance(result[0], Text) and result[0].text == "网络失败")

    # T3 HTTP 500 且正文超长 -> 降级 + 日志截断
    log = H.install_mocks(mod, H.HttpErrClient)
    result = await plugin.mimo_tts_tag("HTTP错误", voice="")
    errs = [m for lvl, m in log.msgs if lvl == "ERROR"]
    H.check("T3 HTTP 错误降级为文本", result and isinstance(result[0], Text))
    H.check("T3 错误日志被截断（不含 1000 字符正文）",
            bool(errs) and len(errs[-1]) < 400 and ("X" * 1000) not in errs[-1],
            (errs[-1][:60] if errs else "no error log"))

    # T4 / T5 空内容与空白内容不发送
    H.install_mocks(mod)
    result = await plugin.mimo_tts_tag("", voice="")
    H.check("T4 空内容返回空列表", result == [])
    result = await plugin.mimo_tts_tag("   \n ", voice="")
    H.check("T5 空白内容返回空列表", result == [])

    # T6 未配置密钥 -> 文本降级并告警
    log2 = H.install_mocks(mod)
    p2 = mod.MiMoTTSPlugin(None, {"api_key": "", "default_voice": "v"})
    result = await p2.mimo_tts_tag("无密钥", voice="")
    H.check("T6 未配置密钥降级为文本并告警",
            result and isinstance(result[0], Text)
            and any(lvl == "WARN" for lvl, _ in log2.msgs))

    H.finish()


if __name__ == "__main__":
    asyncio.run(main())
