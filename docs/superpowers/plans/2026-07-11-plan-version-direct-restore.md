# Plan 历史版本直接回退 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 图片和视频策划流程选择历史 `plan.md` 时直接激活所选版本，不创建重复的新版本；只有提交新的修改意见时才创建递增的新版本。

**Architecture:** `plan_history` 继续作为不可变版本档案，当前激活版本由最新 Plan artifact 的 `plan_version` 指向。回退只切换当前指针和内容，不追加历史；后续修订从历史最大版本号继续递增。新历史条目同时保存创作合同与分镜时长快照，旧条目缺少快照时兼容沿用当前权威合同。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、pytest、React 19、TypeScript、Node test。

## Global Constraints

- 图片和视频必须使用同一套回退语义。
- 回退 v1 后历史仍为 `[v1, v2]`；再次修改必须生成 v3，不能覆盖 v2，也不能错误生成 v2。
- 新生成的每个历史版本必须保存 `creation_contract` 和 `scene_durations_sec` 快照。
- 老对话历史若没有快照，回退时沿用请求中的当前权威合同和时长，不得清空生产参数。
- 回退成功后必须立即持久化 conversation context，刷新或切换对话后仍看到正确的激活版本。
- 不改变“重新生成新创意”入口；只有“继续修改”提交意见才调用 `/plan/revise`。
- 新增注释、测试说明、用户文案、文档和提交信息使用中文；程序标识符保持英文。

---

### Task 1: 用失败测试锁定直接回退和历史最大版本语义

**Files:**
- Modify: `backend/tests/test_creative_plan_markdown.py:1-220`
- Modify: `backend/tests/test_pixelflow_planning_router.py:242-275`
- Test: `backend/tests/test_creative_plan_markdown.py`
- Test: `backend/tests/test_pixelflow_planning_router.py`

**Interfaces:**
- Consumes: `restore_plan_version(...) -> PlanMarkdownResult`
- Produces: 直接激活历史版本、不追加历史、后续版本号取历史最大值加一的回归合同。

- [ ] **Step 1: 给纯业务层补直接回退失败测试**

修改 import：

```python
from pixelflow.creative.plan_markdown import (
    PlanMarkdownResult,
    build_plan_markdown,
    build_plan_markdown_with_llm,
    restore_plan_version,
)
```

在文件末尾增加：

```python
def test_restore_plan_version_activates_history_without_appending():
    history = [
        {
            "version": 1,
            "plan_markdown": "# plan.md v1",
            "creation_contract": {"video_model": "seedance-1.5-pro", "video_duration_sec": 20},
            "scene_durations_sec": [10, 10],
        },
        {
            "version": 2,
            "plan_markdown": "# plan.md v2",
            "creation_contract": {"video_model": "seedance-2.0", "video_duration_sec": 20},
            "scene_durations_sec": [5, 15],
        },
    ]

    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=history,
        restore_version=1,
        creation_contract=history[1]["creation_contract"],
        scene_durations_sec=history[1]["scene_durations_sec"],
    )

    assert result.plan_version == 1
    assert result.plan_markdown == "# plan.md v1"
    assert result.plan_history == history
    assert result.restored_from_version == 1
    assert result.creation_contract == history[0]["creation_contract"]
    assert result.scene_durations_sec == [10, 10]


def test_restore_legacy_history_keeps_current_authoritative_contract():
    current_contract = {"video_model": "seedance-2.0", "video_duration_sec": 20}

    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=[
            {"version": 1, "plan_markdown": "# plan.md v1"},
            {"version": 2, "plan_markdown": "# plan.md v2"},
        ],
        restore_version=1,
        creation_contract=current_contract,
        scene_durations_sec=[10, 10],
    )

    assert result.plan_version == 1
    assert result.creation_contract == current_contract
    assert result.scene_durations_sec == [10, 10]
```

- [ ] **Step 2: 给“回退后继续修改”补失败测试**

```python
def test_next_version_uses_history_max_after_restore():
    restored = PlanMarkdownResult(
        output_type="image",
        plan_markdown="# plan.md v1",
        template_path=Path("plan_image.md"),
        plan_version=1,
        plan_history=[
            {"version": 1, "plan_markdown": "# plan.md v1"},
            {"version": 2, "plan_markdown": "# plan.md v2"},
        ],
        creation_contract={"image_size": "9:16"},
    )

    revised = restored.next_version(
        plan_markdown="# plan.md v3",
        current_version=restored.plan_version,
    )

    assert revised.plan_version == 3
    assert [item["version"] for item in revised.plan_history] == [1, 2, 3]
    assert revised.plan_history[-1]["creation_contract"] == {"image_size": "9:16"}
```

同时给测试文件增加：

```python
from pathlib import Path
```

- [ ] **Step 3: 把 Router 旧断言改成图片、视频共用的直接回退合同**

用下面的参数化测试替换 `test_planning_router_restores_history_as_a_new_version`：

```python
@pytest.mark.parametrize("intent", ["image", "video"])
def test_planning_router_restores_history_without_creating_version(intent: str):
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)
    history = [
        {"version": 1, "plan_markdown": "# plan.md v1"},
        {"version": 2, "plan_markdown": "# plan.md v2"},
    ]

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan/restore",
            json={
                "intent": intent,
                "current_plan_markdown": "# plan.md v2",
                "current_plan_version": 2,
                "plan_history": history,
                "restore_version": 1,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["plan_version"] == 1
    assert data["restored_from_version"] == 1
    assert data["plan_markdown"] == "# plan.md v1"
    assert data["plan_history"] == history
```

在 import 区增加 `import pytest`（若尚不存在）。

- [ ] **Step 4: 运行测试确认 RED，失败原因必须是旧的追加版本语义**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest tests/test_creative_plan_markdown.py tests/test_pixelflow_planning_router.py -q
```

Expected: FAIL；旧实现返回 v3、历史长度 3，且历史条目没有合同快照。

- [ ] **Step 5: 提交失败测试**

```powershell
git add backend/tests/test_creative_plan_markdown.py backend/tests/test_pixelflow_planning_router.py
git commit -m "test: 锁定 Plan 历史版本直接回退语义"
```

---

### Task 2: 实现直接激活、完整快照和单调递增版本号

**Files:**
- Modify: `backend/pixelflow/creative/plan_markdown.py:29-101`
- Modify: `backend/pixelflow/creative/plan_markdown.py:275-308`
- Modify: `backend/pixelflow/creative/plan_markdown.py:589-612`
- Test: `backend/tests/test_creative_plan_markdown.py`

**Interfaces:**
- Produces: 历史条目 `{version, plan_markdown, creation_contract, scene_durations_sec, restored_from_version?}`。
- Preserves: 旧历史条目仍可读取；缺少快照时不破坏现有生产合同。

- [ ] **Step 1: 让初始版本和新修订版本保存完整快照**

将 `__post_init__` 中的历史条目创建改为：

```python
[
    _history_entry(
        self.plan_version,
        self.plan_markdown,
        self.restored_from_version,
        creation_contract=self.creation_contract,
        scene_durations_sec=self.scene_durations_sec,
    )
]
```

扩展 `_history_entry`：

```python
def _history_entry(
    version: int,
    plan_markdown: str,
    restored_from_version: int | None = None,
    *,
    creation_contract: dict[str, Any] | None = None,
    scene_durations_sec: list[int] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "version": version,
        "plan_markdown": plan_markdown,
        "creation_contract": dict(creation_contract or {}),
        "scene_durations_sec": list(scene_durations_sec or []),
    }
    if restored_from_version is not None:
        item["restored_from_version"] = restored_from_version
    return item
```

- [ ] **Step 2: 修正 `next_version`，以整个历史的最大版本号为准**

替换方法内的版本和历史处理：

```python
history = _normalized_history(plan_history or self.plan_history)
history_max = max((int(item["version"]) for item in history), default=0)
version = max(1, int(current_version or self.plan_version), history_max) + 1
next_contract = dict(creation_contract or self.creation_contract)
next_durations = list(self.scene_durations_sec)
history.append(
    _history_entry(
        version,
        plan_markdown,
        restored_from_version,
        creation_contract=next_contract,
        scene_durations_sec=next_durations,
    )
)
return replace(
    self,
    plan_markdown=plan_markdown,
    plan_version=version,
    plan_history=history,
    restored_from_version=restored_from_version,
    llm_used=self.llm_used if llm_used is None else llm_used,
    error=error,
    creation_contract=next_contract,
    scene_durations_sec=next_durations,
)
```

- [ ] **Step 3: 把 `restore_plan_version` 改成直接构造激活结果**

用下面逻辑替换 `base.next_version(...)`：

```python
source_contract = source.get("creation_contract")
source_durations = source.get("scene_durations_sec")
resolved_contract = (
    dict(source_contract)
    if isinstance(source_contract, dict) and source_contract
    else dict(creation_contract or {})
)
resolved_durations = (
    [int(value) for value in source_durations]
    if isinstance(source_durations, list) and source_durations
    else list(scene_durations_sec or [])
)
return PlanMarkdownResult(
    output_type=intent,
    plan_markdown=str(source.get("plan_markdown") or ""),
    template_path=_template_path(intent),
    plan_version=restore_version,
    plan_history=history,
    creation_contract=resolved_contract,
    scene_durations_sec=resolved_durations,
    restored_from_version=restore_version,
)
```

删除已不再使用的 `base` 局部变量。

- [ ] **Step 4: 运行后端目标测试确认 GREEN**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest tests/test_creative_plan_markdown.py tests/test_pixelflow_planning_router.py -q
```

Expected: PASS。

- [ ] **Step 5: 运行策划相关回归测试**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest tests/test_creative_plan_markdown.py tests/test_pixelflow_planning_router.py tests/test_video_creation_contract.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交后端实现**

```powershell
git add backend/pixelflow/creative/plan_markdown.py
git commit -m "fix: Plan 历史版本回退不再创建副本"
```

---

### Task 3: 前端持久化激活版本并修正文案

**Files:**
- Modify: `web/src/lib/api.ts:240-260`
- Modify: `web/src/pages/WorkspacePage.tsx:6525-6575`
- Modify: `web/tests/mainFlowContract.test.mjs:170-190`
- Test: `web/tests/mainFlowContract.test.mjs`

**Interfaces:**
- Consumes: `POST /agent/flows/planning/plan/restore` 的直接回退响应。
- Produces: 对话上下文中的 `planMarkdown`、`planVersion`、`planHistory`、`creationContract` 与当前 UI 一致。

- [ ] **Step 1: 先补前端静态合同失败测试**

在现有 Plan 版本测试附近增加：

```javascript
test("plan rollback activates history directly and persists conversation context", () => {
  const start = workspaceSource.indexOf("const handleRollbackPlan = async");
  const end = workspaceSource.indexOf("const handle", start + 30);
  assert.notEqual(start, -1, "Plan rollback handler must exist");
  assert.notEqual(end, -1, "the next handler must follow Plan rollback");
  const rollbackSource = workspaceSource.slice(start, end);

  assert.equal(
    rollbackSource.includes("并保留为新版本"),
    false,
    "rollback must not claim that it creates another version",
  );
  assert.match(rollbackSource, /api\.updateConversation/, "rollback must persist the active version");
  assert.match(rollbackSource, /if \(targetConversationId\)/, "context persistence must use the validated conversation id");
  assert.match(rollbackSource, /plan_version:\s*plan\.plan_version/, "context must save active version");
  assert.match(rollbackSource, /plan_history:\s*plan\.plan_history/, "context must save unchanged history");
  assert.match(rollbackSource, /creation_contract:\s*plan\.creation_contract/, "context must save restored contract");
  assert.ok(
    rollbackSource.indexOf("api.updateConversation") < rollbackSource.indexOf("已回退到 plan.md"),
    "success message must follow persistence",
  );
});
```

- [ ] **Step 2: 运行前端合同测试确认 RED**

Run:

```powershell
Set-Location web
node --test tests/mainFlowContract.test.mjs
```

Expected: FAIL；当前文案仍声称创建新版本，且 handler 没有持久化 conversation context。

- [ ] **Step 3: 扩展 API 类型中的历史快照字段**

将 `PlanMarkdownResponse.plan_history` 条目类型改为：

```typescript
plan_history: Array<{
  version: number;
  plan_markdown: string;
  restored_from_version?: number;
  creation_contract?: Record<string, unknown>;
  scene_durations_sec?: number[];
}>;
```

- [ ] **Step 4: 回退成功后立即保存完整会话快照**

在 `handleRollbackPlan` 取得 `plan` 并 `pushPlanArtifact(...)` 后，增加：

```typescript
if (targetConversationId) {
  await api.updateConversation(targetConversationId, {
    last_phase: "plan_review",
    context: {
      ...makeSnapshot(targetConversationId),
      selected_direction: artifact.selectedDirection,
      plan_markdown: plan.plan_markdown,
      plan_version: plan.plan_version,
      plan_history: plan.plan_history,
      creation_contract: plan.creation_contract,
      scene_durations_sec: plan.scene_durations_sec,
      restored_from_version: plan.restored_from_version,
    } as unknown as Record<string, unknown>,
  });
}
pushAssistant(`已回退到 plan.md v${plan.plan_version}，未创建新版本。`, targetConversationId);
```

将调用 restore 前的进度提示改为：

```typescript
pushAssistant(
  `正在把 plan.md v${artifact.plan.plan_version || 1} 直接回退到 v${version}，不会创建新版本…`,
  targetConversationId,
);
```

沿用当前 handler 已通过 `messageConversationId(...)` 解析的 `targetConversationId`，不新增第二套 conversation id 来源。

- [ ] **Step 5: 运行前端合同测试和 TypeScript 构建**

Run:

```powershell
Set-Location web
node --test tests/mainFlowContract.test.mjs
corepack pnpm build-test
```

Expected: PASS。

- [ ] **Step 6: 提交前端实现**

```powershell
git add web/src/lib/api.ts web/src/pages/WorkspacePage.tsx web/tests/mainFlowContract.test.mjs
git commit -m "fix: 前端直接激活 Plan 历史版本"
```

---

### Task 4: 同步项目事实文档并做图片、视频回归

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CONTENT_APP_API_CALLS.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Test: `backend/tests/test_creative_plan_markdown.py`
- Test: `backend/tests/test_pixelflow_planning_router.py`
- Test: `web/tests/mainFlowContract.test.mjs`

**Interfaces:**
- Produces: 文档与实际 API 语义一致，明确“回退不创建新版本、修改才创建新版本”。

- [ ] **Step 1: 查找所有旧语义**

Run:

```powershell
rg -n "回退.*新版本|恢复为新的当前版本|restore.*新版本|append-only|保留为新版本" README.md AGENTS.md CONTENT_APP_API_CALLS.md docs web backend
```

Expected: 列出需要修正的接口说明、流程说明和旧用户文案；不要改动历史 spec 文件，它们是当时的设计记录。

- [ ] **Step 2: 更新当前事实文档**

至少写清：

```markdown
- `/agent/flows/planning/plan/restore` 直接激活所选历史版本，不追加重复版本。
- 回退后再次“继续修改”时，以历史最大版本号加一创建新版本。
- 新版本历史条目保存创作合同与分镜时长快照；旧对话缺少快照时沿用当前权威合同。
```

- [ ] **Step 3: 运行图片、视频共用回归测试**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest tests/test_creative_plan_markdown.py tests/test_pixelflow_planning_router.py -q
Set-Location ..\web
node --test tests/mainFlowContract.test.mjs
corepack pnpm build-test
```

Expected: 全部 PASS。

- [ ] **Step 4: 检查差异没有意外扩大范围**

Run:

```powershell
git diff --check
git diff --stat
git status --short
```

Expected: 只有本计划列出的 Plan、测试和当前事实文档发生变化。

- [ ] **Step 5: 提交文档**

```powershell
git add README.md AGENTS.md CONTENT_APP_API_CALLS.md docs/pixelflow-agent-skill-flow-latest-design.md
git commit -m "docs: 更新 Plan 历史版本回退说明"
```

## Completion Gate

- [ ] 图片、视频 Router 测试均证明回退不追加历史。
- [ ] 回退 v1 后继续修改生成 v3，并保留 v2。
- [ ] 新历史条目包含合同和分镜时长快照。
- [ ] 老对话缺少快照时仍保留当前权威合同。
- [ ] 前端刷新或重新进入对话后仍显示回退后的激活版本。
- [ ] 文档不再把 `/plan/restore` 描述为“创建新版本”。
