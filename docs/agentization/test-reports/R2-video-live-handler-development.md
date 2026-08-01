# R2 视频 live handler 开发记录

- 日期：`2026-08-02`
- 任务：`Task 13`
- 分支：`codex/r2-live-video-handler`
- 基线：`0960f7d9c298293e0332386047762145d8504faf`
- 状态：`review_fix_local_verified:Task13 / awaiting_independent_review`

## 1. 结论

Task 13 已在隔离开发分支补齐视频 live handler 与 Web Supervisor 控件接线。后端现在可以消费当前 Turn 和 interrupt，复用 M11 领域 Service 与 M06 Operation 边界推进视频全流程；前端从 Snapshot 的 `ui_kind` 恢复既有表单、方向、Plan、分镜和成片组件，并把人工操作转换为定向 `ExplicitActionSignal`。

本记录不是 R2 发布证据。Gateway 注册、`primary_execution_intents=[video]`、R2 真实全流程门禁、真实付费 Provider、生产 `primary(video)`、Agent→dev 合并均属于后续独立任务；当前生产继续保持 R1 `assist / [] / 100 / true`。

## 2. 后端实现

- intake 取消只接受 `{form_cancelled: true}`；确认继续使用完整 `form_values` 和轮次。
- 方向审核支持选择和重新生成；Plan 审核支持同意、Agent 修订、历史版本恢复以及返回方向生成新创意。
- 场景包审核只接受有界修改：单镜可编辑字段，或明确的全局素材 `add/replace/delete`。领域 Service 保留冻结 Plan/合同/时长和身份，自行重算 mentions、参考引用与执行提示，不接收客户端整份权威快照覆盖。
- 分镜视频、合并、质检、人工确认、剪映草稿和最终交付继续沿用既有状态机、Operation 幂等和恢复合同。
- 五类人工 interrupt 都携带稳定 `ui_kind`；最终成片下载只接受 `delivery_download_url`，剪映下载继续使用独立三字段合同。
- 未修改 `state_codec.py`：新增行为复用现有状态类型，定向测试未证明 codec 存在往返缺口。

## 3. Web 实现

- `submitSupervisorAction()` 每次只生成一个 `clientInputId`，把原 `explicitAction`、interrupt 和 Artifact 引用一起写入可恢复 pending；已注册动作只查询原 `run_id`，不重新提交或生成新 ID。
- 当前目标只由 Snapshot interrupt 的 `workflow_id + stage + artifact_ref` 和同一会话 Workflow 投影确定；不按标题或任意 Workflow 猜测目标。
- 视频表单确认/取消、方向、Plan、场景包、分镜视频、合并、QC、最终确认/修改、剪映生成/重试/下载与成片下载均提交结构化动作。
- Supervisor 分镜文本先保存在面板草稿中，点击“保存”后才把当前分镜合并为一次结构化响应；存在未保存草稿时禁止切换分镜或直接开始生成，避免同一 interrupt 被逐键或并发重复响应。
- Supervisor 控件不调用旧供应商推进 handler；`frontend_v2` 和非视频流程继续保留原行为。
- 刷新只从权威 Snapshot、Workflow、消息 Artifact 和原 pending 恢复，不自动重发结构化动作。

## 4. TDD 与验证证据

### 4.1 RED

- 后端新增领域与 handler 用例首次运行：`16 failed, 91 passed`。失败覆盖 Plan 返回方向、场景包单镜/素材有界修改、严格取消、最终下载字段等尚未实现语义。
- 数字人素材兼容复核先得到 `2 failed`，证明既有选择器提交的 `asset://{thirdAssetId}` 尚未被来源相关校验接受；收窄修复后相同用例 `2 passed`，并继续拒绝 ID 不匹配。
- Web 新增四个合同文件用例首次运行：新增 `9` 项失败。失败覆盖结构化提交入口、唯一 UUID、注册后只查原 run、五类 UI 纯恢复以及表单/Plan/分镜/剪映/交付接线。
- 最终复审新增分镜草稿合同首次运行 `1 failed`，证明文本输入仍会逐键提交；改为本地草稿和显式保存单次提交后，该合同与类型检查转绿。

### 4.2 GREEN

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端 Task 13 焦点 | `.venv\Scripts\python.exe -m pytest tests/test_agent_video_workflow_planning.py tests/test_agent_video_workflow_scene_packages.py tests/test_agent_video_live_handler.py -q` | `109 passed, 1 warning` |
| Web 三组新增源码合同 | `node --test tests/mainFlowContract.test.mjs tests/videoSceneUiContract.test.mjs tests/jianyingDraftUiContract.test.mjs` | `94 passed` |
| Web 正确环境全量 | `node scripts/run-tests.mjs` | `399 passed` |
| Web 类型检查 | `corepack pnpm lint` | 通过，`tsc --noEmit` 无错误 |
| Web 生产构建 | `corepack pnpm build-prod` | 通过；仅有既存大 chunk 警告 |
| Python 静态检查 | `.venv\Scripts\python.exe -m ruff check`（本次 6 个 Python 文件） | 通过 |
| 差异格式 | `git diff --check` | 通过 |

扩展运行全部 `test_agent_video_*.py` 得到 `573 passed, 1 failed, 1 warning`。唯一失败为未修改文件 `tests/test_agent_video_live_capabilities.py` 对 `pixelflow.agent_runtime.__all__` 仍要求只有四个 replay 导出，而当前基线实际还包含六个 `SupervisorTurnExecutor` 相关公共导出；该测试单独复现仍失败，Task 13 未修改 `agent_runtime/__init__.py` 或该测试，因此记录为基线漂移，不据此扩大修改范围或伪报全绿。

## 5. Task 13 独立审核整改候选

### 5.1 审核意见闭环

- C1：M06 Operation 最终完成不再只更新业务 Repository。Gateway 把同一个真实 Supervisor Graph 注入完成桥；完成桥先在原会话 checkpoint 上幂等写入更新后的 `WorkflowDispatchResult`，再经专用 marker 进入统一 `workflow_interrupt` 节点。只有真实 Graph pause 已建立，Memory/SQL Repository 才在同一临界区或事务中原子写权威 state/workflow/messages、原 Turn `waiting_user`、唯一 open interrupt、`interrupt.opened` 和完成事件确认。checkpoint 后进程退出时重放复用同一 pause，不新建 Turn，也不重新调用 Provider start。
- I1：intake interrupt 从权威状态投影 `form_values`、当前输入、附件和轮次；缺少凭据时保存可恢复的原结构化 action 与原 Artifact 引用，但不保存 Authorization、token、header 或 credential。
- I2：助手 Artifact 消息同时携带 `run_id`、`workflow_id` 和 `artifact_ref`。Web 只按当前 interrupt 的 Workflow、Artifact 与允许类型选择精确消息；双 Workflow 或身份错配时失败关闭，不按最后一条卡片猜测。
- I3：真实 Memory/SQLite 链路覆盖“无凭据打开授权中断 → Executor 重启 → 响应携带瞬时凭据 → 恢复原 action”。整改中发现原子响应登记会合法写入 `pre_input_context_version`，而普通 workflow 恢复入口此前把它当成 DTO 额外字段拒绝；现与 clarification 使用同一严格字段集和版本校验，剥离内部字段后再构造公开 DTO，不放宽公开合同。
- I4：人工新增全局素材继续复用统一 ID/名称生成器；后端要求所属分组前缀，并拒绝跨 characters/scenes/props/visual_style 的 ID、名称和 content asset 冲突。未知 patch 字段仍先失败关闭。

### 5.2 RED / GREEN 证据

- C1 首次真实 Graph 集成用例在 `VideoOperationCompletionHandler(..., graph=graph)` 处得到预期 RED，证明原完成桥没有 Graph checkpoint 接口；完成实现后 Memory 与 SQLite 均恢复同一原 Turn。随后在 Graph pause 成功、Repository 事务提交前模拟进程退出，首次重放得到 open interrupt 为空的 RED；整改后领域投影和 interrupt 都复用稳定的 `completion_event.occurred_at`，Repository 租约确认仍读取当前时钟，并复用 checkpoint 中首个 `opened_at`，31 秒租约后重放可提交同一中断且不重复 Provider start。
- I3 重启用例首次在普通 workflow resume 得到 `pre_input_context_version` 额外字段校验失败，Provider start 保持 0；按严格内部信封修复后双 Repository 均通过，三个分镜各 start 一次且恢复与后续完成阶段都没有重复 start。
- Web 类型检查首次发现精确 Artifact 选择可能返回空消息；无权威消息时显式不渲染 Supervisor 卡片后，`tsc --noEmit` 与生产构建转绿。

| 门禁 | 命令/范围 | 结果 |
| --- | --- | --- |
| C1/I3 真实 Graph + completion 矩阵 | `pytest tests/test_agent_video_live_operations.py -q -k "operation_completion_opens_real_graph_interrupt or operation_completion_interrupt_matrix"` | `6 passed` |
| Executor 相邻回归 | `pytest tests/test_agent_runtime_turn_executor.py -q` | `26 passed` |
| Runtime Repository 相邻回归 | `pytest tests/test_agent_runtime_video_repository.py -q` | `82 passed` |
| 视频 Operation 全量 | `pytest tests/test_agent_video_live_operations.py -q` | `146 passed` |
| 视频 Handler | `pytest tests/test_agent_video_live_handler.py -q` | `53 passed` |
| 场景包领域 Service | `pytest tests/test_agent_video_workflow_scene_packages.py -q` | `41 passed` |
| Gateway readiness | `pytest tests/test_agent_runtime_gateway_readiness.py -q` | `9 passed` |
| Web 全量 | `npm.cmd test` | `404 passed` |
| Web 类型检查 | `npm.cmd run lint` | 通过 |
| Web 生产构建 | `npm.cmd run build-prod` | 通过；仅有既存大 chunk 警告 |
| Python 静态检查 | `.venv\Scripts\python.exe -m ruff check`（本次 10 个 Python 文件） | 通过 |
| 差异格式 | `git diff --check` | 通过 |

当前整改状态为 `review_fix_local_verified:Task13 / awaiting_independent_review`。该状态不是 Task 14、R2 真实门禁或发布批准。

## 6. 安全与停止边界

- 未修改 dev/prod 运行模式、intent、比例、上下文预算、严格模型档案或 30 秒退避。
- 未调用真实 LLM、content-app、PowerMem、图片、视频、PPT、剪映或其他付费接口。
- 未注册 Gateway handler，未执行 Task 14，未执行 R2 真实全流程或生产发布。
- 未迁移历史对话或运行中任务，未 push、未合并 Agent→dev。
