# M12 最终集成冲突修复报告

## 结论

M12 已在模块分支上合入冻结的最新 Agent 基线，完成 `WorkspacePage` 六处冲突的组合修复，并通过 M12 Final 权威门禁。模块恢复为 `ready_for_integration`；本报告不更新 Agent、总看板或合并日志，后续最终集成必须创建全新候选。

## 冻结输入

- Agent：`6c25a7bf7eae3a7a806874f5299926898d1c039a`
- dev：`fb7450775a227d891372c19eae1b308045c51e68`
- M12 修复开工状态：`834b36fa6d8300c24f08dee0c2dc0e3429996985`
- M12 修复实现检查点：`5786c5ad23f69b5585d7c8cb56440a8d453f13c3`
- 上次已进入 Agent 的 M12 检查点：`af3f7c1ec64044c6c05307b533e4fac621d3c282`

实现检查点的两个父提交分别是 M12 修复开工状态和冻结 Agent。合并使用普通 `--no-ff`，没有 rebase、force-push，也没有复用首次阻塞候选 `codex/integrate-m12-20260729-004147-406e3815`。

## 修复内容

1. 合并 pending Turn DTO：同时保存 R1 接力字段 `continueLegacy/registrationStatus/runId` 和 M12 目标字段 `replyToMessageId/artifactRefs/interruptId`，并兼容恢复任一旧形状。
2. 普通输入由 `buildSupervisorSubmission()` 构造冻结的 `TurnStartRequest`，只调用一次 `startTurn()`，完整透传 reply、Artifact 和场景 mention 元数据，并解析服务端 `run_id`。
3. interrupt 仅在 `supervisor_v1` 下调用 `respondToInterrupt()`；`assist/shadow` 始终按普通 Turn 登记。成功响应后先持久化移除 pending，失败重试继续复用同一 `client_response_id`，不会新增 Turn。
4. `assist` 使用与用户消息相同的 `crypto.randomUUID()` 登记 Turn，只在服务端输入可执行后接力旧 v2 一次；pending 先写入对话恢复上下文，再向页面和注册 effect 暴露。
5. Snapshot/SSE 权威消息与当前会话 pending 用户消息通过独立投影函数合并；相同 UUID 以服务端消息为准，路由切换时过滤其他会话数据，避免输入在入库前从 UI 消失或跨会话串流。

## 回归与权威门禁

- `corepack pnpm test`：`326/326` 通过。
- `corepack pnpm lint`：通过。
- `corepack pnpm build-prod`：通过，仅有既存 chunk 体积提醒。
- `git diff --check`：通过。
- 中文工程规范：以冻结 Agent SHA 为 `ChinesePolicyBaseRef` 通过仓库脚本检查。
- M12 Final：`Passed=True`、`GateType=Final`、`CommandCount=4`。

新增回归覆盖 assist 同 UUID 登记与单次接力、assist/shadow 禁止 interrupt API、普通 Turn 单次启动及目标元数据、interrupt 幂等响应与成功清理、Snapshot 保留 pending 消息，以及切换对话后的消息和 interrupt 隔离。

## 安全边界

- 没有调用真实图片、视频、PPT、剪映、LLM、PowerMem 或其他付费 API。
- 没有修改生产配置、生产 Feature Flag 或 rollout 比例。
- 没有执行其他模块、切片、发布或 Agent→dev 合并。
- `status/BOARD.md` 与 `integration/MERGE_LOG.md` 在模块恢复阶段保持不变。
- 自动化状态保持 `automation_local_ready`，没有写成 `automation_active`。

## 下一步

模块状态与实现检查点完成中文提交并 push 后，重新 fetch 并冻结最新 Agent、dev 和 M12 远端引用。只有三条远端基线未漂移且单槽锁可独占时，才调用仓库 `Integrate-AgentModule.ps1` 创建全新候选并重新运行 M12 Final；任何失败继续按 fail-closed 写 `integration_blocked`，Agent 保持不变。
