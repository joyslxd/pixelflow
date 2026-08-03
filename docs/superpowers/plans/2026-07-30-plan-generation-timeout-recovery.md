# Plan 生成超时与可恢复轮询 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让视频和图片 Plan 在模型请求异常缓慢时有限收敛，并让前端始终恢复同一个 job，杜绝 10 分钟误报和重复创建。

**Architecture:** 在 Plan 专用模型 Client 边界增加有限 read timeout 和零透明重试，在 Planning Application Service 增加总预算与安全兜底；前端把 `pendingPlanJob` 当成业务单号，只在权威终态清理。R1 Turn/Snapshot/SSE 继续负责会话基础设施，视频业务仍由 `frontend_v2` 接力。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、LangChain `ChatOpenAI`、pytest、React 19、TypeScript 5、Node test runner、agent-browser。

## Global Constraints

- Plan 单次模型请求超时固定为 600 秒，传输层自动重试固定为 0 次。
- Plan 生成或修订 job 总预算固定为 1200 秒。
- 新增或修改的代码注释、docstring、测试说明、提交标题和提交正文使用中文主体语义。
- 不修改生产 R2 发布范围；`primary_execution_intents` 继续为空，视频仍安全接力 `frontend_v2`。
- 不把 content-app Authorization、模型密钥、供应商原始错误或完整 Prompt 写入仓库和测试报告。
- 每个任务先执行 RED，再写最小实现，最后执行定向 GREEN。

---

### Task 1: 给 Plan 模型 Client 增加有限超时

**Files:**
- Create: `backend/tests/test_plan_llm_timeout.py`
- Modify: `backend/pixelflow/creative/plan_llm.py:1-30`
- Modify: `backend/pixelflow/creative/plan_llm.py:360-390`

**Interfaces:**
- Consumes: `deerflow.models.factory.create_chat_model(name, **kwargs)`。
- Produces: `PLAN_LLM_REQUEST_TIMEOUT_SECONDS: float`、`PlanModelTimeoutError` 和 `_default_model_factory()` 的有限 Client。

- [ ] **Step 1: Write the failing tests**

```python
def test_default_plan_model_has_finite_timeout_and_no_transport_retry(monkeypatch):
    captured = {}

    def fake_create_chat_model(name, **kwargs):
        captured.update({"name": name, **kwargs})
        return object()

    monkeypatch.setattr("deerflow.models.factory.create_chat_model", fake_create_chat_model)
    assert plan_llm._default_model_factory("deepseek-v4-pro") is not None
    assert captured["timeout"] == 600.0
    assert captured["max_retries"] == 0


def test_invoke_json_model_maps_provider_timeout_to_safe_exception():
    class TimeoutModel:
        def invoke(self, _prompt):
            raise TimeoutError("provider detail must not cross boundary")

    with pytest.raises(plan_llm.PlanModelTimeoutError, match="Plan 模型请求超时"):
        plan_llm._invoke_json_model("prompt", "deepseek-v4-pro", lambda *_args, **_kwargs: TimeoutModel())
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_plan_llm_timeout.py -q
```

Expected: FAIL，因为默认工厂尚未传入 `timeout/max_retries`，且异常类型尚不存在。

- [ ] **Step 3: Implement the bounded Client**

```python
PLAN_LLM_REQUEST_TIMEOUT_SECONDS = 600.0


class PlanModelTimeoutError(TimeoutError):
    """说明：Plan 模型请求超过有限等待边界。"""


def _is_model_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or exc.__class__.__name__ in {
        "APITimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
    }


def _default_model_factory(model_name: str, *, attach_tracing: bool = False) -> Any:
    from deerflow.models.factory import create_chat_model

    return create_chat_model(
        model_name,
        attach_tracing=attach_tracing,
        timeout=PLAN_LLM_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )
```

在 `_invoke_json_model()` 外层捕获供应商超时并转换为固定中文异常，不拼接供应商异常字符串。

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_plan_llm_timeout.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/pixelflow/creative/plan_llm.py backend/tests/test_plan_llm_timeout.py
git commit -m "修复：限制 Plan 模型请求等待时间" \
  -m "显式恢复 600 秒有限超时并关闭透明传输重试，统一脱敏超时异常。"
```

### Task 2: 让初始 Plan 和 Seedance 超时安全降级

**Files:**
- Modify: `backend/pixelflow/creative/plan_markdown.py:230-450`
- Modify: `backend/pixelflow/creative/plan_markdown.py:999-1060`
- Test: `backend/tests/test_scene_blueprint_quality.py`
- Test: `backend/tests/test_creative_plan_markdown.py`

**Interfaces:**
- Consumes: `PlanModelTimeoutError`。
- Produces: 超时初始 Plan 的 `error=None` 可审核结果，以及 Seedance 超时单次调用后的确定性资产绑定。

- [ ] **Step 1: Write failing timeout fallback tests**

```python
def test_initial_plan_timeout_returns_reviewable_contract_without_error():
    class TimeoutModel:
        def invoke(self, _prompt):
            raise TimeoutError("provider detail")

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {
                "direction_id": "direction_1",
                "title": "全天健康陪伴",
                "description": "从晨跑、办公到睡眠展示戒指价值。",
            },
            model_factory=lambda *_args, **_kwargs: TimeoutModel(),
        )
    )
    assert result.llm_used is False
    assert result.error is None
    assert sum(result.scene_durations_sec) == result.creation_contract["video_duration_sec"]
    assert any("模型请求超时" in item for item in result.consistency_issues)


def test_seedance_timeout_does_not_retry_same_slow_call():
    initial_payload = {
        "plan_markdown": (
            "# AuroraFit 智能健康戒指新品宣传\n\n"
            "## 一、选题方向\n全天健康陪伴。\n\n"
            "## 三、视频规格\n- 时长：180 秒\n- 画幅：9:16\n"
        ),
        "scene_image_ratio": "9:16",
        "scene_image_size": "4K",
        "scene_blueprints": _valid_generation_blueprints(180),
    }

    class FirstPlanThenTimeoutModel:
        def __init__(self):
            self.prompts = []

        def invoke(self, prompt):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return FakeMessage(json.dumps(initial_payload, ensure_ascii=False))
            raise TimeoutError("provider detail")

    model = FirstPlanThenTimeoutModel()
    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {
                "direction_id": "direction_1",
                "title": "全天健康陪伴",
                "description": "从晨跑、办公到睡眠展示戒指价值。",
            },
            model_factory=lambda *_args, **_kwargs: model,
        )
    )

    assert len(model.prompts) == 2
    assert result.error is None
    assert all(item["shot_description"] for item in result.scene_blueprints)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_creative_plan_markdown.py \
  tests/test_scene_blueprint_quality.py -q
```

Expected: 新测试 FAIL；当前初始超时把 `error` 暴露给前端，Seedance 超时会执行第二次业务重试。

- [ ] **Step 3: Implement safe fallback**

在 `build_plan_markdown_with_llm()` 中把 `PlanModelTimeoutError` 与普通结构错误分开：

```python
except PlanModelTimeoutError:
    fallback = build_plan_markdown(
        intent,
        form_values,
        selected_direction,
        profile,
        materials,
        context,
    )
    return replace(
        fallback,
        consistency_issues=[
            *fallback.consistency_issues,
            "Plan 模型请求超时，已使用确定性创作合同生成可审核方案",
        ],
        error=None,
        model_name=model_name,
    )
```

在 `_author_seedance_plan_blueprints()` 中单独捕获 `PlanModelTimeoutError`，保存安全原因并
`break`，随后复用 `rebuild_scene_shot_descriptions()` 和
`bind_seedance_plan_assets()`；其他结构校验错误仍允许携带反馈重试一次。

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_creative_plan_markdown.py \
  tests/test_scene_blueprint_quality.py \
  tests/test_seedance_plan_authoring.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/pixelflow/creative/plan_markdown.py \
  backend/tests/test_creative_plan_markdown.py \
  backend/tests/test_scene_blueprint_quality.py
git commit -m "修复：让 Plan 超时返回可审核合同" \
  -m "初始模型超时使用确定性方案，Seedance 超时停止重复等待并保留稳定资产绑定。"
```

### Task 3: 给 Planning job 增加总预算和阶段快照

**Files:**
- Modify: `backend/app/gateway/routers/pixelflow_planning.py:1-110`
- Modify: `backend/app/gateway/routers/pixelflow_planning.py:180-460`
- Test: `backend/tests/test_pixelflow_planning_router.py`

**Interfaces:**
- Consumes: `build_plan_markdown()`、`build_plan_markdown_with_llm()`、`revise_plan_markdown_with_llm()`。
- Produces: `PlanJobStatusResponse.stage/started_at/updated_at` 和 1200 秒有限 job 生命周期。

- [ ] **Step 1: Write failing router tests**

```python
def _video_plan_start_payload() -> dict:
    return {
        "intent": "video",
        "form_values": {
            "product_info": "智能健康戒指",
            "video_duration_sec": 30,
            "video_ratio": "9:16",
            "video_model": "seedance-2.0",
            "image_model": "gpt-image-2",
        },
        "selected_direction": {
            "direction_id": "direction_1",
            "title": "全天健康陪伴",
            "description": "从晨跑到睡眠展示产品价值。",
        },
    }


def _start_generation_and_poll(client: TestClient) -> dict:
    response = client.post(
        "/agent/flows/planning/plan/start",
        json=_video_plan_start_payload(),
    )
    assert response.status_code == 200
    return _poll_plan_job(
        client,
        "/agent/flows/planning/plan/jobs",
        response.json()["job_id"],
    )


def test_plan_job_status_exposes_stage_and_timestamps():
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)
    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan/start",
            json=_video_plan_start_payload(),
        )
        assert response.status_code == 200
        status = client.get(
            f"/agent/flows/planning/plan/jobs/{response.json()['job_id']}"
        ).json()

    assert status["stage"] in {"planning", "fallback", "completed"}
    assert status["started_at"]
    assert status["updated_at"]


def test_generation_job_total_timeout_returns_deterministic_plan(monkeypatch):
    from app.gateway.routers import pixelflow_planning

    async def slow_builder(*_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(pixelflow_planning, "_PLAN_JOB_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(pixelflow_planning, "build_plan_markdown_with_llm", slow_builder)
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)
    with TestClient(app) as client:
        status = _start_generation_and_poll(client)

    assert status["status"] == "completed"
    assert status["result"]["error"] is None
    assert status["result"]["llm_used"] is False


def test_revision_job_total_timeout_preserves_current_version(monkeypatch):
    from app.gateway.routers import pixelflow_planning
    from pixelflow.creative.plan_markdown import build_plan_markdown

    initial = build_plan_markdown(
        "image",
        {
            "image_goal": "书包宣传图",
            "image_type": "海报",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "1:1",
        },
        {"title": "通学收纳", "description": "突出容量和护脊"},
    )

    async def slow_revision(**_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(pixelflow_planning, "_PLAN_JOB_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(pixelflow_planning, "revise_plan_markdown_with_llm", slow_revision)
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)
    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan/revise/start",
            json={
                "intent": "image",
                "form_values": {
                    "image_goal": "书包宣传图",
                    "image_type": "海报",
                    "image_usage": "社媒发布",
                    "image_style": "真实摄影",
                    "image_size": "1:1",
                },
                "selected_direction": {
                    "title": "通学收纳",
                    "description": "突出容量和护脊",
                },
                "current_plan_markdown": initial.plan_markdown,
                "current_plan_version": 1,
                "plan_history": initial.plan_history,
                "revision_feedback": "增加开学氛围",
                "creation_contract": initial.creation_contract,
            },
        )
        assert response.status_code == 200
        status = _poll_plan_job(
            client,
            "/agent/flows/planning/plan/revise/jobs",
            response.json()["job_id"],
        )

    assert status["status"] == "failed"
    assert "当前版本已保留" in status["error"]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_pixelflow_planning_router.py -q
```

Expected: 新字段不存在，且 job 尚无总预算。

- [ ] **Step 3: Implement job snapshots and budgets**

```python
_PLAN_JOB_TIMEOUT_SECONDS = 1200.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_plan_job(jobs, job_id, *, status, stage, result=None, error=None, user_id=None):
    previous = jobs.get(job_id) or {}
    now = _utc_now_iso()
    jobs[job_id] = {
        "status": status,
        "stage": stage,
        "result": result,
        "error": error,
        "user_id": user_id if user_id is not None else previous.get("user_id"),
        "started_at": previous.get("started_at") or now,
        "updated_at": now,
    }
```

生成 job 使用 `asyncio.timeout(_PLAN_JOB_TIMEOUT_SECONDS)`；总预算超时时调用
`build_plan_markdown()` 构造 `error=None` 的完成结果。修订 job 总预算超时时写入固定
失败摘要“Plan 修订超时，当前版本已保留。”。查询 DTO 增加三个可选字段并继续执行
`user_id` 归属校验。

- [ ] **Step 4: Run router tests to verify GREEN**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_pixelflow_planning_router.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/gateway/routers/pixelflow_planning.py \
  backend/tests/test_pixelflow_planning_router.py
git commit -m "修复：限制 Plan job 总执行时间" \
  -m "增加阶段时间快照，生成超时安全完成，修订超时保留当前审核版本。"
```

### Task 4: 前端保留同一 Plan job 并自动恢复

**Files:**
- Create: `web/src/lib/planJobRecovery.ts`
- Create: `web/tests/planJobRecovery.test.mjs`
- Modify: `web/scripts/run-tests.mjs`
- Modify: `web/src/lib/api.ts:20-45`
- Modify: `web/src/lib/api.ts:1087-1110`
- Modify: `web/src/pages/WorkspacePage.tsx:1-70`
- Modify: `web/src/pages/WorkspacePage.tsx:4624-4710`

**Interfaces:**
- Consumes: `ApiError.status`、`PendingPlanJob`、`api.getPlanMarkdownJob()` 和 `api.pollPlanMarkdownJob()`。
- Produces: `classifyPlanJobResume()`、`planJobResumeDelayMs()` 和无客户端终止时限的 `pollPlanJob()`。

- [ ] **Step 1: Write failing pure recovery tests**

```javascript
test("Plan 查询临时失败和隐藏时保留 pending", () => {
  assert.equal(classifyPlanJobResume({ errorStatus: 408 }), "retain_pending");
  assert.equal(classifyPlanJobResume({ errorStatus: 503 }), "retain_pending");
  assert.equal(classifyPlanJobResume({ hidden: true }), "retain_pending");
  assert.equal(classifyPlanJobResume({ status: "running" }), "retain_pending");
});

test("Plan 只在权威终态清理", () => {
  assert.equal(classifyPlanJobResume({ status: "completed", hasResult: true }), "complete");
  assert.equal(classifyPlanJobResume({ status: "completed", hasResult: false }), "clear_failed");
  assert.equal(classifyPlanJobResume({ status: "failed" }), "clear_failed");
  assert.equal(classifyPlanJobResume({ errorStatus: 404 }), "clear_not_found");
  assert.equal(classifyPlanJobResume({ errorStatus: 409 }), "clear_failed");
});

test("Plan 恢复退避有上限", () => {
  assert.equal(planJobResumeDelayMs(0), 1000);
  assert.equal(planJobResumeDelayMs(1), 2000);
  assert.equal(planJobResumeDelayMs(20), 30000);
});
```

- [ ] **Step 2: Run frontend tests to verify RED**

Run:

```bash
cd web
node scripts/run-tests.mjs
```

Expected: FAIL，因为恢复模块尚不存在。

- [ ] **Step 3: Implement the pure classifier**

```typescript
export type PlanJobResumeAction =
  | "complete"
  | "retain_pending"
  | "clear_not_found"
  | "clear_failed";

export function classifyPlanJobResume(input: {
  status?: string;
  hasResult?: boolean;
  hidden?: boolean;
  errorStatus?: number;
}): PlanJobResumeAction {
  if (input.hidden) return "retain_pending";
  if (input.status === "completed") return input.hasResult ? "complete" : "clear_failed";
  if (input.status === "failed") return "clear_failed";
  if (input.errorStatus === 404) return "clear_not_found";
  if (input.errorStatus === 409 || input.errorStatus === 422) return "clear_failed";
  return "retain_pending";
}

export function planJobResumeDelayMs(attempt: number): number {
  const normalized = Number.isInteger(attempt) && attempt > 0 ? attempt : 0;
  return Math.min(30_000, 1000 * (2 ** Math.min(normalized, 5)));
}
```

把新模块加入 `run-tests.mjs` 的独立编译列表和
`PLAN_JOB_RECOVERY_TEST_MODULE` 环境映射。

- [ ] **Step 4: Remove the artificial poll deadline**

把 `pollPlanJob()` 改成：

```typescript
while (shouldContinue()) {
  const status = await req<PlanJobStatusResponse>(path);
  if (!shouldContinue()) return null;
  if (status.status === "completed") {
    if (status.result) return status.result;
    throw new ApiError(422, "Plan job completed without result");
  }
  if (status.status === "failed") {
    throw new ApiError(409, status.error || status.message || "Plan job failed");
  }
  await delay(PLAN_JOB_POLL_INTERVAL_MS);
}
return null;
```

删除 `PLAN_JOB_TIMEOUT_MS`，保留隐藏对话退出。

- [ ] **Step 5: Make Workspace recovery retain pending**

增加每个 `job_id` 的重试次数和一次性提示集合。`resumePendingPlanJob()` 的 catch 先通过
`classifyPlanJobResume()` 分类：

```typescript
const errorStatus = err instanceof ApiError ? err.status : undefined;
const action = classifyPlanJobResume({
  hidden: stopIfHidden(),
  errorStatus,
});
if (action === "retain_pending") {
  const attempt = (planJobResumeAttemptsRef.current.get(pollKey) || 0) + 1;
  planJobResumeAttemptsRef.current.set(pollKey, attempt);
  if (!planJobRecoveryNoticesRef.current.has(pollKey)) {
    planJobRecoveryNoticesRef.current.add(pollKey);
    pushAssistant("Plan 查询暂时中断，正在使用原任务继续恢复…", targetConversationId);
  }
window.setTimeout(
    () => {
      if (!document.hidden) {
        void resumePendingPlanJob(pendingPlanJob);
      }
    },
    planJobResumeDelayMs(attempt),
  );
  return;
}
```

保留路径不得调用 `releaseArtifactAction()` 或 `clearPendingPlanJob()`。明确失败和 404 才
释放动作并清理。成功后删除重试计数和一次性提示标记。

- [ ] **Step 6: Add source-contract assertions**

在 `web/tests/mainFlowContract.test.mjs` 增加源码合同：

```javascript
assert.equal(apiSource.includes("PLAN_JOB_TIMEOUT_MS"), false);
assert.match(workspaceSource, /classifyPlanJobResume/);
assert.match(workspaceSource, /Plan 查询暂时中断，正在使用原任务继续恢复/);
```

- [ ] **Step 7: Run tests and type checking**

Run:

```bash
cd web
node scripts/run-tests.mjs
./node_modules/.bin/tsc --noEmit
```

Expected: 全部 PASS。

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/planJobRecovery.ts web/tests/planJobRecovery.test.mjs \
  web/scripts/run-tests.mjs web/src/lib/api.ts web/src/pages/WorkspacePage.tsx \
  web/tests/mainFlowContract.test.mjs
git commit -m "修复：保留 Plan 异步任务恢复句柄" \
  -m "移除前端十分钟误判，临时失败继续查询原 job，仅在权威终态清理。"
```

### Task 5: 同步文档并执行自动化门禁

**Files:**
- Modify: `README.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Create: `docs/agentization/test-reports/M13.2-R2-plan-timeout-recovery.md`

**Interfaces:**
- Consumes: Tasks 1-4 的最终行为和测试输出。
- Produces: 中文设计说明、测试矩阵和安全验收记录。

- [ ] **Step 1: Update architecture documentation**

在最新设计文档的 Plan 异步流程章节记录：

```text
Plan Client 单次请求以 600 秒有限等待且不做透明重试；Plan job 以 1200 秒总预算收敛。
前端不再用固定轮询时长推断失败，Snapshot 中的 pendingPlanJob 只有在权威终态或 404
时清理。模型超时生成确定性可审核合同，Seedance 超时保留已完成结构并确定性绑定资产。
```

- [ ] **Step 2: Run backend focused and full tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_plan_llm_timeout.py \
  tests/test_creative_plan_markdown.py \
  tests/test_scene_blueprint_quality.py \
  tests/test_seedance_plan_authoring.py \
  tests/test_pixelflow_planning_router.py -q
.venv/bin/python -m pytest -q
```

Expected: 全部 PASS。

- [ ] **Step 3: Run frontend full tests and builds**

Run:

```bash
cd web
node scripts/run-tests.mjs
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/tsc -b
./node_modules/.bin/vite build --mode test
```

Expected: 全部 PASS，构建成功。

- [ ] **Step 4: Run repository quality gates**

Run:

```bash
git diff --check
pwsh -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 \
  -RepositoryPath . \
  -BaseRef origin/feature/agent_0.8.4_boguan \
  -HeadRef HEAD
```

Expected: 中文工程规范通过。

- [ ] **Step 5: Write the test report**

报告必须记录精确命令、通过数量、真实测试范围、外部失败、是否产生费用和脱敏原则；不得
出现 Authorization、API key 或完整用户 Prompt。

- [ ] **Step 6: Commit**

```bash
git add README.md docs/pixelflow-agent-skill-flow-latest-design.md \
  docs/agentization/test-reports/M13.2-R2-plan-timeout-recovery.md
git commit -m "文档：记录 Plan 超时恢复门禁" \
  -m "同步 R1/R2 接力边界、自动化结果和真实验收范围。"
```

### Task 6: 使用真实浏览器执行完整视频验收

**Files:**
- Modify: `docs/agentization/test-reports/M13.2-R2-plan-timeout-recovery.md`

**Interfaces:**
- Consumes: 本机 `backend/config.dev.yml`、测试模式前端和临时 Authorization。
- Produces: 从新对话到最终合并视频、下载交付的脱敏验收证据。

- [ ] **Step 1: Read dogfood issue taxonomy**

Run:

```bash
sed -n '1,260p' \
  /Users/wu-bob/local/node20/node-global/lib/node_modules/agent-browser/skill-data/dogfood/references/issue-taxonomy.md
```

- [ ] **Step 2: Start local services**

Run:

```bash
cd backend
env PIXELFLOW_CONFIG_ENV=dev PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  .venv/bin/python -m app.gateway.run

cd web
./node_modules/.bin/vite --mode test --host 127.0.0.1 --port 5273
```

Expected: `/health` 为 200，前端入口可访问。

- [ ] **Step 3: Verify long Plan recovery**

使用用户截图对应的长需求或同等复杂的 60 秒脚本，验证：

- 只出现一个 `/planning/plan/start`；
- 10 分钟边界不会显示 `Plan generation polling timed out`；
- 模型或总预算触发时仍出现可审核 Plan；
- 刷新、切换对话后继续查询同一 `job_id`。

- [ ] **Step 4: Exercise all pre-generation controls**

逐个点击并记录：

- 新建对话、任务看板折叠/展开；
- 表单各字段、模型自动推荐/手选、关闭 `X` 取消；
- 三个方向选择、重新生成方向；
- Plan 同意、编辑保存、反馈修订、历史版本回退、重新生成方向；
- 刷新和历史对话恢复。

- [ ] **Step 5: Exercise scene package and asset controls**

逐个点击并记录：

- 同意 Plan、生成场景包；
- 全局角色/场景/道具新增、替换、编辑、融合、删除；
- 失败参考图继续生成和重新生成；
- 分镜字段编辑、`@` 素材引用、保存和恢复；
- 场景包确认。

- [ ] **Step 6: Exercise video generation and delivery controls**

逐个点击并记录：

- 场景视频生成；
- 单场景失败重试；
- 单场景修改重生成；
- 完整视频合并；
- QC 质检、按问题修改、重新合并；
- 合并视频预览、下载并完成“导出交付”；
- 剪映草稿能力、创建/重试/下载入口；
- “无意见，结束”最终确认。

如真实 Provider 返回 402、404、timeout 或能力未配置，验证对应暂停/重试/安全提示后，
继续使用测试环境允许的恢复入口；发现代码问题立即回到 RED-GREEN 修复，不在报告中把
外部不可用误报为代码通过。

- [ ] **Step 7: Update report with evidence**

记录每个按钮的结果、conversation ID、内部 job ID、最终 TOS 产物 URL 的域名和文件类型；
URL 查询串、token、用户完整 Prompt 和供应商原始错误必须脱敏。

- [ ] **Step 8: Commit browser evidence**

```bash
git add docs/agentization/test-reports/M13.2-R2-plan-timeout-recovery.md
git commit -m "测试：补充视频全流程人工验收证据" \
  -m "记录 Plan 恢复、R1/R2 接力、全部按钮和最终交付结果，敏感信息已脱敏。"
```

### Task 7: 最终同步、复核和提交

**Files:**
- Modify only files needed to resolve upstream conflicts or review findings.

**Interfaces:**
- Consumes: 所有实现提交、测试证据和远端最新分支。
- Produces: 无冲突、工作树干净、中文门禁绿色的最终提交。

- [ ] **Step 1: Fetch and merge upstream**

Run:

```bash
git fetch origin --prune
git merge --no-ff origin/feature/agent_0.8.4_boguan \
  -m "合并：同步 Plan 修复提交前上游更新"
```

Expected: `Already up to date` 或生成中文 merge commit；若有冲突，逐文件组合双方语义后
重新执行 Tasks 5-6 的相关门禁。

- [ ] **Step 2: Review complete diff**

Run:

```bash
git diff origin/feature/agent_0.8.4_boguan...HEAD --check
git diff --stat origin/feature/agent_0.8.4_boguan...HEAD
git log --format='%h %s%n%b' origin/feature/agent_0.8.4_boguan..HEAD
```

确认没有 token、纯英文提交正文、未解释配置或无关变更。

- [ ] **Step 3: Run final verification**

重新执行后端全量、前端全量、test 构建、中文工程规范和 `git diff --check`。输出必须来自
当前最终 HEAD。

- [ ] **Step 4: Record final state**

```bash
git status --short --branch
git rev-list --left-right --count HEAD...origin/feature/agent_0.8.4_boguan
```

Expected: 工作树干净；本地只领先本次中文提交，不落后远端。
