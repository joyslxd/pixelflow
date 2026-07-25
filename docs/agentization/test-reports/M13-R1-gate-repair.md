# M13.1 / R1 单槽门禁入口修复记录

- 模块：`M13`
- release：`R1`
- slice：`M13.1`
- 模块分支：`codex/agent-0.8.4-m13-integration`
- 原阻塞状态提交：`5f444442b05f073b74d9a691aaae06fbf32e0f07`
- 原业务检查点：`e4eb45838d20bf110841aa360f24d699b32ead3d`
- 冻结 Agent：`f03f733115fb0ddd554dcb434f368cef5f09b39e`
- 冻结 dev：`fb7450775a227d891372c19eae1b308045c51e68`
- 自动化状态：`automation_local_ready`

## 根因

首次 M13.1 / R1 单槽候选正确组合了冻结 Agent、dev 和 M13 增量，但任务临时生成在 `.git` 下的包装脚本使用无 BOM UTF-8 和 LF。Windows PowerShell 5.1 按系统 ANSI 编码读取时，把中文注释末尾字节与换行错误组合，导致下一行 `$gateScript` 赋值被吞入注释，随后以 `VariableIsUndefined` 失败。

外层 `Integrate-AgentModule.ps1` 按 fail-closed 规则把 M13 写为 `phase_integration_blocked`，并保持 Agent 不变。M13 权威八项业务门禁没有开始执行，因此不能把历史候选认定为绿色，也不能复用。

## 修复内容

- 新增 `scripts/agentization/Invoke-M13R1PhaseGate.ps1`，只绑定 `M13 / Phase / R1 / M13.1`。
- `Integrate-AgentModule.ps1` 只在 GateScript 调用期间通过进程环境显式传入本次单槽任务冻结的 Agent SHA，并在 `finally` 中恢复调用前环境。
- 固定入口只接受集成器显式传入的冻结 SHA，不再读取可变的远端跟踪引用；同时验证该提交是候选 `HEAD` 的祖先。
- 固定入口把冻结 SHA 作为 `ChinesePolicyBaseRef` 传给候选内 canonical `Invoke-AgentModuleGate.ps1`；缺失引用、非法 SHA、非祖先或 canonical 脚本缺失时全部 fail-closed。
- 脚本使用 UTF-8 BOM 和 CRLF，人工注释与错误信息全部使用中文。
- 原业务 checkpoint `e4eb45838d20bf110841aa360f24d699b32ead3d` 保留为历史证据；M13 权威可重试 checkpoint 前移到包含全部门禁修复的实现提交，下一次必须创建全新候选。

## TDD 与验证

1. 基线：显式设置 Windows PowerShell 5.1 控制台为 UTF-8 后，原 `BranchAutomation.Tests.ps1` 为 `36 passed, 0 failed`。
2. RED：先加入三个固定入口合同但不创建生产脚本，完整 Pester 为 `36 passed, 3 failed`；失败分别来自入口缺失、固定调用无法执行和编码文件不存在。
3. GREEN：加入最小固定入口并规范化编码后，新增参数绑定、Agent 引用 fail-closed、Windows PowerShell 5.1 编码三项合同全部通过；完整 Pester 为 `39 passed, 0 failed`。
4. 固定入口 `PlanOnly` 通过受控测试仓库验证精确传递 `ModuleId=M13`、`GateType=Phase`、`ReleaseId=R1`、`Slice=M13.1` 和冻结 Agent SHA，没有运行 M13 业务命令。
5. 首轮编码合同确认前三字节为 `EF BB BF`，当时 49 个换行均为 CRLF。
6. 独立审查首次结论为 `With fixes`，指出可变远端引用、错误原因不精确的缺失引用测试、未锁定 CRLF 和 checkpoint 文案矛盾四项 Important。
7. 第二轮 RED 为 `37 passed, 4 failed`，精确命中冻结 SHA 传递、缺失/非祖先 SHA、EOL 属性四项合同；修复后 GREEN 为 `41 passed, 0 failed`。
8. `.gitattributes` 固定入口 `eol=crlf`；修复后脚本前三字节为 `EF BB BF`，48 个换行全部为 CRLF，裸 LF 为 0。

## 安全边界

- 本次没有调用 `Integrate-AgentModule.ps1`，没有创建、复用或更新任何集成候选。
- 没有执行 M13.1 / R1 八项权威业务门禁；历史门禁结果仍不得认定为绿色。
- 没有执行 M13.2 或其他模块门禁。
- 没有调用图片、视频、PPT、剪映、LLM 或其他真实付费 API。
- 没有修改 Agent、dev、BOARD、MERGE_LOG、生产配置、生产 Feature Flag 或 rollout 比例。
- 自动化状态保持 `automation_local_ready`，没有 Jenkins 或其他远端 CI，不得记录为 `automation_active`。

## 下一步

开发者另开一个独立任务重新执行 M13.1 / R1 9.10A 单槽集成。该任务必须重新 fetch 并冻结最新 Agent、dev 和 M13 远端引用，从已 push 的 M13 分支/worktree 调用修复后的 `scripts/agentization/Integrate-AgentModule.ps1`，并把同一分支内 `scripts/agentization/Invoke-M13R1PhaseGate.ps1` 作为 `GateScript`，创建全新候选并重新运行八项权威非付费门禁。只有新候选绿色且远端基线未漂移后，才可以更新 Agent 并写 `phase_integrated:R1`、`awaiting_release_approval:R1`。
