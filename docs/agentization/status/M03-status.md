# M03 模型档案、Token 预算与 ContextEnvelope

- phase：`in_progress`
- owner：A
- branch：`codex/agent-0.8.4-m03-context-runtime`
- base Agent SHA：`5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- 依赖：M00
- 当前切片：`M03.2`（等待开发者手动启动）
- 当前唯一写入者：尚未领取
- M03.1 开始时间：`2026-07-24 02:55:58 +08:00`
- M03.1 完成时间：`2026-07-24 03:12:35 +08:00`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m03-context-runtime`
- 锁定文件：无；M03.1 写锁已释放

## 切片

- [x] M03.1 ModelContextProfile 与 128K 保守降级（2h）
- [ ] M03.2 TokenMeter/usable budget（2.5h）
- [ ] M03.3 ContextEnvelope assembler（2.5h）
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

## 恢复提示

256K/384K/512K 是建议上限，不是当前 AIRouter 已验证事实。缺失档案必须走 128K；未验证或过期档案使用不超过 128K、且不放大已声明能力的保守上限。M03.2 不得把业务建议上限误当成模型已验证能力。
