# M13.1 / R1 门禁入口修复设计

## 目标

在不重新执行 M13.1 / R1 单槽集成、不更新 Agent、不运行付费 API 的前提下，为 M13.1 / R1 提供可审计、可重复调用且兼容 Windows PowerShell 5.1 的固定权威门禁入口，并把 M13 模块状态恢复为可由下一次独立任务重新触发的 `ready_for_phase_integration:R1`。

## 根因

失败任务使用了 `.git` 下的临时包装脚本。该文件采用无 BOM 的 UTF-8 和 LF 换行，Windows PowerShell 5.1 按系统 ANSI 编码读取时，错误地把中文注释末尾字节与换行组合，导致下一行 `$gateScript` 赋值被解析为注释。随后调用未赋值变量，外层集成器按 fail-closed 规则写入 `phase_integration_blocked`，Agent 保持不变。

M13 权威业务门禁没有开始执行，因此本次修复针对门禁入口和模块状态，不修改 M13 业务实现。

## 方案比较

### 方案一：只修复 `.git` 临时脚本

改动最小，但文件不受 Git 管理，新的 Codex 任务无法可靠发现或审计，仍可能再次生成错误编码的脚本，因此不采用。

### 方案二：改造公共集成器的 GateScript 参数协议

可以从公共集成器直接传递模块、release、slice 和中文门禁基线，但会改变 M01–M13 共用接口，影响范围超过当前 M13.1 / R1 修复，因此暂不采用。

### 方案三：新增 M13.1 / R1 固定门禁入口

在 `scripts/agentization/` 新增受 Git 管理的固定入口，参数只接受候选仓库路径，从冻结的远端 Agent 跟踪引用取得中文工程门禁基线，再精确调用 `Invoke-AgentModuleGate.ps1` 的 `M13 / Phase / R1 / M13.1` 合同。脚本统一保存为 UTF-8 BOM 和 CRLF，符合仓库现有 PowerShell 文件格式。

本次采用方案三。它不改变公共集成接口，且下一次任务可直接把该脚本传给 `Integrate-AgentModule.ps1 -GateScript`。

## 文件与职责

- `scripts/agentization/Invoke-M13R1PhaseGate.ps1`
  - 解析候选仓库。
  - 固定 `M13 / Phase / R1 / M13.1`，禁止调用其他模块或切片。
  - 从 `refs/remotes/origin/feature/agent_0.8.4_boguan` 读取本次 fetch 后冻结的 Agent SHA。
  - 把该 SHA 作为 `ChinesePolicyBaseRef` 调用权威模块门禁。
  - 支持 `PlanOnly`，用于在不运行测试命令的情况下验证固定合同。
- `scripts/agentization/tests/BranchAutomation.Tests.ps1`
  - 把固定入口加入必备脚本列表。
  - 验证 Windows PowerShell 5.1 能解析并执行入口。
  - 验证 `PlanOnly` 返回的仍是 M13 R1 八项非付费命令，且非法或缺失 Agent 跟踪引用时 fail-closed。
- `docs/agentization/status/M13-status.md`
  - 把权威字段恢复为 `phase=ready_for_phase_integration`、`checkpoint_status=ready`。
  - 保留原 `checkpoint_commit`，因为业务实现没有变化。
  - 记录失败根因、修复提交和下一次必须创建全新候选的要求。
- `docs/agentization/test-reports/M13-R1-gate-repair.md`
  - 记录 RED/GREEN、编码验证、限定测试结果和安全边界。

## 调用流程

1. 下一次独立 M13.1 / R1 任务 fetch 并冻结最新 Agent、dev、M13 远端引用。
2. 任务确认 M13 权威状态为 `ready_for_phase_integration:R1`。
3. 任务调用 `Integrate-AgentModule.ps1`，把仓库内的 `Invoke-M13R1PhaseGate.ps1` 作为 `GateScript`。
4. 集成器创建全新候选，固定入口从本地远端跟踪引用取得已冻结 Agent SHA。
5. 固定入口调用 canonical M13 R1 权威门禁。
6. 集成器仍负责远端防漂移检查、原子更新和失败关闭；本修复不改变这些规则。

## 错误处理

- 固定 Agent 跟踪引用不存在、不是 40 位提交 SHA、不是候选 HEAD 的祖先或权威门禁返回失败时，入口立即报错。
- 入口不得回退到状态文件中的旧 base SHA，也不得猜测 Agent 基线。
- 修复完成后只恢复可重试状态；不会把状态写成 `phase_integrated:R1` 或 `awaiting_release_approval:R1`。
- 历史失败候选继续保留用于审计，下一次集成必须创建全新候选。

## 测试策略

1. RED：先增加固定入口合同测试，确认因脚本不存在而失败。
2. GREEN：加入最小固定入口后，在 Windows PowerShell 5.1 下通过解析和 `PlanOnly` 行为测试。
3. 编码验证：断言入口文件以 UTF-8 BOM 开头并使用 CRLF，且 Windows PowerShell 5.1 AST 中存在 Agent SHA 与 canonical gate 的赋值语句。
4. 回归验证：运行 `BranchAutomation.Tests.ps1`，确保既有单槽、白名单、失败关闭和远端原子更新合同不受影响。
5. 中文工程门禁：以当前远端 M13 阻塞提交为基线检查修复提交。
6. 安全验证：确认 Agent、dev、BOARD、MERGE_LOG、生产配置未变化，自动化状态仍为 `automation_local_ready`。

## 非目标

- 不执行 M13.1 / R1 权威业务门禁或单槽集成。
- 不复用历史失败候选。
- 不执行 M13.2 或其他模块门禁。
- 不修改生产 Feature Flag、生产运行模式或 rollout 比例。
- 不调用图片、视频、PPT、剪映、LLM 或其他真实付费 API。
- 不把自动化状态提升为 `automation_active`。
