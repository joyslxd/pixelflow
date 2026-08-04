# Unified Video Agent Design

## Status

Approved design baseline for `feature/agent_0.8.5_boguan_joyce`.

This document is the durable source of truth for the unified video-agent migration. Future work must update this document before changing the architecture materially.

## Problem

PixelFlow owns the necessary video primitives, including script and scene planning, reference-video decomposition, asset replacement, generation, QC, composition, and Jianying export. The current experience remains workflow-led:

- A user with a finished script is forced through Plan review.
- A user with only an idea cannot naturally explore creative directions before generation.
- A user with a reference video cannot ask for product replacement as one goal-oriented task.
- Scene inspection can identify a problem without reliably creating the scene-level repair and regeneration work.
- `WorkspacePage.tsx` owns too much legacy state and behavior.

The new system must use one conversational entry point. It infers the user's video goal, loads the relevant Skill guidance, chooses controlled tools, forms a short executable plan, and continues after each tool result. It must remain safe for expensive asynchronous video operations.

## Goals

- One video entry point for script-to-video, creative discussion, reference remix, review, repair, composition, and export.
- Agent-selected Skills and controlled Tools rather than a mandatory Plan.md workflow.
- A persistent project workspace containing scripts, references, assets, scenes, variants, QC evidence, and generated outputs.
- A persistent plan and step history that survive reloads, retries, and worker recovery.
- A visible execution timeline that reports each step's status, result, timestamps, and duration without exposing hidden model reasoning.
- Default to the existing DeepSeek model in V1. The architecture must allow a later model switch or A/B evaluation.
- Preserve user isolation, idempotency, quota confirmation, external-job recovery, SSE delivery, and auditability.

## Non-goals

- Do not expose raw provider APIs or arbitrary shell execution to the model.
- Do not turn every user request into a newly hardcoded workflow state.
- Do not delete V1 workflows or migrate running V1 tasks during the first release.
- Do not add new feature logic to `WorkspacePage.tsx` or the V1 `agent_workflows` orchestration layer.

## Target Architecture

```text
Unified chat input
  -> thin conversation router
  -> VideoAgent
       -> VideoWorkspace evidence pack
       -> Skill catalog and controlled tool contracts
       -> AgentPlan / AgentPlanStep
       -> tool execution loop
  -> Agent Runtime reliability layer
       -> persistence, idempotency, leases, polling, quota, interrupt, SSE
  -> existing domain capabilities and provider skills
```

### Thin Router

The existing Supervisor is reduced to a cross-domain router. It may identify that a request is video, image, PPT, or general conversation; enforce authentication, global concurrency, and hard safety checks; then hand the unmodified user input, attachments, and project reference to the target agent.

It must not translate a video request into a fixed `modify_workflow`, `regenerate_stage`, or `retry_failed` action. Video intent has one owner: `VideoAgent`.

### VideoAgent

`VideoAgent` is the video domain's sole planning and tool-selection agent. For each turn it:

1. Loads only the relevant project evidence.
2. Selects the applicable Skill manifests.
3. Produces a short structured `AgentPlan`, or asks a precise question when necessary.
4. Invokes approved tools in a bounded loop.
5. Persists every result and revises the remaining plan when tool output changes the situation.
6. Requests confirmation before billable, batch, or destructive work.

Example reference-remix plan:

```text
analyze_reference_video
-> extract_storyboard_and_assets
-> replace_product_asset
-> build_scene_patch
-> confirm_generation_cost
-> generate_affected_scenes
-> inspect_scene_results
-> compose_video
```

## Project and Execution Data

### VideoWorkspace

`VideoWorkspace` is the persistent project blackboard. It is versioned and holds:

- product and materials;
- source scripts, imported scripts, and conversational script drafts;
- reference videos and decomposition results;
- global assets and scene-local assets;
- scene definitions, prompts, generation variants, and selected variants;
- scene-level QC reports, visual evidence, and repair suggestions;
- composed outputs and export packages.

`Plan.md` becomes an optional script artifact. A supplied mature script can be used directly; an idea creates drafts only; a reference video contributes evidence and scene constraints.

### AgentPlan and AgentPlanStep

Each meaningful turn creates a durable `AgentPlan`. Each step records:

```text
step_id, plan_id, sequence
skill_id, tool_name
title, input_summary, result_summary
status: pending | running | awaiting_confirmation | completed | failed | skipped
artifact_refs, job_ids, error_code
started_at, completed_at
```

The durable step record, not the SSE stream, is the recovery source. Step duration is calculated from persisted timestamps. A running step displays elapsed time from `started_at`; a completed step displays `completed_at - started_at` after reload or reconnect.

### Job DAG

`Job DAG` holds only asynchronous or billable execution work, such as reference decomposition, scene generation, QC, composition, and export. It reuses the Agent Runtime Operation identity, idempotency, leases, provider polling, quota authorization, and recovery model.

## Skill and Tool Model

Skills are guidance, not executable authority. A Skill describes applicability, inputs, outputs, constraints, examples, and recommended order. Tools are typed code contracts that are registered server-side and validate all inputs.

The initial tool catalog is intentionally small:

- `inspect_video_workspace`
- `import_script`
- `brainstorm_script`
- `analyze_reference_video`
- `replace_project_assets`
- `inspect_scene`
- `patch_scene`
- `generate_scenes`
- `review_generated_scenes`
- `compose_or_export_video`

Every tool declares input and output schemas, cost level, confirmation policy, idempotency policy, recovery behavior, and permitted project mutations. The model never calls Borgrise, FFmpeg, Jianying, database, or provider endpoints directly.

## Existing Code Boundaries

### Keep

- `backend/pixelflow/agent_runtime/jobs/`: operation identity, leases, polling, recovery, quota.
- `backend/pixelflow/agent_runtime/persistence/`: repositories, event outbox, user isolation, durable state.
- `backend/pixelflow/agent_runtime/context/`: context budgeting and compaction, extended with scene evidence packs.
- Existing video domain capabilities in `agent_workflows/video`, `generate`, `qc`, and `skills`.
- Existing AgentEvent SSE infrastructure and frontend Supervisor event projections as migration references.

### Freeze and Adapt

The V1 flow orchestration files remain only for compatibility:

- `agent_workflows/video/live_handler.py`
- `agent_workflows/video/live_operations.py`
- `agent_workflows/video/live_quota.py`
- `agent_workflows/video/state_codec.py`

No V2 feature is added to those files. V2 wraps reusable deterministic capabilities from `planning.py`, `scene_packages.py`, `video_generation.py`, `postproduction.py`, and `delivery.py` behind adapters. Later, stable capability code may move to `video_domain/`; this move is not required for the V2 launch.

### New Backend Package

```text
backend/pixelflow/video_agent/
  contracts/       # plans, steps, workspace, tool calls, tool results
  workspace/       # workspace persistence and evidence selection
  skills/          # Skill manifests and applicability selection
  tools/           # controlled tool implementations
  adapters/        # bridges to existing video domain capabilities
  planner/         # bounded model/tool loop and plan repair
  executor/        # plan-to-job-DAG and confirmation enforcement
  context/         # scene-level evidence packs
```

`agent_runtime/supervisor/` is retained for `SUPERVISOR_V1` only and reduced to thin-routing responsibilities for V2.

## Event Timeline and Frontend

The frontend shows an execution narrative, not model chain-of-thought. Each timeline item contains a plain title, visible inputs and result summary, linked artifacts, status, timestamp, and duration. It may display examples such as "Identified 6 scenes" or "Scene 3 needs regeneration". It must never display hidden reasoning.

New persisted event types:

- `agent.plan.created`
- `agent.step.started`
- `agent.step.progressed`
- `agent.step.completed`
- `agent.step.failed`
- `agent.confirmation.requested`

The existing event outbox and monotonic SSE sequence are reused. `message.upserted` remains the user-facing conclusion, while the new events project the step timeline.

`WorkspacePage.tsx` currently contains legacy state, V1 supervisor behavior, task polling, and UI behavior. New functionality must not be added there. The first migration milestone moves its existing body to:

```text
web/src/features/legacy-workspace/LegacyWorkspace.tsx
web/src/features/video-agent/
  VideoAgentWorkspace.tsx
  AgentPlanTimeline.tsx
  AgentConfirmationCard.tsx
  SceneEvidencePanel.tsx
  hooks/useVideoAgent.ts
  state/reducer.ts
```

The final `web/src/pages/WorkspacePage.tsx` is a 100-200 line route/layout shell that renders the legacy feature or `VideoAgentWorkspace` based on orchestration mode.

## Model Strategy

V1 uses the existing `deepseek-v4-pro` configuration. The agent loop must depend on a model-provider interface supporting structured output, tool calls, tool-result replay, image evidence where required, and capability profiles.

Kimi K3 is not a V1 dependency. It may later be registered as a controlled, high-complexity planner after provider-specific replay support and golden-case evaluation. Video decomposition and visual QC remain dedicated VLM capabilities rather than depending on a planning model.

## Rollout

Add a `video_agent_v2` orchestration mode. New conversations can be allowlisted or percentage-routed to V2. Existing and non-allowlisted conversations remain `supervisor_v1`. Running V1 workflows are never migrated in place. A V2 failure can be rolled back by routing subsequent new conversations to V1.

## Milestones

1. Extract the legacy frontend feature and reduce `WorkspacePage.tsx` to a shell with behavior-preserving tests.
2. Add workspace, plan, and step persistence with migrations, repositories, event contracts, and recovery tests.
3. Add the VideoAgent planner, Skill catalog, tool registry, DeepSeek provider boundary, and visible plan/step timeline.
4. Adapt script import, creative discussion, and reference-video analysis as the first unified-entry tools.
5. Adapt scene inspection, patching, selective regeneration, QC, composition, and export tools.
6. Add `video_agent_v2` routing, canary rollout, observability, and rollback controls.

## Verification and Acceptance

- A mature script reaches generation without a mandatory creative Plan review.
- An idea supports multi-turn creative discussion and creates no generation job until explicit confirmation.
- A reference video is decomposed, product replacement identifies affected scenes, and generation cost is confirmed before jobs start.
- "Inspect scene 3 and regenerate it if wrong" reads scene evidence, records a repair decision, performs only scoped work, and reports the full step timeline.
- Reload, reconnect, retry, duplicate submission, and worker restart preserve plan steps, durations, external jobs, and cost safety.
- V1 regression tests remain green; V2 adds contract, planner, tool, repository, SSE/reducer, and end-to-end tests.
- Golden cases cover 30-50 realistic requests and measure intent/tool selection correctness, clarification correctness, scene targeting, multi-step completion, erroneous generation, duplicate billing, latency, and cost.

## Size Estimate

The V2 migration is expected to add or materially modify approximately 45-65 files and 7,000-11,000 lines across backend, frontend, migrations, and tests. The milestones are separately releasable and must not require a big-bang rewrite.
