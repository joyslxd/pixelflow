# M12 交互 UI、双运行时与 Legacy 迁移

- phase：`ready_for_phase_integration`
- owner：B
- branch：`codex/agent-0.8.4-m12-workspace-ui`
- 依赖：M07
- 当前切片：M12.3
- base Agent SHA：`7510f8fcbe0ac2b3f37aaba73126fa2cfe53a17d`
- M12.3 模块分支基线：`12bcff09e37ea7fc61b51fa044dbf0e250933b5e`
- 当前唯一写入者：`尚未领取`
- 开始时间：`2026-07-25 11:16:05 +0800`
- M12.3 已释放文件：`web/src/lib/supervisor/reducer.ts`、`web/src/lib/supervisor/runtimeNotice.ts`、`web/src/components/chat/ConversationRuntimeNotice.tsx`、`web/src/components/chat/ChatPanel.tsx`、`web/src/pages/WorkspacePage.tsx`、`web/scripts/run-tests.mjs`、`web/tests/supervisorReducer.test.mjs`、`web/tests/supervisorRuntimeNotice.test.mjs`、`docs/agentization/status/M12-status.md`、`docs/agentization/test-reports/M12.3.md`
- release_id：`R1`
- checkpoint_slice：`M12.3`
- checkpoint_commit：`4d9931811f23eb306f2bf8b8dc33357aacbb46e4`
- last_integrated_commit：—
- checkpoint_status：`ready`

## 切片

- [x] M12.1 orchestration mode 双运行时挂载（2h）
- [x] M12.2 拆分 busy/action policy（2h）
- [x] M12.3 压缩 Notice/排队 badge（2h）
- [ ] M12.4 reply/artifact/interrupt/mention 元数据（2.5h）
- [ ] M12.5 消息/进度/历史/task board 投影（2.5h）

R1 规则：M12.3 完成后运行 `R1-assist-ui` 阶段门禁，绿色后写 `ready_for_phase_integration` 并停止；开发者按执行手册 9.10A 人工触发单槽候选，绿色进入 Agent 后写 `phase_integrated`。M12.4 仍需开发者再次手动启动，继续复用本模块分支。

## 恢复提示

不要同时大拆 `MessageBubble`、GenParamsDialog、StoryboardPanel。先把 `WorkspacePage` 变成 runtime 选择和 ViewModel 挂载点。

## M12.1 完成记录

- 完成时间：`2026-07-25 10:06:20 +0800`
- 实现：Workspace 按服务端 `conversation.orchestration_mode` / `orchestration_version` 二选一挂载 `frontend_v2` 旧 runner 或 `supervisor_v1` Hook；普通 context 伪造、非法版本、非法 context 和旧 pending 均 fail-closed 到旧运行时。
- 首次发送：创建会话使用服务端返回的归属；历史会话恢复期间暂存输入，Supervisor 首个 Turn 等待 Snapshot/SSE 连接，不进入旧 `/messages/start`。
- 动态 CAS：每个 Supervisor Turn 前后刷新 Snapshot，使用当前 `context_version`；Hook 暴露直接版本读取，避免 reducer 无状态回退时使用旧版本。
- 动作隔离：Supervisor 模式不向 ChatPanel、StoryboardPanel、参数弹窗、Plan 修订和旧画布传递会触发供应商 `/start` 的 Legacy handler；只保留打开、预览和下载。
- TDD：初始 RED `6 failed / 277 passed`；整改后 `npm test` `285/285`，覆盖伪造归属、非法版本/context、创建 mode、恢复排队、Hook 无网络/切换、动态 CAS 和旧动作隔离。
- 验证：`npm run lint`、`npm run build-prod`、`git diff --check` 全部通过；生产构建仅有既存 chunk 体积提醒；未调用真实付费 API。
- 独立审核：`/root/m12_1_reviewer` 最终结论 `Ready`，Critical/Important/Minor 均为 0；未修改文件、未提交、未推送。
- 中文规范：本机无 `pwsh`/Windows PowerShell，按脚本 fail-closed 条件完成中文提交、注释、状态、报告和配置变更人工检查；本片无新增配置、依赖或锁文件。
- 状态：M12.1 已完成但 M12 模块仍 `in_progress`；本片不是阶段检查点，不写 `ready_for_phase_integration` 或 `ready_for_integration`，不更新 `status/BOARD.md`，不触发集成。
- 提交与推送：本状态和测试报告随 M12.1 独立中文提交推送到 `origin/codex/agent-0.8.4-m12-workspace-ui` 后停止。
- 下一步：`M12.2 拆分 busy/action policy`；需由开发者重新启动并复用本模块分支/worktree，M12 内部继续严格串行。

## M12.2 完成记录

- 完成时间：`2026-07-25 10:48:53 +0800`
- 实现：新增 `WorkspaceInteractionPolicy`，将页面单一忙碌状态拆成 composer、artifact 和 runtime 三类策略，并由 `WorkspacePage` 统一计算后分别传给 `ChatPanel` 与 `Composer`。
- 旧运行时兼容：`frontend_v2` 继续以旧业务忙碌态、参数弹窗和 Plan 修订选择作为输入框与产物动作闸门；全新空会话仍允许发送首条输入。
- Supervisor 交互：运行、压缩和输入排队期间允许继续提交 Turn；连接进入 `fatal` 或编排归属未决时输入失败关闭；运行态不统一锁死预览、打开和下载等只读产物入口，供应商 `/start` 动作仍由 M12.1 的运行时归属策略裁剪。
- 运行时状态：单独暴露综合编排恢复、旧运行时忙碌、Supervisor run、压缩、连接和本地待提交 Turn 的 `runtime.busy`，通过 `aria-busy` 保留 M12.3 Notice 接入点。
- TDD：新增策略测试首次运行得到 `resolveWorkspaceInteractionPolicy is not a function` 的预期 RED；完成最小实现和组件接线后，`npm test` 最终为 `289/289` 通过。
- 验证：`npm run lint`、`npm run build-prod`、`git diff --check` 全部通过；生产构建仅有既存 chunk 体积提醒；未调用真实付费 API。
- 独立审核：`/root/m12_2_reviewer` 最终结论 `Ready`，Critical/Important/Minor 均为 0；Reviewer 未修改文件、未提交、未推送、未调用真实或付费 API。
- 中文规范：本机无 `pwsh`/Windows PowerShell，按脚本 fail-closed 条件完成人工等价检查；新增和修改的解释性注释、状态、报告均使用中文，本片无新增配置、依赖或锁文件。
- 状态：M12.2 已完成但 M12 模块仍为 `in_progress`；本片不是阶段检查点，不写 `ready_for_phase_integration` 或 `ready_for_integration`，不更新 `status/BOARD.md`，不触发集成。
- 提交与推送：本状态和测试报告随 M12.2 独立中文提交推送到 `origin/codex/agent-0.8.4-m12-workspace-ui` 后停止。
- 下一步：`M12.3 压缩 Notice/排队 badge`；该片是 R1 阶段检查点，必须由开发者重新启动并复用本模块分支/worktree，本任务不得自动进入。

## M12.3 完成记录

- 完成时间：`2026-07-25 11:30:09 +0800`
- 实现：新增独立的 `ConversationRuntimeNotice` 和稳定 ViewModel，在 Composer 上方展示压缩开始、不确定或明确进度、完成、可恢复失败及排队 badge；`WorkspacePage` 只负责从 Supervisor reducer 状态装配 ViewModel。
- 运行时合同：reducer 兼容 M04 已进入 Agent 的真实 `status/action/step` 压缩事件；缺少 `progress_percent` 时保持不确定进度或已有快照进度并推进 cursor，显式非法百分比仍失败关闭。
- 排队权威：badge 只从 `inputQueue` 中的 `queued` 项派生数量和最小位置；压缩终态清理兼容字段中的旧计数，避免 Snapshot 恢复后长期显示虚高队列。
- 双运行时隔离：Notice 只在服务端归属为 `supervisor_v1` 时启用；`frontend_v2` 不展示，也未改变旧 runner、供应商 `/start` 或 M12.4 元数据提交合同。
- 可访问性：开始、完成、队列使用 `status`，失败使用 `alert`，统一 `aria-live=polite`；只有后端明确提供百分比时展示带 `aria-valuenow` 的进度条，不伪造精确进度。
- TDD：首次新增 ViewModel 合同测试时以模块缺失形成预期 RED，最小实现后达到 `293/293`；首轮审核发现真实 M04 payload 漂移后，新增合同测试得到 `3 failed / 293 passed` 的二次 RED，整改后 `npm test` 最终 `296/296` 通过。
- 验证：`npm run lint`、`npm run build-prod`、`git diff --check` 全部通过；生产构建仅有既存 chunk 体积提醒；未调用真实或付费 API。
- 独立审核：`/root/m12_3_reviewer` 首轮发现 2 项 Important；整改后二轮结论 `Ready`，Critical/Important/Minor 均为 0，Reviewer 未修改文件、未提交、未推送、未调用真实或付费 API。
- 中文规范：本机无 `pwsh`/Windows PowerShell，按脚本 fail-closed 条件完成人工等价检查；新增和修改的人工说明、状态和报告均为中文，本片无新增配置、依赖或锁文件。
- 阶段门禁：固定实现提交 `4d9931811f23eb306f2bf8b8dc33357aacbb46e4` 后，按 `Invoke-AgentModuleGate.ps1` 的 `M12 / Phase / R1 / M12.3` 权威范围执行 `git diff --check`、`corepack pnpm test`、`corepack pnpm lint`、`corepack pnpm build-prod`，全部通过；聚合测试为 `296/296`，生产构建只有既存 chunk 体积提醒。
- 状态：`R1-assist-ui` 阶段门禁和中文规范人工等价检查均绿色，M12 已登记 `ready_for_phase_integration`、`checkpoint_status=ready`；唯一写入权和 M12.3 文件锁已释放，等待开发者人工启动单槽阶段集成。
- 边界：不更新 `status/BOARD.md` 或集成记录，不执行 M12.4/M12.5，不创建切片子分支，不修改两个长期 feature 分支。
