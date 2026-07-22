# PixelFlow Agent 化测试与合并闸门

## 1. 原则

- 日常模块测试全部使用 fake/mock，不调用真实图片、视频、PPT、剪映供应商，不产生费用。
- 真实流程只在 M13、获得人工批准并提供临时 Authorization 后执行。
- feature flag 默认 `off`。每次模块合并都要证明关闭开关时当前 v2 行为不变。
- 所有恢复测试都要证明“查询原 job”，不能用重新 `/start` 伪装成恢复成功。
- 代码通过不等于模块完成；测试报告和状态交接必须同时完成。

## 2. 所有模块公共闸门

1. `git diff --check`。
2. 后端变更：相关 pytest + `ruff check` 变更路径。
3. 前端变更：相关 Node 测试 + `corepack pnpm lint` + `corepack pnpm build-prod`。
4. DTO/API 变更：Python/TypeScript 共享 fixture + OpenAPI operation ID 测试。
5. Agent/Skill/恢复逻辑变更：同步最新设计、README、AGENTS；content-app 合同变化再同步 `CONTENT_APP_API_CALLS.md`。
6. 测试不能输出 Authorization、API key、用户原始长 prompt 或完整供应商 URL 查询参数。

当前前端没有统一 `test`/`build` 脚本，部分 UI 合同测试未纳入 package script，且部分脚本使用 Unix `/tmp/rm`。M00 必须先补跨平台聚合入口和分支自动化测试；完成前，以现有独立命令为准，构建使用 `build-prod`，不要使用不存在的 `pnpm build`。

## 3. 模块最小测试集

| 模块 | 必测范围 |
| --- | --- |
| M00 | 新 Python/TS 合同；`test_openapi_operation_ids.py`；前端现有 18 个测试文件聚合；PowerShell 临时仓库验证 dev-sync guard、模块分支/worktree、逐切片串行、阶段检查点白名单、`ready_for_phase_integration/ready_for_integration`、增量/最终单槽自动集成、每日调度和失败不写 Agent 主干；M00-A/M00-B 同源设计/Agent SHA、文件所有权和固定集成顺序 |
| M01 | `test_pixelflow_task_store.py`、`test_pixelflow_conversations_router.py`、owner isolation、CAS/Inbox/Outbox 新测试、剪映原子 patch |
| M02 | checkpointer、run manager、gateway runtime cleanup/recovery、harness boundary、新 graph interrupt/restart |
| M03 | model profile、token budget、context relevance、PowerMem helper、artifact externalization |
| M04 | summarization middleware 现有测试、structured summary、四阈值、压缩锁、事实保护、输入排队 |
| M05 | Supervisor rules、structured output、validator、golden cases、clarification、answer-only 状态不变 |
| M06 | operation 状态机、lease 竞争、crash window、402、job 404/timeout/restart、鉴权不落库 |
| M07 | events、reducer、cursor/gap/reconnect、legacy adapter、conversation switch isolation |
| M08 | `test_image_prepare.py`、image router/asset edit/Borgrise image；前端 image review/main flow/task board |
| M09 | PPT router/SmartPPT skill/intake form/profile；PPT pending 恢复、页级重生成、生产构建 |
| M10 | Borgrise decompose/poll/video router；单/多视频、大 artifact context 测试 |
| M11 | Plan/asset/blueprint/scene package/video router/provider/QC/Jianying 全组；前端 scene/Jianying/main flow |
| M12 | conversation routing、active Plan snapshot、Plan recovery、main flow、task board、新旧 runtime UI |
| M13 | 后端全量、前端全量、mock E2E、三进程本地冒烟、shadow、回滚；批准后真实 verifier |

## 4. 新 Agent 必测场景

### 4.1 智能交互

- 同一对话先生成图片，再说“按这个风格做 30 秒视频”：新建 video workflow，不覆盖 image。
- “把刚才第三张图背景换白”：修改指定 artifact，不重新做整组。
- “继续”：一个 interrupt 时恢复；多个候选时追问。
- “再生成一次”：目标唯一才执行；多个图片/PPT 页/分镜时追问。
- “为什么选这个模型”：只回答，不改变 stage/version/pending job。
- 回复历史 Plan 卡片“恢复这个版本”：走 restore，不生成新方向/新 Plan。
- 对已完成 workflow 说“再做一个”：新 workflow ID；说“重做这一版”：当前 workflow 新 stage version。

### 4.2 压缩与记忆

- 在 59%、60%、71%、72%、84%、85%、91%、92% 边界触发正确策略。
- 压缩前后 `creation_contract/scene_blueprints/asset_manifest/pending_action/operations` hash 完全一致。
- 用户否定要求、目标、时长、比例、模型、未决问题、artifact ID 不丢失。
- SQL 中完整原消息仍在；前端仍显示完整历史。
- 压缩期间连续提交 3 条消息：全部入队、顺序处理、刷新后仍在、不由前端重发。
- 压缩失败：不超窗调用；输入保留；可恢复失败有事件。
- PowerMem 不可用 fail-open；Context CAS/operation 幂等失败 fail-closed。

### 4.3 恢复与并发

- 相同 turn 重试 3 次只产生一个 run。
- 相同计费 command 并发 2 次只产生一个 provider start。
- provider 已成功、checkpoint 写入前崩溃：恢复后查询原 operation，不重新计费。
- SSE 断线、重复事件、乱序、sequence gap 后状态一致。
- 应用重启后从 SQLite/Postgres checkpointer 和 operation store 继续。
- 用户 A 无法读取用户 B 的 workflow/turn/event/operation，统一返回 404 风格。

### 4.4 旧流程兼容

- `frontend_v2` 对话继续使用所有现有 pending job 恢复。
- 有 pending job 的旧对话不能迁移为 `supervisor_v1`。
- `supervisor_v1` 对话刷新后前端不调用任何业务 `/start`。
- 图片 60 秒默认满意；视频不自动结束；场景包无倒计时。
- 表单 X 记录 `form_cancelled`。
- 额度不足暂停并可从同一 operation 恢复。
- 最终下载才完成任务看板“导出交付”。

## 5. 质量指标门槛

| 指标 | 门槛 |
| --- | ---: |
| Supervisor action 黄金集准确率 | ≥ 92% |
| 目标 workflow/artifact 准确率 | ≥ 95% |
| 歧义追问召回率 | ≥ 95% |
| 计费动作误执行 | 0 |
| 关键业务事实/ID/否定约束保留 | 100% |
| 一般摘要事实保留 | ≥ 98% |
| 重复供应商 start | 0 |
| 跨会话/跨用户污染 | 0 |
| turn accepted 事件 P95 | ≤ 300ms |
| 确定性 interrupt 路由 P95 | ≤ 500ms |
| LLM 动作判断 P95 目标 | ≤ 5s |
| 压缩 started 事件 P95 | ≤ 300ms |

## 6. Shadow 规则

- Shadow 只比较决策、标准业务 Command/DTO、prompt 参数准备，绝不第二次调用付费 provider。
- Shadow 禁止 PowerMem record，避免把未执行决策沉淀为经验；允许必要的 fail-open search。
- 记录旧前端实际动作与 Supervisor 建议动作、target、reason code、是否本应追问。
- 任何重复计费、跨会话污染、鉴权泄漏、job 丢失、402 不可恢复或状态覆盖，立即 kill switch。

## 7. 分支自动化门禁

- 模块开始脚本发现最新 dev 不是 Agent 祖先时，必须先创建同步候选；不能从陈旧 Agent 创建模块分支。
- 模块集成候选必须按最新 agent → 最新 dev → 模块分支构建。
- 候选测试期间 dev 或 agent 前进时，最终祖先检查失败，候选不得合入。
- 模拟 dev/模块同时修改同一文件时，脚本必须停在候选分支并保持两个 feature 分支不变。
- 模拟定向测试失败、module status 既非合法 `ready_for_phase_integration` 也非 `ready_for_integration`、脏工作区、错误分支和缺少远端时都必须 fail-closed；任意未列入四阶段计划的中间切片不得伪造阶段检查点。
- 两个集成任务并发时只能有一个进入共享分支合并区；另一个排队或因基线变化重建。
- 切片 Codex 只能 push 当前模块分支；测试必须拒绝创建 `mXX-sYY-*` 切片分支，也必须拒绝直接 push `feature/dev_*` 或 `feature/agent_*`。
- M00-A/M00-B 必须从同一个已包含评审设计的 Agent SHA 创建；两分支 HEAD 必须能证明该 SHA 是共同祖先。
- M00-B 修改规范 Python DTO/fixture、M00-A 修改 B 锁定的 `web/**` 合同路径、任一分支修改模块汇总状态时，文件所有权门禁必须失败。
- M00 集成必须按 `最新 Agent + 最新 dev → m00-a → 定向测试 → m00-b → 跨端/全量/flag-off/自动化门禁` 执行；交换顺序、遗漏分支或两个 Codex 同写临时候选都必须 fail-closed。
- 在两个 worktree 强制 checkout 同一模块分支、两个对话尝试并发 push 同一模块或前一切片未 push 就启动下一片时，分支策略测试必须拒绝执行并提示模块内串行。
- 普通模块合法中间检查点写 `ready_for_phase_integration`、最后一片写 `ready_for_integration` 后必须触发单槽流水线；绿色更新 Agent/BOARD/MERGE_LOG，并分别写 `phase_integrated/merged`；失败分别写 `phase_integration_blocked/integration_blocked` 且 Agent 不变。
- 同一模块第二次检查点只允许集成 `last_integrated_commit..checkpoint_commit`，测试必须拒绝 force-push/rebase、重复集成旧 commit 和跳过前置切片。
- 每天 02:00 调度必须验证 dev 无领先、可安全同步和失败不污染三种路径；已结束的 Codex 对话不能被当作调度器。
- 未配置 Gitee/Jenkins/Codex Automation 时自动化状态只能是 `automation_local_ready`；远端调度、保护分支和绿色自动合并实际验证后才能是 `automation_active`。

## 8. 四阶段上线门禁

| 批次 | 上线前必须证明 | 立即停止条件 |
| --- | --- | --- |
| R1 压缩可感知版 | 60/72/85/92 阈值、关键事实 hash 100% 保留、压缩期输入排队、SSE 续传、刷新恢复、flag-off 旧流程等价 | 丢失合同/ID/否定约束/当前输入，前端重发，恢复后重复 start |
| R2 视频会话 Agent | 视频黄金对话、Supervisor target、Plan/合同/场景包继承、Operation 幂等、重启/402/部分失败恢复、视频人工结束 | 任何计费误执行、重复供应商 start、视频自动结束或目标不唯一仍执行 |
| R3 其余 intent | 图片/编辑、PPT、视频分析 mock E2E，跨 workflow 切换、artifact 定向引用、旧 API 与 flag-off 回归 | 串 workflow/artifact/user、图片编辑绕过原图或参数确认、PPT 整体误重生 |
| R4 全量 | 五主流程+直接图片编辑全矩阵、Shadow 无副作用、保持 `primary+四类intent+100%`、kill switch/排空、批准后的真实冒烟 | 跨会话污染、鉴权泄漏、job 丢失、无法回滚、真实凭据进入日志/状态 |

阶段候选进入 Agent 不等于自动发布生产。每次 R1–R4 运行模式、`enabled_intents`、Feature Flag 或真实付费验证都需要发布负责人显式批准并把证据写入 `integration/MERGE_LOG.md`；当前阶段比例固定100%，不验证随机百分比灰度或用户白名单。

## 9. 建议命令

后端定向示例：

```powershell
cd backend
uv run pytest tests/test_pixelflow_task_store.py tests/test_pixelflow_conversations_router.py -q
uv run ruff check pixelflow app/gateway tests
```

前端当前示例：

```powershell
cd web
corepack pnpm test:conversation-routing
corepack pnpm test:active-plan-snapshot
corepack pnpm test:plan-message-recovery
corepack pnpm test:jianying-draft
corepack pnpm test:workflow-task-board
node --test tests/mainFlowContract.test.mjs tests/jianyingDraftUiContract.test.mjs tests/videoSceneUiContract.test.mjs tests/reviewWindow.test.mjs
corepack pnpm lint
corepack pnpm build-prod
```

M00 完成后使用新聚合命令替代逐条 Node 命令，并把实际命令写回本文件和 `AGENTS.md`。

真实验证脚本 `scripts/verify_video_asset_manifest_flow.py` 可能调用外部环境、下载文件和产生费用，只能在 M13 人工批准后运行；其 Authorization 只存在进程环境变量中，不能写入文档或测试报告。
