# M12 交互 UI、双运行时与 Legacy 迁移

- phase：`integration_blocked`
- owner：B
- branch：`codex/agent-0.8.4-m12-workspace-ui`
- 依赖：M07
- 当前切片：`M12.5`
- base Agent SHA：`7510f8fcbe0ac2b3f37aaba73126fa2cfe53a17d`
- M12.3 模块分支基线：`12bcff09e37ea7fc61b51fa044dbf0e250933b5e`
- M12.4 模块分支基线：`b69a13eaebfec53bdccf7e374e1824c01f14058d`
- M12.5 模块分支基线：`c0e3d94ad308794d2fb1914bcc5b66c625f8506b`
- 当前唯一写入者：`尚未领取`
- 开始时间：`—`
- M12.5 已释放文件：`web/src/lib/supervisor/workspaceProjection.ts`、`web/src/lib/supervisor/reducer.ts`、`web/src/hooks/useSupervisorConversation.ts`、`web/src/pages/WorkspacePage.tsx`、`web/scripts/run-tests.mjs`、`web/tests/supervisorWorkspaceProjection.test.mjs`、`web/tests/supervisorReducer.test.mjs`、`web/tests/workspaceOrchestrationMode.test.mjs`、`docs/agentization/status/M12-status.md`、`docs/agentization/test-reports/M12.5.md`
- M12.4 已释放文件：`web/src/lib/authStorage.ts`、`web/src/lib/supervisor/turnSubmission.ts`、`web/src/pages/WorkspacePage.tsx`、`web/scripts/run-tests.mjs`、`web/tests/authStorage.test.mjs`、`web/tests/supervisorTurnSubmission.test.mjs`、`docs/agentization/status/M12-status.md`、`docs/agentization/test-reports/M12.4.md`
- M12.3 已释放文件：`web/src/lib/supervisor/reducer.ts`、`web/src/lib/supervisor/runtimeNotice.ts`、`web/src/components/chat/ConversationRuntimeNotice.tsx`、`web/src/components/chat/ChatPanel.tsx`、`web/src/pages/WorkspacePage.tsx`、`web/scripts/run-tests.mjs`、`web/tests/supervisorReducer.test.mjs`、`web/tests/supervisorRuntimeNotice.test.mjs`、`docs/agentization/status/M12-status.md`、`docs/agentization/test-reports/M12.3.md`
- release_id：`R1`
- checkpoint_slice：`M12.3`
- checkpoint_commit：`4d9931811f23eb306f2bf8b8dc33357aacbb46e4`
- last_integrated_commit：`af3f7c1ec64044c6c05307b533e4fac621d3c282`
- checkpoint_status：`blocked`

## 切片

- [x] M12.1 orchestration mode 双运行时挂载（2h）
- [x] M12.2 拆分 busy/action policy（2h）
- [x] M12.3 压缩 Notice/排队 badge（2h）
- [x] M12.4 reply/artifact/interrupt/mention 元数据（2.5h）
- [x] M12.5 消息/进度/历史/task board 投影（2.5h）

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
- locked files：`无`
- integration failure evidence：`docs/agentization/test-reports/M12-final-integration-blocked-20260729.md`

## M12 最终单槽集成阻塞记录

- 阻塞时间：`2026-07-29 08:41:51 +0800`
- 冻结远端：Agent `6c25a7bf7eae3a7a806874f5299926898d1c039a`；dev `fb7450775a227d891372c19eae1b308045c51e68`；M12 `1bb603f08a01e3a2b0fc238ceb6240f1b49ee447`。
- 候选：`codex/integrate-m12-20260729-004147-406e3815`，由仓库 `Integrate-AgentModule.ps1` 按最新 Agent、最新 dev 和 M12 最终增量创建并保留。
- 阻塞原因：合入 M12 增量时，`web/src/pages/WorkspacePage.tsx` 出现 6 个语义耦合冲突块，涉及 R1 `assist/shadow` Turn 接力、M12 目标元数据、interrupt 单路响应以及 Snapshot/SSE 消息与任务投影；不能依据文本优先级安全自动选择。
- 门禁状态：候选未完成合并，因此 M12 Final 权威门禁未开始；不得沿用模块分支上的历史绿色结果代替候选门禁。
- 长期分支：Agent 保持冻结 SHA 未变化；dev 未修改；M12 仅追加 `integration_blocked` 状态和本安全证据。
- 安全边界：未调用真实图片、视频、PPT、剪映、LLM、PowerMem 或其他付费 API；未修改生产配置，未执行其他模块或切片，自动化状态保持 `automation_local_ready`。
- 恢复要求：由 M12 模块修复任务基于最新 Agent 明确整合上述两组语义并重新执行 M12 Final 门禁；恢复为 `ready_for_integration` 并 push 后，必须重新人工启动 9.10A，创建全新候选，禁止复用本 blocked 候选。

## M12.4 完成记录

- 完成时间：`2026-07-28 19:39:41 +0800`
- 实现：新增独立 `buildSupervisorSubmission()` 提交 Service，将同一 Composer 输入确定性转换为冻结的 `TurnStartRequest` 或 interrupt response；reply、Artifact 引用、场景素材 `asset_id/storyboard_message_id/artifact_ref/mention_ref` 均沿现有 DTO 提交，不新增 `mention_refs` HTTP 字段。
- 目标安全：消息引用冲突和素材跨会话归属失败关闭；Artifact 引用按出现顺序去空、去重并只接受 `artifact:` 引用；素材先复制为合法 JSON，错误只返回固定安全提示，不回显用户内容或底层异常。
- interrupt 幂等：存在合法 `interrupt_id` 时只调用 `respondToInterrupt()`，以同一 `client_input_id` 作为稳定 `client_response_id`，不额外创建 Turn；普通输入继续携带最新 `expected_context_version` 走 `startTurn()`。
- 场景 mention：Supervisor 只释放分镜面板的“引用/删除素材”元数据入口，并附带当前 `conversation_id`；替换、生成、重试和其他旧供应商动作仍由 `legacyArtifactActionsEnabled` 隔离，`frontend_v2` 原路径不变。
- TDD：首轮以缺少 `turnSubmission.ts` 得到预期 `TS6053` RED，最小实现后达到 `302/302`；自审新增 Supervisor 场景引用可达性合同得到 `302 passed / 1 failed` RED，修复后达到 `303/303`；补齐可信 content-app 桥接回归后最终 `npm test` 为 `304/304`。
- 验证：主审核与独立 Reviewer 均完成 `npm test`（`304/304`）、`npm run lint`、`npm run build-prod` 和 `git diff --check`，全部通过；生产构建仅有既存 chunk 体积提醒，未调用真实或付费 API。
- 独立审核：`/root/m12_4_reviewer` 只读审核结论 `Ready`，Critical/Important/Minor 均为 0；Reviewer 未修改文件、未提交、未推送，并确认冻结 DTO、interrupt 单路由、跨会话隔离、旧运行时兼容和 M12.5 边界。
- 中文规范：push 前使用仓库 `Test-ChineseEngineeringPolicy.ps1` 对本片独立中文提交、人工注释和配置边界执行检查；本片新增/修改说明均为中文，无新增配置、依赖或锁文件。
- 状态：M12.4 是普通中间切片，M12 保持 `in_progress`；不写 `ready_for_phase_integration` 或 `ready_for_integration`，不运行阶段/最终模块门禁，不更新 `status/BOARD.md` 或集成记录。
- 下一步：`M12.5 消息/进度/历史/task board 投影`；唯一写入权和本片文件锁已释放，必须由开发者重新启动任务并复用当前模块分支/worktree，本任务到此停止。

## M12.5 完成记录

- 完成时间：`2026-07-28 20:30:40 +08:00`
- 实现：新增独立 `workspaceProjection` Application Service，把 Supervisor Snapshot 与 SSE 的持久化消息、Artifact、Workflow 和 interrupt 严格转换为页面 ViewModel；Reducer 统一维护恢复状态，`WorkspacePage` 在连接完成且对话归属一致时用权威消息替换旧 detail 历史，并复用既有 task board。
- 消息与事件：`message.upserted` 按 `client_message_id/message_id` 稳定 ID 原位更新，Artifact 与消息在同一事件原子投影；`workflow.progressed` 按 `workflow_id/stage_version/updated_at` 幂等更新。冻结合同的直载 DTO 与当前后端包装形状均兼容，重复/旧 sequence 不回退，sequence gap 不应用越级数据而等待 Snapshot。
- 历史与任务看板：Snapshot 完整恢复消息材料、Artifact、Workflow 和当前 interrupt；最新 Workflow 确定性映射为既有 `WorkflowProgressSnapshot`，图片、视频、PPT 显示任务看板，`video_analysis` 不显示；最终交付状态仍只读取最新消息 Artifact 的下载记录。
- 运行时隔离：全部投影先校验 `conversation_id`，切换对话后旧 Hook 与旧事件不能写入新页面；恢复的 interrupt 仅在状态所属对话等于当前目标对话时进入 M12.4 单路响应。`frontend_v2` 继续使用原历史与 runner，Supervisor 未新增或恢复任何旧供应商 `/start`。
- TDD：首轮新增 M12.5 合同测试并接入聚合编译，`npm test` 因 `workspaceProjection.ts` 缺失得到预期 `TS6053` RED；最小实现转绿后补齐直载事件、sequence gap、安全错误、多工作流目标和 task board 回归，最终 `npm test` 为 `315/315`。
- 验证：`npm test`（`315/315`）、`npm run lint`、`npm run build-prod` 和 `git diff --check` 全部通过；生产构建仅有既存 chunk 体积提醒，未调用真实或付费 API。
- 独立审核：`/root/m12_5_reviewer` 首轮结论 `Not Ready`，Critical 0、Important 1、Minor 0，指出事件只接受包装形状；兼容冻结直载 DTO 并保留当前后端包装后，二轮结论 `Ready`，Critical/Important/Minor 均为 0。Reviewer 全程只读，未修改文件、未提交、未推送、未调用真实或付费 API。
- 中文规范：以开工基线 `c0e3d94ad308794d2fb1914bcc5b66c625f8506b` 到固定实现提交执行仓库中文工程规范脚本并通过；新增人工说明、状态和报告均为中文，本片未新增配置、依赖或锁文件。
- Final 门禁：固定实现提交 `4753d62a7509ea8b5725bd324a07e495f45d42f6` 后，执行 `Invoke-AgentModuleGate.ps1 -ModuleId M12 -GateType Final`，结果 `Passed=True / CommandCount=4`；`git diff --check`、`corepack pnpm test`（`315/315`）、`corepack pnpm lint`、`corepack pnpm build-prod` 全部通过，生产构建仅有既存 chunk 体积提醒。
- 工具链说明：本机 Corepack 初始因上游签名 keyid 轮换失败，捆绑的 `pnpm 9.9.0` 又不能解析仓库现有 pnpm 10 工作区配置；将 Corepack 运行时固定到机器已安装的 `pnpm 10.34.4` 后原样重跑权威门禁并通过，未修改 `package.json`、`pnpm-workspace.yaml` 或锁文件。
- 状态：M12.5 是模块最后一片，完整 M12 Final 门禁绿色，M12 已写 `ready_for_integration`；唯一写入权和全部文件锁已释放，不更新 `status/BOARD.md` 或集成记录。
- 边界：不创建切片子分支，不修改两个长期 feature 分支，不自动进入其他切片或单槽集成；当前自动化状态为 `automation_local_ready`。
- final checkpoint commit：`4753d62a7509ea8b5725bd324a07e495f45d42f6`
- final gate status：`passed`
- locked files：`无`
- integration failure evidence：`候选 codex/integrate-m12-20260729-004147-406e3815 已保留；Agent 未更新；错误类型 RuntimeException`
