# 🎙️ MiMo TTS

[![version](https://img.shields.io/badge/version-1.4.0-blue?style=flat-square)](#)

> 用自然语言描述音色，让 AI 用那种声音说话。

基于[小米 MiMo v2.5 VoiceDesign](https://platform.xiaomimimo.com) 的 KiraAI 语音合成插件。不用选编号、不用调参数——你怎么跟人描述声音，就怎么告诉 AI。

---

## 🚀 怎么用

直接对 AI 说：

- 「用温柔的女声念一下这段话」
- 「用纪录片旁白的语气跟我讲话」
- 「用活泼可爱的少女声线说句早上好」

AI 会自动生成 `<mimo_tts>` 标签触发语音合成，把文字+语音一起发出。不需要记任何命令。

---

## 🎤 音色描述示例

| 场景 | 描述 |
|------|------|
| 日常聊天 | 一个年轻温柔的女性声音，语速适中，语调自然亲切 |
| 解说旁白 | 低沉磁性的男声，缓慢沉稳，像纪录片旁白 |
| 娱乐卖萌 | 活泼俏皮的少女声线，语速稍快，充满活力 |
| 正式场合 | 沉稳大气的中年男性播音员，字正腔圆 |

描述里可以带上**性别、年龄、嗓音质感、语速、情感风格**，越具体效果越好。

---

## 🔧 工作方式

插件注册了 `<mimo_tts>` 消息标签，与 KiraAI 官方 `<record>` 同机制：**决策、合成、发送三步在同一个 LLM 轮次完成**。没有额外工具调用，不浪费 token，也不打断持续对话。

```xml
<mimo_tts voice="低沉磁性的男声">要说的内容</mimo_tts>
```

省略 `voice` 属性则使用配置中的默认音色。普通语音（不需要自定义音色）仍可用官方 `<record>`。

---

## 🩹 语音格式自动修复（v1.3.0 起，默认开启）

QQ 上语音和 @ / 回复混在同一条消息里会无法正常显示。插件在发送前自动处理：

- 每条语音拆成独立消息
- @ 和回复自动移到文字消息上
- 对官方 `<record>` 同样生效

可在插件设置中关闭。

---

## 📦 安装与配置

### 1. 获取 API 密钥

在 [MiMo 平台](https://platform.xiaomimimo.com) 注册并获取 API Key。

### 2. 安装

把插件文件夹放入 `data/plugins/`，无需重启。

### 3. 配置

在 KiraAI 插件设置中填入 **MiMo API 密钥**，可选项：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| MiMo API 密钥 | 必填 | - |
| 默认音色描述 | 未指定音色时使用 | 年轻温柔女性声音 |
| 语音格式自动修复 | 开启/关闭 | 开启 |

---

## ❓ FAQ

<details>
<summary>合成失败怎么办？</summary>
会自动降级为文字发送，消息不会丢。检查 API 密钥和账户余额。
</details>

<details>
<summary>和官方 <code>&lt;record&gt;</code> 有什么区别？</summary>
<code>&lt;record&gt;</code> 音色固定；<code>&lt;mimo_tts&gt;</code> 可自然语言自定义音色。两者互不冲突。
</details>

<details>
<summary>支持英文吗？</summary>
支持中英文文本合成。音色描述建议用中文写。
</details>

---

## 📝 版本信息

- **当前版本**：v1.4.0 · 兼容 KiraAI v2.6.1+
- **原作者**：[xxynet](https://github.com/xxynet) · 维护者：[znq19](https://github.com/znq19)

<details>
<summary>更新日志</summary>

### v1.4.0
- XML 解析前自动摊平 `<mimo_tts><text>…</text></mimo_tts>` 嵌套，避免内容丢失
- 合成前清洗残留标签，防止 TTS 把标签名念出来

### v1.3.0
- 新增语音格式自动修复，解决 QQ 上语音显示异常
- 对官方 `<record>` 同样生效，可配置开关

### v1.2.0
- 改为 `<mimo_tts>` 标签模式，与官方 `<record>` 同机制

### v1.0.0
- 初始版本，基于 MiMo v2.5 VoiceDesign，支持自然语言描述音色

</details>

---

<p align="center">
  <sub>Made with ❤️ by xxynet & znq19 | Powered by MiMo v2.5 VoiceDesign</sub>
</p>
