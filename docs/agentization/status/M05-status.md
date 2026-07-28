# M05 Supervisor 决策与目标解析

- phase：`in_progress`
- owner：A
- base Agent SHA：`38310bb64385fe276edc0ad99c2f996db2c8c1f8`
- branch：`codex/agent-0.8.4-m05-supervisor`
- 依赖：M02、M03、M04
- 当前切片：`M05.3`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- M05.2 开始时间：`2026-07-28T10:09:07+08:00`
- M05.2 完成时间：`2026-07-28T10:29:08+08:00`
- M05.1 开始时间：`2026-07-28T08:40:27+08:00`
- M05.1 完成时间：`2026-07-28T09:16:25+08:00`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m05-supervisor`

## 切片

- [x] M05.1 deterministic target resolver（2.5h）
- [x] M05.2 LLM structured classifier（3h）
- [ ] M05.3 validator/version/risk gate（2.5h）
- [ ] M05.4 clarify/answer/command 图路由（2h）
- [ ] M05.5 中文黄金集和评估（2h）

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

## 恢复提示

任何目标不唯一的计费动作都必须追问。只保存 reason code，不保存思维链。
