# R2 视频 live Handler 本地门禁报告

- 日期：`2026-08-02`
- 任务：`Task 14`
- 分支：`codex/r2-live-video-handler`
- 取证前 HEAD：`b1d2a64b754982fe0eef5578f5762a8e97b1a4d8`
- 回滚目标：`b1d2a64b754982fe0eef5578f5762a8e97b1a4d8`
- 状态：`development_slice_complete:Task14 / awaiting_independent_slot_integration`

## 结论

视频 live Handler 开发切片已完成，待独立单槽集成。隔离候选已用真实 FastAPI conversation、turn、snapshot、SSE 和 interrupt response 入口跑通从零视频链路，并用公开 M06 worker port 投递本地 fake Provider 完成结果。该状态不是 R2 生产发布证据，不授权真实付费 Provider、生产 `primary(video)`、M13.3、Agent→dev 合并或历史会话迁移。

生产继续保持 R1 `assist / enabled_intents=[] / 100% / context_compaction=true`。`backend/config.prod.yml` 相对 `origin/feature/agent_0.8.4_boguan...HEAD` 的差异命令退出码为 `0` 且无输出。

## 从零公共视频链路

端到端用例从创建 `initial_intent=video` 新对话开始，首轮 Turn 携带图片 URL、名称和稳定引用，依次完成需求表单、三方向、Plan、场景包和素材、三段分镜视频、第一次合并、QA 失败反馈、只修改第二镜、该镜 attempt 2、第二次合并、最终确认与当前成片下载。

- 新对话权威归属为 `supervisor_v1`。
- Snapshot 与 SSE 最终 `cursor/sequence` 一致；SSE 测试消费到目标 cursor 后主动发送 `http.disconnect`，测试进程正常退出且无残留。
- fake Provider start 计数：分镜 `4`、合并 `2`、QA `1`、剪映 `0`。
- 相同 `client_input_id` 重放与三次 Snapshot 刷新新增 Provider start 为 `0`。
- 最终 Workflow 为 `completed`；下载证据只绑定第二次合并后的当前视频，旧合并 Artifact 没有继承下载字段。

## 故障、恢复与隔离矩阵

`test_video_live_fault_matrix_is_recoverable_and_isolated` 参数化执行 11 个真实生产对象场景，不调用其他测试函数，也不以源码扫描替代行为验证：

1. Graph checkpoint 前退出：完成事件保留，租约到期后用同一 event ID 恢复，Provider start 不重复。
2. Graph checkpoint 后退出：已应用 checkpoint 以 event ID 去重，ack 前退出后重放仍只应用一次。
3. Provider start 后、完成事件前退出：恢复扫描只查询原 provider job，新增 start 为 0。
4. status 402：用后续请求携带的新凭据恢复原 provider job，attempt 仍为 1，不再次 start。
5. timeout 与 failed：固定安全原因落库，上层显式建立 attempt 2 和新 provider job。
6. HTTP 404：原 Operation 固定为 expired，只能创建 attempt 2。
7. 三分镜部分失败：两条成功保持不变，只为失败的第二镜创建 attempt 2。
8. 跨租户引用：攻击者携带 conversation/workflow/artifact/interrupt 引用时，公开 Turn、Snapshot 和 interrupt response 均在 conversation 所有权边界返回 404；可见跨租户对象为 0。
9. 模型档案失效：在 Graph/Handler 前失败关闭，Graph 与 Provider 调用均为 0。
10. Handler 重启后缺失：新视频对话保持 `frontend_v2`；已冻结的旧 `supervisor_v1` 对话在新增 Turn 登记前固定返回 `agent_runtime_unavailable`，新增 Turn 为 0，原归属不迁移。

每项统一断言 expected/actual attempt、provider job ID、Turn 状态、安全 reason、敏感值零泄漏、重复 Provider start 为 0、跨租户对象为 0。`frontend_v2` 对话仍把 R1 Turn 持久化为 `accepted`，供既有 v2 接力，但 Supervisor Executor 通知计数为 0。

## TDD RED → GREEN

- 公共全链路先暴露四个真实冻结对象缺口：权威 Workflow Snapshot、Executor claim Turn、QA 完成 payload 在深拷贝或 JSON 序列化时遇到嵌套 `mappingproxy`；QA 定向修改更新状态后没有为受影响镜头启动新 Operation。分别在 Context Assembler、Executor 证据加载、完成桥和 Handler 的定向恢复边界最小修复后转绿。
- QA 定向 Operation RED：`1 failed`，第二镜修订后 fake Provider start 仍为 `5`，期望 `6`；GREEN 后只新增第二镜 attempt，重放新增 start 为 `0`。
- 冻结 QA payload RED：`1 failed`，固定错误为 `视频质检 issues 必须是可序列化 JSON`；解冻权威完成事件 payload 后 Memory/SQL 均通过。
- Service 归属 RED：`2 failed, 1 warning`。`frontend_v2` 错误通知 Executor，旧 `supervisor_v1` 缺 Handler 时错误返回 200；GREEN 为 `2 passed, 1 warning`，分别固定为 `accepted + executor 0` 和 `409 agent_runtime_unavailable + 0 Turn`。
- 11 项故障矩阵收集 RED：`11 failed, 1 warning`；完成真实场景驱动器后逐项和整组均转绿。
- 第一版 Starlette TestClient 对无限 SSE 流发生缓冲等待；改为可主动断开的 ASGI 消费后，同一完整 E2E `1 passed, 1 warning`，并复查无遗留 Python 进程。

## 本地门禁证据

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| Task 14 公共 E2E + R2 integration | `.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_video_live_e2e.py tests/test_agent_runtime_r2_integration.py -q` | `31 passed, 1 warning in 10.27s` |
| Service/Executor/R1 相邻回归 | `.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_r2_integration.py tests/test_agent_runtime_r1_integration.py tests/test_agent_runtime_turn_executor.py -q` | `83 passed, 1 warning in 11.41s` |
| Runtime + 视频组合 | 由 `rg --files` 展开 `test_agent_runtime_*.py` 与 `test_agent_video_*.py` 后运行 pytest | 最终 `1658 passed, 1 failed, 1 warning in 216.15s`；唯一失败为未修改基线问题 |
| 后端全量 | `.venv\Scripts\python.exe -m pytest -q` | 最终 `5862 passed, 48 skipped, 1 failed, 7 warnings in 341.91s`；唯一失败为未修改基线问题 |
| 黄金报告复现 | `.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_supervisor_evaluation.py::test_离线评估达到模块四项门槛且报告可复现 -q` | `1 passed, 1 warning in 1.98s` |
| Web 全量 | `corepack pnpm test` | `404 passed / 0 failed` |
| Web 类型检查 | `corepack pnpm lint` | 退出码 `0`，`tsc --noEmit` 无错误 |
| Web 生产构建 | `corepack pnpm build-prod` | 退出码 `0`，`2432 modules transformed`；仅既有大 chunk 警告 |
| Python Ruff | `.venv\Scripts\python.exe -m ruff check .` | `All checks passed!` |
| 生产配置 | `git diff origin/feature/agent_0.8.4_boguan...HEAD -- backend/config.prod.yml` | 退出码 `0`，无输出 |

PowerShell 不会替 pytest 展开 `tests/test_agent_runtime_*.py`，直接照抄通配符命令得到 `0 tests / file or directory not found`；随后只用 `rg --files` 生成等价显式文件数组重跑，没有跳过任何匹配文件。

## 已知基线问题

`tests/test_agent_video_live_capabilities.py::test_agent_runtime_package_keeps_public_export_identity_and_errors` 在本任务基线 `b1d2a64b` 已失败：测试硬编码 `pixelflow.agent_runtime.__all__` 只有 4 个 replay 导出，但同一基线实现实际还有 6 个 Executor 稳定导出，共 10 个。本任务对 `backend/pixelflow/agent_runtime/__init__.py` 和该测试的基线差异均为空；单独复现仍失败，因此未越界改写公共导出或测试。该既有失败不能把本地组合/全量门禁表述为全绿。

## 网络、安全和发布边界

- 未调用真实付费供应商。所有 Provider start/status 都来自进程内 `_FaultProviderService` 或 `_ScriptedProvider`；HTTP Controller 使用本地 ASGI transport。
- fake 不保存 Authorization；持久化 Operation、Event、Turn、Snapshot 和测试结果扫描中没有 `Bearer` 凭据、原始供应商错误、token 或 credential。
- 未修改 dev/prod 配置、模型档案、Provider 合同或发布比例；生产配置 diff 为空。
- 未 push，未执行独立单槽集成、真实付费测试、生产 `primary(video)`、R2 发布、M13.3 或 Agent→dev 合并。
- 若独立 reviewer 拒绝本候选，以 `b1d2a64b754982fe0eef5578f5762a8e97b1a4d8` 为回滚目标，不迁移历史对话或运行中 Turn。

最终中文提交计划使用标题 `验证：完成 R2 视频 live Handler 本地门禁`，正文明确本提交不代表生产发布；实际提交 SHA 以本报告所在候选提交和最终交接输出为准。
