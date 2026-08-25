# PixelFlow Agent Harness Sidecar

本目录是 PixelFlow 的独立 Agent Harness Sidecar。M0 阶段只提供框架无关的稳定 DTO、`AgentEngine` Port、隔离 Skill 快照和无副作用 Fake Tool；它不连接真实模型、不持有用户 Authorization，也不访问 PixelFlow 数据库或 Provider。

真实 DeepSeek Harness SDK/Runtime 已固定为 `0.1.1rc1`。当前 M0 只验证安全 Composition 的 JSON-RPC 启动；Cordis Profile、外部 TypeScript Plugin、Tool Broker 回调与真实模型请求仍须按实施方案继续完成。兼容性结论以 [兼容性报告](../../docs/deepseek-harness-compatibility.md) 为准。

本地验证：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install deepseek-harness-sdk==0.1.1rc1
python -m pip install pytest pytest-asyncio ruff
python -m pytest -q
python -m ruff check src tests
```

本地 macOS ARM64 与 Linux 服务器都必须使用同一 `uv.lock`。生产镜像在原生 Linux 架构执行 `uv sync --locked`，不得复制本机 macOS Runtime 二进制；本机若 `uv` 在 Rosetta x86_64 下运行，应使用 ARM64 Python venv 执行 M0 Runtime 测试。环境变量逐项含义、默认值、重启影响和 Secret 要求见 [配置说明](CONFIGURATION.md)。

真实模型 M0 用例默认跳过，避免本地全量测试意外消耗 token。仅在隔离测试账号已由 Secret Manager 或进程环境注入后，显式设置 `PIXELFLOW_RUN_REAL_M0=1`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3` 和可选的 `PIXELFLOW_M0_DEEPSEEK_MODEL`，再执行 `python -m pytest -m m0_real -q`。测试只报告终态和非空回复，不写入 API key、用户正文或模型原始响应；该用例只验证官方 Composition 的真实模型 Turn，不能替代 Gateway、Tool Broker、Repository 和 SSE 的完整 M0 准入链路。
