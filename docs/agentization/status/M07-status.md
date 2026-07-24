# M07 前端 Supervisor 事件 Runtime

- phase：`in_progress`
- owner：B
- branch：`codex/agent-0.8.4-m07-web-runtime`
- 依赖：M00
- 当前切片：M07.4
- base SHA：`5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- 当前唯一写入者：已释放（M07.3 完成）
- 开始时间：`2026-07-24 07:19:26 +0800`
- M07.1 完成时间：`2026-07-24 08:30:24 +0800`
- M07.1 已释放文件：`web/src/lib/supervisor/api.ts`、`web/tests/supervisorApi.test.mjs`、`web/scripts/run-tests.mjs`、`docs/agentization/status/M07-status.md`、`docs/agentization/test-reports/M07.1.md`
- M07.2 开始时间：`2026-07-24 08:48:36 +0800`
- M07.2 锁定文件：`web/src/lib/supervisor/events.ts`、`web/tests/supervisorEvents.test.mjs`、`web/scripts/run-tests.mjs`、`docs/agentization/status/M07-status.md`、`docs/agentization/test-reports/M07.2.md`
- M07.2 完成时间：`2026-07-24 09:12:20 +0800`
- M07.2 已释放文件：`web/src/lib/supervisor/events.ts`、`web/tests/supervisorEvents.test.mjs`、`web/scripts/run-tests.mjs`、`docs/agentization/status/M07-status.md`、`docs/agentization/test-reports/M07.2.md`
- M07.3 开始时间：`2026-07-24 10:11:42 +0800`
- M07.3 唯一写入者：已释放
- M07.3 锁定文件：`web/src/lib/supervisor/reducer.ts`、`web/tests/supervisorReducer.test.mjs`、`web/scripts/run-tests.mjs`、`docs/agentization/status/M07-status.md`、`docs/agentization/test-reports/M07.3.md`
- M07.3 完成时间：`2026-07-24 10:50:15 +0800`
- M07.3 已释放文件：`web/src/lib/supervisor/reducer.ts`、`web/tests/supervisorReducer.test.mjs`、`web/scripts/run-tests.mjs`、`docs/agentization/status/M07-status.md`、`docs/agentization/test-reports/M07.3.md`

## 开工检查

- 依赖验证：远端 `feature/agent_0.8.4_boguan` 为 `5826c74`，已包含 `M00-status phase=merged`。
- dev→agent 预检：远端 dev `fb74507` 已是远端 Agent 的祖先，无需修改长期分支。
- 环境说明：本机没有可执行的 PowerShell；已按 `Start-AgentModule.ps1` 的 fail-closed 条件逐项验证干净工作区、远端基线、分支不存在、worktree 路径不存在和唯一写入者后，创建并推送模块分支。

## 切片

- [x] M07.1 API transport（2h）
- [x] M07.2 SSE/cursor/gap/reconnect（2.5h）
- [x] M07.3 reducer 四维状态机（2.5h）
- [ ] M07.4 conversation hook/Abort 隔离（2h）
- [ ] M07.5 legacy snapshot adapter（2h）

## 恢复提示

本模块不改 `WorkspacePage.tsx`。全部使用 fixture/mock server 开发，先证明重复/乱序事件和切换对话安全。

## M07.3 完成记录

- 实现：新增 Supervisor 四维纯 reducer，统一管理 connection、run、compression、input queue 和 cursor/sequence 恢复点；支持本地发送状态、SSE 事件映射、Snapshot hydration 与对话 reset。
- 边界：重复、乱序、跨对话和 sequence gap 均 fail-closed；非法事件不推进恢复点；Snapshot 先隔离旧对话再校验四维交叉约束；`client_input_id` 与 `turn_id` 保持一一绑定；状态只保存固定中文安全错误，不落原始 payload。
- TDD：基线 `244/244`；初始模块缺失 RED；首轮 GREEN `253/253`；自审边界 RED `251/253` 后 GREEN `253/253`；独立审核整改 RED `250/255` 后最终 GREEN `255/255`。
- 验证：`npm test`（`255/255`）、`npm run lint`、`npm run build-prod`、`git diff --check` 全部通过；生产构建只有既存 chunk 体积提醒。未调用真实付费 API。
- 审核：独立只读 reviewer `/root/m07_3_reviewer` 第二轮结论 `Ready`，无 Critical/Important；首轮 3 项 Important 均已通过 RED/GREEN 关闭。
- 环境：本机没有可执行的 PowerShell；按仓库 fail-closed 条件完成人工中文规范检查，无新增配置、无代码注释，未修改锁文件。
- 阶段：M07.3 不是 `phased-rollout-plan.md` 检查点，也不是模块最后一片，不写阶段或模块集成就绪状态。
- 下一步：后续开发者需重新执行安全预检并取得唯一写入权后，才能串行开始 `M07.4 conversation hook/Abort 隔离`。

## M07.2 完成记录

- 实现：新增 Supervisor SSE 事件流 Client，支持 `/agent` 路径、Authorization 延迟注入、CRLF 与分块 frame 解析、cursor 断点续传、`event_id + sequence` 幂等、乱序丢弃、sequence gap 的 Snapshot 恢复、瞬时网络重连、跨对话 fail-closed、Abort/close 和安全错误文案。
- 边界：SSE 连接在未完成 frame 中途结束时丢弃残片并从最后成功 cursor 重连；Snapshot 恢复的普通异常转换为固定安全错误并停止，不进入无限网络重连；消费回调异常和致命协议错误会终止并清理 reader。
- TDD：基线 `226/226`；初始模块缺失 RED；首轮实现 `234/236` 后 GREEN `236/236`；自审边界 RED `238/240` 后 GREEN `240/240`；reader 清理 RED `240/241` 后 GREEN `241/241`；独立审核整改 RED `242/244` 后最终 GREEN `244/244`。
- 验证：`npm test`（`244/244`）、`npm run lint`、`npm run build-prod`、`git diff --check` 全部通过；生产构建只有既存 chunk 体积提醒。未调用真实付费 API。
- 审核：独立只读 reviewer `/root/m07_2_reviewer` 第二轮结论 `Ready`，无 Critical/Important；Minor 建议记录在测试报告中，不阻塞本片。
- 环境：本机没有可执行的 PowerShell；按仓库 fail-closed 条件完成人工中文规范检查，无新增配置、无解释性英文注释，Corepack 签名 keyid 问题下使用 npm 兜底且未修改锁文件。
- 下一步：后续开发者需重新执行安全预检并取得唯一写入权后，才能串行开始 `M07.3 reducer 四维状态机`。

## M07.1 完成记录

- 实现：新增 snapshot、Turn 启动、Interrupt 响应和运行状态 API transport；统一 `/agent` 路径、动态 ID 编码、Authorization、AbortSignal 与安全协议错误。
- TDD：经历模块缺失、reviewer 边界用例、非 2xx 正文安全和非法 JSON 安全映射四阶段 RED/GREEN，最终 Web 聚合测试 `226/226` 通过。
- 验证：`npm test`、`npm run lint`、`npm run build-prod`、`git diff --check` 全部通过；生产构建只有既存 chunk 体积提醒。
- 审核：独立 reviewer `/root/m07_1_review_retry` 最终确认无未解决 Important 或 Minor。
- 环境：Corepack 因签名 keyid 错误不可用，按仓库兜底流程使用 npm；未修改锁文件。
- 外部调用：未调用任何真实付费 API。
- 下一步：后续开发者需重新执行安全预检并取得唯一写入权后，才能串行开始 `M07.2 SSE/cursor/gap/reconnect`。
