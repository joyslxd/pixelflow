# M03 模型档案、Token 预算与 ContextEnvelope

- phase：`ready_for_integration`
- owner：A
- branch：`codex/agent-0.8.4-m03-context-runtime`
- base Agent SHA：`5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- 依赖：M00
- 当前切片：`M03.4`（已完成）
- 当前唯一写入者：已释放；最终收口写入者为 `/root`
- M03.1 开始时间：`2026-07-24 02:55:58 +08:00`
- M03.1 完成时间：`2026-07-24 03:12:35 +08:00`
- M03.2 开始时间：`2026-07-24 05:34:37 +08:00`
- M03.2 完成时间：`2026-07-24 05:43:06 +08:00`
- M03.3 开始时间：`2026-07-24 08:05:26 +08:00`
- M03.3 完成时间：`2026-07-24 08:25:07 +08:00`
- M03.4 开始时间：`2026-07-24 08:41:08 +08:00`
- M03.4 实现与审核完成时间：`2026-07-24 09:12:27 +08:00`
- M03.4 门禁阻塞确认时间：`2026-07-24 09:17:51 +08:00`
- M03.4 门禁恢复开始时间：`2026-07-24 17:46:57 +08:00`
- M03.4 门禁恢复完成时间：`2026-07-24 18:03:45 +08:00`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m03-context-runtime`
- 锁定文件：无；M03.4 写锁已释放

## 切片

- [x] M03.1 ModelContextProfile 与 128K 保守降级（2h）
- [x] M03.2 TokenMeter/usable budget（2.5h）
- [x] M03.3 ContextEnvelope assembler（2.5h）
- [x] M03.4 tool/artifact externalizer（2h）

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

## M03.4 交付记录

- 产物：新增 `ContextPayloadExternalizer`、完整载荷写入记录、Store 幂等复合键和外置证据；仅遍历近期消息中的明确 tool/artifact 大载荷，不遍历当前输入或目标 Workflow。完整载荷交给注入 Store，Prompt 只保留稳定引用、内容 hash、原始字节数和受限片段。
- 预算与安全：`ContextAssembler` 仅在初次预算达到 60% 级别时调用 externalizer，成功外置后重新计量；未经验证模型仍先降级到 128K 保守档案。字符串片段采用有界首尾提取并过滤 URL、Bearer 和 credential；引用过长、单项替换不能严格缩小、最终 Prompt 未严格下降或 Store 失败时均 fail-closed。
- 权威不变量：测试覆盖 `creation_contract/scene_blueprints/asset_manifest/pending_action/operations` 组成的业务合同 hash、当前用户输入和完整目标 Workflow 在外置前后保持一致；调用方原始载荷不会被修改。
- 修改文件：`backend/pixelflow/agent_runtime/context/externalizer.py`、`backend/pixelflow/agent_runtime/context/assembler.py`、`backend/pixelflow/agent_runtime/context/__init__.py`、`backend/tests/test_agent_runtime_context_externalizer.py`、本状态文件；未修改配置、长期 feature 分支或 `status/BOARD.md`，未实现 M04 压缩行为。
- TDD 证据：首轮新增测试因 `externalizer` 模块不存在出现 4 项预期失败，最小实现后全部转绿；独立审核复现普通字符串无最小片段和超长引用可能放大 Prompt，再新增 3 项失败断言，逐项补齐安全片段、严格缩小和 Store 幂等协议后转绿。门禁恢复复审又发现 JSON 常见的带引号凭据键未被脱敏，先新增 `test_externalizer_redacts_quoted_json_credentials_from_snippet` 并得到 `1 failed`，再以最小正则修复支持单引号/双引号键值，得到 `1 passed`。
- 定向测试：`python -m pytest tests/test_agent_runtime_context_externalizer.py tests/test_agent_runtime_context_assembler.py tests/test_agent_runtime_token_meter.py tests/test_agent_runtime_context_profiles.py tests/test_agent_runtime_contracts.py tests/test_agent_runtime_config.py tests/test_profile_config.py tests/test_pixelflow_memory_helper.py -q`，最终结果 `120 passed, 1 warning`。该 warning 与开工基线一致，来自既有 LangGraph 依赖的 `LangChainPendingDeprecationWarning`。
- 静态检查：修改范围 `ruff check` 通过，5 个相关 Python 文件 `ruff format --check` 通过，`git diff --check` 通过。
- 独立审核：首轮结论 `Ready to merge: With fixes`，Critical 0、Important 2、Minor 1；修复无预置片段、Prompt 严格缩小和 Store 幂等问题后，增量复审结论 `Ready to merge: Yes`，Critical/Important/Minor 均为 0，并独立复跑 `119 passed, 1 warning` 及静态检查。门禁恢复的全新 reviewer 首轮又指出 JSON 带引号凭据泄露这一项 Important；按 TDD 修复后只读复审确认该问题已关闭，最终 Critical/Important/Minor 均为 0，`Ready to merge: Yes`。
- 中文规范：新增/修改注释和 docstring 均为中文说明；本切片没有配置变更；本地中文工程规范已由权威 M03 Final 门禁执行，状态收口提交后还会复跑同一门禁，绿色后才允许 push。
- 历史门禁阻塞（已解除）：旧版 `Invoke-AgentModuleGate.ps1` 曾把 M03 Final 扩大到仓库全量 pytest/Ruff，得到 `4292 passed, 108 failed, 37 errors, 42 skipped, 12 warnings`，红灯集中在 M03 范围外的既有 auth、sandbox/provisioner、Skills、上传、Harness 等测试。基线独立复跑也得到相同代表性失败，因此当时按 fail-closed 写入 `integration_blocked`，没有启动集成。
- 依赖与恢复证据：`origin/feature/dev_0.8.4_boguan` 的 `fb7450775a227d891372c19eae1b308045c51e68` 已进入当前 Agent `2648723185655e2e59faf916147cbb9b0359b363`；门禁基线修复提交也已成为该 Agent 的祖先。恢复时远端 M03 为 `0f12e235bb7fb9ab8ff4088f1475ed8723848cad`，本地模块分支与其一致，唯一 worktree 和唯一写入者检查通过。模块使用项目虚拟环境 Python `3.12.13`，没有恢复已确认无需保留的 Docker/Sandbox 文件。
- 最终模块门禁：使用当前 Agent `2648723185655e2e59faf916147cbb9b0359b363` 中修复后的权威脚本执行 `Invoke-AgentModuleGate.ps1 -ModuleId M03 -GateType Final -ChinesePolicyBaseRef 5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`，结果 `Passed=True`、`CommandCount=4`；覆盖 `git diff --check`、项目 Python 3.12、M03 的 120 项 pytest 和对应 Ruff 范围。
- 最终结论：M03.4 是模块最后一片且不是阶段中间检查点；完整模块门禁已绿色，状态写为 `ready_for_integration`。未修改 `status/BOARD.md`、两个长期 feature 分支或真实付费 API，也未自动启动单槽集成。
- 下一步第一动作：开发者复制执行手册 9.10A 话术，指定 M03，手动启动唯一单槽集成人；不得重新执行 M03.4，也不得自动进入 M04。

## 恢复提示

256K/384K/512K 是建议上限，不是当前 AIRouter 已验证事实。缺失档案必须走 128K；未验证或过期档案使用不超过 128K、且不放大已声明能力的保守上限。M03.4 只能外置大 tool/artifact 输出并提取必要片段，不得裁剪当前用户输入、目标 Workflow 权威字段或修改 business contract。
