# Task 14 实施报告：R2 视频 status 402 持久化暂停与恢复

## 当前结论

- 状态：`review_fix_local_verified:Task14 / awaiting_independent_slot_integration`。
- 对比基线：`b1d2a64b754982fe0eef5578f5762a8e97b1a4d8`；Tasks 1–7 最终实现 HEAD：`d32adf4c548e934fe171421d3c45f39cd1fbd57f`。
- 生产继续保持 R1 `assist / [] / 100 / true`。未修改 dev/prod 配置，未执行生产迁移、真实付费 Provider、R2 发布、`primary(video)`、M13.3、独立单槽集成、push 或 Agent→dev 合并。
- Task 8 最终门禁已按实际结果执行并记录：候选聚焦、Ruff、Web、中文与静态隔离门禁通过，后端全量只剩计划登记的 Runtime 公开导出基线失败。首轮 spec compliance 的三个 Important 已通过正式计划同步、实际配置校正和公共 E2E 四事件实采关闭；最终 spec compliance 与 code quality 独立复审均为 Critical `0`、Important `0`、Minor `0`。本文件不得替代四个正式跟踪文档。

## Tasks 1–7 提交链

- Task 1：`95654b5`。
- Task 2：`ea471f0`、`1a4feab`、`2e0ca32`。
- Task 3：`5e76d3c`。
- Task 4：`567c92f`、`db53eaf`、`66cd0b1`。
- Task 5：`660b228`、`9fdfee6`。
- Task 6：`38ad218`、`f9b0ed3`。
- Task 7：`d32adf4`。

## 权威 402 链路

- Memory/SQL 在同一临界区或事务内提交 `quota_pause_revision`、Operation、pause/resume `external_job.quota_state_changed` Event、租约和 due-operation 隔离；Event ID、cursor 与 run ID由 `job_id + revision + quota_state` 稳定派生。
- Recovery Runtime 先投递 quota，再投递 completion，最后才允许轮询。2026-08-03 人工裁定把后台物理线程冻结为 `quota-{paused|resumed}:<event_id>:v<workflow_version>`：Event ID 仍是 Outbox、投递 claim 与业务提交幂等键，同 Event 加同目标版本才重放同一线程，不同目标版本隔离以避免 LangGraph 无 CAS 的 TOCTOU。正常公共授权响应继续原 pause interrupt 的版本化线程，不能把四条 Event 或同 Event 的不同目标版本误写成固定一个物理 checkpoint。
- 公共链路不调用 `recover_manually()`，不预置 `WAITING_USER` Turn。fake Provider status 真实返回 402 后，经 Recovery Runtime、QuotaStateHandler 和 Supervisor Graph 打开原 Turn 的 `authorization_required`；新 Authorization 由公开 FastAPI interrupt response 提交，经精确 `source_interrupt_id` 和 Memory/SQL 权威校验恢复原 job。
- 连续两轮 402 分别产生 revision `1` 与 `2`。第二轮携旧 revision `1` 固定返回 `409 video_quota_resume_stale` 且零副作用；有效 revision `2` 随后恢复成功。内部 job、provider job、attempt、原 Turn 不变，Provider start 增量为 `0`。
- 从首个 Snapshot 开始，九次普通 response、两次 pause、两次 resume、五次 worker completion 和下载均从上一 cursor 逐段消费 SSE，并与公开 Snapshot 六类状态逐项等值。
- 泄漏扫描覆盖四个实际 Authorization 完整值及四个裸 token，共八个 marker；扫描 Repository Turns/Operations/全部 Events、两轮 pause/resume checkpoint、逐段 Snapshot/SSE、projection messages 和安全日志。有效 quota 凭据各消费一次并销毁。
- 11 项真实故障矩阵覆盖 checkpoint 前后退出、Provider start 后恢复、公共 402、timeout、failed、404/expired、部分失败、跨租户、模型档案失效和 Handler 重启缺失。

## 公共 fake E2E 四事件实采

2026-08-03 用不落盘的一次性只读脚本复用 `_live_client`、`_start_scene_generation`、两轮真实 worker 和公共 interrupt response；脚本 exit `0`，耗时 `8.2s`，没有输出 Authorization。Repository 四条 quota Event 与 `InMemorySaver` 历史按 `last_action_key` 实际关联如下：

| revision / state | event_id | sequence | cursor | run_id | 实际 checkpoint thread / 状态版本 |
| --- | --- | ---: | --- | --- | --- |
| `1 / paused` | `evt_job_quota_a1442601d2d5de0f75362e2a73e37462` | `42` | `cursor_job_quota_a1442601d2d5de0f75362e2a73e37462` | `run_job_quota_a1442601d2d5de0f75362e2a73e37462` | `quota-paused:evt_job_quota_a1442601d2d5de0f75362e2a73e37462:v7` / `7` |
| `1 / resumed` | `evt_job_quota_ad30457a19a24836600c24d6324bfeb6` | `52` | `cursor_job_quota_ad30457a19a24836600c24d6324bfeb6` | `run_job_quota_ad30457a19a24836600c24d6324bfeb6` | 继续第一轮 pause thread `quota-paused:evt_job_quota_a1442601d2d5de0f75362e2a73e37462:v7` / `9` |
| `2 / paused` | `evt_job_quota_049ffe78939f244f82d02d7379fcbb5a` | `56` | `cursor_job_quota_049ffe78939f244f82d02d7379fcbb5a` | `run_job_quota_049ffe78939f244f82d02d7379fcbb5a` | `quota-paused:evt_job_quota_049ffe78939f244f82d02d7379fcbb5a:v10` / `10` |
| `2 / resumed` | `evt_job_quota_4d2d696d1a380d411f57f30bb25c2153` | `64` | `cursor_job_quota_4d2d696d1a380d411f57f30bb25c2153` | `run_job_quota_4d2d696d1a380d411f57f30bb25c2153` | 继续第二轮 pause thread `quota-paused:evt_job_quota_049ffe78939f244f82d02d7379fcbb5a:v10` / `11` |

因此正常公共链路是“四条业务 Event、两条版本化 pause 物理线程”；两条 resume Event 仍各有独立 Event ID/cursor/run ID，并在原 pause 线程历史中成为新的 `last_action_key`。只有未提交 resume Event 被后台 Outbox 接管时，才建立 `quota-resumed:<resume_event_id>:v<target_workflow_version>` 隔离线程。

## Task 7 冻结证据

- 公共 E2E 与故障矩阵：`40 passed, 1 warning`，exit `0`。
- Task 7 后端冻结集合：`361 passed, 1 warning`，exit `0`。
- Graph/Dispatcher 专项：`52 passed, 1 warning`，exit `0`。
- Ruff：`All checks passed!`，exit `0`。
- 公共 402 独立进程稳定性：`10/10` 通过。
- 完整值与裸 token 守卫：`8 passed`。
- Task 7 最终独立复审：Critical `0`、Important `0`、Minor `0`。

## Task 8 最终门禁记录

| 序号 | 命令 | exit / 结果 |
| --- | --- | --- |
| 1 | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_runtime_r2_integration.py tests/test_agent_runtime_video_live_e2e.py tests/test_agent_video_live_handler.py tests/test_agent_video_live_operations.py tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_operation_completion.py tests/test_agent_runtime_repositories.py tests/test_agent_runtime_migration.py tests/test_agent_runtime_event_outbox.py tests/test_agent_runtime_gateway_readiness.py tests/test_agent_runtime_turn_executor.py tests/test_agent_runtime_context_assembler.py -q` | exit `0`；`607 passed, 1 warning in 196.69s`；外层 `200.49s` |
| 2 | `cd backend; .venv\Scripts\python.exe -m pytest -q` | exit `1`；`6016 passed, 48 skipped, 2 failed, 7 warnings in 487.67s`；失败为 `test_video_package_keeps_all_public_exports_in_clean_process` 与计划已登记的 `test_agent_runtime_package_keeps_public_export_identity_and_errors`，后续门禁已停止 |
| 2a | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_video_live_capabilities.py::test_video_package_keeps_all_public_exports_in_clean_process -q` | 主控授权只补三项测试映射后 exit `0`；`1 passed, 1 warning in 5.01s`；外层 `7.92s` |
| 2b | `cd backend; .venv\Scripts\python.exe -m pytest -q` | exit `1`；`6017 passed, 48 skipped, 1 failed, 7 warnings in 418.55s`；外层 `423.34s`；唯一失败为计划登记 Runtime 基线，无新增失败 |
| 3 | `cd backend; .venv\Scripts\python.exe -m ruff check .` | exit `0`；`All checks passed!`；外层 `0.21s` |
| 4 | `cd web; corepack pnpm test:agent-runtime-contracts` | exit `0`；`18 passed, 0 failed`；外层 `3.90s` |
| 5 | `cd web; corepack pnpm test` | exit `0`；`405 passed, 0 failed`；外层 `9.89s` |
| 6 | `cd web; corepack pnpm lint` | exit `0`；`tsc --noEmit` 无错误；外层 `5.91s` |
| 7 | `cd web; corepack pnpm build-prod` | exit `0`；`2432 modules transformed`、`built in 15.07s`；外层 `22.88s`；只有既有大 chunk 警告 |
| 8 | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 -RepositoryPath (Get-Location).Path -BaseRef b1d2a64 -HeadRef HEAD` | 首次 exit `1`，外层 `10.25s`；根因是跨语言正则把 Python `**mapping`/`*ITERABLE` 误判为块注释。Pester 回归 RED 为 `14 passed, 1 failed in 57.32s`；主控授权最窄修复后 GREEN 为 `15 passed, 0 failed in 45.48s`。真实门禁重跑 exit `0`，`Passed=True / CommitCount=17 / ChangedPathCount=51`，外层 `10.3s`。该提交范围仍不覆盖当前未提交 Task 8 差异 |
| 9 | `git diff --check b1d2a64..HEAD`；`git diff --check` | 均 exit `0`；工作区检查仅有 Git 的既有 LF→CRLF 提示，无 whitespace error |
| 10 | `git diff --exit-code b1d2a64..HEAD -- backend/config.prod.yml`；同范围 dev/prod；工作区 dev/prod | 三项均 exit `0`，配置差异为 `0` |
| 11 | 计划规定的 `rg` 禁用占位符扫描 | exit `1` 且无输出，表示 `TODO/TBD/FIXME/待定/稍后补/后续补` 无命中 |
| 12 | 除当前查询 PowerShell 外扫描 worktree 相关 pytest/operation-recovery/pixelflow 进程 | exit `0` 且无输出，残留进程 `0` |
| 13 | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_runtime_video_live_e2e.py -q` | spec 整改后 exit `0`；`10 passed, 1 warning in 13.42s`；外层 `18.0s` |
| 14 | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_video_live_operations.py -k "quota_pause_event_opens_one_graph_interrupt_on_original_turn or second_quota_pause_waits_without_poisoning_checkpoint or concurrent_quota_pauses_isolate_losing_checkpoint or quota_resume_event_atomically_restores_domain_state_once" -q` | exit `0`；`8 passed, 216 deselected, 1 warning in 12.34s`；外层 `18.4s` |
| 15 | `cd backend; .venv\Scripts\python.exe -m ruff check tests/test_agent_runtime_video_live_e2e.py tests/test_agent_video_live_operations.py` | exit `0`；`All checks passed!` |

### Gate 2 根因分析

- 两个失败定向复现为 `2 failed, 1 warning in 8.19s`，exit `1`。
- `agent_runtime`：实际公开导出 `10` 项，旧测试只期望 `4` 项；`b1d2a64..HEAD` 对包入口和测试均零差异，属于计划已登记基线。
- `video`：Task 4 提交 `567c92f` 新增三个 quota 稳定公开导出，包实际 `41` 项，穷举测试仍是旧 `38` 项；这是本候选新增的测试合同缺口。主控 Agent 已授权只补三项映射，定向 GREEN 为 `1 passed`；production 与 Runtime 基线未改。

### Gate 8 根因与最窄修复

- 门禁函数 `Get-AddedCommentText()` 的首个跨语言正则无条件识别行首 `*+`，因此 Python 调用参数 `**claim.turn.model_dump(...)` 和集合解包 `*FLOW_AUTHORIZATIONS`、`*AUTHORIZATION_TOKENS` 被错误当成人工注释；脚本在 `b1d2a64..HEAD` 无既有差异。
- 新增 Pester 回归同时冻结两条边界：Python `**MAPPING`/`*ITERABLE` 必须放行，JavaScript/TypeScript 的 `/* ... * English ... */` 仍必须拒绝。RED 唯一失败为 Python 用例，JS/TS 用例保持通过。
- 主控 Agent 授权后，只把独立 `\*+` 从跨语言正则移到 `.js/.jsx/.ts/.tsx/.mjs/.cjs` 扩展分支；保留 `/*`、`//` 和其他既有识别。完整 Pester 与真实中文工程门禁随后通过，未扩大到其他注释解析或生产代码。

## 停止点

Task 8 最终双阶段复审已清零，允许在当前隔离候选提交本地证据。该提交不授权 push、独立单槽集成、部署、生产迁移、真实付费 Provider、R2 发布、`primary(video)` 切换、M13.3 或 Agent→dev 合并。
