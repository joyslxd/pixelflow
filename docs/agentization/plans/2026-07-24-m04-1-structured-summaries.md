# M04.1 结构化摘要与证据仓库实施计划

> **执行约束：** 本计划只覆盖 M04.1。完成 TDD、测试、独立审核、状态记录、一个独立提交和推送后必须停止，不得进入 M04.2。

**目标：** 在冻结 `ContextSummary` 线合同和 M01 双实现 Repository 之上，建立可验证的结构化摘要版本链、消息覆盖范围与 Artifact 证据引用仓库。

**架构：** `ContextSummary` 相当于 DTO，继续保持跨模块字段不变；`StructuredSummaryRepository` 相当于领域 Repository/Service，组合 M01 的 `AgentRuntimeRepository` 与只读 `SummaryEvidenceSource`。写入前先验证同用户、同会话证据，再验证连续版本链，最后调用 M01 Repository 持久化；原始消息和 Artifact 只读取、不删除、不覆盖。

**技术栈：** Python 3.12、Pydantic v2、SQLAlchemy 2 async、aiosqlite、pytest、ruff。

## 全局约束

- 只修改 M04 上下文目录、M04 测试、M04 计划/状态/测试记录。
- 不修改冻结 DTO 字段、数据库表或 migration，不调用真实 LLM、PowerMem 或付费供应商。
- M04.1 不实现 SummaryBuilder、四阈值 Coordinator、压缩锁、输入队列或压缩事件。
- 摘要保存失败必须 fail-closed；原始消息、Plan、创作合同、资产清单和 pending job 不得被修改。
- 新增或修改的解释性注释、docstring、测试/状态记录使用中文。

---

### 任务一：用失败测试固定结构化摘要 schema

**文件：**

- 修改：`backend/pixelflow/agent_runtime/contracts/context.py`
- 新增：`backend/tests/test_agent_runtime_structured_summaries.py`

**接口：**

- 消费：M00 冻结的 `ContextSummary` 字段。
- 产出：保持相同 JSON 字段的结构约束；版本 1 不允许前驱，版本 2 及以上必须声明前驱；覆盖起止必须成对、顺序合法，并与有序消息 ID 数量一致。

- [ ] 先构造缺前驱、非法覆盖范围、重复/空证据引用等输入，验证当前合同错误地接受这些输入。
- [ ] 运行 `pytest tests/test_agent_runtime_structured_summaries.py -q`，确认测试因缺少约束而失败。
- [ ] 使用 Pydantic `model_validator` 和字符串元素约束实现最小校验，不增加或删除线字段。
- [ ] 重跑新测试，确认 schema 用例转绿。

### 任务二：用失败测试固定证据与版本链 Repository

**文件：**

- 新增：`backend/pixelflow/agent_runtime/context/summaries.py`
- 修改：`backend/pixelflow/agent_runtime/context/__init__.py`
- 修改：`backend/tests/test_agent_runtime_structured_summaries.py`

**接口：**

```python
class SummaryEvidenceSource(Protocol):
    async def load_summary_evidence(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> SummaryEvidenceSnapshot: ...


class StructuredSummaryRepository:
    async def save(self, user_id: str, summary: ContextSummary) -> ContextSummary: ...
    async def get(self, user_id: str, summary_id: str) -> ContextSummary | None: ...
    async def list(self, user_id: str, conversation_id: str) -> list[ContextSummary]: ...
```

- [ ] 为 Memory/SQL 两种 M01 Repository 编写同一套 round-trip 测试。
- [ ] 编写连续版本、错误前驱、跳版、覆盖回退、消息缺失/乱序、Artifact 缺失和跨用户/跨会话证据失败测试。
- [ ] 运行新测试，确认因 `summaries` 模块尚不存在而失败。
- [ ] 实现不可变证据快照、证据 Source 协议和领域 Repository；证据校验在底层 `create_summary()` 前完成。
- [ ] 验证第二版只能指向同会话最新摘要，覆盖消息必须保持已有前缀且不能回退。

### 任务三：回归、审核与交接

**文件：**

- 修改：`docs/agentization/status/M04-status.md`
- 新增：`docs/agentization/test-reports/M04.1.md`

- [ ] 运行结构化摘要新测试、M01 Repository、M03 Context assembler、冻结合同和既有 summarization middleware 回归。
- [ ] 对变更 Python 路径运行 `ruff check`、`ruff format --check`，并运行 `git diff --check`。
- [ ] 启动独立只读 reviewer，检查版本链、所有者隔离、证据有效性、原消息不变和 M04.2–M04.5 越界。
- [ ] 处理全部 Critical/Important 意见，重新运行本切片完整门禁。
- [ ] 用中文更新状态和测试报告，勾选 M04.1、把下一切片设为 M04.2，释放当前写入者；phase 保持 `in_progress`。
- [ ] 运行中文工程规范检查，创建一个中文独立 commit，推送 `codex/agent-0.8.4-m04-context-compaction` 并核对远端 SHA后停止。
