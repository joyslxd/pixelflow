# 图片与视频 GenerationJob 轻量链路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 执行状态（2026-09-01）：Task 1–7 已完成。旧图片/视频 Batch、Batch Child、M06 Operation
> 及其恢复/完成回调生产代码已删除；当前实现以 GenerationJob 直接链路为准。下方步骤保留为
> 实施记录，复核时应以代码和验证结果为准。

**Goal:** 删除图片/视频强制 Batch + Operation 编排，改为 Gateway GenerationJob 直接启动、轮询并回写 Workspace。

**Architecture:** Gateway 新增 GenerationJob Repository、Service 和 Worker。图片与视频 Tool 只提交 GenerationJob；Worker 直接复用稳定 Provider Adapter 启动和 Poll，终态直接由 Gateway 投影 Workspace，不创建 Operation Resume Run。新链路验收后删除旧图片/视频 Batch、Child、M06 Operation、Completion Callback 和 Resume 代码。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy Async、SQLite/PostgreSQL、Pydantic、httpx、pytest、ruff。

**Spec:** `docs/superpowers/specs/2026-09-01-generation-job-simplification-design.md`

## Global Constraints

- 只使用新 Harness 架构；所有业务写入仍由 Gateway 完成。
- Sidecar 只能通过 Tool Broker 调用业务能力，不能访问数据库、Provider 或宿主文件系统。
- 用户 Authorization 只保存在 Gateway 进程内短时凭据仓，不落库、不进日志。
- Provider 原始响应、用户正文、Token、Secret 不写入仓库、日志或测试夹具。
- 图片/视频 GenerationJob 启动和 Poll 最多 6 个并发。
- GenerationJob 的幂等键必须绑定 user、conversation、workspace、item、variant 和 attempt。
- 不自动重放 Provider start 结果不确定的 Job。
- 新增或修改 YAML/TOML 配置叶子项必须紧邻中文用途与影响说明。

---

### Task 1: 建立 GenerationJob 合同和持久化模型

**Files:**
- Create: `backend/pixelflow/generation_jobs/contracts.py`
- Create: `backend/pixelflow/generation_jobs/repository.py`
- Modify: `backend/pixelflow/agent_control_plane/persistence/models.py`
- Modify: `backend/pixelflow/platform/persistence/engine.py`
- Test: `backend/tests/test_generation_jobs.py`

**Interfaces:**
- `GenerationJobKind`: `image`、`video`。
- `GenerationJobStatus`: `queued`、`starting`、`polling`、`succeeded`、`failed`、`timeout`、`expired`、`indeterminate`。
- `GenerationJobRecord`：包含 `generation_job_id`、owner 范围、`kind`、`item_id`、`variant_index`、`status`、`request_json`、`request_hash`、`idempotency_key`、Provider 字段、结果字段和租约字段。
- `GenerationJobRepository.create_or_read(...)`：按幂等键回读或创建。
- `claim_start_jobs(...)`、`bind_provider_job(...)`、`claim_poll_jobs(...)`、`complete(...)`：全部使用 owner、状态和租约条件更新。

- [ ] **Step 1: Write the failing tests**

  覆盖：相同幂等键只创建一个 Job；最多领取 6 个；启动租约过期可重新领取；绑定 Provider Job ID 后只能进入 polling；终态更新不会覆盖其他租约；indeterminate 不进入自动重试集合。

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

  Expected: FAIL because GenerationJob contracts and Repository do not exist.

- [ ] **Step 3: Implement contracts, Memory Repository, SQL row and schema initialization**

  新增 `PixelFlowGenerationJobRow`，字段与设计文档一致；`ensure_schema` 通过 `Base.metadata.create_all` 建表，并为既有 SQLite/PostgreSQL 增加 GenerationJob 索引。Repository 的 SQL 和 Memory 行为保持一致。

- [ ] **Step 4: Run focused tests**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add backend/pixelflow/generation_jobs backend/pixelflow/agent_control_plane/persistence/models.py backend/pixelflow/platform/persistence/engine.py backend/tests/test_generation_jobs.py && git commit -m "新增 GenerationJob 持久化合同"`

### Task 2: 实现凭据仓和 GenerationJob Service

**Files:**
- Create: `backend/pixelflow/generation_jobs/credentials.py`
- Create: `backend/pixelflow/generation_jobs/service.py`
- Modify: `backend/pixelflow/agent_tools/video/contracts.py`
- Test: `backend/tests/test_generation_jobs.py`

**Interfaces:**
- `TransientGenerationJobCredentialStore.put/get/discard/aclose`：只保存 Gateway 进程内凭据。
- `GenerationJobService.submit_image(...)`：校验 `planned_generation + planned` 图片资产并创建图片 Job。
- `GenerationJobService.submit_video(...)`：校验完整视频生产合同、分镜引用和版本后创建视频 Job。
- Service 返回 `GenerationJobSubmission`，包含 Job ID、item_id、状态和是否需要 Poll。

- [ ] **Step 1: Write failing tests**

  覆盖图片只允许 planned 资产、视频只允许已登记 ready 素材、同一请求幂等回读、凭据不落库且 `aclose` 清理凭据。

- [ ] **Step 2: Run tests and verify failure**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

- [ ] **Step 3: Implement Service and credential store**

  Service 从 Gateway 传入的权威 Workspace 构造最小 Provider 请求，计算 request hash 和稳定幂等键；只把请求快照保存到 GenerationJob，不把 Authorization 放入记录。

- [ ] **Step 4: Run focused tests**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

- [ ] **Step 5: Commit**

  Run: `git add backend/pixelflow/generation_jobs backend/tests/test_generation_jobs.py && git commit -m "新增 GenerationJob 提交服务"`

### Task 3: 实现直接 Provider Start/Poll Worker

**Files:**
- Create: `backend/pixelflow/generation_jobs/worker.py`
- Create: `backend/pixelflow/generation_jobs/providers.py`
- Modify: `backend/pixelflow/capabilities/image_generation/providers/content_app.py`
- Modify: `backend/pixelflow/capabilities/video_generation/providers/content_app.py`
- Test: `backend/tests/test_generation_jobs.py`

**Interfaces:**
- `GenerationJobProvider`：`start(request, authorization, idempotency_key)` 和 `status(provider_job_id, user_id, conversation_id)`。
- `GenerationJobProviderResolver.resolve(kind)`：返回图片或视频 Provider。
- `GenerationJobWorker.run_once()`：直接领取 queued/start-expired Job 和 due polling Job，最多 6 个并发。

- [ ] **Step 1: Write failing tests**

  用 MockTransport Provider 验证：queued Job 只调用一次 start；返回 Provider Job ID 后变为 polling；Poll succeeded 后直接进入终态；Provider 200 响应映射失败写入 `indeterminate` 和受控原因码；重启 Worker 能继续 Poll。

- [ ] **Step 2: Run tests and verify failure**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

- [ ] **Step 3: Implement Worker and Provider boundary**

  复用现有稳定 ProviderJobAdapter 的六态结果，但将 mapping error 的 reason_code 保留下来；start 不确定时标记 indeterminate，不做自动重试；Provider Job ID 已存在时只允许 status 调用。

- [ ] **Step 4: Run focused tests**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

- [ ] **Step 5: Commit**

  Run: `git add backend/pixelflow/generation_jobs backend/pixelflow/capabilities/image_generation/providers/content_app.py backend/pixelflow/capabilities/video_generation/providers/content_app.py backend/tests/test_generation_jobs.py && git commit -m "实现 GenerationJob 直接启动与轮询"`

### Task 4: 实现图片和视频 Workspace 终态投影

**Files:**
- Create: `backend/pixelflow/generation_jobs/projection.py`
- Modify: `backend/pixelflow/video/workspace/repository.py`
- Modify: `backend/pixelflow/video/workspace/sql_repository.py`
- Test: `backend/tests/test_generation_jobs.py`

**Interfaces:**
- `project_image_job(workspace, job, now)`：返回安全图片资产 Patch。
- `project_video_job(workspace, job, now)`：返回按 scene_id/variant_index 合并的分镜 Patch。
- `GenerationJobWorker` 在终态调用 `apply_workspace_patch(... expected_revision=...)`，冲突时重新读取并执行幂等判断，不覆盖并发结果。

- [ ] **Step 1: Write failing tests**

  覆盖图片 ready 字段、失败原因码、视频版本合并、并发镜头不丢失、重复终态不重复修改 revision。

- [ ] **Step 2: Run tests and verify failure**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

- [ ] **Step 3: Implement projection and retry-safe revision handling**

  从旧 `video/adapters/operations/projector.py` 迁移纯 Workspace 投影语义到 GenerationJob projection，删除对 Operation stage、completion event 和 batch callback 的依赖。

- [ ] **Step 4: Run focused tests**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py -q`

- [ ] **Step 5: Commit**

  Run: `git add backend/pixelflow/generation_jobs backend/pixelflow/video/workspace backend/tests/test_generation_jobs.py && git commit -m "接入 GenerationJob Workspace 终态投影"`

### Task 5: 切换图片/视频 Tool 和 Gateway 装配

**Files:**
- Modify: `backend/pixelflow/agent_tools/video/image_assets.py`
- Modify: `backend/pixelflow/agent_tools/video/scene.py`
- Modify: `backend/pixelflow/agent_tools/video/operation_batch.py` or delete after Tool replacement
- Modify: `backend/pixelflow/agent_tools/catalog.py`
- Modify: `backend/app/gateway/app.py`
- Test: `backend/tests/test_generation_jobs.py`
- Test: existing image/video Tool and Gateway contract tests

**Interfaces:**
- `GenerateImageAssetsTool(generation_job_port=...)` 返回 `generation_job_ids`。
- `GenerateScenesTool(generation_job_port=...)` 返回 `generation_job_ids`。
- `InspectGenerationJobsTool` 替换 `InspectOperationBatchTool`。
- Gateway 只装配 `GenerationJobService`、Provider Resolver 和 `GenerationJobWorker`；不再装配图片/视频 Batch/Operation Worker。

- [ ] **Step 1: Write failing Tool/Gateway tests**

  验证图片两资产只创建两个 GenerationJob；视频单镜只创建一个 GenerationJob；Manifest 不再发布 `inspect_operation_batch`；Gateway 生成能力装配不包含 OperationBatch 或 OperationRecovery Worker。

- [ ] **Step 2: Run tests and verify failure**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py tests/test_video_agent_tool_registry.py -q`

- [ ] **Step 3: Switch Tool Port and Gateway lifespan wiring**

  Tool 保留确认和参数校验，改为调用 GenerationJob Service；Gateway Provider 设置和 Authorization Store 继续复用，但只将 Provider Resolver 注入 GenerationJob Worker。

- [ ] **Step 4: Run Tool/Gateway tests**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py tests/test_video_agent_tool_registry.py tests/test_agent_runtime_gateway_readiness.py -q`

- [ ] **Step 5: Commit**

  Run: `git add backend/app/gateway/app.py backend/pixelflow/agent_tools backend/tests/test_generation_jobs.py backend/tests/test_video_agent_tool_registry.py && git commit -m "切换图片视频 Tool 到 GenerationJob"`

### Task 6: 删除旧图片/视频 Batch 与 Operation 代码

**Files:**
- Delete: `backend/pixelflow/video/adapters/operations/images.py`
- Delete: `backend/pixelflow/video/adapters/operations/scenes.py`
- Delete or reduce: `backend/pixelflow/video/adapters/operations/__init__.py`
- Delete: `backend/pixelflow/agent_tools/video/operation_batch.py`
- Delete: `backend/pixelflow/operations/jobs/batch.py`
- Delete: `backend/pixelflow/operations/jobs/batch_callback.py`
- Delete: `backend/pixelflow/operations/jobs/batch_repository.py`
- Delete: `backend/pixelflow/operations/jobs/batch_resume.py`
- Delete: `backend/pixelflow/agent_harness/operation_batch_resume.py` if no remaining consumer
- Modify/Delete: `backend/pixelflow/operations/jobs/recovery.py`, `completion.py`, `coordinator.py`, `leases.py`
- Modify: `backend/pixelflow/agent_control_plane/persistence/models.py`
- Modify: `backend/pixelflow/platform/persistence/engine.py`
- Delete/Rewrite: `backend/tests/test_operation_batch.py`

**Interfaces:**
- 生产代码不再创建图片/视频 OperationBatch、Batch Child 或 M06 Operation。
- 旧表只在确认无消费者后移除 ORM/新建路径；历史数据库记录不通过代码伪造 ready。

- [ ] **Step 1: Run full reference inventory before deletion**

  Run: `rg -n "OperationBatch|BatchChild|M06Image|M06Scene|operation_batch|OperationRecoveryRuntime|Operation Resume" backend/app backend/pixelflow backend/tests`

  逐项确认每个引用已迁移到 GenerationJob 或属于其他仍需保留的通用能力。

- [ ] **Step 2: Delete old code and rewrite only required tests**

  删除旧生产路径和仅服务旧链路的测试；保留通用 Provider Adapter 测试并将图片/视频行为迁到 `test_generation_jobs.py`。

- [ ] **Step 3: Run zero-reference gate**

  Run: `rg -n "OperationBatch|BatchChild|M06ImageGeneration|M06SceneGeneration|inspect_operation_batch|operation_batch_resume" backend/app backend/pixelflow`

  Expected: no production references, except migration notes explicitly marked historical.

- [ ] **Step 4: Commit deletion**

  Run: `git add -A backend && git commit -m "删除图片视频旧 Batch Operation 链路"`

### Task 7: 完整回归和本地运行验证

**Files:**
- Modify: only files required by failing tests or Chinese engineering gate findings
- Test: all `backend/tests`

- [ ] **Step 1: Run focused generation tests**

  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_generation_jobs.py tests/test_image_generation_capability.py tests/test_content_app_video_provider.py -q`

- [ ] **Step 2: Run full backend tests**

  Run: `cd backend && PYTHONPATH=. uv run pytest -q`

- [ ] **Step 3: Run static and reference gates**

  Run: `cd backend && uv run ruff check app pixelflow tests`

  Run: `git diff --check`

  Run: `rg -n "OperationBatch|BatchChild|M06ImageGeneration|M06SceneGeneration|inspect_operation_batch|operation_batch_resume" backend/app backend/pixelflow`

- [ ] **Step 4: Start local services and verify readiness**

  Verify frontend, Gateway and Sidecar `/live`/`/ready` return 200；只验证 Provider 已装配和 Worker 已启动，不发起真实生成请求。

- [ ] **Step 5: Commit final verification fixes**

  Run: `git add backend && git commit -m "完成 GenerationJob 链路回归验证"`
