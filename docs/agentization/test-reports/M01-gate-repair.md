# M01 最终权威门禁修复记录

- 模块：`M01`
- 模块分支：`codex/agent-0.8.4-m01-runtime-store`
- 原阻塞状态提交：`b01c9c29390444b29e11be71bbdc791f44027840`
- 同步 Agent SHA：`ac25357ffbc956a0b76364837846967a5dc576e7`
- 门禁修复提交：`42df90a0ff4b9458c2598373276c4d56207e57fb`
- 自动化状态：`automation_local_ready`
- 验证类型：Windows PowerShell 5.1 + Pester 3.4、项目 Python 3.12、M01 精确 pytest、限定 Ruff、中文工程门禁、独立只读审核

## 根因

首次 M01 单槽候选已经正确组合最新 Agent、最新 dev 和 M01 模块增量，但最新 Agent 的 `Invoke-AgentModuleGate.ps1` 仍保留“M01.5 Event Outbox 权威测试清单尚未建立”的 fail-closed 占位。M01.5 已经完成业务实现和 222 项精确范围验证，却没有把同一清单固化到 canonical gate 及其 Pester 自动化合同，因此集成脚本按设计拒绝更新 Agent，并把模块状态安全写为 `integration_blocked`。

本次只修复门禁定义和自动化测试，不修改 M01 业务实现、migration、配置、其他模块状态或生产运行范围。

## 修复内容

- 先把最新 Agent 门禁基线合入 M01 模块分支，保留 D-009 的项目 Python 3.12 检查、后端模块 fail-closed 和 M13 全量边界。
- M01 Final 使用项目 `backend/.venv` Python，启动时验证解释器主次版本必须为 `3.12`。
- pytest 精确固定 14 个 M01 文件，覆盖 CAS、Event Outbox、migration、Repository、Turn Inbox、冻结合同/config/legacy invariants、旧 Store、对话路由、owner isolation、harness boundary、剪映原子 patch 和 OpenAPI。
- Ruff 只检查 M01 实际生产路径与同一测试集合，不回退到仓库全量。
- Pester 自动化逐项比较 pytest 与 Ruff 参数，证明只有一条 pytest、一条 Ruff、同一个项目 Python，且使用兼容 Pester 3.4 的数组比较写法。

## TDD 与验证

1. RED：先把原“M01 必须 fail-closed”测试替换为精确计划合同；完整 `BranchAutomation.Tests.ps1` 得到 `34 passed, 1 failed`。唯一失败为 canonical gate 仍在 `Invoke-AgentModuleGate.ps1:110` 主动拒绝 M01。
2. GREEN：加入最小 M01 测试和 Ruff 清单后，同一 Pester 文件得到 `35 passed, 0 failed`。
3. 精确 pytest：14 个文件共 `222 passed, 1 warning`，耗时 `21.17s`。唯一 warning 是既有 LangChain `allowed_objects` pending deprecation，本次未修改相关依赖。
4. 限定 Ruff：`All checks passed!`。
5. canonical M01 Final：`Passed=True`、`GateType=Final`、`CommandCount=4`，四项为 `git diff --check`、Python 3.12、M01 精确 pytest、M01 限定 Ruff。
6. 中文工程门禁以冻结 Agent SHA 为基线通过；没有新增或修改配置项，没有新增人工英文注释。

## 独立审核

- reviewer：`/root/m01_5_independent_review`（M01 权威门禁修复复审）。
- 结论：Critical 0、Important 0、Minor 0，`Ready to merge: Yes`。
- reviewer 独立复验 Python `3.12.13`、pytest `222 passed`、Ruff 通过、Pester 3.4 `35 passed / 0 failed`。
- reviewer 确认 pytest 没有带入 M02/M13 或后端全量，Ruff 没有越过 M01 路径，新增 Pester 断言未使用数组不兼容的 `Should Contain`。

## 安全边界与下一步

- 未调用图片、视频、PPT、剪映、LLM 或其他真实付费 API。
- 没有 Jenkins 或其他远端 CI，状态继续保持 `automation_local_ready`，不得写为 `automation_active`。
- 原失败候选只作为历史审计证据，不得复用。
- 本次开发者已明确授权在模块分支恢复 `ready_for_integration` 并 push 后，由同一任务重新冻结最新 Agent、dev 和 M01 远端引用，调用 `Integrate-AgentModule.ps1` 创建全新候选完成最终单槽集成。
