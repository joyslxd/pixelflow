# M02 LangGraph 会话/Workflow 内核

- phase：`in_progress`
- owner：A
- reviewer：`/root/m02_1_independent_review`
- base Agent SHA：`390e2a3203dada5df1507a4a722c4efe03ce7365`
- branch：`codex/agent-0.8.4-m02-graph-kernel`
- 依赖：M00、M01
- 当前切片：`M02.2`
- 最近完成：`M02.1`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- M02.1 完成时间：`2026-07-27 23:36:20 +08:00`

## 切片

- [x] M02.1 State/reducer/namespace（2h）
- [ ] M02.2 fake registry/dispatcher（2.5h）
- [ ] M02.3 interrupt/resume/projection 顺序（2.5h）
- [ ] M02.4 composition/graph ID/lifespan（2h）

## M02.1 完成记录

- 实现：新增最小 `SupervisorState`，工作流投影使用按 `workflow_id` upsert 的纯 reducer；既有投影和增量均深拷贝，Map 键不一致及已有工作流的 `conversation_id`、`kind` 变化均 fail-closed。
- 命名：Supervisor 与 Workflow thread 严格采用合同中的版本化格式；顶层 runnable config 使用 LangGraph 根 `checkpoint_ns=""`，应用级隔离由完整 `thread_id` 保证，不误用为子图保留的 namespace。
- TDD：初始因 `pixelflow.agent_runtime.graph` 不存在形成预期 RED；最小实现达到 `12 passed`。独立审核发现非空 `checkpoint_ns` 无法恢复后，真实 InMemorySaver 用例得到 `2 failed / 11 passed`，最小整改后最终为 `13 passed`。
- 验证：M02.1 聚焦测试 `13 passed, 1 warning`；checkpointer、run manager、gateway recovery、harness boundary 与本片合并回归 `91 passed, 1 warning`；Ruff 和 `git diff --check` 通过。唯一 warning 为既有 LangGraph pending deprecation。
- 审核：独立只读 reviewer 首轮为 Critical 0、Important 1、Minor 0；整改复审为 Critical 0、Important 0、Minor 0，可以提交。SQLite、interrupt/restart 属 M02.3 或最终模块门禁，不阻塞本片。
- 边界：未实现 M02.2 registry/dispatcher、M02.3 interrupt/resume 或 M02.4 composition/graph ID/lifespan；未修改旧 `backend/pixelflow/graph.py`、旧 graph ID、`backend/langgraph.json`、配置或依赖。
- 阶段：M02.1 不是阶段检查点，也不是模块最后一片，因此保持 `in_progress`，不写 `ready_for_phase_integration` 或 `ready_for_integration`；当前自动化状态仍为 `automation_local_ready`。

## 恢复提示

下一切片是 `M02.2 fake workflow registry + command dispatcher`。开始前必须重新 fetch、执行 dev→agent 安全预检、确认本片提交已在远端并重新领取唯一写入权；继续复用当前模块分支和 worktree，不得创建切片子分支/worktree。不得替换旧 `backend/pixelflow/graph.py` 或旧 graph ID。
