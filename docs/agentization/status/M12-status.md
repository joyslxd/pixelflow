# M12 交互 UI、双运行时与 Legacy 迁移

- phase：`in_progress`
- owner：B
- branch：`codex/agent-0.8.4-m12-workspace-ui`
- 依赖：M07
- 当前切片：M12.1
- base Agent SHA：`7510f8fcbe0ac2b3f37aaba73126fa2cfe53a17d`
- 当前唯一写入者：`/root`
- 开始时间：`2026-07-25 09:33:52 +0800`
- M12.1 锁定文件：`web/src/pages/WorkspacePage.tsx`、`web/src/lib/api.ts`、`web/src/lib/supervisor/legacyAdapter.ts`、`web/src/hooks/useSupervisorConversation.ts`、`web/tests/workspaceOrchestrationMode.test.mjs`、`web/tests/useSupervisorConversation.test.mjs`、`web/scripts/run-tests.mjs`、`docs/agentization/status/M12-status.md`、`docs/agentization/test-reports/M12.1.md`
- release_id：R1（M12.3 中间检查点）/ R2（最终）
- checkpoint_slice：M12.3
- checkpoint_status：pending

## 切片

- [x] M12.1 orchestration mode 双运行时挂载（2h）
- [ ] M12.2 拆分 busy/action policy（2h）
- [ ] M12.3 压缩 Notice/排队 badge（2h）
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
