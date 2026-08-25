# M00 本地门禁

M00 不再依赖 PowerShell 或 Pester。macOS 和 Linux 使用受控的 `backend/.venv` 直接执行 Python 门禁：

```bash
backend/.venv/bin/python scripts/agentization/m00_local_gate.py \
  --repository-path . \
  --base-ref <准备推送前的共同基线 SHA>
```

该入口先检查提交、注释、docstring 和配置的中文工程规范，再依次执行后端、Web 与 Sidecar 的固定命令矩阵。Sidecar 在 macOS 强制由 `/usr/bin/arch -arm64` 运行其独立 `.venv`；Linux 使用部署目标架构的同一 venv。任何缺失环境、静态检查、官方安全 Composition 或 Capability Plugin 构建失败都会失败关闭。

默认门禁不设置 `PIXELFLOW_RUN_REAL_M0=1`，因此不会消耗 DeepSeek token。真实 Gateway→Sidecar→模型→Tool Broker 纵向验证仍须由发布负责人显式注入测试凭据后单独执行并记录证据。

只查看将执行的命令而不运行：

```bash
backend/.venv/bin/python scripts/agentization/m00_local_gate.py \
  --repository-path . \
  --base-ref <共同基线 SHA> \
  --plan-only
```

`Invoke-AgentModuleGate.ps1` 仍服务其他历史模块，但拒绝 `ModuleId=M00`，防止 PowerShell 与 Python 两套 M00 门禁发生漂移。
