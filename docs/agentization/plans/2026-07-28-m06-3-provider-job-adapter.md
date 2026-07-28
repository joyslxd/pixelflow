# M06.3 Provider Job Adapter 实施计划

> **执行要求：** 使用 `superpowers:test-driven-development` 严格执行红灯、绿灯、重构。本计划只覆盖 M06.3；完成独立只读审核、一个中文提交和 push 后必须停止。

**目标：** 在持久化 Operation 与现有 v2 异步任务 Service 之间增加稳定的 `ProviderJobAdapter`，把各业务 `start/status` 返回归一为轮询、成功、业务失败、额度暂停和超时五类结果，同时保证 Authorization、供应商原始错误和未知状态不会进入稳定结果。

**架构：** `ProviderJobAdapter` 是类似 Java 防腐层 Client 的边界。上游 Workflow 只依赖规范 `ProviderJobSnapshot`；下游继续由图片、视频、PPT、视频分析或剪映的现有 start/status Service 实现。Adapter 通过一个最小异步 Service Protocol 调用下游，显式透传 operation 幂等键和本次 Authorization，但不把二者保存在实例或结果中；现有 Service 的 `ok/job_id/status/result/error/message` DTO 只在 Adapter 内解析，未知或自相矛盾的响应 fail-closed。

**技术栈：** Python 3.12、Pydantic v2、pytest、ruff。

## 全局约束

- 不修改两个长期 feature 分支，不建立切片子分支或额外 worktree。
- 不新增表、字段、索引、migration、配置、HTTP API 或 content-app 合同。
- 不调用真实图片、视频、PPT、视频分析、剪映或其他付费 API；测试只使用确定性 fake Service。
- 不实现 M06.4 的 Operation 终态落库、Event Outbox、Workflow Graph resume、终态 claim 或 crash window。
- 不实现 M06.5 的 shutdown、restart、404/expired 人工恢复或进程级扫描。
- Adapter 只接受结构化 start/status DTO；现有 DTO 中明确的供应商 raw/provider response 字段先递归剔除，未知状态、缺失或错配 provider job ID、其余敏感结果和带认证信息、查询串或 fragment 的完整 URL 一律 fail-closed。
- Authorization、token、API key、secret、凭据、供应商异常原文和完整 traceback 不进入规范结果、日志、状态或测试快照。
- 所有新增或修改的人工注释、计划、测试报告和 commit 使用中文主体语义。

---

### 任务一：冻结 Provider Service 与规范结果合同

**文件：**

- 新增：`backend/tests/test_agent_runtime_provider_job_adapter.py`

- [x] **步骤 1：编写 start 透传与轮询结果合同**

使用确定性 fake Service 断言 Adapter 把规范请求、单次 Authorization 和 operation 幂等键原样传给 start；`running/queued/pending/polling/processing` 统一映射为 `polling`，稳定返回 provider job ID，结果中不出现 Authorization。

- [x] **步骤 2：编写成功与业务失败映射合同**

`succeeded/success/completed/done` 统一映射为成功并保留业务结果；`failed/error/cancelled` 或显式 `ok=false` 统一映射为业务失败，只返回固定安全 reason/message，不回显供应商错误原文。

- [x] **步骤 3：编写 402 与超时映射合同**

结构化 `status_code=402`、`quota_insufficient=true` 和受控额度不足字段统一映射为 `paused_quota`；显式 `timeout/timed_out` 与调用边界 `TimeoutError` 统一映射为 `timeout`。异常字符串即使带 token 或 URL 也不能进入规范结果。

- [x] **步骤 4：编写 fail-closed 边界合同**

未知状态、缺失 start job ID、status job ID 与查询目标不一致、非对象 DTO、敏感结果键和非法 JSON 结果必须抛出 `ProviderJobMappingError`，不得猜测为成功或重启任务。

- [x] **步骤 5：运行新测试并确认 RED**

```powershell
& E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe `
  -m pytest tests/test_agent_runtime_provider_job_adapter.py -q
```

### 任务二：实现最小 Provider Job Adapter

**文件：**

- 新增：`backend/pixelflow/agent_runtime/jobs/providers.py`
- 修改：`backend/pixelflow/agent_runtime/jobs/__init__.py`

- [x] **步骤 1：定义最小异步 Service Protocol**

定义 start/status 两个异步方法；start 显式接收规范业务请求、Authorization 和 operation 幂等键，status 只查询原 provider job ID。Protocol 不缓存调用参数。

- [x] **步骤 2：定义规范 Snapshot**

提供五态结果、受限 provider job ID、深度只读 JSON 业务结果、固定 `reason_code` 和安全中文 message；Pydantic 校验额外字段、非法 JSON、敏感键和凭据字符串，序列化前再次执行安全校验。

- [x] **步骤 3：实现响应与异常映射**

兼容 Mapping 和 Pydantic DTO；递归投影现有 DTO 的供应商 raw 字段，先识别 `quota_paused` 等额度暂停，再识别明确状态；TimeoutError、402 和其他业务异常分别映射，不回显异常字符串。未知状态或身份错配抛出 `ProviderJobMappingError`。

- [x] **步骤 4：导出稳定入口并运行定向 GREEN**

只从 `agent_runtime.jobs` 导出本片公开类型，不修改 M00 冻结合同 DTO、`OperationPort` 或现有 v2 Router。

### 任务三：回归、独立审核与交接

**文件：**

- 修改：`README.md`
- 修改：`AGENTS.md`
- 修改：`docs/pixelflow-agent-skill-flow-latest-design.md`
- 修改：`docs/agentization/status/M06-status.md`
- 新增：`docs/agentization/test-reports/M06.3.md`
- 修改：`docs/agentization/plans/2026-07-28-m06-3-provider-job-adapter.md`

- [x] **步骤 1：运行 M06.3 范围回归与静态检查**

覆盖 Provider Adapter、M06.1 operation、M06.2 lease、M00 合同、M01 Repository/migration、全部 Agent Runtime 扩展回归、Ruff、格式和 `git diff --check`。

- [x] **步骤 2：发起独立只读审核**

审核重点为状态映射优先级、402 可恢复语义、超时边界、provider job ID 错配、未知状态 fail-closed、Authorization/异常泄漏和 M06.4+ 越界。

- [x] **步骤 3：处理 Critical/Important 并重新验证**

每个有效问题先补失败合同再做最小修复；最终记录 Critical、Important、Minor 状态。

- [x] **步骤 4：完成中文状态与测试记录**

勾选 M06.3，释放唯一写入权，下一切片设为 M06.4；phase 保持 `in_progress`，不得更新 `status/BOARD.md` 或写任何 integration ready 状态。

- [x] **步骤 5：执行中文工程门禁并提交推送**

只暂存本切片文件，使用一个中文 commit；push `codex/agent-0.8.4-m06-external-jobs` 后核对远端 SHA 并停止。
