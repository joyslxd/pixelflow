# M03 模型档案、Token 预算与 ContextEnvelope

- phase：`in_progress`
- owner：A
- branch：`codex/agent-0.8.4-m03-context-runtime`
- base Agent SHA：`5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- 依赖：M00
- 当前切片：`M03.4`（等待开发者手动启动）
- 当前唯一写入者：尚未领取
- M03.1 开始时间：`2026-07-24 02:55:58 +08:00`
- M03.1 完成时间：`2026-07-24 03:12:35 +08:00`
- M03.2 开始时间：`2026-07-24 05:34:37 +08:00`
- M03.2 完成时间：`2026-07-24 05:43:06 +08:00`
- M03.3 开始时间：`2026-07-24 08:05:26 +08:00`
- M03.3 完成时间：`2026-07-24 08:25:07 +08:00`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m03-context-runtime`
- 锁定文件：无；M03.3 写锁已释放

## 切片

- [x] M03.1 ModelContextProfile 与 128K 保守降级（2h）
- [x] M03.2 TokenMeter/usable budget（2.5h）
- [x] M03.3 ContextEnvelope assembler（2.5h）
- [ ] M03.4 tool/artifact externalizer（2h）

## M03.1 交付记录

- 产物：新增 `ModelContextProfile`、现有 DeerFlow `ModelConfig` 的 `context_profile` 解析、验证证据与显式过期判断，以及缺失/未验证/过期时的保守降级。未知模型使用 128K；已声明更小窗口的模型不会在降级时反向放大上下文或输出上限。
- 修改文件：`backend/pixelflow/agent_runtime/context/__init__.py`、`backend/pixelflow/agent_runtime/context/profiles.py`、`backend/tests/test_agent_runtime_context_profiles.py`、本状态文件。
- TDD 证据：先后验证包缺失、降级解析缺失、配置不变量缺失以及审核补充边界的红灯，再以最小实现转绿；覆盖 256K/384K/512K、128K 缺省、64K 声明上限、未来验证时间、显式过期、合同最小字段、模型身份防覆盖和无效配置。
- 最后测试：`python -m pytest tests/test_agent_runtime_context_profiles.py tests/test_agent_runtime_contracts.py tests/test_agent_runtime_config.py tests/test_profile_config.py -q`，结果 `70 passed, 1 warning`。该 warning 与开工基线一致，来自既有 LangGraph 依赖的 `LangChainPendingDeprecationWarning`。
- 静态检查：`python -m ruff check pixelflow/agent_runtime/context tests/test_agent_runtime_context_profiles.py` 通过；`git diff --check` 通过。
- 独立审核：首轮为 `With fixes`，指出小窗口反向放大、内层模型身份覆盖和冻结合同最小字段偏差；全部补充 TDD 并修复。第二轮复审为 `Ready to merge: Yes`，Critical/Important 均为 0；唯一说明文字 Minor 已同步修正。
- 中文规范：新增/修改注释和 docstring 均为中文说明；本切片未修改配置；独立 reviewer 已复核。提交级门禁首次把 Python 行首字典解包误识别为星号注释，已改为行为等价的显式字典更新；最终中文工程规范脚本通过后才允许 push。
- commit/push：本状态文件所在独立提交；门禁通过后推送至 `origin/codex/agent-0.8.4-m03-context-runtime`，远端以该提交为准。
- 遗留问题：无硬阻塞；本切片不是阶段检查点或模块最后一片，因此保持 `in_progress`，不运行 M03 模块门禁，不更新 `status/BOARD.md`。
- 下一切片第一动作：开发者手动启动 M03.2 后，先恢复同一模块分支/worktree，确认 M03.1 远端提交和唯一 writer，再为 TokenMeter 的 `effective_context`、输出/安全预留与 `usable_input` 百分比边界编写失败测试。

## M03.2 交付记录

- 产物：新增不可变 `ContextBudgetPolicy` 和七类统一业务预算，`TokenMeter` 按模型能力与业务上限的较小值计算 `effective_context`，按模型输出能力与节点输出预留的较小值保留实际输出空间，再扣除 safety reserve 得到 `usable_input`。利用率可如实超过 100%，压缩等级仅输出 0–4 级信号，不越界执行 M04 的压缩动作。
- 修改文件：`backend/pixelflow/agent_runtime/context/__init__.py`、`backend/pixelflow/agent_runtime/context/token_meter.py`、`backend/tests/test_agent_runtime_token_meter.py`、本状态文件；未修改任何配置、长期 feature 分支或 `status/BOARD.md`。
- TDD 证据：新增测试首次运行因 `token_meter` 模块尚不存在而出现 24 项预期失败；最小实现后 24 项全部转绿。测试覆盖七类冻结预算、模型与业务双重上限、实际输出预留、128K 保守档案、59/60/71/72/84/85/91/92 边界、超过 100% 利用率、非法估算类型、无可用输入和未知节点。
- 最后测试：`python -m pytest tests/test_agent_runtime_token_meter.py tests/test_agent_runtime_context_profiles.py tests/test_agent_runtime_contracts.py tests/test_agent_runtime_config.py tests/test_profile_config.py -q`，结果 `94 passed, 1 warning`。该 warning 与开工基线一致，来自既有 LangGraph 依赖的 `LangChainPendingDeprecationWarning`。
- 静态检查：`python -m ruff check pixelflow/agent_runtime/context tests/test_agent_runtime_token_meter.py tests/test_agent_runtime_context_profiles.py` 通过；`git diff --check` 通过。
- 独立审核：只读 reviewer 独立复跑上述 94 项回归、Ruff 和差异检查，结论为 `Ready to merge: Yes`，Critical/Important/Minor 均为 0；确认公式、业务预算、整数阈值、保守档案、冻结合同、中文说明和修改范围均符合要求。
- 中文规范：新增/修改注释和 docstring 均为中文说明；本切片没有配置项变更；提交前本地中文工程规范门禁通过后才允许 push。
- commit/push：本状态文件所在 M03.2 中文独立提交；门禁通过后推送至 `origin/codex/agent-0.8.4-m03-context-runtime`，远端以该提交为准。
- 遗留问题：无硬阻塞；本切片不是 `phased-rollout-plan.md` 明确检查点，也不是模块最后一片，因此保持 `in_progress`，不运行阶段/完整模块门禁，不写 `ready_for_phase_integration` 或 `ready_for_integration`。
- 下一切片第一动作：开发者手动启动 M03.3 后，先恢复同一模块分支/worktree并确认唯一 writer；用失败测试固定 `ContextEnvelope` assembler 对当前输入、目标 workflow、最近消息、摘要、PowerMem 和 artifact 引用的相关性、顺序与用户隔离。组装预算时必须先调用 `resolve_model_context_profile()`，再把已解析档案交给 `TokenMeter`。

## M03.3 交付记录

- 产物：新增 `ContextAssembler`、带 owner/context version 的 `ContextAssemblySnapshot`、消息/Workflow 摘要/artifact 归属记录及数据源、PowerMem 搜索协议。assembler 以显式目标优先于 active workflow，按固定顺序选择未被摘要覆盖的最近消息、最新对话摘要、其他 Workflow 的最新摘要、显式及目标相关 artifact 引用和未决问题；PowerMem 搜索按用户检索四类记忆并 fail-open。
- 权威与预算：当前输入和目标 Workflow 不按预算裁剪；显式目标、显式 artifact、用户/会话归属和 `expected_context_version` 均在 PowerMem 调用前 fail-closed。组装器先调用 `resolve_model_context_profile()`，再把保守降级后的档案交给 `TokenMeter`；默认估算器使用 UTF-8 字节数保守估算，调用方可注入已验证 tokenizer。
- 修改文件：`backend/pixelflow/agent_runtime/context/assembler.py`、`backend/pixelflow/agent_runtime/context/__init__.py`、`backend/tests/test_agent_runtime_context_assembler.py`、本状态文件；未修改任何配置、长期 feature 分支或 `status/BOARD.md`，未实现 M03.4 的大输出外置。
- TDD 证据：新增测试首次运行因 `assembler` 模块尚不存在出现 10 项预期失败，最小实现后全部转绿；自查另以失败测试纠正 PowerMem `source_agent` 过滤，确保不会屏蔽其他 Agent 已沉淀记忆。首轮独立审核复现 PowerMem `await` 期间共享快照嵌套 DTO 漂移，新增 request、Workflow 合同、对话摘要和相关摘要同时变更的失败测试；assembler 在首次非关键等待前复制请求，并在数据源返回后立即取得深快照，定向测试转绿。
- 最后测试：`python -m pytest tests/test_agent_runtime_context_assembler.py tests/test_agent_runtime_token_meter.py tests/test_agent_runtime_context_profiles.py tests/test_agent_runtime_contracts.py tests/test_agent_runtime_config.py tests/test_profile_config.py tests/test_pixelflow_memory_helper.py -q`，结果 `113 passed, 1 warning`。该 warning 与开工基线一致，来自既有 LangGraph 依赖的 `LangChainPendingDeprecationWarning`。
- 静态检查：`python -m ruff check pixelflow/agent_runtime/context tests/test_agent_runtime_context_assembler.py tests/test_agent_runtime_token_meter.py tests/test_agent_runtime_context_profiles.py` 通过；M03.3 三个 Python 路径的 `ruff format --check` 通过；`git diff --check` 通过。
- 独立审核：首轮结论为 `Ready to merge: With fixes`，Critical 0、Important 1、Minor 0，唯一问题为跨 PowerMem `await` 的嵌套 DTO 别名漂移；按 TDD 修复后，reviewer 独立复跑 105 项回归和静态检查，增量复审结论为 `Ready to merge: Yes`，Critical/Important/Minor 均为 0。
- 中文规范：新增/修改注释和 docstring 均为中文说明；本切片没有配置项变更；提交后必须由本地中文工程规范门禁验证中文提交信息和工程说明，绿色后才允许 push。
- commit/push：本状态文件所在 M03.3 中文独立提交；门禁通过后推送至 `origin/codex/agent-0.8.4-m03-context-runtime`，远端以该提交为准。
- 遗留问题：无硬阻塞；M03.3 不是 `phased-rollout-plan.md` 明确检查点，也不是模块最后一片，因此保持 `in_progress`，不运行阶段/完整模块门禁，不写 `ready_for_phase_integration` 或 `ready_for_integration`，不更新 `status/BOARD.md`。
- 下一切片第一动作：开发者手动启动 M03.4 后，恢复同一模块分支/worktree并确认唯一 writer；先用失败测试固定大 tool/artifact 输出外置后 business contract hash 不变、当前输入和目标 Workflow 权威字段不变、仅保留必要片段且 prompt 大小下降。

## 恢复提示

256K/384K/512K 是建议上限，不是当前 AIRouter 已验证事实。缺失档案必须走 128K；未验证或过期档案使用不超过 128K、且不放大已声明能力的保守上限。M03.4 只能外置大 tool/artifact 输出并提取必要片段，不得裁剪当前用户输入、目标 Workflow 权威字段或修改 business contract。
