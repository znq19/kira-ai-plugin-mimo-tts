"""发送管线回归测试（真实 MessageProcessor + QQ 出站转换）。

覆盖：单条语音 / 文字混排拆分 / 嵌套写法与摊平保护 / voice 属性 /
@ 与回复处理 / 同消息多段语音 / 通用拆分器 / 空内容。
"""
import base64
import asyncio

import _harness as H

H.bootstrap()  # 定位框架 + 切换独立工作目录（含 data/）

from core.chat import MessageChain  # noqa: E402
from core.chat.message_elements import At, Record, Text  # noqa: E402


async def main():
    mod = H.load_plugin_module()
    H.install_mocks(mod)
    plugin = mod.MiMoTTSPlugin(None, {"api_key": "k", "default_voice": "默认温柔女声"})
    pipe = H.PipelineHarness(plugin)

    # 0) 注册情况
    from core.plugin.plugin_registry import _plugin_components

    comp = _plugin_components.get(H.PLUGIN_ID)
    H.check("注册了 mimo_tts 标签",
            comp is not None and any(t.get("name") == "mimo_tts" for t in comp.tags))
    H.check("注册了摊平 + 格式整理两个钩子", comp is not None and len(comp.hooks) >= 2)

    # A. 单条语音
    sent, _, _ = await pipe.send("<msg><mimo_tts>你好世界</mimo_tts></msg>")
    H.check("A 单条语音：1 条消息链且含 Record", len(sent) == 1 and H.count_records(sent) == 1)
    if sent:
        payload = await H.qq_payload(sent[0])
        b64 = payload[0]["data"]["file"].split("base64://", 1)[1]
        H.check("A QQ 载荷为 record 且解码与原始 WAV 一致",
                payload[0]["type"] == "record" and base64.b64decode(b64) == H.WAV_BYTES)

    # B. 文字 + 语音混排 -> 拆分成两条干净消息
    sent, _, _ = await pipe.send("<msg><text>先说话</text><mimo_tts>再语音</mimo_tts></msg>")
    H.check("B 混排拆分：[文字] 与 [语音] 各自成链",
            len(sent) == 2
            and isinstance(sent[0].message_list[0], Text)
            and isinstance(sent[1].message_list[0], Record))

    # C. 嵌套且未摊平（LLM 响应钩子未运行）-> 框架解析器只取直接文本，内容为空
    sent, _, _ = await pipe.send("<msg><mimo_tts><text>讲个故事</text></mimo_tts></msg>")
    H.check("C 未摊平时嵌套内容会丢失（框架解析器行为，说明摊平钩子必要性）", len(sent) == 0)

    # D. 嵌套走真实流程（含摊平钩子）-> 修复后正常发语音
    sent, _, flat = await pipe.send(
        "<msg><mimo_tts><text>讲个故事</text></mimo_tts></msg>", dispatch_llm_response=True)
    H.check("D 摊平钩子生效：嵌套被修复后正常发语音",
            flat == "<msg><mimo_tts>讲个故事</mimo_tts></msg>" and H.count_records(sent) == 1)

    # E. 关闭「格式整理」开关时，摊平保护仍然生效（v1.5.0 起不受开关影响）
    plugin.auto_format_fix = False
    sent, _, _ = await pipe.send(
        "<msg><mimo_tts><text>讲个故事</text></mimo_tts></msg>", dispatch_llm_response=True)
    plugin.auto_format_fix = True
    H.check("E 关闭 auto_format_fix 后嵌套保护仍生效", H.count_records(sent) == 1)

    # F. voice 属性保留 + 嵌套内容拼合
    sent, _, flat = await pipe.send(
        '<msg><mimo_tts voice="低沉磁性的男声">前缀<text>中间</text>后缀</mimo_tts></msg>',
        dispatch_llm_response=True)
    H.check("F voice 属性保留且嵌套内容拼合",
            'voice="低沉磁性的男声"' in flat and "前缀中间后缀" in flat and H.count_records(sent) == 1)

    # G. 回复 + 语音 + 文字：语音独立成链，回复随文字链（保持最前）
    sent, _, _ = await pipe.send(
        "<msg><reply>12345</reply><mimo_tts>语音在此</mimo_tts><text>文字在后</text></msg>")
    kinds = [[type(e).__name__ for e in c.message_list] for c in sent]
    H.check("G 回复/语音/文字：语音独立、回复随文字（Reply 在前）",
            kinds == [["Record"], ["Reply", "Text"]])

    # H. 一条消息里两段语音 -> 拆成两条独立语音
    sent, _, _ = await pipe.send("<msg><mimo_tts>第一段</mimo_tts><mimo_tts>第二段</mimo_tts></msg>")
    H.check("H 同消息两段语音拆成两条", len(sent) == 2 and H.count_records(sent) == 2)

    # I. 拆分器对任意 Record 生效（包括官方 <record> 产生的语音）
    voice = Record(record=H.WAV_B64, mime="audio/wav", name="t.wav")
    actions = [MessageChain([At("999"), voice, Text("后文")])]
    await plugin.fix_voice_format(None, actions)
    H.check("I 通用拆分器：任意 Record 独立成链、@ 归文字链",
            len(actions) == 2 and H.count_records(actions) == 1)

    # J. 空内容不发送
    sent, _, _ = await pipe.send("<msg><mimo_tts></mimo_tts></msg>")
    H.check("J 空标签内容不发送", len(sent) == 0)

    H.finish()


if __name__ == "__main__":
    asyncio.run(main())
