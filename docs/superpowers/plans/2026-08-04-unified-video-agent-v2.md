

# Unified Video Agent V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace and retire the video-specific V1 fixed workflow with a DeepSeek-powered, tool-using VideoAgent that persists its workspace and visible execution timeline.

**Architecture:** A thin cross-domain router assigns video turns to `VideoAgent`. `VideoAgent` reads a persistent `VideoWorkspace`, selects registered Skill guidance and controlled tools, stores `AgentPlan` / `AgentPlanStep`, and delegates async work to the existing Agent Runtime operation coordinator. Reusable lower-level video capabilities are called through V2 adapters; the V1 video handler state machine and its UI are removed.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy/Alembic, LangGraph/LangChain, DeepSeek via `ChatOpenAI`, React 19, TypeScript, Vite, Node test runner.

## Global Constraints

- The V2 default model is `deepseek-v4-pro`; Kimi K3 is not part of this implementation.
- Preserve existing user isolation, event outbox ordering, operation idempotency, lease recovery, and quota authorization.
- The model may choose only server-registered tools; it never receives direct provider, database, FFmpeg, or shell tools.
- `Plan.md` is an optional script artifact, never a mandatory entry gate.
- New V2 feature code must not be added to `backend/pixelflow/agent_workflows/video/` or `web/src/pages/WorkspacePage.tsx`.
- Delete the V1 video workflow, V1 video Supervisor action path, and old Workspace implementation after their V2 replacements pass acceptance tests. `WorkspacePage.tsx` must end as a 100-200 line V2 route/layout shell.
- Persisted step timestamps are the source for displayed duration. Do not expose model chain-of-thought.
- There is one active video mode: `VIDEO_AGENT`. Existing V1 video conversations are archived as historical records and return `video_workflow_retired`; they never resume or migrate in place.
- Migration `20260804_08_video_agent_runtime.py` is additive only: create V2 tables and indexes; never drop, rename, backfill, or mutate V1 rows. Take a production SQLite snapshot and pause writes while applying schema DDL.

---

## File Structure

```text
backend/pixelflow/video_agent/
  contracts/{plan.py,workspace.py,tools.py,__init__.py}
  workspace/{repository.py,evidence.py,__init__.py}
  skills/{catalog.py,__init__.py}
  tools/{registry.py,inspect_workspace.py,script.py,reference.py,scene.py,delivery.py,__init__.py}
  adapters/{video_domain.py,__init__.py}
  planner/{model.py,loop.py,__init__.py}
  executor/{service.py,__init__.py}

backend/pixelflow/agent_runtime/
  contracts/{enums.py,events.py,api.py,__init__.py}
  persistence/{models.py,repositories.py}
  service.py
  executor.py
  config.py

backend/packages/harness/deerflow/persistence/migrations/versions/
  20260804_08_video_agent_runtime.py

web/src/
  features/video-agent/{VideoAgentWorkspace.tsx,AgentPlanTimeline.tsx,AgentConfirmationCard.tsx,SceneEvidencePanel.tsx}
  features/video-agent/hooks/useVideoAgent.ts
  features/video-agent/state/{contracts.ts,reducer.ts}
  pages/WorkspacePage.tsx
```

## Task 1: Replace the Old Workspace Page With a V2 Feature Shell

**Files:**
- Create: `web/src/features/video-agent/VideoAgentWorkspace.tsx`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Delete: `web/src/features/legacy-workspace/LegacyWorkspace.tsx` if it exists from an earlier local refactor
- Test: `web/tests/videoAgentWorkspaceShell.test.mjs`

**Interfaces:**
- Consumes: the existing workspace route props and V2 workspace snapshot API.
- Produces: `export function VideoAgentWorkspace(): JSX.Element` and a default page shell that renders only the V2 feature.

- [ ] **Step 1: Write the failing shell-size and export test**

```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../src/pages/WorkspacePage.tsx", import.meta.url), "utf8");
assert.match(page, /VideoAgentWorkspace/);
assert.ok(page.split("\n").length <= 200);
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && node --test tests/videoAgentWorkspaceShell.test.mjs`

Expected: FAIL because the V2 feature shell does not exist and `WorkspacePage.tsx` exceeds 200 lines.

- [ ] **Step 3: Replace the legacy page body with the V2 feature shell**

Delete the old page-local state, effects, handlers, and V1 workflow UI. Keep only route/layout concerns in `WorkspacePage.tsx`; place V2 workspace state and UI in `VideoAgentWorkspace.tsx`. Do not preserve an old-workspace fallback.

```tsx
// web/src/pages/WorkspacePage.tsx
import { VideoAgentWorkspace } from "@/features/video-agent/VideoAgentWorkspace";

export default function WorkspacePage() {
  return <VideoAgentWorkspace />;
}
```

- [ ] **Step 4: Run V2 shell tests and type checking**

Run: `cd web && node --test tests/videoAgentWorkspaceShell.test.mjs && npm run lint`

Expected: PASS; the V2 workspace shell compiles and no legacy workspace import remains.

- [ ] **Step 5: Commit the extraction**

```bash
git add -A web/src/pages/WorkspacePage.tsx web/src/features/video-agent web/tests/videoAgentWorkspaceShell.test.mjs
git commit -m "refactor: replace legacy workspace shell"
```

## Task 2: Add V2 Wire Contracts and Event Types

**Files:**
- Create: `backend/pixelflow/video_agent/contracts/{plan.py,workspace.py,tools.py,__init__.py}`
- Modify: `backend/pixelflow/agent_runtime/contracts/{enums.py,events.py,api.py,__init__.py}`
- Create: `backend/tests/test_video_agent_contracts.py`
- Create: `web/src/features/video-agent/state/{contracts.ts,reducer.ts,workspaceProjection.ts}`
- Create: `web/tests/videoAgentContracts.test.mjs`

**Interfaces:**
- Produces: `VideoWorkspace`, `AgentPlan`, `AgentPlanStep`, `VideoToolCall`, `VideoToolResult`, `AgentPlanStatus`, `PlanStepStatus`, and the sole `OrchestrationMode.VIDEO_AGENT` value.
- Produces event values `agent.plan.created`, `agent.step.started`, `agent.step.progressed`, `agent.step.completed`, `agent.step.failed`, and `agent.confirmation.requested`.

- [ ] **Step 1: Write failing Python contract tests**

```python
def test_completed_step_requires_timestamps_and_duration_source():
    step = AgentPlanStep(
        step_id="step-1", plan_id="plan-1", sequence=1,
        tool_name="inspect_video_workspace", title="读取项目",
        status=PlanStepStatus.COMPLETED,
        started_at=datetime(2026, 8, 4, tzinfo=UTC),
        completed_at=datetime(2026, 8, 4, 0, 0, 3, tzinfo=UTC),
    )
    assert step.duration_ms == 3000
```

- [ ] **Step 2: Write failing TypeScript event parsing tests**

```js
assert.equal(parseAgentEvent({ type: "agent.step.completed", payload: stepPayload }).type,
  "agent.step.completed");
assert.equal(projectVideoAgentDuration(stepPayload, new Date("2026-08-04T00:00:03Z")), 3000);
```

- [ ] **Step 3: Implement strict contracts and matching wire values**

Use Pydantic frozen models with `extra="forbid"`. Model plan status as `planning`, `running`, `awaiting_confirmation`, `completed`, `failed`, and `cancelled`; model step status as `pending`, `running`, `awaiting_confirmation`, `completed`, `failed`, and `skipped`. Require `completed_at` for terminal steps, require `started_at` for non-pending steps, and expose computed `duration_ms` only from timestamps. Mirror literals exactly in TypeScript.

```python
class VideoToolCall(ContractModel):
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    requires_confirmation: bool = False
```

- [ ] **Step 4: Run contract suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_contracts.py -v`

Run: `cd web && node --test tests/videoAgentContracts.test.mjs && npm run test:agent-runtime-contracts`

Expected: PASS with Python and TypeScript values agreeing on every enum and event name.

- [ ] **Step 5: Commit contracts**

```bash
git add backend/pixelflow/video_agent backend/pixelflow/agent_runtime/contracts backend/tests/test_video_agent_contracts.py web/src/lib/supervisor web/tests/videoAgentContracts.test.mjs
git commit -m "feat: add video agent contracts"
```

## Task 3: Persist Video Workspaces, Plans, and Steps

**Files:**
- Create: `backend/packages/harness/deerflow/persistence/migrations/versions/20260804_08_video_agent_runtime.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/{models.py,repositories.py}`
- Create: `backend/pixelflow/video_agent/workspace/repository.py`
- Create: `backend/tests/test_video_agent_repository.py`

**Interfaces:**
- Produces repository methods `create_workspace`, `get_workspace`, `save_plan`, `start_step`, `complete_step`, `fail_step`, and `list_plan_steps`.
- All methods take `user_id` and reject cross-user access.

- [ ] **Step 1: Write failing repository tests for ownership, idempotency, and duration**

```python
async def test_complete_step_persists_timestamps_and_rejects_other_user(repository):
    await repository.create_workspace("u1", workspace)
    await repository.save_plan("u1", plan)
    started = await repository.start_step("u1", "plan-1", "step-1", now=t0)
    completed = await repository.complete_step("u1", "plan-1", "step-1", result, now=t3)
    assert completed.duration_ms == 3000
    assert await repository.get_workspace("u2", workspace.workspace_id) is None
```

- [ ] **Step 2: Run the repository test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_repository.py -v`

Expected: FAIL because V2 rows and repository methods do not exist.

- [ ] **Step 3: Add rows and migration**

Create `pixelflow_video_agent_workspaces`, `pixelflow_video_agent_plans`, and `pixelflow_video_agent_plan_steps`. Store typed business payloads as JSON snapshots, use `(user_id, workspace_id)` and `(plan_id, sequence)` indexes, and use a unique `(plan_id, step_id)` identity. The migration must include upgrade and downgrade operations and follow the naming style in `20260802_07_operation_quota_revision.py`.

- [ ] **Step 4: Implement atomic repository transitions**

`start_step` changes only `pending -> running`; `complete_step` and `fail_step` accept only `running`; repeated calls with the same terminal snapshot are idempotent; conflicting payloads raise the existing runtime conflict error. Emit no events in this task; persistence is committed before Task 4 adds outbox events.

- [ ] **Step 5: Run migration and repository suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_agent_runtime_migration.py tests/test_video_agent_repository.py -v`

Expected: PASS for SQLite and existing migration compatibility fixtures.

- [ ] **Step 6: Commit persistence**

```bash
git add backend/packages/harness/deerflow/persistence/migrations/versions/20260804_08_video_agent_runtime.py backend/pixelflow/agent_runtime/persistence backend/pixelflow/video_agent/workspace backend/tests/test_video_agent_repository.py
git commit -m "feat: persist video agent plans"
```

## Task 4: Publish Durable Plan-Step Events and Project Them in the UI

**Files:**
- Modify: `backend/pixelflow/agent_runtime/persistence/repositories.py`
- Create: `backend/pixelflow/video_agent/executor/events.py`
- Modify: `web/src/lib/supervisor/{reducer.ts,workspaceProjection.ts}`
- Create: `web/src/features/video-agent/state/{contracts.ts,reducer.ts}`
- Create: `backend/tests/test_video_agent_plan_events.py`
- Create: `web/tests/videoAgentTimelineReducer.test.mjs`

**Interfaces:**
- Produces `publish_plan_created`, `publish_step_started`, `publish_step_progressed`, `publish_step_completed`, `publish_step_failed`, and `publish_confirmation_requested`.
- Produces `VideoAgentTimelineState` keyed by `planId` and `stepId`.

- [ ] **Step 1: Write failing backend outbox tests**

```python
async def test_step_completion_writes_ordered_outbox_event(repository):
    await repository.complete_step("u1", "plan-1", "step-1", result, now=t3)
    events = await repository.list_events("u1", conversation_id)
    assert events[-1].type is AgentEventType.AGENT_STEP_COMPLETED
    assert events[-1].payload["duration_ms"] == 3000
```

- [ ] **Step 2: Write failing reducer tests**

```js
const next = reduceVideoAgentEvent(initial, completedEvent);
assert.equal(next.plans["plan-1"].steps["step-1"].status, "completed");
assert.equal(next.plans["plan-1"].steps["step-1"].durationMs, 3000);
```

- [ ] **Step 3: Implement transactional event publication**

Construct agent events only after the corresponding workspace/plan/step write succeeds in the same repository transaction. Event payloads contain IDs, title, status, result summary, artifact references, `started_at`, `completed_at`, and `duration_ms`; they never contain prompt internals or model reasoning.

- [ ] **Step 4: Implement frontend event projection**

Parse the six V2 event values in the V2 feature state module. Derive elapsed time from `startedAt` in the renderer for running steps; store completed duration from the backend event to keep reconnect behavior deterministic.

- [ ] **Step 5: Run event suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_plan_events.py -v`

Run: `cd web && node --test tests/videoAgentTimelineReducer.test.mjs tests/supervisorEvents.test.mjs`

Expected: PASS and monotonic event order preserved.

- [ ] **Step 6: Commit event timeline foundation**

```bash
git add backend/pixelflow/agent_runtime/persistence/repositories.py backend/pixelflow/video_agent/executor/events.py backend/tests/test_video_agent_plan_events.py web/src/features/video-agent/state web/tests/videoAgentTimelineReducer.test.mjs
git commit -m "feat: publish video agent step timeline"
```

## Task 5: Implement the Skill Catalog and Controlled Tool Registry

**Files:**
- Create: `backend/pixelflow/video_agent/skills/catalog.py`
- Create: `backend/pixelflow/video_agent/tools/{registry.py,inspect_workspace.py,__init__.py}`
- Create: `backend/tests/test_video_agent_tool_registry.py`

**Interfaces:**
- Produces `VideoToolSpec`, `VideoToolRegistry`, and `VideoTool.execute(context, arguments) -> VideoToolResult`.
- Initial registered tool: `inspect_video_workspace`.

- [ ] **Step 1: Write failing registry tests**

```python
def test_registry_exposes_only_declared_tools():
    registry = VideoToolRegistry([InspectVideoWorkspaceTool()])
    assert registry.names() == ("inspect_video_workspace",)
    assert registry.resolve("delete_database") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_tool_registry.py -v`

Expected: FAIL because the registry does not exist.

- [ ] **Step 3: Implement metadata-first Skill selection and tool validation**

`SkillCatalog` loads enabled `SKILL.md` metadata through the existing DeerFlow storage API and returns only applicable manifests. `VideoToolSpec` contains `name`, `description`, JSON-schema-compatible input model, `cost_level`, `confirmation_required`, `idempotency_mode`, and `recovery_mode`. Define `VideoToolValidationError(ValueError)` for user-correctable missing or invalid input and map it to a structured tool result rather than an unhandled runtime failure. `InspectVideoWorkspaceTool` returns a compact evidence summary and artifact refs, never raw provider credentials or full hidden payloads.

- [ ] **Step 4: Run registry tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_tool_registry.py -v`

Expected: PASS; unknown tools and invalid arguments are rejected before execution.

- [ ] **Step 5: Commit the catalog and registry**

```bash
git add backend/pixelflow/video_agent/skills backend/pixelflow/video_agent/tools backend/tests/test_video_agent_tool_registry.py
git commit -m "feat: add video agent tool registry"
```

## Task 6: Implement DeepSeek Agent Planning and Bounded Tool Loop

**Files:**
- Create: `backend/pixelflow/video_agent/planner/{model.py,loop.py,__init__.py}`
- Create: `backend/pixelflow/video_agent/executor/service.py`
- Create: `backend/tests/test_video_agent_planner.py`
- Create: `backend/tests/test_video_agent_executor.py`

**Interfaces:**
- Produces `VideoAgentPlanner.plan_turn(context) -> AgentPlan`, `VideoAgentExecutor.run_plan(user_id, plan_id) -> AgentPlan`, `confirm_step(user_id, plan_id, step_id) -> AgentPlan`, and `resume_plan(user_id, plan_id) -> AgentPlan`.
- Consumes `VideoToolRegistry`, `VideoWorkspaceRepository`, and the existing `create_chat_model(name="deepseek-v4-pro")` factory.

- [ ] **Step 1: Write failing planner tests with a fake structured model**

```python
async def test_planner_turn_for_reference_video_starts_with_analysis(fake_model, executor):
    plan = await executor.plan_turn(user_id="u1", content="参考这个视频，换成我的商品", materials=[reference])
    assert [step.tool_name for step in plan.steps][:2] == [
        "inspect_video_workspace", "analyze_reference_video"
    ]
```

- [ ] **Step 2: Write failing loop stop-condition tests**

```python
async def test_executor_stops_before_billable_tool_until_confirmation(executor):
    plan = await executor.run_plan("u1", "plan-1")
    assert plan.status is AgentPlanStatus.AWAITING_CONFIRMATION
    assert plan.steps[-1].tool_name == "generate_scenes"
```

- [ ] **Step 3: Implement a typed model boundary**

Use `with_structured_output` for a plan proposal schema. The proposal may contain only registered tool names. Limit one turn to eight tool calls and two model repair attempts. Tool output is appended as a typed, compact result record before the next model call. Do not persist hidden reasoning; persist the plan and public tool summaries only.

- [ ] **Step 4: Implement plan execution and confirmation gate**

For each step, persist `running`, publish the start event, execute one tool, persist/publish terminal result, then continue. Stop and open a confirmation request before any tool whose spec requires confirmation. `confirm_step` records a valid approval against the persisted step and re-enters the plan; `resume_plan` recovers a persisted plan after reconnect/restart and reclaims only eligible work through existing leases and idempotency keys.

- [ ] **Step 5: Run planner and executor tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_planner.py tests/test_video_agent_executor.py -v`

Expected: PASS; no unknown tool is called, no billable tool executes before confirmation, and plan resume starts at the pending step.

- [ ] **Step 6: Commit the agent loop**

```bash
git add backend/pixelflow/video_agent/planner backend/pixelflow/video_agent/executor backend/tests/test_video_agent_planner.py backend/tests/test_video_agent_executor.py
git commit -m "feat: add deepseek video agent loop"
```

## Task 7: Adapt Script, Creative, and Reference Analysis Tools

**Files:**
- Create: `backend/pixelflow/video_agent/tools/{script.py,reference.py}`
- Create: `backend/pixelflow/video_agent/adapters/video_domain.py`
- Create: `backend/tests/test_video_agent_script_tools.py`
- Create: `backend/tests/test_video_agent_reference_tools.py`

**Interfaces:**
- Produces tools `import_script`, `brainstorm_script`, and `analyze_reference_video`.
- Consumes existing `creative/plan_markdown.py`, `creative/brief_generate.py`, `nodes._parse_reference_videos` behavior, and registered decompose skills through an adapter.

- [ ] **Step 1: Write failing mature-script tests**

```python
async def test_import_script_creates_script_artifact_without_plan_review(tool_context):
    result = await ImportScriptTool().execute(tool_context, {"markdown": MATURE_SCRIPT})
    assert result.workspace_patch["script"]["source"] == "user_import"
    assert result.requires_confirmation is False
```

- [ ] **Step 2: Write failing reference-analysis tests**

```python
async def test_reference_analysis_persists_scenes_and_assets(tool_context, fake_decompose_skill):
    result = await AnalyzeReferenceVideoTool().execute(tool_context, {"reference_asset_ref": "artifact:ref-1"})
    assert result.workspace_patch["reference_videos"][0]["storyboard"][0]["scene_id"]
```

- [ ] **Step 3: Implement adapters without importing V1 handlers**

`ImportScriptTool` normalizes a user script into the workspace script artifact and returns missing requirements as a public summary. `BrainstormScriptTool` creates a versioned draft only. `AnalyzeReferenceVideoTool` starts/reuses a durable operation through the existing coordinator, then persists normalized storyboard and asset evidence when complete. It must call only V2 adapters, never the retired V1 handler or Plan-review interrupt.

- [ ] **Step 4: Run script and reference tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_script_tools.py tests/test_video_agent_reference_tools.py tests/test_reference_video_nodes.py -v`

Expected: PASS; imported scripts do not enter a Plan review, and repeated analysis reuses the same operation.

- [ ] **Step 5: Commit first user journeys**

```bash
git add backend/pixelflow/video_agent/tools/script.py backend/pixelflow/video_agent/tools/reference.py backend/pixelflow/video_agent/adapters/video_domain.py backend/tests/test_video_agent_script_tools.py backend/tests/test_video_agent_reference_tools.py
git commit -m "feat: add script and reference video tools"
```

## Task 8: Adapt Asset Replacement, Scene Inspection, and Scoped Generation Tools

**Files:**
- Create: `backend/pixelflow/video_agent/tools/scene.py`
- Modify: `backend/pixelflow/qc/{video_review.py,revision_scope.py}`
- Create: `backend/tests/test_video_agent_scene_tools.py`

**Interfaces:**
- Produces `replace_project_assets`, `inspect_scene`, `patch_scene`, `generate_scenes`, and `review_generated_scenes`.
- `generate_scenes` accepts `{scene_ids: list[str], variant_count: int}` and always requires confirmation when it creates billable operations.

- [ ] **Step 1: Write failing scene inspection and scoped generation tests**

```python
async def test_inspect_scene_returns_repairable_evidence(tool_context):
    result = await InspectSceneTool().execute(tool_context, {"scene_id": "scene-3"})
    assert result.workspace_patch["qc"]["scene-3"]["repair_suggestion"]

async def test_generate_scenes_requires_confirmation_and_scopes_ids(tool_context):
    result = await GenerateScenesTool().execute(tool_context, {"scene_ids": ["scene-3"], "variant_count": 3})
    assert result.requires_confirmation is True
    assert result.preview["scene_ids"] == ["scene-3"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_scene_tools.py -v`

Expected: FAIL because V2 scene tools and scene-level evidence contracts do not exist.

- [ ] **Step 3: Implement scene evidence and patch flow**

Normalize VLM/QC output to `{scene_id, issues, evidence_refs, repair_suggestion, affected_assets}`. `PatchSceneTool` changes only declared mutable scene fields and writes a new workspace revision. `GenerateScenesTool` validates IDs against the workspace, creates one operation per scene/variant after confirmation, and records job IDs in the corresponding plan steps. `ReviewGeneratedScenesTool` selects or rejects variants without silently changing unrelated scenes.

- [ ] **Step 4: Run focused regression suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_scene_tools.py tests/test_video_quality_review.py -v`

Expected: PASS; V2 scoping works and no retired V1 video module is imported.

- [ ] **Step 5: Commit scene tools**

```bash
git add backend/pixelflow/video_agent/tools/scene.py backend/pixelflow/qc/video_review.py backend/pixelflow/qc/revision_scope.py backend/tests/test_video_agent_scene_tools.py
git commit -m "feat: add video agent scene tools"
```

## Task 9: Adapt Composition and Export Tools

**Files:**
- Create: `backend/pixelflow/video_agent/tools/delivery.py`
- Create: `backend/tests/test_video_agent_delivery_tools.py`

**Interfaces:**
- Produces `compose_or_export_video` with `output_type` values `mp4` and `jianying_package`.
- Consumes existing delivery and Jianying skills through the V2 adapter.

- [ ] **Step 1: Write failing delivery tests**

```python
async def test_export_rejects_workspace_with_unresolved_dirty_scenes(tool_context):
    with pytest.raises(VideoToolValidationError, match="dirty_scene_ids"):
        await ComposeOrExportVideoTool().execute(tool_context, {"output_type": "mp4"})
```

- [ ] **Step 2: Implement delivery validation and operations**

Require all selected scenes to have an approved variant and no unresolved QC/dirty state. Use the existing composition/Jianying services behind the adapter. Mark MP4/Jianying creation as billable/confirmation-gated when provider or storage cost is incurred, and persist resulting artifact refs to the workspace.

- [ ] **Step 3: Run delivery tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_delivery_tools.py -v`

Expected: PASS; V2 does not export an inconsistent project and no V1 delivery path remains.

- [ ] **Step 4: Commit delivery tools**

```bash
git add backend/pixelflow/video_agent/tools/delivery.py backend/tests/test_video_agent_delivery_tools.py
git commit -m "feat: add video agent delivery tool"
```

## Task 10: Render V2 Workspace, Plan Timeline, and Confirmation Cards

**Files:**
- Create: `web/src/features/video-agent/{VideoAgentWorkspace.tsx,AgentPlanTimeline.tsx,AgentConfirmationCard.tsx,SceneEvidencePanel.tsx}`
- Create: `web/src/features/video-agent/hooks/useVideoAgent.ts`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Create: `web/tests/videoAgentWorkspace.test.mjs`

**Interfaces:**
- Produces `VideoAgentWorkspace` that consumes `VideoAgentTimelineState` and V2 SSE snapshots.
- Produces a visible step row with title, status, artifact links, start/end time, and duration.

- [ ] **Step 1: Write failing UI contract tests**

```js
assert.match(read("src/features/video-agent/AgentPlanTimeline.tsx"), /durationMs/);
assert.match(read("src/features/video-agent/AgentPlanTimeline.tsx"), /正在/);
assert.match(read("src/pages/WorkspacePage.tsx"), /VideoAgentWorkspace/);
```

- [ ] **Step 2: Implement the timeline and elapsed-time renderer**

Render pending, running, waiting-confirmation, completed, failed, and skipped states with icons and concise copy. For running steps, use a one-second timer only in `AgentPlanTimeline`; calculate `Date.now() - startedAt`. For terminal steps, render the persisted `durationMs`. Never render prompts, raw tool payloads, or hidden reasoning.

- [ ] **Step 3: Implement project evidence and confirmation UI**

`SceneEvidencePanel` displays selected scene media, QC issues, repair suggestion, and related artifact links. `AgentConfirmationCard` displays the public cost summary, affected scenes, and explicit confirm/cancel controls. Its submit action sends the persisted plan-step confirmation ID, not a free-form workflow action.

- [ ] **Step 4: Render V2 from the thin page shell**

Keep only the V2 feature import and layout in `WorkspacePage.tsx`; no mode selection or legacy fallback remains:

```tsx
return <VideoAgentWorkspace />;
```

- [ ] **Step 5: Run frontend tests and build**

Run: `cd web && node --test tests/videoAgentWorkspace.test.mjs tests/videoAgentTimelineReducer.test.mjs && npm run lint && npm run build-dev`

Expected: PASS; V2 UI renders a duration for terminal steps and a live elapsed value for running steps.

- [ ] **Step 6: Commit V2 frontend**

```bash
git add web/src/features/video-agent web/src/pages/WorkspacePage.tsx web/tests/videoAgentWorkspace.test.mjs
git commit -m "feat: add video agent workspace"
```

## Task 11: Make V2 the Only Video Entry and Retire V1

**Files:**
- Modify: `backend/pixelflow/agent_runtime/{config.py,service.py,executor.py}`
- Create: `backend/pixelflow/agent_runtime/video_router.py`
- Delete: `backend/pixelflow/agent_workflows/video/`
- Delete: the V1 video decision/action modules under `backend/pixelflow/agent_runtime/supervisor/`
- Delete: V1 video workflow tests under `backend/tests/test_agent_video_workflow_*.py` and superseded Supervisor-routing tests
- Create: `backend/tests/{test_video_agent_entry.py,test_video_agent_e2e.py,test_video_agent_retirement.py}`

**Interfaces:**
- Produces one active entry, `VideoAgentEntrypoint.submit_turn`, for every video conversation.
- Produces `video_workflow_retired` for a historical V1 workflow ID; the caller can inspect its records but cannot resume or mutate it.

- [ ] **Step 1: Write failing entry, retirement, and recovery tests**

```python
def test_every_new_video_turn_uses_video_agent_entrypoint(app):
    assert app.video_router.resolve("video") is app.video_agent_entrypoint

async def test_historical_v1_workflow_is_read_only(runtime):
    result = await runtime.resume_workflow("old-v1-workflow")
    assert result.code == "video_workflow_retired"

async def test_reference_remix_resumes_after_generation_operation_restart(runtime):
    plan = await runtime.submit("u1", "参考这个视频，把商品换成我的", [reference, product])
    await runtime.confirm_step("u1", plan.plan_id, plan.pending_confirmation_step_id)
    restored = await runtime.resume_plan("u1", plan.plan_id)
    assert restored.steps[-1].status in {PlanStepStatus.RUNNING, PlanStepStatus.COMPLETED}
```

- [ ] **Step 2: Replace V1 routing with the single video entrypoint**

Remove `SUPERVISOR_V1`, `VIDEO_AGENT_V2`, rollout flags, and mode-selection branches. `VideoAgentEntrypoint` resolves every video turn to `VideoAgentExecutor`; non-video routing remains outside this change. Preserve the existing generic runtime operation coordinator, event outbox, quota checks, and ownership checks.

- [ ] **Step 3: Implement historical V1 read-only retirement**

Add a retirement lookup that recognizes existing V1 workflow rows and returns a stable public `video_workflow_retired` result containing only workflow ID, creation time, and historical artifact links. Do not migrate the V1 state payload into V2, restart any V1 job, or issue new provider calls. Existing database rows remain for audit and can be removed later by an explicit data-retention job.

- [ ] **Step 4: Delete V1 implementation and tests**

Remove `backend/pixelflow/agent_workflows/video/`, the V1 video Supervisor action/decision path, its HTTP handlers, and all tests that assert V1 video workflow stages. Remove the legacy Workspace feature and client Supervisor mode reducer. Update imports so reusable planning, scene generation, QC, composition, and Jianying services are reachable only through V2 adapters.

- [ ] **Step 5: Implement V2 snapshot/SSE restoration**

Expose current workspace, active plan, plan steps, open confirmation, and event cursor in the V2 conversation snapshot. Rehydrate frontend state from snapshot before applying live SSE events. Resume pending operations through the existing coordinator and write a new step-progress event instead of recreating the plan.

- [ ] **Step 6: Run focused retirement and full verification**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_entry.py tests/test_video_agent_retirement.py tests/test_video_agent_e2e.py tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_event_outbox.py -v`

Run: `cd web && node --test tests/videoAgentWorkspaceShell.test.mjs tests/videoAgentWorkspace.test.mjs && npm run lint`

Expected: PASS; every video turn takes the V2 entry, retired V1 work cannot execute, and V2 can recover without duplicate billable operations.

- [ ] **Step 7: Run the golden-case evaluation suite**

Create 30-50 fixture-driven cases covering mature scripts, creative ideas, reference remix, scene repair, ambiguous targets, quota confirmation, duplicate submit, and restart recovery. Record expected first tool, confirmation boundary, scoped scene IDs, and terminal outcome. Fail the suite when the DeepSeek planner selects an unregistered tool or starts a billable step before confirmation.

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_evaluation.py -v`

Expected: PASS with the recorded baseline before deleting V1 production paths.

- [ ] **Step 8: Commit V1 retirement and V2 entry**

```bash
git add -A backend/pixelflow/agent_runtime backend/pixelflow/agent_workflows backend/tests web/src/features web/src/lib/supervisor web/tests
git commit -m "refactor: retire v1 video workflow"
```

## Final Verification

- [ ] Run `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_*.py -v`.
- [ ] Run `cd backend && PYTHONPATH=. uv run pytest tests/test_agent_runtime_* -v`.
- [ ] Run `cd web && npm test && npm run lint && npm run build-dev`.
- [ ] Confirm `wc -l web/src/pages/WorkspacePage.tsx` is between 100 and 200 with only the V2 feature shell.
- [ ] Confirm `git diff --check` is clean and `rg -n 'agent_workflows.video|SUPERVISOR_V1|VIDEO_AGENT_V2|LegacyWorkspace' backend web` has no production-code matches.
