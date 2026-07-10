# PixelFlow Video Creation Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the user-confirmed video duration, video ratio, video model, scene-image model, Plan-selected image ratio/quality, and versioned Plan the authoritative contract for creative directions, scene packages, scene assets, and every content-app generation call.

**Architecture:** Add a typed Python creation-contract layer and a single strict duration allocator, then pass that contract through intake, planning, scene preparation, image generation, and video generation. Keep model-option presentation in a focused TypeScript module, use separate video/image Plan templates with DeepSeek generation plus deterministic fallback, and preserve Plan versions in chat artifacts and conversation snapshots. The vendored Seedance Skill remains source material while a small runtime adapter injects its relevant rules into scene-package prompting.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, React 19, TypeScript 5.7, Node test runner, content-app REST APIs, DeepSeek `deepseek-v4-pro`.

## Global Constraints

- All Python APIs exposed to the frontend remain under `/agent`.
- Video total duration is an integer from 4 through 300 seconds.
- Every scene duration is an integer from 4 through 15 seconds, and scene durations sum exactly to the confirmed total.
- The video form exposes video model and image model, but never exposes scene-image ratio or scene-image quality.
- Video models come from `video_generate` and are filtered to Seedance models; image models come from `image_generate` without Seedance filtering.
- Default resolved video model is `seedance-2.0`; default image model is `gpt-image-2`; default video ratio is `9:16`.
- Plan LLM chooses scene-image ratio and quality only from the selected image model capability lists.
- The selected video model is used only for video APIs. The selected image model is used only for character, scene, and prop image APIs.
- Plan and scene-package generation use the same strict duration allocation.
- The five content-app video request bodies exactly match their Java DTOs.
- Async jobs and callbacks remain bound to the originating `conversation_id` and are never restarted automatically on restore.
- Never persist the user-provided Bearer token in source, tests, docs, logs, or configuration.

---

### Task 1: Typed Creation Contract And Strict Duration Allocation

**Files:**
- Create: `backend/pixelflow/creative/duration.py`
- Create: `backend/pixelflow/creative/contract.py`
- Create: `backend/tests/test_video_creation_contract.py`

**Interfaces:**
- Produces: `split_video_duration(total_seconds: int, preferred_seconds: int = 10) -> list[int]`
- Produces: `scene_time_ranges(durations: Sequence[int]) -> list[tuple[int, int]]`
- Produces: `ImageModelCapabilities(BaseModel)` with `aspect_ratios` and `sizes`.
- Produces: `VideoCreationContract(BaseModel)` with confirmed input fields and optional resolved scene-image fields.
- Produces: `build_video_creation_contract(form_values: Mapping[str, Any]) -> VideoCreationContract`
- Produces: `resolve_scene_image_spec(contract: VideoCreationContract, suggested_ratio: str | None, suggested_size: str | None) -> tuple[VideoCreationContract, list[str]]`

- [ ] **Step 1: Write failing duration and contract tests**

```python
def test_split_video_duration_is_exact_for_all_supported_boundaries():
    for total in (4, 30, 60, 90, 180, 300):
        durations = split_video_duration(total)
        assert sum(durations) == total
        assert all(4 <= item <= 15 for item in durations)


def test_creation_contract_rejects_out_of_range_duration():
    with pytest.raises(ValidationError):
        build_video_creation_contract({**VALID_VIDEO_FORM, "video_duration_sec": 301})


def test_scene_image_spec_is_constrained_to_selected_model_capabilities():
    contract = build_video_creation_contract(VALID_VIDEO_FORM)
    resolved, corrections = resolve_scene_image_spec(contract, "3:4", "8K")
    assert resolved.scene_image_ratio == "9:16"
    assert resolved.scene_image_size == "4K"
    assert corrections
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `cd backend && uv run pytest tests/test_video_creation_contract.py -q`

Expected: collection fails because `pixelflow.creative.duration` and `pixelflow.creative.contract` do not exist.

- [ ] **Step 3: Implement the allocator and models**

Use this allocation rule in `duration.py`:

```python
def split_video_duration(total_seconds: int, preferred_seconds: int = 10) -> list[int]:
    if isinstance(total_seconds, bool) or not isinstance(total_seconds, int) or not 4 <= total_seconds <= 300:
        raise ValueError("video total duration must be an integer between 4 and 300 seconds")
    minimum_count = math.ceil(total_seconds / 15)
    maximum_count = total_seconds // 4
    preferred_count = max(1, round(total_seconds / preferred_seconds))
    count = min(max(preferred_count, minimum_count), maximum_count)
    base, remainder = divmod(total_seconds, count)
    durations = [base + (1 if index < remainder else 0) for index in range(count)]
    if sum(durations) != total_seconds or any(item < 4 or item > 15 for item in durations):
        raise ValueError("unable to allocate exact scene durations")
    return durations
```

In `contract.py`, normalize aliases once, validate required fields, preserve `image_model_capabilities`, and make `resolve_scene_image_spec` prefer the LLM suggestion, then the supported video ratio, then the first available ratio; for size prefer the suggestion, then `4K`, `2K`, `1080p`, then the first available size.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `cd backend && uv run pytest tests/test_video_creation_contract.py -q`

Expected: all contract and duration tests pass.

- [ ] **Step 5: Run formatter and commit the task**

Run: `cd backend && uv run ruff check pixelflow/creative/duration.py pixelflow/creative/contract.py tests/test_video_creation_contract.py`

Commit: `git commit -am "feat: add video creation contract"` after staging the three task files.

---

### Task 2: Intake Form And LLM Extraction

**Files:**
- Modify: `backend/pixelflow/intake/forms.py`
- Modify: `backend/pixelflow/intake/llm.py`
- Modify: `backend/tests/test_intake_forms.py`
- Modify: `backend/tests/test_intake_llm.py`
- Modify: `backend/tests/test_pixelflow_intake_router.py`

**Interfaces:**
- Consumes: `build_video_creation_contract()` from Task 1 for final backend validation.
- Produces video form fields: `video_duration_sec`, `video_ratio`, `video_model_mode`, `video_model`, `image_model`, `image_model_capabilities`, `video_usage`, `visual_style`.
- LLM intent output `values` may contain `video_duration_sec`, `video_ratio`, `video_model`, `image_model`, `video_usage`, and `visual_style`.

- [ ] **Step 1: Extend tests before production code**

Add assertions that the video schema contains all new required fields, `video_duration_sec=30`, `video_ratio=9:16`, `video_model_mode=system_recommended`, `video_model=seedance-2.0`, and `image_model=gpt-image-2`. Add LLM and fallback cases for:

```python
prompt = "用 seedance-2.0 和 gpt-image-2 做一个180秒、16:9、电影写实风的新品宣传视频"
assert result.values["video_duration_sec"] == 180
assert result.values["video_ratio"] == "16:9"
assert result.values["video_model"] == "seedance-2.0"
assert result.values["image_model"] == "gpt-image-2"
assert result.values["video_usage"] == "新品宣传"
assert result.values["visual_style"] == "电影写实风"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `cd backend && uv run pytest tests/test_intake_forms.py tests/test_intake_llm.py tests/test_pixelflow_intake_router.py -q`

Expected: failures show the new fields are absent from schema and normalized LLM values.

- [ ] **Step 3: Add schema fields and normalization**

Add `select` to `FormField.type`, add defaults, and preserve `image_model_capabilities` as a structured value during `_normalize_values`. Make every new video field except `visual_style` required. Validate the normalized video form through `build_video_creation_contract` so direct API clients cannot bypass the 4-300 rule.

- [ ] **Step 4: Extend the intent prompt and fallback extraction**

The JSON contract in `_intent_prompt()` must explicitly distinguish `video_model` from `image_model`. Add deterministic extraction for durations, ratios, Seedance model names, common image model names, video usage, and visual style while preserving LLM-first behavior.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_intake_forms.py tests/test_intake_llm.py tests/test_pixelflow_intake_router.py -q`

Expected: all selected intake tests pass.

- [ ] **Step 6: Commit the task**

Commit message: `feat: collect video creation contract fields`.

---

### Task 3: Dynamic Video And Image Model Form UI

**Files:**
- Create: `web/src/lib/videoRequirementConfig.ts`
- Create: `web/tests/videoRequirementConfig.test.mjs`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/components/composer/GenParamsDialog.tsx`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/tests/mainFlowContract.test.mjs`
- Modify: `web/package.json`

**Interfaces:**
- Produces: `listVideoGenerateModelConfigs()` calling `/api/modelParamConfig/listByCategory/video_generate`.
- Reuses: `listImageGenerateModelConfigs()` for `image_generate`.
- Produces: `filterSeedanceConfigs(configs)`, `resolveVideoModel(configs, requested)`, `resolveImageModel(configs, requested)`, `videoRatios(config)`, and `imageModelCapabilities(config)`.
- Extends `VideoRequirementForm` with all contract fields, including structured `image_model_capabilities`.

- [ ] **Step 1: Write the pure model-selection tests**

```javascript
test("filters non-seedance video models and defaults to seedance-2.0", () => {
  const filtered = filterSeedanceConfigs(CONFIGS);
  assert.deepEqual(filtered.map((item) => item.modelType), ["seedance-2.0", "seedance-2.0-mini"]);
  assert.equal(resolveVideoModel(filtered, "").modelType, "seedance-2.0");
});

test("image model capabilities are submitted but ratio and quality are not user fields", () => {
  const capabilities = imageModelCapabilities(IMAGE_CONFIG);
  assert.deepEqual(capabilities.aspect_ratios, ["1:1", "16:9", "9:16"]);
  assert.deepEqual(capabilities.sizes, ["1080p", "2K", "4K"]);
});
```

- [ ] **Step 2: Run the web test and verify RED**

Run: `cd web && node --test tests/videoRequirementConfig.test.mjs`

Expected: module import fails because `videoRequirementConfig.ts` does not exist.

- [ ] **Step 3: Implement the pure configuration module and API method**

Keep content-app response parsing tolerant of both direct arrays and wrapped `data`. Export a fallback Seedance config and fallback `gpt-image-2` config with exact ratio/size capabilities from the approved spec.

- [ ] **Step 4: Build the video form controls**

In `GenParamsDialog.tsx`:

- Load video and image configs in parallel when a video dialog opens.
- Render labels `视频总时长`, `视频画幅`, `视频模型`, `图片模型`, `视频用途`, `视觉风格`.
- Render duration presets `30/60/90/180/自定义`.
- Show an integer input only for custom duration; set `min=4`, `max=300`, `step=1`; disable submit for empty, fractional, or out-of-range values.
- Use select controls for video model, video ratio, and image model.
- Display the resolved model beside “系统推荐模型”.
- Never render scene-image ratio or scene-image quality controls.
- Submit the selected image model capability lists in `image_model_capabilities`.

- [ ] **Step 5: Persist the normalized values in Workspace**

Ensure `initialValuesFromIntake`, `valuesFromForm`, `FlowDraft`, conversation restore, and direction-job context preserve the complete video form. User-confirmed values override intake values.

- [ ] **Step 6: Run web unit tests and TypeScript build**

Run:

```bash
cd web
node --test tests/videoRequirementConfig.test.mjs
node --test tests/mainFlowContract.test.mjs
npm run build-dev
```

Expected: tests pass and Vite build exits 0.

- [ ] **Step 7: Commit the task**

Commit message: `feat: add dynamic video requirement form`.

---

### Task 4: Separate Plan Templates And LLM Planning

**Files:**
- Delete: `backend/skills/public/borgrise-creative-assistant-v2/templates/plan.md`
- Create from `/Users/wu-bob/Documents/plan_video.md`: `backend/skills/public/borgrise-creative-assistant-v2/templates/plan_video.md`
- Create from `/Users/wu-bob/Documents/plan_image.md`: `backend/skills/public/borgrise-creative-assistant-v2/templates/plan_image.md`
- Create: `backend/pixelflow/creative/plan_llm.py`
- Modify: `backend/pixelflow/creative/plan_markdown.py`
- Modify: `backend/app/gateway/routers/pixelflow_planning.py`
- Modify: `backend/tests/test_creative_plan_markdown.py`
- Modify: `backend/tests/test_pixelflow_planning_router.py`

**Interfaces:**
- Consumes: `split_video_duration`, `scene_time_ranges`, `VideoCreationContract`, and `resolve_scene_image_spec`.
- Produces: `async build_plan_markdown_with_llm(...) -> PlanMarkdownResult`.
- Produces: `async revise_plan_markdown_with_llm(...) -> PlanMarkdownResult`.
- Extends `PlanMarkdownResult` and API response with `plan_version`, `plan_history`, `creation_contract`, `scene_durations_sec`, `llm_used`, `model_name`, and `error`.
- Adds: `POST /agent/flows/planning/plan/revise`.
- Adds: `POST /agent/flows/planning/plan/restore`.

- [ ] **Step 1: Write failing template, contract, LLM, revision, and restore tests**

Cover these exact outcomes:

```python
assert video_result.template_path.name == "plan_video.md"
assert image_result.template_path.name == "plan_image.md"
assert video_result.creation_contract["scene_image_ratio"] in ["9:16", "1:1"]
assert video_result.creation_contract["scene_image_size"] in ["2K", "4K"]
assert sum(video_result.scene_durations_sec) == 180
assert video_result.plan_version == 1
assert revised.plan_version == 2
assert restored.plan_version == 3
assert restored.restored_from_version == 1
```

Use fake model responses that include `plan_markdown`, `scene_image_ratio`, and `scene_image_size`; also test an invalid `3:4/8K` response is corrected against capabilities.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `cd backend && uv run pytest tests/test_creative_plan_markdown.py tests/test_pixelflow_planning_router.py -q`

Expected: tests fail on missing template paths, LLM methods, response fields, and endpoints.

- [ ] **Step 3: Install the two uploaded templates and remove the old template**

Preserve the uploaded UTF-8 content as the canonical structural examples. The LLM prompt must state that all product names, people, claims, and scene content from the example must be replaced by current request data.

- [ ] **Step 4: Implement LLM planning and deterministic fallback**

`plan_llm.py` must call `deepseek-v4-pro` in a thread, request JSON, and validate required Markdown sections. For video planning, pass an exact scene timeline generated by `split_video_duration`; require the Markdown to include video model, image model, scene-image ratio, and scene-image quality. The fallback builder must produce the same sections and exact timeline without sample-entity leakage.

- [ ] **Step 5: Implement versioned revise and restore endpoints**

`/plan/revise` only modifies the current Plan and returns the next version. `/plan/restore` copies the selected historical content into a new current version and records `restored_from_version`; neither endpoint returns creative directions.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_creative_plan_markdown.py tests/test_pixelflow_planning_router.py -q`

Expected: all planning tests pass.

- [ ] **Step 7: Commit the task**

Commit message: `feat: add llm plan templates and versions`.

---

### Task 5: Plan Revision Choice And Rollback UI

**Files:**
- Create: `web/src/components/composer/PlanRevisionDialog.tsx`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/chat.ts`
- Modify: `web/src/components/chat/MessageBubble.tsx`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/tests/mainFlowContract.test.mjs`

**Interfaces:**
- Adds artifact fields `planVersion`, `planHistory`, `creationContract`, and `restoredFromVersion`.
- Adds callbacks `onRollbackPlan(message, version)` and Plan revision mode confirmation.
- Adds API methods `revisePlanMarkdown()` and `restorePlanMarkdown()`.

- [ ] **Step 1: Add failing source-contract tests**

Assert that the revision dialog exposes exactly two options, defaults to `extend_current`, Workspace calls the Plan revision API for that mode, only calls creative directions for `regenerate_directions`, and MessageBubble displays `plan.md vN` plus rollback when history exists.

- [ ] **Step 2: Run the contract test and verify RED**

Run: `cd web && node --test tests/mainFlowContract.test.mjs`

Expected: assertions fail because revision mode and rollback do not exist.

- [ ] **Step 3: Implement the dialog and state machine**

Clicking “继续修改” keeps the source artifact. The next user feedback opens `PlanRevisionDialog`; no intake or direction job starts before the user chooses. `extend_current` calls `/plan/revise`; `regenerate_directions` calls the existing directions job with revision feedback. Persist pending revision state and version history in the original conversation.

- [ ] **Step 4: Implement rollback**

Show rollback only on the latest Plan artifact with history. Calling restore pushes a new Plan artifact with incremented version and preserves all earlier versions.

- [ ] **Step 5: Run test and build**

Run:

```bash
cd web
node --test tests/mainFlowContract.test.mjs
npm run build-dev
```

Expected: test and build pass.

- [ ] **Step 6: Commit the task**

Commit message: `feat: add versioned plan revision flow`.

---

### Task 6: Vendor And Apply The Seedance Shot Prompt Skill

**Files:**
- Create from ZIP: `backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/SKILL.md`
- Create: `backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/THIRD_PARTY_NOTICE.md`
- Create: `backend/pixelflow/generate/seedance_prompt.py`
- Modify: `backend/pixelflow/generate/scene_packages.py`
- Modify: `backend/tests/test_video_scene_packages.py`
- Create: `backend/tests/test_seedance_prompt_skill.py`
- Modify: `backend/skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py`

**Interfaces:**
- Produces: `load_seedance_guidance() -> str`.
- Produces: `build_seedance_shot_prompt(...) -> str`.
- Consumes the final production contract and exact timeline from Tasks 1 and 4.

- [ ] **Step 1: Write failing Skill and prompt tests**

Tests must assert the vendored Skill exists, contains Seedance 2.0 time-stamp and `@` reference guidance, and that runtime prompts include exact seconds, plan content, visual style, available asset ids, video ratio, and the nine-reference limit.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && uv run pytest tests/test_seedance_prompt_skill.py tests/test_video_scene_packages.py skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py -q`

Expected: missing Skill/runtime adapter failures.

- [ ] **Step 3: Vendor the ZIP content and add the adapter**

Copy the ZIP's `.claude/skills/seedance/SKILL.md` into the project Skill directory. The archive has no standalone license file, so add `THIRD_PARTY_NOTICE.md` recording the source repository named in its README and the README's MIT license declaration without inventing missing copyright metadata. `load_seedance_guidance()` reads the Skill file and extracts the platform limits, time-stamp method, camera vocabulary, sound/dialogue rules, and reference rules for the LLM prompt.

- [ ] **Step 4: Replace scene-package prompt rules with the adapter**

Keep one LLM call for the scene package, but require every `scene_packages[i].shot_description.text` to follow the Seedance guidance for its exact duration. Preserve existing `@asset_id` normalization, mentions, and maximum nine references. Remove the 18-scene cap and use Task 1's allocator.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_seedance_prompt_skill.py tests/test_video_scene_packages.py skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the task**

Commit message: `feat: apply seedance shot prompt skill`.

---

### Task 7: Propagate Final Contract Into Scene Assets And Scene Videos

**Files:**
- Modify: `backend/pixelflow/generate/scene_assets.py`
- Modify: `backend/app/gateway/routers/pixelflow_video.py`
- Modify: `backend/tests/test_scene_assets.py`
- Modify: `backend/tests/test_pixelflow_video_router.py`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/chat.ts`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/tests/mainFlowContract.test.mjs`

**Interfaces:**
- `PrepareScenePackagesRequest` gains `creation_contract` and derives target duration from it.
- `GenerateSceneAssetsRequest` gains `image_ratio`, `image_size`, and `model` from final contract.
- Scene video job receives `ratio`, `size`, `model`, `sound` from final contract.

- [ ] **Step 1: Write failing propagation tests**

Assert all character, scene, prop text-to-image and reference-image calls use the Plan-resolved `model="gpt-image-2"`, `ratio="9:16"`, and `size="4K"`. Assert a 180-second prepare request returns exact scene durations. Assert scene-video calls receive `model="seedance-2.0"`, `ratio="9:16"`, and their exact 4-15 second durations.

- [ ] **Step 2: Run backend and web tests and verify RED**

Run:

```bash
cd backend && uv run pytest tests/test_scene_assets.py tests/test_pixelflow_video_router.py -q
cd ../web && node --test tests/mainFlowContract.test.mjs
```

Expected: current hard-coded `1:1`, `9:16`, `seeddream-5.0`, `2K`, and legacy provider-duration conversion break the new assertions.

- [ ] **Step 3: Remove scene-asset hard coding**

Change `generate_scene_assets()` to accept one planned ratio, quality, and model and use them for character, scene, prop, text-to-image, and reference-image calls. Preserve reference fallback and quota handling.

- [ ] **Step 4: Use exact provider duration**

Replace `_provider_video_duration_seconds` legacy 5/10 mapping with exact integer seconds from `duration_ms`; reject values outside 4-15 rather than changing them.

- [ ] **Step 5: Propagate contract through frontend jobs**

On Plan approval, use `artifact.plan.creation_contract` for `target_duration_ms`, scene asset model/ratio/size, and scene video model/ratio/size/sound. Save the contract in every pending scene/video job so restore does not recalculate it.

- [ ] **Step 6: Run focused tests and build**

Run:

```bash
cd backend && uv run pytest tests/test_scene_assets.py tests/test_pixelflow_video_router.py -q
cd ../web && node --test tests/mainFlowContract.test.mjs && npm run build-dev
```

Expected: tests and build pass.

- [ ] **Step 7: Commit the task**

Commit message: `feat: enforce plan contract in video generation`.

---

### Task 8: Align All Five Video Request Bodies With content-app DTOs

**Files:**
- Modify: `backend/pixelflow/skills/borgrise/run_generation.py`
- Modify: `backend/pixelflow/skills/borgrise/skill.py`
- Modify: `backend/pixelflow/skills/base.py`
- Create: `backend/tests/test_borgrise_video_payloads.py`
- Modify: `backend/tests/test_borgrise_project_id.py`

**Interfaces:**
- Keeps the existing public Skill method names.
- Guarantees exact request keys for text, image, two-image, reference, and edit video endpoints.

- [ ] **Step 1: Write failing payload-capture tests**

Monkeypatch `make_request`, call each wrapper with `auto_poll=False`, and assert exact dictionaries. The image-to-video expected payload must not contain `negative_prompt` or `seed`.

- [ ] **Step 2: Run payload tests and verify RED**

Run: `cd backend && uv run pytest tests/test_borgrise_video_payloads.py tests/test_borgrise_project_id.py -q`

Expected: image-to-video fails due to unsupported extra keys and Seedance duration validation fails for 4/15 seconds.

- [ ] **Step 3: Align request data and validation**

Use exactly:

```python
text_payload = {"prompt": prompt, "model": model, "ratio": ratio, "size": size, "duration": duration, "videoCount": video_count, "sound": sound}
image_payload = {"image_url": image_url, "prompt": prompt, "duration": duration, "ratio": ratio, "model": model, "size": size, "sound": sound, "videoCount": video_count}
```

Build the other three payloads in the key sets from the approved design. Permit integer 4-15 durations for Seedance 2.0 models.

- [ ] **Step 4: Run payload tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_borgrise_video_payloads.py tests/test_borgrise_project_id.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the task**

Commit message: `fix: align content app video payloads`.

---

### Task 9: Documentation And Full Automated Regression

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Modify: `CONTENT_APP_API_CALLS.md`

**Interfaces:**
- Documents both model-config calls, two-phase creation contract, Plan LLM image-spec resolution, separate Plan templates, Plan versions, Seedance Skill, exact duration rules, and five video DTOs.

- [ ] **Step 1: Update documentation with actual implemented names and paths**

Record `video_model`, `image_model`, `image_model_capabilities`, `scene_image_ratio`, `scene_image_size`, the new planning endpoints, template paths, and Seedance Skill path. Remove references to the deleted `templates/plan.md`.

- [ ] **Step 2: Run backend feature regression**

Run:

```bash
cd backend
uv run pytest \
  tests/test_video_creation_contract.py \
  tests/test_intake_forms.py \
  tests/test_intake_llm.py \
  tests/test_pixelflow_intake_router.py \
  tests/test_creative_plan_markdown.py \
  tests/test_pixelflow_planning_router.py \
  tests/test_seedance_prompt_skill.py \
  tests/test_video_scene_packages.py \
  tests/test_scene_assets.py \
  tests/test_pixelflow_video_router.py \
  tests/test_borgrise_video_payloads.py \
  -q
uv run ruff check pixelflow app/gateway/routers tests/test_video_creation_contract.py tests/test_seedance_prompt_skill.py tests/test_borgrise_video_payloads.py
```

Expected: zero test failures and zero Ruff errors.

- [ ] **Step 3: Run frontend regression and build**

Run:

```bash
cd web
node --test tests/videoRequirementConfig.test.mjs
node --test tests/mainFlowContract.test.mjs
node --test tests/conversationRouting.test.mjs
node --test tests/scenePackages.test.mjs
npm run build-dev
```

Expected: zero failures and Vite build exit 0.

- [ ] **Step 4: Verify repository consistency**

Run:

```bash
git diff --check
rg -n "templates/plan\.md" README.md AGENTS.md docs CONTENT_APP_API_CALLS.md backend web || true
```

Expected: no whitespace errors and no live code/docs reference to the deleted template.

- [ ] **Step 5: Commit documentation and regression updates**

Commit message: `docs: document video creation contract flow`.

---

### Task 10: Real Image And Video Flow Verification

**Files:**
- Do not persist credentials or generated response dumps in the repository.
- Store temporary request/response captures under `/tmp/pixelflow-creation-contract-verification/` with tokens redacted.

**Interfaces:**
- Uses the local PixelFlow backend with test configuration and the user-provided Bearer token supplied only through an environment variable.
- Uses `/agent/flows/...` APIs and content-app asynchronous task polling.

- [ ] **Step 1: Start local backend and frontend**

Run the backend with `backend/config.dev.yml`, set `CONTENT_APP_AUTHORIZATION` only in the process environment, and start the frontend on a free local port. Confirm `/agent/flows/intake/forms/video` responds before testing.

- [ ] **Step 2: Verify the 180-second contract without paid generation**

Run intake, form validation, creative directions, planning, and scene-package core preparation for a 180-second 9:16 video using `seedance-2.0` and `gpt-image-2`. Assert the Plan includes both models and the LLM-selected legal image ratio/quality; assert every scene is 4-15 seconds and the sum is exactly 180.

- [ ] **Step 3: Complete a paid minimal video flow**

Use a custom 4-second product video to minimize charge while exercising every stage: intake, form, directions, Plan, Plan approval, one scene package, planned scene assets, one scene video, and single-scene merge bypass. Record the final video URL, task id, models, ratios, sizes, and observed content with the token redacted.

- [ ] **Step 4: Exercise Plan revision modes**

Revise the video Plan with “把风格改成电影写实并加强运镜”, choose current-creative extension, and confirm Plan v2 appears without directions. Then use a separate test conversation to choose “重新生成新创意” and confirm exactly three directions appear. Restore v1 and confirm a new current version is created.

- [ ] **Step 5: Complete a paid minimal image flow**

Run a single-image request through intake, directions, the image Plan template, approval, and final image generation. Confirm output content matches the selected direction and current Plan.

- [ ] **Step 6: Inspect final media and capture verification evidence**

Open the generated image and play the generated video. Confirm visible subject, requested style, 9:16 output where requested, and no sample-template entity leakage. Report exact URLs and any provider-side limitations to the user.

- [ ] **Step 7: Run final fresh verification**

Re-run the Task 9 backend regression, frontend regression, build, and `git diff --check` after any real-flow fixes. Only then report completion.
