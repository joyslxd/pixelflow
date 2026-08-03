# R2 视频 status 402 持久化暂停与恢复本地候选报告

- 日期：`2026-08-03`
- 任务：`Task 14 / Task 8 文档与最终门禁`
- 分支：`codex/r2-live-video-handler`
- 对比基线：`b1d2a64b754982fe0eef5578f5762a8e97b1a4d8`
- Tasks 1–7 实现 HEAD：`d32adf4c548e934fe171421d3c45f39cd1fbd57f`
- 状态：`review_fix_local_verified:Task14 / awaiting_independent_slot_integration`

## 结论与边界

Tasks 1–7 已在隔离开发候选中补齐视频 status 402 的持久化暂停、授权恢复和真实公共链路证据。该候选不是生产发布结果，也尚未进入 Agent 长期分支。Task 8 已按实际命令完成候选聚焦、全量、Web、中文、配置隔离和进程残留检查：候选相关门禁通过，后端全量仍保留一个计划登记的 Runtime 公开导出基线失败。首轮 spec compliance 的三个 Important 已全部整改并在第二轮关闭；最终 spec compliance 与 code quality 独立复审均为 Critical `0`、Important `0`、Minor `0`。

生产继续保持 R1 `assist / enabled_intents=[] / 100% / context_compaction=true`，即简写 `assist / [] / 100 / true`。未修改 dev/prod 配置，未执行生产数据库迁移、真实付费 Provider、生产 `primary(video)`、R2 发布、M13.3、独立单槽集成、push 或 Agent→dev 合并。

## Tasks 1–7 中文提交链

| Task | 提交 | 结果 |
| --- | --- | --- |
| Task 1 | `95654b5` | 增加 `quota_pause_revision` 迁移、ORM 合同和 Memory/SQL 往返；非负约束与有审计数据时的安全降级保持一致 |
| Task 2 | `ea471f0`、`1a4feab`、`2e0ca32` | 在 Memory 临界区或 SQL 事务内原子提交 pause/resume revision 与 quota Outbox；补齐字面前缀、sequence 串行化和 owner 校验 |
| Task 3 | `5e76d3c` | 接入 quota Coordinator、Dispatcher 和恢复顺序；先投递 quota，再投递 completion，最后才允许轮询 Provider |
| Task 4 | `567c92f`、`db53eaf`、`66cd0b1` | 用同一投影 Service 原子更新原 Workflow、原 Turn、interrupt、Event 和 Graph checkpoint；收紧并发隔离与数据库锁序 |
| Task 5 | `660b228`、`9fdfee6` | 复用 `retry_failed`，只接受 `job_id + quota_pause_revision` 安全 patch；新 Authorization 仅在当前恢复调用栈消费，并重新派生规范 Workflow namespace |
| Task 6 | `38ad218`、`f9b0ed3` | Gateway 依赖完整时全有装配 Quota/Completion Handler、Executor、Bridge 与恢复 Runtime；不完整时继续安全接力 v2；指标观察失败开放且重放不重复 |
| Task 7 | `d32adf4` | 移除旧的手工恢复/预置 Turn 自证，补齐公共 FastAPI 402、Supervisor/Graph 恢复身份链、Memory/SQL 原子 stale 校验、两轮 revision、逐段 SSE、完整凭据泄漏扫描和故障矩阵 |

计划与设计冻结提交为 `6500b1a`、`164aefe`；Tasks 1–7 的最终实现检查点为 `d32adf4`。

## Memory/SQL、revision、Event 与 checkpoint

- `quota_pause_revision` 从 `0` 开始，只在 Provider status 进入 402/`quota_paused` 时单调递增。Memory 与 SQL 使用同一 CAS 合同；revision、Operation、quota Event、投递租约和 due-operation 隔离在同一临界区或事务内提交。
- 每个 revision 分别生成 `paused` 与 `resumed` 两个幂等 `external_job.quota_state_changed` Event。Event ID、cursor 和 run ID 只由内部 `job_id + revision + quota_state` 的版本化 SHA-256 派生；同一状态重放回读同一身份，不同 revision 或不同状态不会碰撞。
- pause Event 完成 Graph 投影前禁止恢复 Provider 轮询；resume Event 被领取或投递完成前同样禁止轮询。有效租约阻止其他 worker 越权领取，租约过期后允许同一 Event ID 接管。
- 2026-08-03 人工裁定冻结两层幂等：Event ID 是 Outbox、投递 claim 与业务提交幂等键；后台 Handler 的物理 checkpoint 使用 `quota-paused:<event_id>:v<workflow_version>` 或 `quota-resumed:<event_id>:v<workflow_version>`。LangGraph 没有 CAS，同 Event 不同目标版本必须隔离，避免旧版本通过检查后晚写覆盖新版本；只有同 Event 加同目标版本才重放同一线程并精确比较内容，不能声称同 Event 永远只有一个物理 checkpoint。正常公共授权响应继续原 pause interrupt 的版本化线程，resume Event 仍由 `last_action_key` 与提交 claim 绑定；后台接管未提交的 resume Event 时才使用该 resume Event 自己的 `quota-resumed:*:v*` 线程。
- 公共全流程连续触发 revision `1` 与 revision `2`。第二次暂停时，携 revision `1` 的旧响应在任何 response/message/context/Event 写入前由 Memory/SQL 权威校验拒绝，FastAPI 固定返回 `409 video_quota_resume_stale`；随后 revision `2` 的响应仍可恢复同一 Operation。

## 公共 FastAPI 402 真实链路

本轮证据不再调用 `recover_manually()`，也不预置 `WAITING_USER` Turn。测试从 `POST /agent/conversations` 和 `POST /agent/conversations/{conversation_id}/turns/start` 创建真实 `supervisor_v1` 视频会话，先让 fake Provider 的真实 status 边界返回 402，再由 `OperationRecoveryRuntime` 持久化 pause Event，生产 `QuotaStateHandler` 把原 Workflow/Turn 投影为 `authorization_required` 中断。

用户随后通过 `POST /agent/conversations/{conversation_id}/interrupts/{interrupt_id}/responses` 提交新 Authorization 与冻结的 `retry_failed` 动作。Supervisor Graph 把已校验的精确 `source_interrupt_id` 传给视频 Handler；Handler 用中断主键回读权威 occurrence/thread/Event/version/payload/owner，再由 Repository 原子校验 Operation 的 owner、conversation、workflow、stage、job、revision、status、provider job、轮询计划与租约。只有校验全部成立才把凭据交给当前调用栈并恢复原 job。

两轮 402 恢复都保持内部 `job_id`、`provider_job_id`、attempt 与原 Turn 不变；每轮恢复前后 Provider start 增量均为 `0`，该 provider job 的累计 start 次数仍为 `1`。402 不生成 completion；最终 Provider 成功后才产生既有 `external_job.state_changed` completion，并进入原视频审核节点。

### 公共 fake E2E 四事件实采

2026-08-03 使用不落盘的一次性只读脚本复用 `_live_client`、`_start_scene_generation`、两轮真实 M06 worker 和公共 interrupt response，随后从实际 Repository 与 `InMemorySaver` 历史采集并核对 Event；脚本 exit `0`，耗时 `8.2s`，没有输出 Authorization。四条 `external_job.quota_state_changed` 的顺序严格为 `(1, paused) → (1, resumed) → (2, paused) → (2, resumed)`，实际记录如下：

| revision / quota_state | event_id | sequence | cursor | run_id | 实际 checkpoint 物理线程与关联状态版本 |
| --- | --- | ---: | --- | --- | --- |
| `1 / paused` | `evt_job_quota_a1442601d2d5de0f75362e2a73e37462` | `42` | `cursor_job_quota_a1442601d2d5de0f75362e2a73e37462` | `run_job_quota_a1442601d2d5de0f75362e2a73e37462` | `quota-paused:evt_job_quota_a1442601d2d5de0f75362e2a73e37462:v7`；`last_action_key` 对应状态版本 `7` |
| `1 / resumed` | `evt_job_quota_ad30457a19a24836600c24d6324bfeb6` | `52` | `cursor_job_quota_ad30457a19a24836600c24d6324bfeb6` | `run_job_quota_ad30457a19a24836600c24d6324bfeb6` | 公共同步响应继续第一轮 pause 线程 `quota-paused:evt_job_quota_a1442601d2d5de0f75362e2a73e37462:v7`；该线程历史中 `last_action_key` 对应状态版本 `9` |
| `2 / paused` | `evt_job_quota_049ffe78939f244f82d02d7379fcbb5a` | `56` | `cursor_job_quota_049ffe78939f244f82d02d7379fcbb5a` | `run_job_quota_049ffe78939f244f82d02d7379fcbb5a` | `quota-paused:evt_job_quota_049ffe78939f244f82d02d7379fcbb5a:v10`；`last_action_key` 对应状态版本 `10` |
| `2 / resumed` | `evt_job_quota_4d2d696d1a380d411f57f30bb25c2153` | `64` | `cursor_job_quota_4d2d696d1a380d411f57f30bb25c2153` | `run_job_quota_4d2d696d1a380d411f57f30bb25c2153` | 公共同步响应继续第二轮 pause 线程 `quota-paused:evt_job_quota_049ffe78939f244f82d02d7379fcbb5a:v10`；该线程历史中 `last_action_key` 对应状态版本 `11` |

该实采说明四条业务 Event 不等于四个物理 checkpoint 线程：两条 resume Event 在正常公共响应路径中分别进入原 pause 线程，同时仍以各自 Event ID、cursor、run ID 和 `last_action_key` 保持业务幂等；如果进程在提交前退出并由后台 resume Outbox 接管，才按人工裁定使用 `quota-resumed:<resume_event_id>:v<target_workflow_version>` 的隔离线程。

## 逐段 SSE 与凭据安全

- 从首个 intake Snapshot 开始，在九次普通 interrupt response、两次 quota pause、两次 quota resume、五次 M06 worker completion 和最终下载后，都从上一 cursor 消费 SSE。独立 reducer 逐段重建 run、workflow、messages、interrupt、context version、cursor 与 sequence，并与同一时点公开 Snapshot 精确比较。
- 完整流程最终 fake Provider start 计数保持分镜 `4`、合并 `2`、QA `1`、剪映 `0`；相同 `client_input_id` 重放和三次 Snapshot 刷新新增 start 为 `0`。
- 泄漏守卫覆盖四个实际测试 Authorization 完整值，以及分别去掉 `Bearer ` scheme 后的四个裸 token，共八个精确安全 marker。扫描边界包括 Repository Turns/Operations/全部 quota、completion 与 projection Events，两轮 pause/resume Graph checkpoint values/interrupts，逐段 Snapshot/SSE，projection messages 和安全日志。
- 两轮有效 quota Authorization 都经真实消费函数各消费一次并在 `finally` 销毁；stale revision 的 Authorization 不会污染原 Turn 或 Operation。带 `secret_only` 字段的 Pydantic 对抗子类继续在严格 DTO 边界失败关闭。

## 故障矩阵与独立复审

11 项真实故障矩阵覆盖：Graph checkpoint 前退出、checkpoint 后退出、Provider start 后但完成事件前退出、status 402、timeout、failed、HTTP 404/expired、三分镜部分失败、跨租户引用、模型档案失效、Handler 重启后缺失。每项逐一断言固定安全 reason、attempt、provider job ID、Operation/Turn 状态、原 Turn/interrupt 身份、重复 Provider start 为 `0`、跨租户可见对象为 `0`；checkpoint 场景使用生产 Supervisor Graph 与 SQLite 持久 Checkpointer。

Task 7 最终独立复审结论为 Critical `0`、Important `0`、Minor `0`。Task 8 初轮 code quality 为 Critical `0`、Important `0`、Minor `0`，spec compliance 为 Critical `0`、Important `3`、Minor `0`；三项问题已按人工裁定同步正式计划、校正配置事实并补充公共 E2E 四事件实采，第二轮 spec compliance 与最终 code quality 回归复审均为 Critical `0`、Important `0`、Minor `0`。

## Task 7 冻结门禁证据

| 门禁 | 命令范围 | 结果 |
| --- | --- | --- |
| 公共 E2E 与真实故障矩阵 | `test_agent_runtime_video_live_e2e.py` + `test_agent_runtime_r2_integration.py` | `40 passed, 1 warning`，exit `0` |
| Task 7 后端冻结集合 | 计划 Gate 2 显式文件集合 | `361 passed, 1 warning`，exit `0` |
| Graph/Dispatcher 专项 | Graph composition/dispatcher/interrupt/state 与相邻恢复用例 | `52 passed, 1 warning`，exit `0` |
| Ruff | `.venv\Scripts\python.exe -m ruff check .` | `All checks passed!`，exit `0` |
| 公共 402 稳定性 | 独立进程连续执行公开 402 用例 | `10/10` 通过 |
| 历史 token 正负集合 | 完整值与裸 token 守卫 | `8 passed` |
| 差异与配置 | `git diff --check`、dev/prod 配置 diff、pytest/Ruff 进程 | 无错误、配置差异 `0`、残留进程 `0` |

## Task 8 最终门禁记录

以下结果来自本轮对 `d32adf4` 加 Task 8 工作区的实际执行；后端全量的既有基线失败单独保留，不表述为全绿。

| 门禁 | 完整命令 | 本轮结果 |
| --- | --- | --- |
| 后端 12 文件聚焦 | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_runtime_r2_integration.py tests/test_agent_runtime_video_live_e2e.py tests/test_agent_video_live_handler.py tests/test_agent_video_live_operations.py tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_operation_completion.py tests/test_agent_runtime_repositories.py tests/test_agent_runtime_migration.py tests/test_agent_runtime_event_outbox.py tests/test_agent_runtime_gateway_readiness.py tests/test_agent_runtime_turn_executor.py tests/test_agent_runtime_context_assembler.py -q` | `607 passed, 1 warning in 196.69s`，exit `0` |
| 后端全量 | `cd backend; .venv\Scripts\python.exe -m pytest -q` | exit `1`；`6016 passed, 48 skipped, 2 failed, 7 warnings in 487.67s`。失败为 `test_video_package_keeps_all_public_exports_in_clean_process` 与计划已登记的 `test_agent_runtime_package_keeps_public_export_identity_and_errors`；按计划已停止后续门禁并进入根因分析 |
| 视频包公开导出定向 RED/GREEN | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_video_live_capabilities.py::test_video_package_keeps_all_public_exports_in_clean_process -q` | RED 已在全量及两用例定向复现；授权只补三项测试映射后 GREEN 为 `1 passed, 1 warning in 5.01s`，exit `0` |
| 后端全量重跑 | `cd backend; .venv\Scripts\python.exe -m pytest -q` | exit `1`；`6017 passed, 48 skipped, 1 failed, 7 warnings in 418.55s`；唯一失败为计划登记且可从 `b1d2a64` 复现的 `test_agent_runtime_package_keeps_public_export_identity_and_errors`，无新增失败 |
| 后端 Ruff | `cd backend; .venv\Scripts\python.exe -m ruff check .` | `All checks passed!`，exit `0`，外层 `0.21s` |
| Web Agent Runtime 合同 | `cd web; corepack pnpm test:agent-runtime-contracts` | `18 passed, 0 failed`，exit `0`，外层 `3.90s` |
| Web 全量 | `cd web; corepack pnpm test` | `405 passed, 0 failed`，exit `0`，外层 `9.89s` |
| Web lint | `cd web; corepack pnpm lint` | `tsc --noEmit` 无错误，exit `0`，外层 `5.91s` |
| Web 生产构建 | `cd web; corepack pnpm build-prod` | `2432 modules transformed`、`built in 15.07s`，exit `0`，外层 `22.88s`；只有既有大 chunk 警告 |
| 中文工程门禁 RED/GREEN | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 -RepositoryPath (Get-Location).Path -BaseRef b1d2a64 -HeadRef HEAD` | 首次 exit `1`：Python `**mapping`/`*ITERABLE` 被跨语言块注释正则误判。Pester RED 为 `14 passed, 1 failed in 57.32s`；最窄脚本修复后的完整 Pester GREEN 为 `15 passed, 0 failed in 45.48s`。真实门禁重跑 exit `0`，`Passed=True / CommitCount=17 / ChangedPathCount=51`，外层 `10.3s`；该提交范围不覆盖当前未提交 Task 8 差异 |
| 提交与工作区 diff-check | `git diff --check b1d2a64..HEAD`；`git diff --check` | 均 exit `0`；工作区只有 Git 的 LF→CRLF 提示，无 whitespace error |
| dev/prod 配置隔离 | `git diff --exit-code b1d2a64..HEAD -- backend/config.prod.yml`；同范围 dev/prod；工作区 dev/prod | 三项均 exit `0`，配置差异 `0` |
| 禁用占位符 | 计划规定的 `rg` 组合扫描 | exit `1` 且无输出，表示 `TODO/TBD/FIXME/待定/稍后补/后续补` 无命中 |
| 进程残留 | 排除当前查询 PowerShell 后扫描 worktree 相关 pytest/operation-recovery/pixelflow 进程 | exit `0` 且无输出，残留进程 `0` |
| spec 整改公共 E2E | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_runtime_video_live_e2e.py -q` | `10 passed, 1 warning in 13.42s`，exit `0`，外层 `18.0s` |
| spec 整改版本化 checkpoint 聚焦 | `cd backend; .venv\Scripts\python.exe -m pytest tests/test_agent_video_live_operations.py -k "quota_pause_event_opens_one_graph_interrupt_on_original_turn or second_quota_pause_waits_without_poisoning_checkpoint or concurrent_quota_pauses_isolate_losing_checkpoint or quota_resume_event_atomically_restores_domain_state_once" -q` | `8 passed, 216 deselected, 1 warning in 12.34s`，exit `0`，外层 `18.4s` |
| spec 整改最小 Ruff | `cd backend; .venv\Scripts\python.exe -m ruff check tests/test_agent_runtime_video_live_e2e.py tests/test_agent_video_live_operations.py` | `All checks passed!`，exit `0` |

后端全量失败后以两用例定向复现，结果为 `2 failed, 1 warning in 8.19s`。根因分为两类：

- `agent_runtime` 用例是已登记基线问题：包实际稳定导出 `10` 项，测试仍硬编码旧的 `4` 项；`b1d2a64..HEAD` 对 `agent_runtime/__init__.py` 和该测试的差异均为 `0`。
- `video` 包用例是本候选新增回归：Task 4 提交 `567c92f` 为包增加 `VideoOperationQuotaProjection`、`VideoOperationQuotaProjectionService`、`VideoOperationQuotaStateHandler` 三个稳定公开导出，实际 `__all__` 为 `41` 项，但穷举测试仍保留旧的 `38` 项映射。主控 Agent 已授权只在同一测试映射补齐三项；定向 GREEN 为 `1 passed`，没有修改 production 或顺手修复 Runtime 基线。

中文工程门禁失败的根因位于 `Get-AddedCommentText()`：首个跨语言正则无条件把行首独立 `*+` 识别为块注释内容，误伤 Python 星号解包。主控 Agent 授权的修复只把该标记移到 `.js/.jsx/.ts/.tsx/.mjs/.cjs` 扩展分支，保留 `/*`、`//` 与其他既有识别。新增 Pester 用例证明 Python `**MAPPING`/`*ITERABLE` 放行，同时证明 JavaScript 和 TypeScript 块注释中的英文人工说明继续拒绝；没有修改其他注释解析或生产逻辑。

## 停止点

Task 8 只交付本地候选文档和可复现门禁证据。最终双阶段复审已清零，允许在当前隔离候选提交 Task 8 证据；未经分别明确授权，不得 push、部署、执行生产迁移、调用真实付费 Provider、切换 `primary(video)`、发布 R2、启动 M13.3、执行独立单槽集成或 Agent→dev 合并。
