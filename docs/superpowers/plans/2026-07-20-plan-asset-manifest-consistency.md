# Plan Asset Manifest Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the approved video Plan asset manifest the only source for scene-package character, prop, and scene names, descriptions, generation prompts, counts, images, and front-end `@` labels.

**Architecture:** Add a focused domain module that normalizes and validates one Plan-level `asset_manifest`, then persist it beside `scene_blueprints` in every Plan version. The scene-package service will deterministically convert the approved manifest into `global_assets` and will no longer ask a second LLM to invent assets; image generation and front-end mentions consume the resulting records without renaming them.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest/pytest-asyncio, React 19, TypeScript 5.7, Node test runner, existing DeerFlow model factory and Borgrise image skill.

## Global Constraints

- `asset_manifest.name` is the canonical display name for Plan Markdown, scene-package assets, `mentions.name`, and front-end `@` chips.
- The per-scene `asset_requirements` deduplicated union must exactly equal the manifest in the same category; no missing, extra, or cross-category assets.
- One unique manifest asset creates one global asset and one successful image URL; repeated cross-scene references reuse it.
- Different clothing, hair, makeup, or appearance variants are separate assets with separate names.
- Characters use `description` plus `three_view_prompt`; props and scenes use `description` plus `image_prompt`.
- Approved names, descriptions, and prompts cannot be rewritten after Plan approval.
- Old video Plans without `asset_manifest` may be viewed but cannot start scene-package generation.
- The supplied Authorization is runtime-only: never save it to source, configuration, test fixtures, snapshots, documentation, PowerMem, or command output.
- All new Python behavior follows test-first RED-GREEN-REFACTOR.

---

### Task 1: Authoritative asset-manifest domain contract

**Files:**
- Create: `backend/pixelflow/creative/asset_manifest.py`
- Create: `backend/tests/test_plan_asset_manifest.py`

**Interfaces:**
- Consumes: normalized `scene_blueprints: list[dict[str, Any]]`.
- Produces: `empty_asset_manifest()`, `normalize_asset_manifest(raw_manifest, scene_blueprints)`, `validate_asset_manifest_consistency(asset_manifest, scene_blueprints)`, `fallback_asset_manifest(scene_blueprints)`, and `render_asset_manifest_markdown(asset_manifest)`.

- [ ] **Step 1: Write failing normalization and exact-union tests**

```python
def test_normalize_asset_manifest_generates_ids_and_preserves_canonical_names():
    manifest = normalize_asset_manifest(
        {
            "characters": [{"name": "林晓", "description": "浅灰风衣通勤者", "three_view_prompt": "林晓同一人物正面、侧面、背面三视图"}],
            "scenes": [{"name": "雨夜公交站", "description": "冷蓝夜雨公交站", "image_prompt": "雨夜公交站环境参考图"}],
            "props": [{"name": "黑色防水背包", "description": "哑光黑色银色拉链", "image_prompt": "黑色防水背包产品参考图"}],
        },
        [_blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"])],
    )
    assert [item["name"] for item in manifest["characters"]] == ["林晓"]
    assert manifest["characters"][0]["asset_id"].startswith("character-")
    assert manifest["scenes"][0]["asset_id"].startswith("scene-")
    assert manifest["props"][0]["asset_id"].startswith("prop-")


def test_validate_asset_manifest_rejects_missing_extra_and_renamed_assets():
    with pytest.raises(ValueError, match="必须与分镜资产需求完全一致"):
        normalize_asset_manifest(
            {
                "characters": [],
                "scenes": [{"name": "室内摄影棚", "description": "错误场景", "image_prompt": "错误场景图"}],
                "props": [],
            },
            [_blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"])],
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `cd backend; uv run pytest tests/test_plan_asset_manifest.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'pixelflow.creative.asset_manifest'`.

- [ ] **Step 3: Implement normalization, stable IDs, validation, fallback prompts, and Markdown rendering**

```python
ASSET_COLLECTIONS = (
    ("characters", "character", "three_view_prompt"),
    ("scenes", "scene", "image_prompt"),
    ("props", "prop", "image_prompt"),
)


def normalize_asset_manifest(raw_manifest: Any, scene_blueprints: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    source = raw_manifest if isinstance(raw_manifest, dict) else {}
    normalized = empty_asset_manifest()
    used_ids: set[str] = set()
    used_names: set[str] = set()
    for collection, prefix, prompt_field in ASSET_COLLECTIONS:
        for raw in source.get(collection) if isinstance(source.get(collection), list) else []:
            if not isinstance(raw, dict):
                raise ValueError(f"asset_manifest.{collection} 必须只包含对象")
            name = _required_text(raw.get("name"), f"asset_manifest.{collection}.name")
            name_key = _name_key(name)
            if name_key in used_names:
                raise ValueError(f"资产名称必须全局唯一：{name}")
            used_names.add(name_key)
            description = _required_text(raw.get("description"), f"资产 {name} description")
            prompt = _required_text(raw.get(prompt_field), f"资产 {name} {prompt_field}")
            asset_id = _unique_asset_id(prefix, name, used_ids)
            used_ids.add(asset_id)
            normalized[collection].append({"asset_id": asset_id, "name": name, "description": description, prompt_field: prompt})
    validate_asset_manifest_consistency(normalized, scene_blueprints)
    return normalized
```

`render_asset_manifest_markdown()` must render `## 四、全局资产清单`, the three fixed subsections, canonical names, descriptions, and exact generation prompts; empty groups render `- 无`.

- [ ] **Step 4: Add classification, duplicate-name, empty-field, exact-union, stable-ID, and fallback tests**

```python
@pytest.mark.parametrize("field", ["description", "three_view_prompt"])
def test_character_manifest_requires_complete_generation_contract(field):
    character = {"name": "林晓", "description": "通勤者", "three_view_prompt": "同一人物正侧背三视图"}
    character[field] = ""
    with pytest.raises(ValueError, match=field):
        normalize_asset_manifest(
            {"characters": [character], "scenes": [], "props": []},
            [_blueprint(characters=["林晓"], scenes=[], props=[])],
        )


def test_render_asset_manifest_markdown_uses_exact_names_and_prompts():
    markdown = render_asset_manifest_markdown(_valid_manifest())
    assert "### 4.1 出场角色列表" in markdown
    assert "- 名称：林晓" in markdown
    assert "三视图生成要求：林晓同一人物正面、侧面、背面三视图" in markdown
```

- [ ] **Step 5: Run Task 1 tests and verify GREEN**

Run: `cd backend; uv run pytest tests/test_plan_asset_manifest.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/pixelflow/creative/asset_manifest.py backend/tests/test_plan_asset_manifest.py
git commit -m "feat: add authoritative video asset manifest"
```

### Task 2: Generate, repair, render, version, and restore the manifest with Plan

**Files:**
- Modify: `backend/pixelflow/creative/plan_llm.py`
- Modify: `backend/pixelflow/creative/plan_markdown.py`
- Modify: `backend/app/gateway/routers/pixelflow_planning.py`
- Modify: `backend/tests/test_creative_plan_markdown.py`
- Modify: `backend/tests/test_scene_blueprint_quality.py`
- Modify: `backend/tests/test_pixelflow_planning_router.py`

**Interfaces:**
- Consumes: Task 1 manifest functions and existing normalized blueprints.
- Produces: `PlanMarkdownResult.asset_manifest`, versioned history entries containing `asset_manifest`, and Plan API request/response fields with the same name.

- [ ] **Step 1: Write failing initial-Plan tests**

```python
def test_video_plan_renders_llm_asset_manifest_as_authoritative_markdown():
    payload = _valid_video_llm_payload()
    payload["asset_manifest"] = _valid_asset_manifest()
    result = asyncio.run(build_plan_markdown_with_llm("video", FORM, DIRECTION, model_factory=_factory(payload)))
    assert result.asset_manifest == normalize_asset_manifest(payload["asset_manifest"], result.scene_blueprints)
    assert "## 四、全局资产清单" in result.plan_markdown
    assert "- 名称：林晓" in result.plan_markdown
    assert result.plan_history[0]["asset_manifest"] == result.asset_manifest
```

- [ ] **Step 2: Run the initial-Plan test and verify RED**

Run: `cd backend; uv run pytest tests/test_creative_plan_markdown.py -k asset_manifest -q`

Expected: failure because `PlanMarkdownResult` has no `asset_manifest` field.

- [ ] **Step 3: Extend Plan prompts and result/history DTOs**

Update initial and revision JSON output contracts to include:

```python
"asset_manifest": {
    "characters": [{"name": "人物唯一名称", "description": "外观与身份说明", "three_view_prompt": "同一人物正面、侧面、背面三视图生成要求"}],
    "scenes": [{"name": "场景唯一名称", "description": "空间、光影和环境说明", "image_prompt": "场景图生成要求"}],
    "props": [{"name": "道具唯一名称", "description": "外观、材质和细节说明", "image_prompt": "道具图生成要求"}],
}
```

Extend `PlanMarkdownResult`, `to_dict()`, `with_revision()`, `_history_entry()`, `_failed_revision_result()`, `restore_plan_version()`, and the history deep-copy path with `asset_manifest: dict[str, list[dict[str, Any]]]`.

- [ ] **Step 4: Add one scoped asset-contract repair call**

Create `repair_plan_asset_contract()` in `plan_llm.py`. Its prompt receives user inputs, current blueprints, the invalid manifest, and validation errors, and returns only:

```json
{
  "asset_manifest": {"characters": [], "scenes": [], "props": []},
  "scene_blueprints": [
    {"scene_index": 1, "asset_requirements": {"characters": [], "scenes": [], "props": []}}
  ]
}
```

Apply only those two asset fields. Preserve title, story, shot description, narration, transition, timing, and creation contract. For initial Plan failure after one repair, build the deterministic fallback Plan and fallback manifest; for revision failure, return the existing failed-revision result without publishing a version.

- [ ] **Step 5: Replace Markdown asset and scene sections from structured contracts**

Update `_with_execution_contract()` so video Markdown is processed as:

```python
base = _replace_video_asset_section(base, asset_manifest)
base = _replace_video_scene_section(base, scene_blueprints)
```

`_replace_video_asset_section()` replaces the content from `## 四、` up to `## 五、` with `render_asset_manifest_markdown(asset_manifest)`, ensuring LLM prose cannot diverge from production data.

- [ ] **Step 6: Write failing revision, manual-edit, history, and router tests**

```python
def test_video_revision_versions_manifest_and_preserves_previous_snapshot():
    original = build_plan_markdown("video", FORM, DIRECTION)
    revised_manifest = _manifest_with_second_character()
    revised = asyncio.run(revise_plan_markdown_with_llm(
        "video", original.plan_markdown, "增加角色赵经理", FORM, DIRECTION,
        original.plan_version, original.plan_history,
        current_creation_contract=original.creation_contract,
        current_scene_blueprints=original.scene_blueprints,
        current_asset_manifest=original.asset_manifest,
        model_factory=_factory(_revision_payload(asset_manifest=revised_manifest)),
    ))
    assert [item["name"] for item in revised.asset_manifest["characters"]] == ["林晓", "赵经理"]
    assert revised.plan_history[0]["asset_manifest"] == original.asset_manifest
    assert revised.plan_history[-1]["asset_manifest"] == revised.asset_manifest


def test_planning_router_serializes_asset_manifest():
    data = client.post("/agent/flows/planning/plan", json=_video_plan_request()).json()
    assert data["asset_manifest"]["props"][0]["name"]
    assert data["plan_history"][0]["asset_manifest"] == data["asset_manifest"]
```

- [ ] **Step 7: Extend planning request/response models and call sites**

Add `asset_manifest` to `PlanMarkdownResponse`, `PlanRevisionRequest`, `PlanRestoreRequest`, and `PlanManualEditRequest`. Pass `current_asset_manifest` into revision/manual-edit and `asset_manifest` into restore. Image plans use `{"characters": [], "scenes": [], "props": []}`.

- [ ] **Step 8: Run focused Plan tests and verify GREEN**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_plan_asset_manifest.py tests/test_creative_plan_markdown.py tests/test_scene_blueprint_quality.py tests/test_pixelflow_planning_router.py -q
```

Expected: all focused Plan tests pass.

- [ ] **Step 9: Commit Task 2**

```powershell
git add backend/pixelflow/creative/plan_llm.py backend/pixelflow/creative/plan_markdown.py backend/app/gateway/routers/pixelflow_planning.py backend/tests/test_creative_plan_markdown.py backend/tests/test_scene_blueprint_quality.py backend/tests/test_pixelflow_planning_router.py
git commit -m "feat: version video asset manifest with plan"
```

### Task 3: Deterministic scene packages and exact image cardinality

**Files:**
- Modify: `backend/pixelflow/generate/scene_packages.py`
- Modify: `backend/pixelflow/generate/scene_assets.py`
- Modify: `backend/app/gateway/routers/pixelflow_video.py`
- Modify: `backend/tests/test_video_scene_packages.py`
- Modify: `backend/tests/test_scene_assets.py`
- Modify: `backend/tests/test_pixelflow_video_router.py`

**Interfaces:**
- Consumes: approved `asset_manifest`, normalized `scene_blueprints`, and `creation_contract` from Task 2.
- Produces: `global_assets` with an exact one-to-one copy of manifest records plus image arrays; canonical `reference_asset_ids` and `mentions.name`; one retained URL per successful asset.

- [ ] **Step 1: Write failing deterministic scene-package tests**

```python
def test_scene_packages_copy_manifest_exactly_and_do_not_call_second_llm():
    factory = FailingFactory("scene-package LLM must not run for approved manifest")
    result = asyncio.run(prepare_video_scene_packages_with_llm(
        form_values=FORM,
        plan_markdown="# approved",
        selected_direction=DIRECTION,
        target_duration_ms=8_000,
        scene_blueprints=[_blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"])],
        asset_manifest=_valid_manifest(),
        model_factory=factory,
    ))
    assert [item["name"] for item in result["global_assets"]["characters"]] == ["林晓"]
    assert result["global_assets"]["characters"][0]["description"] == "浅灰风衣通勤者"
    assert result["scene_packages"][0]["shot_description"]["mentions"][0]["name"] == "林晓"
```

Also add failures for missing manifest, extra manifest item, missing scene reference, renamed mention, repeated cross-scene asset, and more than nine references.

- [ ] **Step 2: Run scene-package tests and verify RED**

Run: `cd backend; uv run pytest tests/test_video_scene_packages.py -k "manifest or canonical" -q`

Expected: failure because scene-package functions do not accept `asset_manifest` and still invoke the LLM.

- [ ] **Step 3: Implement deterministic manifest conversion**

Add `asset_manifest` to both scene-package entry signatures. When authoritative blueprints exist:

```python
manifest = normalize_asset_manifest(asset_manifest, authoritative_blueprints)
global_assets = {
    "characters": [{**item, "three_view_images": []} for item in manifest["characters"]],
    "scenes": [{**item, "images": []} for item in manifest["scenes"]],
    "props": [{**item, "images": []} for item in manifest["props"]],
    "visual_style": _authoritative_visual_style(form_values.get("visual_style"), {}),
}
```

Build scenes from blueprints only. Resolve each requirement in its declared category, do not fall through to another category, and create mentions through `_shot_mentions()` so `name` always comes from `global_assets`. Remove the authoritative-mainline call to `_invoke_scene_package_model`; retain no default presenter, default user, default product, or default scene.

- [ ] **Step 4: Enforce one URL and one generation call per asset**

Add tests that count image-skill calls by `(asset_type, asset_id)` and return two URLs from a fake provider. Implement:

```python
urls = _extract_image_urls(result)[:1]
```

Keep one job per manifest record in manifest order. A zero-URL success becomes a failed asset; partial failure keeps the authoritative name and prompt unchanged.

- [ ] **Step 5: Extend video router contracts and reject legacy approved Plans**

Add `asset_manifest` to `PrepareScenePackagesRequest`. `_prepare_scene_packages_response()` must reject authoritative video generation without it using an actionable HTTP 400 message, pass it to the scene-package service, and preserve it in the asynchronous job request. Add router tests proving the approved manifest reaches the service unchanged and a legacy request does not start image generation.

- [ ] **Step 6: Run focused scene-package and image tests and verify GREEN**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_video_scene_packages.py tests/test_scene_assets.py tests/test_pixelflow_video_router.py -q
```

Expected: all tests pass; each manifest item has one task and at most one retained URL.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/pixelflow/generate/scene_packages.py backend/pixelflow/generate/scene_assets.py backend/app/gateway/routers/pixelflow_video.py backend/tests/test_video_scene_packages.py backend/tests/test_scene_assets.py backend/tests/test_pixelflow_video_router.py
git commit -m "fix: generate scene assets only from approved plan"
```

### Task 4: Front-end transport, recovery, and canonical `@` names

**Files:**
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/planMessageRecovery.ts`
- Modify: `web/src/lib/sceneMentions.ts`
- Modify: `web/src/lib/scenePackages.ts`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/tests/planMessageRecovery.test.mjs`
- Modify: `web/tests/sceneMentions.test.mjs`
- Modify: `web/tests/scenePackages.test.mjs`

**Interfaces:**
- Consumes: Plan API `asset_manifest` and scene-package `global_assets` from Tasks 2-3.
- Produces: typed `PlanAssetManifest`, persisted pending requests, and `@` labels whose names always come from `global_assets`.

- [ ] **Step 1: Write failing API/recovery tests**

```javascript
test("recoverPlanMessage preserves the versioned asset manifest", () => {
  const recovered = recoverPlanMessage(_messageWithManifest());
  assert.deepEqual(recovered.artifact.plan.asset_manifest, MANIFEST);
});
```

Update the Workspace request assertion to require:

```typescript
asset_manifest: artifact.plan.asset_manifest,
```

- [ ] **Step 2: Write failing canonical mention-name tests**

```javascript
test("normalizeShotMentions replaces stale names with the global canonical name", () => {
  const mentions = normalizeShotMentions(
    { mentions: [{ asset_id: "character-lin", type: "character", name: "旧别名" }] },
    ["character-lin"],
    { characters: [{ asset_id: "character-lin", name: "林晓", three_view_images: ["https://x/lin.png"] }], scenes: [], props: [] },
  );
  assert.equal(mentions[0].name, "林晓");
});
```

- [ ] **Step 3: Run front-end tests and verify RED**

Run:

```powershell
Set-Location web
corepack pnpm test:plan-message-recovery
corepack pnpm test:scene-mentions
corepack pnpm test:scene-packages
```

Expected: manifest recovery assertion fails and stale mention name remains `旧别名`.

- [ ] **Step 4: Add TypeScript contracts and transport fields**

Define:

```typescript
export interface PlanCharacterAsset { asset_id: string; name: string; description: string; three_view_prompt: string }
export interface PlanImageAsset { asset_id: string; name: string; description: string; image_prompt: string }
export interface PlanAssetManifest { characters: PlanCharacterAsset[]; scenes: PlanImageAsset[]; props: PlanImageAsset[] }
```

Add `asset_manifest` to `PlanMarkdownResponse`, every Plan version entry, revision/manual-edit/restore requests, scene-package request types, Plan message recovery, and `WorkspacePage` approval/pending context payloads.

- [ ] **Step 5: Make global assets authoritative for mention names**

In `normalizeShotMentions()`, when `byId` contains a candidate, always set `name: candidate.name`; only use the stale mention name when no global asset exists. In `normalizeSceneAssetMentionsForGeneration()`, do not overwrite a name already obtained from `globalAssets` with `mentions` data. Keep chip serialization as `@${mention.name}`, which will now be canonical.

- [ ] **Step 6: Run front-end tests and build and verify GREEN**

Run:

```powershell
Set-Location web
corepack pnpm test:plan-message-recovery
corepack pnpm test:scene-mentions
corepack pnpm test:scene-packages
corepack pnpm build-test
```

Expected: all tests pass and TypeScript/Vite build completes.

- [ ] **Step 7: Commit Task 4**

```powershell
git add web/src/lib/api.ts web/src/lib/planMessageRecovery.ts web/src/lib/sceneMentions.ts web/src/lib/scenePackages.ts web/src/pages/WorkspacePage.tsx web/tests/planMessageRecovery.test.mjs web/tests/sceneMentions.test.mjs web/tests/scenePackages.test.mjs
git commit -m "feat: preserve canonical plan asset names in storyboard"
```

### Task 5: Documentation, full regression, and real flow inspection

**Files:**
- Create: `scripts/verify_video_asset_manifest_flow.py`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Modify: `CONTENT_APP_API_CALLS.md`
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: completed backend and front-end contracts.
- Produces: documented behavior and evidence from automatic and real integration verification.

- [ ] **Step 1: Update project documentation with the implemented contract**

Document that Plan produces and versions `asset_manifest`; the scene-package mainline does no second asset-authoring LLM call; content-app image call count equals unique approved manifest assets; names flow unchanged to `@` chips; legacy Plans require regeneration/revision.

- [ ] **Step 2: Run the complete relevant backend regression suite**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_plan_asset_manifest.py tests/test_creative_plan_markdown.py tests/test_scene_blueprint_quality.py tests/test_pixelflow_planning_router.py tests/test_video_scene_packages.py tests/test_scene_assets.py tests/test_pixelflow_video_router.py -q
```

Expected: all tests pass with no warnings introduced by this change.

- [ ] **Step 3: Run the complete relevant front-end verification**

Run:

```powershell
Set-Location web
corepack pnpm test:plan-message-recovery
corepack pnpm test:scene-mentions
corepack pnpm test:scene-packages
corepack pnpm build-test
```

Expected: all tests and build pass.

- [ ] **Step 4: Create a runtime-only real-flow verifier**

Create `scripts/verify_video_asset_manifest_flow.py`. It must read `PIXELFLOW_REAL_FLOW_AUTHORIZATION` and `PIXELFLOW_REAL_FLOW_BASE_URL` from the process environment, send this exact Plan request, poll the scene-package job every two seconds, redact URL query strings from its summary, and download images into `tempfile.TemporaryDirectory()`:

```python
PLAN_REQUEST = {
    "intent": "video",
    "form_values": {
        "product_info": "曜石黑防水通勤背包",
        "product_category": "通勤背包",
        "target_audience": "城市上班族",
        "conversion_goal": "进入直播间了解产品",
        "video_duration_sec": 30,
        "video_ratio": "9:16",
        "video_model": "seedance-2.0",
        "video_size": "720p",
        "video_sound": "on",
        "image_model": "gpt-image-2",
        "video_usage": "抖音信息流广告",
        "visual_style": "电影写实雨夜广告",
        "video_model_capabilities": {
            "aspect_ratios": ["9:16"],
            "sizes": ["720p"],
            "sounds": ["on"],
            "durations_sec": [5, 10, 15],
            "generation_types": ["reference_mode_video", "text_to_video"]
        },
        "image_model_capabilities": {"aspect_ratios": ["9:16", "1:1"], "sizes": ["2K", "1080p"]}
    },
    "selected_direction": {
        "title": "雨夜双人通勤实测",
        "description": "林晓和赵经理在雨夜公交站及明亮办公室反复使用同一只曜石黑防水通勤背包；固定道具还包括折叠伞和银色保温杯。"
    },
    "product_creative_profile": {"core_message": "同一背包跨场景保持外观一致"},
    "intake_context": {},
    "materials": []
}
```

After the Plan response, assert:

```python
assert set(item["name"] for item in plan["asset_manifest"]["characters"]) == blueprint_union(plan, "characters")
assert set(item["name"] for item in plan["asset_manifest"]["scenes"]) == blueprint_union(plan, "scenes")
assert set(item["name"] for item in plan["asset_manifest"]["props"]) == blueprint_union(plan, "props")
assert all(item["name"] in plan["plan_markdown"] for group in plan["asset_manifest"].values() for item in group)
```

- [ ] **Step 5: Start the backend and execute the real Plan and scene-asset flow**

Start the backend without exposing the Authorization in command arguments:

```powershell
$env:PIXELFLOW_CONFIG_ENV='dev'
$env:PYTHONPATH='.'
$backendProcess = Start-Process -FilePath 'uv' -ArgumentList 'run','python','-m','app.gateway.run' -WorkingDirectory (Resolve-Path 'backend') -WindowStyle Hidden -PassThru
$env:PIXELFLOW_REAL_FLOW_BASE_URL='http://127.0.0.1:8001'
$authorizationSecure = Read-Host 'Paste the Authorization header supplied for this verification' -AsSecureString
$authorizationPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($authorizationSecure)
$env:PIXELFLOW_REAL_FLOW_AUTHORIZATION = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($authorizationPointer)
uv run --project backend python scripts/verify_video_asset_manifest_flow.py
```

The verifier posts the returned `plan_markdown`, `scene_blueprints`, `asset_manifest`, and `creation_contract` to `/agent/flows/video/prepare-scene-packages/start`, polls `/agent/flows/video/prepare-scene-packages/jobs/{job_id}`, and asserts exact ordered equality for `asset_id`, `name`, `description`, and generation Prompt between the Plan manifest and `videoScenePackages.global_assets`. It asserts each successful asset has exactly one URL and every `mentions.name` equals the matching global asset name. Stop the exact process in `finally` with `Stop-Process -Id $backendProcess.Id`, remove the two runtime environment variables, and call `[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($authorizationPointer)`.

- [ ] **Step 6: Download and visually inspect all successful images**

Download each returned public image to a temporary directory outside the repository. Inspect every image against the Plan description: same character identity and approved appearance, matching prop color/material/details, and matching scene space/light. Record only asset ID, canonical name, pass/fail, and a concise non-sensitive reason. If any mismatch is found, add a failing automated test for the traceable contract cause, implement the smallest correction, rerun Steps 2-6, and stop only when no known mismatch remains or an external provider failure is explicitly reported.

- [ ] **Step 7: Verify repository cleanliness and commit documentation**

Run:

```powershell
git diff --check
git status --short
git add scripts/verify_video_asset_manifest_flow.py docs/pixelflow-agent-skill-flow-latest-design.md CONTENT_APP_API_CALLS.md README.md AGENTS.md
git commit -m "docs: document plan asset manifest production contract"
```

Expected: only intentional implementation commits and the pre-existing untracked `scripts/__pycache__/` remain; no Authorization or downloaded image is tracked.
