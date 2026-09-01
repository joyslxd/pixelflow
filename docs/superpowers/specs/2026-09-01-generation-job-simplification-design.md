# 图片与视频 GenerationJob 轻量链路设计

## 1. 背景与目标

当前图片和视频生成使用以下可靠性链路：

```text
Harness Run → Tool Call → OperationBatch → Batch Child → M06 Operation
→ Provider Job → Poll → Completion Callback → Operation Resume Run
```

这套链路适合需要复杂恢复、额度暂停和跨工作流恢复的通用外部 Operation，
但对“一个资产/一个分镜对应一个 Provider 异步任务”的生图、生视频过度编排。
它额外生成 Batch ID、Child 幂等键、Operation Job ID、Provider Job ID 和 Resume Run ID，
导致启动慢、排障困难，并且 Provider 响应解析失败时容易留下孤儿 Operation。

本次目标是把图片和视频生成收敛为：

```text
Harness Run
→ Tool Call
→ Gateway GenerationJob
→ Provider Job
→ Gateway Poll
→ Workspace 回写
```

本次改造不改变 Harness、Gateway、Sidecar、Workspace 和 Provider 的权责边界：
Gateway 仍是所有业务状态的权威写入方，Sidecar 仍只能通过 Tool Broker 请求生成能力。

## 2. 非目标

- 不把 Provider 调用迁入 Sidecar 或 Harness background job。
- 不让 Sidecar 直接访问数据库、Workspace 或 Provider。
- 不保存用户 Authorization、Provider 原始响应或用户正文到日志、代码仓库或 GenerationJob 凭据记录。
- 不改变图片、视频 Provider 的外部接口和计费语义。
- 不自动重放无法确认 Provider 是否受理的 start 请求。
- 不删除 Workspace V2 资产注册表、分镜 Prompt Package、revision 和授权确认约束。

## 3. 领域模型

### 3.1 GenerationJob

每个实际 Provider Job 对应一个 Gateway GenerationJob。一次 Tool 调用提交多张参考图时，
按资产分别创建多个 GenerationJob；它们共享同一 Tool 调用上下文，但不再创建 Batch 或 Child。

GenerationJob 的核心字段：

| 字段 | 说明 |
| --- | --- |
| `generation_job_id` | Gateway 生成的稳定内部 ID，供 Tool 和 Workspace 状态查询使用 |
| `user_id` / `conversation_id` | 权限边界 |
| `workspace_id` | 权威 Workspace 边界 |
| `kind` | `image` 或 `video` |
| `item_id` | 图片资产 ID或视频分镜 ID |
| `variant_index` | 视频版本号；图片固定为 1 |
| `status` | `queued`、`starting`、`polling`、`succeeded`、`failed`、`timeout`、`expired`、`indeterminate` |
| `request_json` | 经过 DTO 校验的最小 Provider 请求快照，禁止记录 Authorization |
| `request_hash` | Provider 请求摘要，用于幂等校验 |
| `idempotency_key` | 用户、Workspace、item、variant、attempt 的稳定幂等键 |
| `provider_id` / `provider_job_id` | Provider 路由和外部任务 ID |
| `result_json` | 仅保存稳定 Artifact/Image/Video 结果字段 |
| `failure_reason_code` | 固定错误码，不保存 Provider 原始异常 |
| `next_poll_at` | Gateway Poll 调度时间 |
| `lease_owner` / `lease_expires_at` | Gateway Worker 的短租约 |
| `created_at` / `updated_at` | 审计时间 |

`indeterminate` 表示 Provider start 的结果无法证明“未受理”。该状态禁止自动重试，
避免响应解析失败后重复计费；由后续受控人工处理或 Provider 幂等查询解决。

### 3.2 状态转换

```text
queued
  └─ start lease → starting
                    ├─ Provider Job ID → polling
                    │                     ├─ Provider success → succeeded
                    │                     ├─ Provider failed → failed
                    │                     ├─ Provider timeout → timeout
                    │                     └─ Provider expired → expired
                    └─ 响应不确定 → indeterminate
```

状态转换必须由 Repository 条件更新保证，租约失效不能覆盖其他 Worker 的结果。

### 3.3 Workspace 投影

图片 GenerationJob 完成时，Gateway 按 `item_id` 和 Workspace revision 原子回写：

```text
planned + planned_generation
  ├─ succeeded → ready + usable_for_video=true + provider_artifact_ref + image_url + completed_at
  └─ failed    → failed + usable_for_video=false + failure_reason_code + failed_at
```

视频 GenerationJob 完成时，Gateway 按 `scene_id` 和 `variant_index` 合并写入分镜版本，
不得覆盖其他并发镜头已经写入的结果。

## 4. 组件设计

### 4.1 GenerationJob Repository

新增 `pixelflow/generation_jobs/`，提供 Gateway 内部 Repository 和 Service：

- `contracts.py`：状态、类型、记录和请求 DTO。
- `repository.py`：SQL/Memory 实现，负责幂等创建、领取 start、绑定 Provider Job ID、领取 Poll、终态更新。
- `credentials.py`：按 GenerationJob 保存短时凭据，进程退出时清理，不落库。
- `service.py`：校验 Workspace、请求摘要、用户确认上下文，创建或回读 GenerationJob。
- `worker.py`：统一启动和 Poll Worker；最多 6 个 Provider Job 并发。
- `projection.py`：图片资产和视频分镜终态 Workspace Patch。

低层 Provider Adapter 可复用现有 `ProviderJobAdapter` 的稳定六态 DTO，
但不再依赖 Operation Repository、Operation Completion 或 Batch 回调。

### 4.2 Tool

`generate_image_assets` 和 `generate_scenes` 保持 Tool 名称、确认策略和输入语义，
只替换底层 Port：

```text
Tool
→ GenerationJobService.submit_image/submit_video
→ 返回 generation_job_ids、queued 状态和 item_id 映射
```

Tool 不等待 Provider，不创建 Harness Resume Run，也不直接写 Workspace。

新增或替换 `inspect_generation_jobs`，按当前用户、会话和 Workspace 查询 GenerationJob，
返回状态、item_id、provider_job 是否已绑定、失败原因码和安全产物引用；删除 `inspect_operation_batch`。

### 4.3 Gateway Worker

Gateway 生命周期只装配 GenerationJob Worker：

1. `run_once` 先领取最多 6 个 `queued/starting lease expired` 的 Job。
2. Worker 根据 `kind` 和 Provider 路由启动 Provider。
3. 成功绑定 Provider Job ID 后立即将 Job 置为 `polling`。
4. 同一 Worker 或重启后的 Worker 领取到期 Poll Job。
5. Provider 终态直接调用 GenerationJob Repository 和 Workspace Repository。
6. 成功回写后清理内存凭据；失败只回写安全原因码。

不再装配：

- 图片/视频 OperationBatch Dispatcher。
- Batch Child Terminal Worker。
- Operation Recovery Runtime 的图片/视频映射。
- Operation Completion Callback。
- OperationBatch Resume Worker。

## 5. 幂等与计费安全

GenerationJob 的幂等键由 Gateway 根据冻结的 Run/Tool Call、Workspace、item 和 attempt 构造。
同一幂等键回读原 Job，不能生成新的 Provider 请求。

Provider start 只有在 GenerationJob start lease 内执行一次。Provider Job ID 一旦写入，
后续只允许 Poll，不允许再次 start。HTTP 200 但响应无法安全提取 Provider Job ID 时标记
`indeterminate`，不自动重试。

最多 6 个并发限制由 GenerationJob Worker 的 Semaphore 和 Repository 领取条件共同保证，
不再通过 Batch Child 槽位实现。

## 6. 旧链路删除与迁移

新链路测试通过后，删除图片/视频旧编排代码：

- 图片/视频 M06 Operation Port、Batch Dispatcher、Batch Operation Port 和 Batch Worker。
- `OperationBatch`、Batch Child、Batch Terminal Callback、Batch Resume 和图片/视频 Operation Recovery 注册。
- `inspect_operation_batch` Tool 及其专用 DTO/测试。
- Gateway 中对应的 SQL Repository、Worker、Completion/Resume 装配。
- 只为图片/视频旧链路存在的 Operation DTO、表模型、迁移和测试。

通用 `ProviderJobAdapter` 如果仍被 GenerationJob 使用，则迁移到 GenerationJob Provider 边界；
通用 Operation 代码只有在全仓无其他业务消费者时才删除，不为了清理而误删其他能力。

旧数据库表不在运行时伪造状态。上线前先停止旧链路写入；已完成历史记录保留，
有 Provider Job ID 的运行记录由一次性迁移登记为历史 GenerationJob 或进入明确的人工核查队列，
没有 Provider Job ID 的孤儿记录不得自动重放。

## 7. 验收标准

- 两张参考图产生两个 GenerationJob，不产生 OperationBatch、Batch Child 或 M06 Operation。
- 单镜视频产生一个 GenerationJob，不产生 OperationBatch、Batch Child 或 M06 Operation。
- Gateway 重启后可继续 Poll 已绑定 Provider Job ID 的 Job。
- 图片完成后资产直接变为 `ready`，写入 Artifact、URL 和完成时间。
- 视频完成后对应分镜版本直接写回，其他镜头结果不丢失。
- 失败和响应解析异常返回固定 `failure_reason_code`，不泄露 Provider 原文。
- 不创建 Operation Resume Run。
- Provider start 失败不会留下可被自动重复计费的活跃 Job。
- 全仓生产代码不再引用图片/视频 OperationBatch、M06 Operation 和 Operation Resume。
- 现有 Workspace、Harness、Tool Broker、Provider 和非生成能力测试保持通过。
