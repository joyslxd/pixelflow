# M04.2 增量 SummaryBuilder 实施计划

> **执行约束：** 本计划只覆盖 M04.2。完成 TDD、测试、独立审核、状态记录、一个独立提交和推送后必须停止，不得进入 M04.3。

**目标：** 基于 M04.1 的 `ContextSummary` 版本/证据合同，新增只消费“上一版结构化摘要 + 尚未覆盖的连续新消息”的增量 `SummaryBuilder`，复用 DeerFlow 的消息 token 计量和异步摘要能力，同时保证 Plan、创作合同、资产清单、pending action、operation 等业务上下文不进入摘要构建输入。

**架构：** `SummaryBuilder` 相当于应用 Service：校验连续消息区间、冻结输入、调用摘要 Engine，再负责生成版本元数据、累计消息证据和规范内容 hash；`SummaryEngine` 相当于可替换 Client。生产适配器组合现有 `DeerFlowSummarizationMiddleware` 的 token 计量与异步摘要能力，测试注入 fake Engine，不调用真实 LLM。M04.1 `StructuredSummaryRepository` 仍是持久化前的证据/版本校验边界，本片不绕过也不内嵌 Repository。

**技术栈：** Python 3.12、Pydantic v2、LangChain message、DeerFlow summarization middleware、pytest、ruff。

## 方案选择

1. 每次重新摘要全部原消息：实现简单，但不符合“旧摘要 + 新消息”增量合同，成本随历史线性增长，因此不采用。
2. Builder 在本地把上一版字段与新摘要字段永久做集合并集：能机械保留旧事实，但无法正确表达“问题已解决”“决策已替换”等状态变化，因此不采用。
3. 把上一版的结构化语义快照与仅新增消息交给结构化 Engine，Engine 返回完整的新语义快照；Builder 只管理可确定的版本、覆盖范围、hash 和证据累计。该方案保持输入增量且允许语义更新，关键事实保留由 M04.5 `SummaryVerifier` 再做 fail-closed 校验，因此采用。

## 全局约束

- 只修改 M04 上下文目录、M04 测试、M04 计划/状态/测试记录。
- 不修改冻结 `ContextSummary` 线字段、数据库表、migration、配置或两个长期 feature 分支。
- Builder 输入合同不接受 `WorkflowRecord`、`ContextEnvelope` 或任意 `business_context` 字段；当前用户输入仍由 Context Runtime 单独保留，本片只处理已经持久化且待覆盖的历史消息。
- 首版消息必须从 sequence 1 连续开始；后续版只能接在上一版覆盖终点之后，禁止重复、跳号、倒序、跨会话或改写既有消息 ID。
- Builder 不删除原消息、不写 Repository、不触发阈值、锁、队列、事件或真实 LLM；M04.3–M04.5 保持未开始。

---

### 任务一：用失败测试冻结增量输入与业务隔离

**文件：**

- 新增：`backend/tests/test_agent_runtime_summary_builder.py`
- 新增：`backend/pixelflow/agent_runtime/context/compaction.py`

**接口：**

```python
class SummaryBuilder:
    async def build(self, request: SummaryBuildRequest) -> SummaryBuildResult: ...
```

- [ ] 先编写首版和“旧摘要 + 新消息”用例，断言 fake Engine 只收到上一版语义快照和未覆盖的新消息，不收到全部历史或业务上下文。
- [ ] 编写空消息、跨会话、sequence 跳号/倒序/重复、消息 ID 与旧覆盖冲突、上一版会话不匹配等 fail-closed 用例。
- [ ] 运行新测试，确认因 `compaction` 模块尚不存在而在收集阶段失败。
- [ ] 实现严格的消息/请求/语义草稿合同、每个 Engine 边界前的深拷贝和最小连续区间校验。

### 任务二：用失败测试冻结 DeerFlow 复用和确定性元数据

**文件：**

- 修改：`backend/tests/test_agent_runtime_summary_builder.py`
- 修改：`backend/pixelflow/agent_runtime/context/compaction.py`
- 修改：`backend/pixelflow/agent_runtime/context/__init__.py`

- [ ] 测试 Builder 使用 Engine 返回的 token 数和语义草稿，自动生成 v1/v2、前驱 ID、累计覆盖消息、压缩模型、UTC 时间和稳定 `sha256` 内容 hash。
- [ ] 测试调用方在 await 期间修改来源对象不会影响结果；Engine 输出非法时不产生半成品摘要。
- [ ] 测试 DeerFlow 适配器使用现有 middleware 的 token counter 与异步摘要入口，并把严格 JSON 输出解析为结构化草稿；错误/非 JSON 输出 fail-closed。
- [ ] 实现最小 Builder 与 DeerFlow 适配器，不复制 DeerFlow 的 token 估算、消息裁剪或模型调用逻辑。

### 任务三：回归、审核与交接

**文件：**

- 修改：`docs/agentization/status/M04-status.md`
- 新增：`docs/agentization/test-reports/M04.2.md`

- [ ] 运行 SummaryBuilder 新测试、M04.1 结构化摘要、M03 ContextAssembler、冻结合同和 DeerFlow summarization/dynamic context 回归。
- [ ] 对变更 Python 路径运行 `ruff check`、`ruff format --check`，并运行 `git diff --check`。
- [ ] 启动独立只读 reviewer，检查增量边界、业务上下文隔离、输入冻结、错误 fail-closed、DeerFlow 复用和 M04.3–M04.5 越界。
- [ ] 处理全部 Critical/Important 意见，重新运行本切片完整门禁。
- [ ] 用中文更新状态和测试报告，勾选 M04.2、把下一切片设为 M04.3，释放当前写入者；phase 保持 `in_progress`。
- [ ] 运行中文工程规范检查，创建一个中文独立 commit，推送 `codex/agent-0.8.4-m04-context-compaction` 并核对远端 SHA 后停止。
