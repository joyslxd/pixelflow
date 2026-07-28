# M05 Supervisor 决策与目标解析

- phase：`ready_for_integration`
- owner：A
- base Agent SHA：`38310bb64385fe276edc0ad99c2f996db2c8c1f8`
- branch：`codex/agent-0.8.4-m05-supervisor`
- 依赖：M02、M03、M04
- 当前切片：`M05.5`
- 最近完成：`M05.5`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- M05.5 开始时间：`2026-07-28T12:52:36+08:00`
- M05.5 完成时间：`2026-07-28T13:19:13+08:00`
- M05.4 开始时间：`2026-07-28T12:03:59+08:00`
- M05.4 完成时间：`2026-07-28T12:22:18+08:00`
- M05.3 开始时间：`2026-07-28T10:43:53+08:00`
- M05.3 完成时间：`2026-07-28T11:15:07+08:00`
- M05.2 开始时间：`2026-07-28T10:09:07+08:00`
- M05.2 完成时间：`2026-07-28T10:29:08+08:00`
- M05.1 开始时间：`2026-07-28T08:40:27+08:00`
- M05.1 完成时间：`2026-07-28T09:16:25+08:00`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m05-supervisor`

## 切片

- [x] M05.1 deterministic target resolver（2.5h）
- [x] M05.2 LLM structured classifier（3h）
- [x] M05.3 validator/version/risk gate（2.5h）
- [x] M05.4 clarify/answer/command 图路由（2h）
- [x] M05.5 中文黄金集和评估（2h）

## M05.1 交付记录

- 产物：新增不可变 `ResolverCandidate`、`ExplicitActionSignal`、`DeterministicResolutionRequest/Resolution` 与 `DeterministicTargetResolver`，按按钮、reply、artifact、结构化或文本 `@mention` 和中文显式动词生成后续分类器可消费的确定性 evidence。
- 动作与意图：规则覆盖 `continue_workflow`、`modify_workflow`、`regenerate_stage`、`retry_failed`、`switch_workflow`、`cancel_workflow`、`start_workflow` 与问题型 `answer_only`；`video_analysis` 作为 `video` 的专门化 intent，图片、视频、PPT 等真实多 intent 冲突不得按词表顺序猜测。
- 目标边界：按钮内嵌目标必须与 reply、artifact、mention 和按钮 intent 交叉一致；同一 workflow 的历史 stage/artifact 不得任取第一项；未知引用、未知 mention 后缀、冲突动作、冲突目标或冲突 intent 全部 fail-closed，不回退到 active workflow。
- switch 与 mention：带明确目标 intent 的 `switch_workflow` 选择唯一 destination，不被当前 active workflow 劫持；文本 mention 只对精确引用和紧邻的中文属格“的……”做确定性归一，`.1`、`/voice` 等未知扩展保持 unresolved。
- 范围隔离：本切片只返回规则解析 evidence，不构造最终 `ActionDecision`，不调用 LLM，不实现 Validator/version/risk gate，不写 Runtime Store，不触发 Graph dispatch，也不包含 M05.2–M05.5。
- 修改文件：新增 `backend/pixelflow/agent_runtime/supervisor/__init__.py`、`resolver.py` 和 `backend/tests/test_agent_runtime_supervisor_resolver.py`；更新本状态文件。未修改配置、`status/BOARD.md` 或两个长期 feature 分支。
- TDD 证据：初始模块不存在得到 `1 failed`；随后 reply/artifact/mention、六类现有流程动词和安全边界分别稳定得到 `4 failed, 1 passed`、`11 failed, 5 passed`、`4 failed, 16 passed`。独立审核的多 intent、显式证据交叉冲突、switch、专门化 intent 与 mention 反例均先单独 RED，再逐项最小修复为 GREEN；最终 resolver 为 `30 passed, 1 warning`。
- 最后测试：M05.1 与 M02–M04 相邻回归为 `146 passed, 1 warning`；独立 reviewer 另行鲜跑全部 `test_agent_runtime_*` 为 `458 passed`。warning 来自既有 LangGraph pending deprecation。
- 静态检查：变更 Python 路径 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：首轮 Critical 0、Important 4、Minor 1；第二轮确认首轮问题关闭后补充 Important 3；全部按独立失败测试整改。第三轮最终结论为“无 Critical/Important 问题”，并确认前两轮全部问题关闭。
- 中文规范：新增/修改注释和 docstring 均使用中文说明；本切片没有配置变更。提交后门禁首次把 Python 列表和字典解包行误识别为人工注释，已改为行为等价的显式拼接与字典合并，并重跑聚焦测试；提交级中文工程门禁通过后才允许 push。
- commit/push：本状态文件随 M05.1 中文独立提交；提交级门禁通过后推送到 `origin/codex/agent-0.8.4-m05-supervisor`，远端以该提交为准。
- 阶段状态：M05.1 不是阶段检查点或模块最后一片，因此保持 `in_progress`，不运行 M05 Final 门禁，不更新 `status/BOARD.md`，不写任何 ready 状态，也不自动继续 M05.2。
- 下一切片第一动作：开发者手动启动 M05.2 后，恢复同一模块分支/worktree，确认 M05.1 远端提交并重新领取唯一 writer；先用失败测试冻结结构化分类器只输出 `ActionDecision` 合同字段、拒绝非法 JSON/枚举/目标，并保留 M05.1 的确定性 evidence 优先级。

## M05.2 交付记录

- 产物：新增不可变 `ActionClassificationTarget`、`ActionClassificationCandidate`、`ActionClassificationRequest`、`DecisionModel`、`DecisionClassificationError` 与 `LLMActionClassifier`；Prompt 直接嵌入冻结 `ActionDecision.model_json_schema()`，不手写第二套字段或枚举合同。
- 结构化边界：模型只可返回 JSON 文本、映射或已解析 `ActionDecision`；非法 JSON、枚举、额外字段、reason code、幂等键和目标归属均拒绝。首次解析失败只携带安全错误码请求一次完整重输，不回显模型原文；第二次失败或模型异常以公开 reason code fail-closed。
- evidence 优先：`RESOLVED/PARTIAL` 的确定性 action、intent、workflow、stage 和 artifact 不得被模型改写；`AMBIGUOUS` 只接受无目标 `clarify`，不得从候选中任选一个目标。每个 Workflow 使用精确 stage/artifact 目标对，当前阶段前进后仍可保留历史引用，来自不同目标对的 stage/artifact 不得拼接。
- 范围隔离：候选状态、版本和 `allowed_actions` 只作为 Prompt 输入；本切片不判断状态转换、context version、置信度或计费风险，不实现 M05.3 Validator，不写 Store、不触发 Graph dispatch，也不包含 M05.4–M05.5。
- 修改文件：新增 `backend/pixelflow/agent_runtime/supervisor/classifier.py` 和 `backend/tests/test_agent_runtime_supervisor_classifier.py`；更新 Supervisor 导出和本状态文件。现有最新设计、Agentization README 与根 AGENTS 已覆盖本片冻结合同和边界，无需重复改写；未修改配置、`status/BOARD.md` 或两个长期 feature 分支。
- TDD 证据：初始分类器导入得到 `1 error`；Prompt 安全短语、跨候选目标和冻结 schema 分别得到 `1 failed, 2 passed`、`3 failed`、`1 failed`。独立审核的歧义追问先得到 `2 failed`，历史目标对先因新合同不存在得到 `1 error`；逐项最小整改后聚焦测试为 `18 passed, 1 warning`。
- 最后测试：M05.2、M05.1 与冻结合同相邻回归为 `67 passed, 1 warning`；全部 25 个 `test_agent_runtime_*` 最终鲜跑为 `476 passed, 1 warning`。warning 仅来自既有 LangGraph pending deprecation。
- 静态检查：Supervisor 与两份测试的 `ruff check`、三个变更 Python 文件的 `ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：首轮 Critical 0、Important 2、Minor 0，发现歧义 evidence 与历史 stage/artifact 目标对问题；两项均先补失败测试再整改。第二轮确认首轮问题全部关闭，最终 Critical、Important、Minor 均为 0，结论“可提交”，且 reviewer 独立鲜跑 `476 passed, 1 warning`。
- 中文规范：新增/修改注释和 docstring 均使用中文说明；本切片没有配置变更。提交级中文工程门禁通过后才允许 push。
- commit/push：本状态文件随 M05.2 中文独立提交；提交级门禁通过后推送到 `origin/codex/agent-0.8.4-m05-supervisor`，远端以该提交为准。
- 阶段状态：M05.2 不是 `phased-rollout-plan.md` 明确列出的阶段检查点，也不是模块最后一片，因此保持 `in_progress`，不运行 Phase/M05 Final 门禁，不更新 `status/BOARD.md`，不写任何 ready 状态，也不触发 9.10A 或自动继续 M05.3。
- 下一切片第一动作：开发者手动启动 M05.3 后，恢复同一模块分支/worktree，确认 M05.2 远端提交并重新领取唯一 writer；先用失败测试冻结 `DecisionValidator` 的 allowed-actions、context version、置信度和计费风险闸门。

## M05.3 交付记录

- 产物：新增不可变 `DecisionValidationRequest`、公开安全错误 `DecisionValidationError` 与 `DecisionValidator`；在任何 Graph command 派发前重新校验分类快照、当前权威候选、会话 context version、Workflow stage/context version、状态、intent、stage/artifact 引用、幂等键和动作白名单。
- 动作白名单：现有 Workflow 动作必须同时出现在分类快照和当前 `allowed_actions`；`answer_only`、`clarify`、`start_workflow` 无论是否携带 target 都必须命中当前 `allowed_global_actions`。Validator 因确认要求、低置信度或目标歧义自动降级为 `clarify` 时也重新校验全局白名单，禁止借安全降级绕过 fail-closed 边界。
- 风险闸门：冻结 `<0.55` 必须追问、`0.55–0.82` 只允许目标唯一的非计费动作、`>=0.82` 仍须通过权威状态与目标校验；`continue_workflow`、`modify_workflow`、`regenerate_stage`、`retry_failed` 会同时比较分类快照与当前状态中的唯一 stage/artifact 目标对，多个 interrupt、图片、PPT 页或分镜一律追问，显式解析到唯一产物时才可批准。
- 安全结果：所有追问副本清空 workflow、stage、artifact、patch，并保留固定幂等键；错误只公开短 reason code，不包含用户内容、候选状态明细或模型思维链。
- 范围隔离：本切片只完成 Validator/version/risk gate，不实现 M05.4 的 clarify/answer/command 图路由，不写 Runtime Store、不触发 Graph command、不调用供应商，也不包含 M05.5 黄金集。
- 修改文件：新增 `backend/pixelflow/agent_runtime/supervisor/validator.py` 与 `backend/tests/test_agent_runtime_supervisor_validator.py`；更新 Supervisor 导出和本状态文件。现有最新设计、Agentization 架构、合同、工作拆分和测试矩阵已覆盖本片冻结合同，无需重复改写；未修改配置、`status/BOARD.md` 或两个长期 feature 分支。
- TDD 证据：初始 Validator 导入得到 collection error；随后非法动作、会话/Workflow 版本冲突、状态和引用陈旧、确定性 evidence、幂等键、置信度边界、计费与非计费歧义、全局动作及新 Workflow 边界均先得到 RED 再最小实现为 GREEN。独立审核发现的同一 Workflow 多产物、targeted 全局动作绕过、内部降级追问绕过、目标对静默变化和多 interrupt 继续等反例，也分别先得到单项 `1 failed` 再修复。
- 最后测试：Validator 聚焦测试为 `34 passed, 1 warning`；全部 `test_agent_runtime_*` 为 `510 passed, 1 warning`。warning 仅来自既有 LangGraph pending deprecation。
- 静态检查：变更 Python 路径 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：首轮 Critical 0、Important 2、Minor 0，发现同一 Workflow 多产物和全局白名单绕过；整改后第二轮补充发现 `continue_workflow` 多 interrupt 未纳入细粒度目标闸门。全部问题按独立失败测试最小修复，最终复审 Critical、Important、Minor 均为 0，结论“可提交”，审核者独立鲜跑 `510 passed, 1 warning`。
- 中文规范：新增/修改注释和 docstring 均使用中文说明；本切片没有配置变更。提交级中文工程门禁通过后才允许 push。
- commit/push：本状态文件随 M05.3 中文独立提交；提交级门禁通过后推送到 `origin/codex/agent-0.8.4-m05-supervisor`，远端以该提交为准。
- 阶段状态：M05.3 不是 `phased-rollout-plan.md` 明确列出的阶段检查点，也不是模块最后一片，因此保持 `in_progress`，不运行 Phase/M05 Final 门禁，不更新 `status/BOARD.md`，不写任何 ready 状态，也不触发 9.10A 或自动继续 M05.4。
- 下一切片第一动作：开发者手动启动 M05.4 后，恢复同一模块分支/worktree，确认 M05.3 远端提交并重新领取唯一 writer；先用失败测试冻结 `clarify`、`answer_only` 与业务 command 的 Graph 路由隔离，确保只有通过 M05.3 Validator 的 command 才可进入业务子图。

## M05.4 交付记录

- 产物：新增 `SupervisorActionRouter` 和安全短码 `SupervisorRoutingError`，把 M05.3 `DecisionValidator` 接到 M02 图入口；图内冻结 `answer_only`、`clarification` 与 `dispatch_workflow` 三个分支，只有校验后的业务命令可以进入 Workflow dispatcher。
- 状态隔离：`answer_only` 只追加 ID 必须等于 `assistant:{decision.idempotency_key}` 的纯助手消息，拒绝借旧消息 ID 覆盖和 tool call，不改 Workflow、active workflow、stage/context version 或 pending job；`clarify` 只打开包含公开问题、reason code 和幂等键的真实 LangGraph interrupt，定向恢复前后均不调用业务处理器。
- 权威绑定：路由前同时校验 state decision 与分类快照一致，重新执行 allowed-actions、版本、置信度和计费风险闸门，并把分类请求绑定当前 `turn_id`、去除首尾空白后的 `current_input`、会话 context version 及 Workflow conversation/kind/status/stage/version 投影；任何输入、快照或投影漂移均 fail-closed。
- 新建与派发：`start_workflow` 的分类决策继续保持无目标，通过校验后才由 conversation 与决策幂等键派生稳定 `wf_...` ID，并通过 dispatcher 的专用预分配参数传入；现有 Workflow 命令要求已校验路由目标与决策目标一致，投影更新后清除临时派发 ID。
- 错误安全：决策、校验请求和 Workflow 投影的 Pydantic 解析失败分别归一为 `invalid_decision`、`invalid_validation_request` 和 `invalid_workflow_projection`，使用无原异常链的公开短码，不回显用户输入、恶意字段值或内部状态。
- TDD 证据：开工基线为 `510 passed`；首轮路由测试先得到 `6 failed, 1 passed`，最小三路分流后为 `7 passed`。随后 Turn/会话版本/Workflow 投影绑定先得到 `3 failed, 7 passed`，回答消息 ID 防覆盖先得到 `1 failed, 10 passed`；独立审核的当前输入漂移先得到 `1 failed, 11 passed`，原始 Pydantic 异常泄漏先得到 `2 failed, 12 passed`，逐项最小修复后聚焦最终为 `14 passed, 1 warning`。
- 最后测试：全部 `test_agent_runtime_*` 为 `524 passed, 1 warning`；图装配与 M05.4 路由合计为 `19 passed, 1 warning`。warning 仅来自既有 LangGraph pending deprecation。
- 静态检查：7 个变更 Python 路径的 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：全新只读 reviewer `/root/m05_4_independent_review` 首轮分两次报告 Important 2，分别发现分类请求未绑定当前输入和 Pydantic 错误可能回显恶意输入；全部按独立失败测试整改。最终复审 Critical、Important、Minor 均为 0，`Ready to commit: Yes`，并独立鲜跑 `14/19/524` 项 pytest 与静态检查。
- 文档与边界：已同步 `docs/pixelflow-agent-skill-flow-latest-design.md` 的当前实现边界；本切片未修改配置、总看板或两个长期 feature 分支，未进入 M05.5，未调用真实供应商或付费 API。
- commit/push：本状态文件随 M05.4 中文独立提交；提交级中文工程门禁通过后推送到 `origin/codex/agent-0.8.4-m05-supervisor`，远端以该提交为准。
- 阶段状态：M05.4 不是 `phased-rollout-plan.md` 明确列出的阶段检查点，也不是模块最后一片，因此保持 `in_progress`，不运行 Phase/M05 Final 门禁，不更新 `status/BOARD.md`，不写 `ready_for_phase_integration` 或 `ready_for_integration`，也不触发 9.10A 或自动继续 M05.5。
- 下一切片第一动作：开发者手动启动 M05.5 后，恢复同一模块分支/worktree，确认 M05.4 远端提交并重新领取唯一 writer；先用失败测试冻结中文黄金集 schema 与离线评估器，再验证 action、target、歧义追问召回和计费误执行四项模块门槛。

## M05.5 交付记录

- 产物：新增严格 `SupervisorDecisionLabel`、`SupervisorGoldenCase/Dataset`、`SupervisorEvaluationReport` 与离线评估/稳定 Markdown 渲染入口；51 条中文黄金集覆盖全部 9 类 `AgentAction`，报告与 fixture 逐字复算，不调用 LLM、供应商或付费 API。
- 指标：action 为 `50/51（98.04%）`，target Workflow/Artifact 为 `21/22（95.45%）`，歧义追问为 `20/21（95.24%）`，计费动作误执行为 `0`；四项均达到 `≥92% / ≥95% / ≥95% / =0` 的 M05 模块门槛。
- 防稀释边界：数据集至少 40 条、target 分母至少 20、clarify 分母至少 10，必须覆盖全部动作；同时拒绝重复 `case_id` 和仅更换 ID 的相同规范化中文输入+完整期望标签，不能靠复制正确样例虚高指标。
- 计费安全：潜在计费动作只要 action、intent、Workflow 或 Artifact 任一不符合期望即计为误执行；无目标样例不进入 target 分母，错误 `start_workflow` intent 仍会被误计费指标捕获。
- 权威门禁：`Invoke-AgentModuleGate.ps1` 为 M05 固定 Supervisor、图路由、合同、旧流程、OpenAPI、Pester 与 Ruff 清单；M06 继续 fail-closed，未回退后端全量或越权执行其他模块门禁。
- TDD 证据：最初因评估模块不存在得到 collection error；最小实现后依次得到 `3 failed, 2 passed` 和缺少报告的 `1 failed, 4 passed`，补齐 fixture/报告后为 `5 passed`。独立审核的语义重复与错误 intent 反例先稳定得到 `2 failed, 4 passed`，最小整改后为 `6 passed, 1 warning`。
- 最后测试：M05 权威 pytest 集合为 `177 passed, 1 warning`；全部 `test_agent_runtime_*` 为 `530 passed, 1 warning`；BranchAutomation Pester 为 `43 passed, 0 failed`。warning 仅来自既有 LangGraph pending deprecation。
- 静态检查：Supervisor/Graph 与权威测试的 Ruff 通过，新增评估文件 `ruff format --check` 通过，`git diff --check` 通过。
- 独立审核：首轮 Critical 0、Important 2、Minor 0，发现换 ID 的同语义样例可稀释分母、同动作错误 intent 可漏记误计费；两项均按失败测试修复。第二轮 Critical/Important/Minor 均为 0，`Ready to commit: Yes`，并独立鲜跑聚焦 pytest、Ruff、格式、差异、报告与 Final PlanOnly。
- 中文规范：新增/修改 docstring、状态和报告均使用中文主体说明；JSON 为测试 fixture，不是配置，本切片没有新增或修改配置项，也没有真实凭据、用户长 Prompt 或完整供应商 URL。
- Final 门禁：以 M05.4 远端提交 `ae04eb3ad2d0f653a83d9287af8661de506d05a1` 为 `ChinesePolicyBaseRef` 执行正式 `Invoke-AgentModuleGate.ps1 -ModuleId M05 -GateType Final`，结果 `Passed=True`、`CommandCount=5`；最终状态 amend 后复跑同一门禁再 push。
- commit/push：本状态文件与实现属于 M05.5 同一个中文独立提交；最终门禁和提交级中文规范检查通过后，仅推送 `origin/codex/agent-0.8.4-m05-supervisor`，远端以该提交为准。
- 阶段状态：M05.5 是模块最后一片，不是 `phased-rollout-plan.md` 的中间检查点；M05 Final 绿色后写 `ready_for_integration`，不更新总看板、不直接修改 Agent、不自动开始单槽集成或其他模块。
- 下一步第一动作：当前自动化状态为 `automation_local_ready`。开发者新开一个 Codex 任务，复制执行手册 9.10A 话术，并在同一条消息中明确模块号 `M05`，手动启动唯一单槽最终集成；不得继续不存在的 M05.6。

## 恢复提示

任何目标不唯一的计费动作都必须追问。只保存 reason code，不保存思维链。

- release_id：`R2`
- checkpoint_slice：`M05.5`
- checkpoint_commit：`本状态文件所在提交；push 后以远端 SHA 为准`
- last_integrated_commit：`—`
- locked files：`无`
- checkpoint_status：`ready_for_integration`
- integration failure evidence：`候选 codex/integrate-m05-20260728-053559-3206adb1 因本地临时门禁 wrapper 的 PowerShell 5.1 解析错误而阻塞；Agent 未更新。wrapper 修复后在保留候选重跑 M05 Final，Passed=True、CommandCount=5；下次必须创建全新候选。`
