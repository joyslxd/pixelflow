# 剪映草稿流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在最终视频结果阶段增加可恢复、可幂等的剪映草稿生成流程，并在真实第三方接口尚未接入时安全展示禁用入口。

**Architecture:** PixelFlow 新增独立剪映草稿领域模型、Service、Skill 和 `/agent/flows/video/jianying-draft` Router；第三方差异全部封装在 Skill Provider 中，当前使用 unavailable 实现。前端基于有序分镜视频计算稳定版本 ID，把 pending job 和按版本结果保存在 conversation context，并在最终视频卡片中展示第三个操作按钮和独立下载结果消息。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、asyncio、pytest、React 19、TypeScript、Vite、Node test、Tailwind CSS。

## Global Constraints

- 所有新 Python 网关接口必须以 `/agent` 开头。
- 当前不得配置或调用任何未提供的第三方剪映草稿 URL、鉴权、请求字段和响应字段。
- 当前不得生成伪剪映草稿或普通占位 ZIP。
- Provider 未接入时，按钮必须展示但禁用，提示固定为“剪映草稿服务待接入”。
- 草稿输入只能使用当前版本全部成功的分镜视频，不能使用合并视频替代。
- 同一 `conversation_id + storyboard_version_id` 只能存在一个有效任务。
- 分镜变化必须产生新版本，旧草稿可以下载但不能被当前版本复用。
- 任务、消息和状态更新必须写回来源对话，不能使用用户切换后的当前对话。
- 前端轮询间隔为 2 秒，总超时为 30 分钟；网络异常最多重试 3 次。
- 生成或下载剪映草稿不得自动结束视频流程。
- PowerMem 只异步记录 `category=experience`、`infer=False` 的安全摘要。
- 新代码注释和 Git 提交信息使用中文。

---

## File Structure

### 新建

- `backend/pixelflow/jianying_draft/models.py`：内部请求、结果、状态和分镜版本摘要。
- `backend/pixelflow/jianying_draft/skill.py`：Skill 协议、能力结果和 unavailable 实现。
- `backend/pixelflow/jianying_draft/service.py`：校验、幂等、异步状态机、超时和容量清理。
- `backend/pixelflow/jianying_draft/__init__.py`：领域模块公开接口。
- `backend/app/gateway/routers/pixelflow_jianying_draft.py`：`/agent` Controller。
- `backend/tests/test_jianying_draft_models.py`：版本和输入校验测试。
- `backend/tests/test_jianying_draft_service.py`：状态机和幂等测试。
- `backend/tests/test_pixelflow_jianying_draft_router.py`：路由合同和鉴权测试。
- `web/src/lib/jianyingDraft.ts`：前端版本、可用性和结果选择纯函数。
- `web/tests/jianyingDraft.test.mjs`：前端纯函数测试。
- `web/tests/jianyingDraftUiContract.test.mjs`：交互与恢复源码合同测试。

### 修改

- `backend/app/gateway/routers/__init__.py`：导出新 Router。
- `backend/app/gateway/app.py`：注册新 Router。
- `backend/config.dev.yml`、`backend/config.prod.yml`：增加默认关闭的内部能力配置，不增加第三方字段。
- `web/src/lib/api.ts`：剪映草稿 API DTO 和调用方法。
- `web/src/lib/chat.ts`：草稿 artifact、pending job 和结果记录类型。
- `web/src/pages/WorkspacePage.tsx`：版本计算、pending job 持久化、恢复、轮询和结果消息。
- `web/src/components/chat/ChatPanel.tsx`：透传草稿操作回调并维护按钮可操作性。
- `web/src/components/chat/MessageBubble.tsx`：三按钮、禁用提示、生成状态和下载卡片。
- `web/package.json`：增加纯函数测试命令。
- `README.md`、`AGENTS.md`、`docs/pixelflow-agent-skill-flow-latest-design.md`：同步流程和接口说明。

---

### Task 1: 剪映草稿领域模型与稳定版本 ID

**Files:**
- Create: `backend/pixelflow/jianying_draft/models.py`
- Create: `backend/pixelflow/jianying_draft/__init__.py`
- Test: `backend/tests/test_jianying_draft_models.py`

**Interfaces:**
- Produces: `JianyingDraftStatus`、`JianyingDraftScene`、`JianyingDraftRequest`、`JianyingDraftResult`、`compute_storyboard_version_id()`。
- Consumes: 无外部 Provider 字段，只使用 PixelFlow 的场景 ID、顺序、视频 URL 和任务 ID。

- [ ] **Step 1: 写失败测试，覆盖规范化、稳定摘要和非法分镜**

```python
def test_storyboard_version_is_stable_after_input_reordering():
    scenes = [
        JianyingDraftScene(scene_id="scene-2", scene_index=2, video_url="https://cdn/2.mp4", task_id="t2"),
        JianyingDraftScene(scene_id="scene-1", scene_index=1, video_url="https://cdn/1.mp4", task_id="t1"),
    ]
    assert compute_storyboard_version_id(scenes) == compute_storyboard_version_id(list(reversed(scenes)))


def test_storyboard_version_changes_when_scene_video_changes():
    before = [JianyingDraftScene(scene_id="scene-1", scene_index=1, video_url="https://cdn/1.mp4", task_id="t1")]
    after = [JianyingDraftScene(scene_id="scene-1", scene_index=1, video_url="https://cdn/1-v2.mp4", task_id="t2")]
    assert compute_storyboard_version_id(before) != compute_storyboard_version_id(after)


@pytest.mark.parametrize("url", ["", "blob:https://local/1", "file:///tmp/1.mp4"])
def test_scene_rejects_non_http_video_url(url: str):
    with pytest.raises(ValidationError):
        JianyingDraftScene(scene_id="scene-1", scene_index=1, video_url=url)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_jianying_draft_models.py -q`

Expected: FAIL，模块或类型尚不存在。

- [ ] **Step 3: 实现最小领域模型和版本算法**

```python
class JianyingDraftStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


class JianyingDraftScene(BaseModel):
    scene_id: str = Field(min_length=1)
    scene_index: int = Field(ge=1)
    video_url: AnyHttpUrl
    task_id: str | None = None


def compute_storyboard_version_id(scenes: Sequence[JianyingDraftScene]) -> str:
    ordered = sorted(scenes, key=lambda item: item.scene_index)
    payload = [
        {
            "scene_id": item.scene_id,
            "scene_index": item.scene_index,
            "task_id": item.task_id or "",
            "video_url": str(item.video_url),
        }
        for item in ordered
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value = 0xCBF29CE484222325
    for byte in canonical.encode("utf-8"):
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"storyboard-{value:016x}"
```

`JianyingDraftRequest` 的模型校验器必须拒绝空集合和重复 `scene_index`，并验证传入 `storyboard_version_id` 与重新计算结果一致。前后端都必须使用按 `scene_index` 排序、键顺序固定为 `scene_id/scene_index/task_id/video_url`、无空白 JSON、UTF-8 编码和 FNV-1a 64 位的同一算法，不能各自选择摘要算法。

- [ ] **Step 4: 运行模型测试并检查格式**

Run: `cd backend && .venv/bin/python -m pytest tests/test_jianying_draft_models.py -q && .venv/bin/ruff check pixelflow/jianying_draft tests/test_jianying_draft_models.py`

Expected: PASS，Ruff 无错误。

- [ ] **Step 5: 中文提交**

```bash
git add backend/pixelflow/jianying_draft backend/tests/test_jianying_draft_models.py
git commit -m "功能：新增剪映草稿领域模型"
```

---

### Task 2: Skill 协议与异步幂等 Service

**Files:**
- Create: `backend/pixelflow/jianying_draft/skill.py`
- Create: `backend/pixelflow/jianying_draft/service.py`
- Modify: `backend/pixelflow/jianying_draft/__init__.py`
- Test: `backend/tests/test_jianying_draft_service.py`

**Interfaces:**
- Consumes: Task 1 的 `JianyingDraftRequest` 和 `JianyingDraftResult`。
- Produces: `JianyingDraftCapability`、`JianyingDraftSkill`、`UnavailableJianyingDraftSkill`、`JianyingDraftService.start()`、`JianyingDraftService.get_job()`。

- [ ] **Step 1: 写失败测试，覆盖未配置、幂等、成功、失败和超时**

```python
@pytest.mark.asyncio
async def test_unavailable_skill_does_not_create_job():
    service = JianyingDraftService(skill=UnavailableJianyingDraftSkill())
    result = await service.start(_request())
    assert result.status == JianyingDraftStatus.NOT_CONFIGURED
    assert service.job_count == 0


@pytest.mark.asyncio
async def test_running_job_is_reused_for_same_version():
    skill = BlockingFakeSkill()
    service = JianyingDraftService(skill=skill)
    first = await service.start(_request())
    second = await service.start(_request())
    assert second.job_id == first.job_id
    assert skill.call_count == 1


@pytest.mark.asyncio
async def test_timeout_is_terminal():
    service = JianyingDraftService(skill=BlockingFakeSkill(), timeout_seconds=0.01)
    started = await service.start(_request())
    result = await _wait_for_terminal(service, started.job_id)
    assert result.status == JianyingDraftStatus.TIMEOUT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_jianying_draft_service.py -q`

Expected: FAIL，Skill 和 Service 尚不存在。

- [ ] **Step 3: 实现 Skill 和 unavailable Provider**

```python
class JianyingDraftSkill(Protocol):
    async def capability(self) -> JianyingDraftCapability: ...
    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult: ...


class UnavailableJianyingDraftSkill:
    async def capability(self) -> JianyingDraftCapability:
        return JianyingDraftCapability(available=False, reason="剪映草稿服务待接入")

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        return JianyingDraftResult(
            status=JianyingDraftStatus.NOT_CONFIGURED,
            message="剪映草稿服务待接入",
        )
```

- [ ] **Step 4: 实现异步 Service 状态机**

Service 必须：

- 用 `(conversation_id, storyboard_version_id)` 建立幂等索引。
- 对 unavailable Provider 直接返回 `not_configured`，不创建 job。
- 对 running/succeeded 未过期结果返回原 job。
- 用 `asyncio.create_task()` 执行 `skill.generate()`。
- 用 `asyncio.timeout(timeout_seconds)` 把超时写成 `timeout`。
- 捕获后台边界异常并写成 `failed`，保存公开错误信息。
- 限制最多 100 条 job，优先清理最旧终态任务，不清理运行中任务。
- 提供 `retry_failed=True` 显式重试，禁止普通重复请求重启失败任务。

- [ ] **Step 5: 运行 Service 测试与 Ruff**

Run: `cd backend && .venv/bin/python -m pytest tests/test_jianying_draft_service.py -q && .venv/bin/ruff check pixelflow/jianying_draft tests/test_jianying_draft_service.py`

Expected: PASS，Ruff 无错误。

- [ ] **Step 6: 中文提交**

```bash
git add backend/pixelflow/jianying_draft backend/tests/test_jianying_draft_service.py
git commit -m "功能：实现剪映草稿异步任务服务"
```

---

### Task 3: `/agent` Router、鉴权和 PowerMem 摘要

**Files:**
- Create: `backend/app/gateway/routers/pixelflow_jianying_draft.py`
- Modify: `backend/app/gateway/routers/__init__.py`
- Modify: `backend/app/gateway/app.py`
- Modify: `backend/config.dev.yml`
- Modify: `backend/config.prod.yml`
- Test: `backend/tests/test_pixelflow_jianying_draft_router.py`

**Interfaces:**
- Consumes: Task 2 的 `JianyingDraftService`。
- Produces: `GET /agent/flows/video/jianying-draft/capability`、`POST /start`、`GET /jobs/{job_id}`。

- [ ] **Step 1: 写失败 Router 测试**

```python
def test_router_paths_are_agent_prefixed():
    paths = {route.path for route in pixelflow_jianying_draft.router.routes}
    assert "/agent/flows/video/jianying-draft/capability" in paths
    assert "/agent/flows/video/jianying-draft/start" in paths
    assert "/agent/flows/video/jianying-draft/jobs/{job_id}" in paths


def test_capability_reports_unavailable_by_default(client):
    response = client.get("/agent/flows/video/jianying-draft/capability")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reason": "剪映草稿服务待接入"}


def test_start_does_not_create_placeholder_job(client):
    response = client.post("/agent/flows/video/jianying-draft/start", json=_payload())
    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "not_configured"
```

- [ ] **Step 2: 运行 Router 测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_pixelflow_jianying_draft_router.py -q`

Expected: FAIL，Router 尚不存在。

- [ ] **Step 3: 实现 Router 和注册**

```python
router = APIRouter(prefix="/agent/flows/video/jianying-draft", tags=["pixelflow-flows"])


@router.get("/capability", response_model=JianyingDraftCapability)
async def capability() -> JianyingDraftCapability:
    return await service.capability()


@router.post("/start", response_model=JianyingDraftJobResponse)
async def start_draft(body: JianyingDraftRequest, request: Request) -> JianyingDraftJobResponse:
    result = await service.start(body)
    if result.status is JianyingDraftStatus.NOT_CONFIGURED:
        raise HTTPException(status_code=503, detail=result.model_dump(mode="json"))
    return result
```

`GET /jobs/{job_id}` 对未知或已清理任务返回 404。终态后使用 `record_power_mem_background(..., category="experience", source_agent="jianying_draft_agent", infer=False)` 记录安全摘要。

开发和生产配置只增加内部开关、2 秒轮询、1800 秒超时和 3 次重试；不得增加第三方 URL 或密钥。

- [ ] **Step 4: 验证 Router、现有视频 Router 和应用注册**

Run: `cd backend && .venv/bin/python -m pytest tests/test_pixelflow_jianying_draft_router.py tests/test_pixelflow_video_router.py -q`

Expected: PASS。

- [ ] **Step 5: 中文提交**

```bash
git add backend/app/gateway/routers/pixelflow_jianying_draft.py backend/app/gateway/routers/__init__.py backend/app/gateway/app.py backend/config.dev.yml backend/config.prod.yml backend/tests/test_pixelflow_jianying_draft_router.py
git commit -m "功能：新增剪映草稿Agent接口"
```

---

### Task 4: 前端版本模型与 API Client

**Files:**
- Create: `web/src/lib/jianyingDraft.ts`
- Create: `web/tests/jianyingDraft.test.mjs`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/chat.ts`
- Modify: `web/package.json`

**Interfaces:**
- Consumes: 当前 `SceneVideoPayload` 和 Task 3 的 API。
- Produces: `storyboardVersionId()`、`draftButtonState()`、剪映草稿 API DTO、`api.getJianyingDraftCapability()`、`api.startJianyingDraftJob()`、`api.getJianyingDraftJob()`。

- [ ] **Step 1: 写失败的纯函数测试**

```javascript
test("storyboard version is stable for the same ordered scene set", async () => {
  const { storyboardVersionId } = await import(moduleUrl);
  const scenes = [scene(2, "b.mp4", "t2"), scene(1, "a.mp4", "t1")];
  assert.equal(storyboardVersionId(scenes), storyboardVersionId([...scenes].reverse()));
});

test("storyboard version changes after one scene is regenerated", async () => {
  const { storyboardVersionId } = await import(moduleUrl);
  assert.notEqual(
    storyboardVersionId([scene(1, "a.mp4", "t1")]),
    storyboardVersionId([scene(1, "a-v2.mp4", "t2")]),
  );
});

test("button is disabled when provider is unavailable", async () => {
  const { draftButtonState } = await import(moduleUrl);
  assert.deepEqual(
    draftButtonState({ providerAvailable: false, scenes: [scene(1, "a.mp4", "t1")] }),
    { enabled: false, label: "生成剪映草稿", reason: "剪映草稿服务待接入" },
  );
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && pnpm test:jianying-draft`

Expected: FAIL，模块或脚本尚不存在。

- [ ] **Step 3: 实现纯函数和 API DTO**

`storyboardVersionId()` 必须先按 `scene_index` 排序，把每项规范化为键顺序固定的 `scene_id/scene_index/task_id/video_url`，使用无空白 JSON 序列化，再对 UTF-8 字节执行与后端完全相同的 FNV-1a 64 位摘要，输出 `storyboard-<16位十六进制>`。TypeScript 使用 `BigInt.asUintN(64, ...)` 保持 64 位溢出语义；不得改用随机 ID、浏览器专属摘要或与后端不同的算法。

`draftButtonState()` 必须依次判断：Provider、pending job、空分镜、失败分镜、缺失 URL、已有成功结果和过期结果，返回稳定 label/reason。

API Client 增加：

```typescript
getJianyingDraftCapability: () => req<JianyingDraftCapability>(`${FLOW_BASE}/video/jianying-draft/capability`),
startJianyingDraftJob: (body: JianyingDraftStartRequest) =>
  req<JianyingDraftJobResponse>(`${FLOW_BASE}/video/jianying-draft/start`, { method: "POST", body: JSON.stringify(body) }),
getJianyingDraftJob: (jobId: string) =>
  req<JianyingDraftJobResponse>(`${FLOW_BASE}/video/jianying-draft/jobs/${encodeURIComponent(jobId)}`),
```

- [ ] **Step 4: 运行纯函数测试和 TypeScript 检查**

Run: `cd web && pnpm test:jianying-draft && pnpm lint`

Expected: PASS。

- [ ] **Step 5: 中文提交**

```bash
git add web/src/lib/jianyingDraft.ts web/tests/jianyingDraft.test.mjs web/src/lib/api.ts web/src/lib/chat.ts web/package.json
git commit -m "功能：新增剪映草稿前端合同"
```

---

### Task 5: 对话持久化、轮询恢复和结果消息

**Files:**
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/src/lib/chat.ts`
- Create: `web/tests/jianyingDraftUiContract.test.mjs`

**Interfaces:**
- Consumes: Task 4 的版本函数和 API Client。
- Produces: `PendingJianyingDraftJob`、`persistPendingJianyingDraftJob()`、`resumePendingJianyingDraftJob()`、`handleGenerateJianyingDraft()`。

- [ ] **Step 1: 写失败的恢复与归属合同测试**

测试必须断言源码包含：

```javascript
assert.match(workspaceSource, /pendingJianyingDraftJob/);
assert.match(workspaceSource, /pending_jianying_draft_job/);
assert.match(workspaceSource, /jianyingDraftRecords/);
assert.match(workspaceSource, /resumePendingJianyingDraftJob/);
assert.match(workspaceSource, /pendingJob\.conversation_id/);
assert.match(workspaceSource, /storyboard_version_id/);
assert.doesNotMatch(resumeSource, /startJianyingDraftJob/);
```

最后一条保证恢复已有 job 时只查询，不重复启动。

- [ ] **Step 2: 运行合同测试确认失败**

Run: `cd web && node --test tests/jianyingDraftUiContract.test.mjs`

Expected: FAIL，Workspace 尚未接入草稿状态。

- [ ] **Step 3: 扩展 WorkspaceSnapshot 和 refs**

增加：

```typescript
interface PendingJianyingDraftJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  storyboard_version_id: string;
  started_at: string;
  request: JianyingDraftStartRequest;
}

type JianyingDraftRecordMap = Record<string, JianyingDraftJobResponse>;
```

`applySnapshot()`、`makeSnapshot()`、`resetWorkspace()` 和 `applyConversation()` 必须同时处理 camelCase 与 snake_case pending 字段，并只恢复当前对话所属 job。

- [ ] **Step 4: 实现启动、轮询和完成处理**

- capability 不可用时，handler 直接返回，不调用 `/start`。
- 启动前从当前 artifact 的成功分镜计算版本，校验数量和 URL。
- 启动后立即把 pending job 写入原 conversation context。
- 每 2 秒查询一次；隐藏页面时停止主动轮询，恢复可见后继续查询同一 job。
- 30 分钟后前端显示 timeout，但不自动重启。
- succeeded 时清空 pending，把结果写入 `jianyingDraftRecords[version]`，并向原对话追加 `jianying_draft` artifact 消息。
- failed/timeout 时保留失败 artifact 和重试入口。
- 结果处理使用 `targetConversationId`，不得读取切换后的 `conversationIdRef.current` 作为写入目标。

- [ ] **Step 5: 运行合同测试、既有对话路由测试和 lint**

Run: `cd web && node --test tests/jianyingDraftUiContract.test.mjs tests/conversationRouting.test.mjs && pnpm lint`

Expected: PASS。

- [ ] **Step 6: 中文提交**

```bash
git add web/src/pages/WorkspacePage.tsx web/src/lib/chat.ts web/tests/jianyingDraftUiContract.test.mjs
git commit -m "功能：持久化剪映草稿任务状态"
```

---

### Task 6: 最终视频三按钮与草稿结果卡片

**Files:**
- Modify: `web/src/components/chat/ChatPanel.tsx`
- Modify: `web/src/components/chat/MessageBubble.tsx`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/tests/jianyingDraftUiContract.test.mjs`

**Interfaces:**
- Consumes: Task 5 的 handlers 和草稿 artifact。
- Produces: 最终视频三按钮、未配置 tooltip、生成中锁定、历史结束后入口和下载卡片。

- [ ] **Step 1: 扩展失败合同测试**

```javascript
assert.match(messageBubbleSource, /生成剪映草稿/);
assert.match(messageBubbleSource, /剪映草稿服务待接入/);
assert.match(messageBubbleSource, /草稿生成中/);
assert.match(messageBubbleSource, /下载剪映草稿/);
assert.match(messageBubbleSource, /sm:grid-cols-3/);
assert.match(messageBubbleSource, /jianying_draft/);
assert.match(chatPanelSource, /onGenerateJianyingDraft/);
```

测试还必须截取 video result 分支，断言草稿按钮同时出现在未结束和 `videoAccepted` 历史状态，且 disabled 状态带 `title` 或可聚焦提示元素。

- [ ] **Step 2: 运行合同测试确认失败**

Run: `cd web && node --test tests/jianyingDraftUiContract.test.mjs`

Expected: FAIL，按钮和结果卡片尚不存在。

- [ ] **Step 3: 实现三按钮和禁用原因**

- 未结束成功视频使用 `sm:grid-cols-3`。
- `actionsDisabled` 或草稿任务运行时锁定三个按钮。
- Provider 不可用时草稿按钮 disabled，显示固定提示。
- 当前版本已有未过期成功结果时，按钮文案改为`下载剪映草稿`并直接使用结果。
- `videoAccepted` 时保留“视频流程已结束”提示，并在其下显示独立草稿入口。
- 使用 Lucide 的 `FileArchive`/`Download`/`LoaderCircle`，不手绘 SVG。

- [ ] **Step 4: 实现草稿结果卡片**

结果卡片显示：

- 标题`剪映草稿已生成`。
- ZIP 文件名和来源分镜数量。
- `下载剪映草稿`按钮。
- 下载使用普通 HTTPS 链接，不自动触发。
- failed/timeout 卡片显示真实公开原因和`重新生成剪映草稿`按钮。
- Provider unavailable 不生成失败卡片。

- [ ] **Step 5: 运行前端合同、lint 和测试环境构建**

Run: `cd web && node --test tests/jianyingDraftUiContract.test.mjs tests/videoSceneUiContract.test.mjs tests/mainFlowContract.test.mjs && pnpm lint && pnpm build-test`

Expected: 所有测试通过，Vite 构建成功。

- [ ] **Step 6: 中文提交**

```bash
git add web/src/components/chat/ChatPanel.tsx web/src/components/chat/MessageBubble.tsx web/src/pages/WorkspacePage.tsx web/tests/jianyingDraftUiContract.test.mjs
git commit -m "功能：增加剪映草稿生成与下载入口"
```

---

### Task 7: 文档同步、完整验证与交付检查

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`

**Interfaces:**
- Consumes: Tasks 1-6 的最终接口和流程。
- Produces: 后续 Agent 可维护的流程、Skill、状态和第三方接入说明。

- [ ] **Step 1: 更新文档**

文档必须记录：

- 剪映草稿 Agent 和 `JianyingDraftSkill` 的作用。
- 三个 `/agent/flows/video/jianying-draft` 接口。
- 当前 Provider 未接入、按钮置灰的行为。
- 版本 ID、幂等、对话恢复、30 分钟超时和流程结束后入口。
- 第三方接口到位后只实现 Provider，并同步新增独立第三方调用记录；当前不把它错误记录成 content-app 接口。
- PowerMem 使用 `experience/infer=False`。

- [ ] **Step 2: 运行后端目标测试**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_jianying_draft_models.py \
  tests/test_jianying_draft_service.py \
  tests/test_pixelflow_jianying_draft_router.py \
  tests/test_pixelflow_video_router.py \
  -q
```

Expected: 全部通过。

- [ ] **Step 3: 运行后端 Ruff 和 diff 检查**

Run:

```bash
cd backend
.venv/bin/ruff check pixelflow/jianying_draft app/gateway/routers/pixelflow_jianying_draft.py tests/test_jianying_draft_models.py tests/test_jianying_draft_service.py tests/test_pixelflow_jianying_draft_router.py
.venv/bin/ruff format --check pixelflow/jianying_draft app/gateway/routers/pixelflow_jianying_draft.py tests/test_jianying_draft_models.py tests/test_jianying_draft_service.py tests/test_pixelflow_jianying_draft_router.py
cd ..
git diff --check
```

Expected: Ruff 和 diff 检查全部通过。

- [ ] **Step 4: 运行前端全部相关验证**

Run:

```bash
cd web
pnpm test:jianying-draft
node --test tests/jianyingDraftUiContract.test.mjs tests/videoSceneUiContract.test.mjs tests/mainFlowContract.test.mjs tests/conversationRouting.test.mjs
pnpm lint
pnpm build-test
```

Expected: Node 测试、TypeScript 和 Vite 构建全部通过。

- [ ] **Step 5: 手工浏览器验证未配置状态**

使用测试环境配置启动前后端，打开一个已有最终视频结果的对话，验证：

1. 未结束视频卡片显示三个按钮。
2. 草稿按钮禁用且提示“剪映草稿服务待接入”。
3. 点击草稿按钮不会产生网络 `/start` 请求。
4. “无意见，结束”和“提出修改意见”仍可正常使用。
5. 视频流程结束后仍显示禁用草稿入口。
6. 切换对话后按钮和状态不串到其他对话。

- [ ] **Step 6: 中文提交文档**

```bash
git add README.md AGENTS.md docs/pixelflow-agent-skill-flow-latest-design.md
git commit -m "文档：补充剪映草稿Agent流程"
```

- [ ] **Step 7: 最终检查提交边界**

Run: `git status --short --branch && git log -8 --oneline`

Expected: 工作区干净，提交只包含本计划涉及的代码、测试和文档。
