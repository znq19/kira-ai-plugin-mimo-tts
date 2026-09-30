# 测试套件

对**真实 KiraAI 框架**的集成测试（不打桩框架逻辑）：直接驱动 `AsyncTempMonitor`、
`MessageProcessor._parse_xml_msg / send_xml_messages`、`PluginManager` 注册链与
`QQAdapter._process_outgoing_message`，只把 MiMo API 的 httpx 客户端替换为假客户端。

## 运行

```bash
# KIRA_FRAMEWORK_DIR 指向 KiraAI 框架源码目录（需已安装其依赖）
KIRA_FRAMEWORK_DIR=/path/to/KiraAI python3 tests/run_all.py
```

也可以单独运行某个文件：

```bash
KIRA_FRAMEWORK_DIR=/path/to/KiraAI python3 tests/test_pipeline.py
```

可选环境变量：

| 变量 | 说明 |
|------|------|
| `KIRA_FRAMEWORK_DIR` | KiraAI 框架源码目录（推荐显式指定；放在仓库旁或安装目录下时可自动发现） |
| `MIMO_TTS_PLUGIN_PATH` | 指定被测的 main.py（默认：仓库根目录的 main.py） |

## 文件说明

| 文件 | 覆盖内容 |
|------|----------|
| `test_temp_dir_independence.py` | 插件不依赖 `data/temp`（旧版根因场景复现）+ 框架按需物化文件 |
| `test_pipeline.py` | 发送管线：单条 / 混排拆分 / 嵌套摊平 / voice 属性 / @与回复 / 多语音 / 通用拆分器 / 空内容 |
| `test_audio_archive.py` | 可选落盘：路径 Record / QQ 载荷 / 定量与超龄清理 / 写后轻检 / 任务生命周期 / 保存失败回退 |
| `test_failure_fallbacks.py` | 网络异常 / HTTP 错误与日志截断 / 空内容 / 未配置密钥 |
| `_harness.py` | 共享工具（框架定位 / 插件加载 / 假客户端 / 校验器） |
| `run_all.py` | 依次运行全部测试并汇总（失败时退出码非 0） |

## 说明

- 每个测试文件使用独立的临时工作目录（`data/` 基于 cwd 生成），互不污染；
- 测试不是 pytest 用例，请用上面的命令运行；
- 校验通过逐条输出 `PASS`，末尾汇总 `N/N checks passed`；任一失败时退出码为 1。
