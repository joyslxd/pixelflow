import type {
  VideoAgentPlanState,
  VideoAgentPlanStatus,
  VideoAgentPublicEvent,
  VideoAgentStepState,
  VideoAgentStepStatus,
  VideoAgentTimelineState,
} from "./contracts.js";

function asText(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asPositiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

function asDuration(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function asArtifactRefs(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asPlanStatus(value: unknown): VideoAgentPlanStatus | null {
  return ["planning", "running", "awaiting_confirmation", "completed", "failed", "cancelled"].includes(String(value))
    ? value as VideoAgentPlanStatus
    : null;
}

function asStepStatus(value: unknown): VideoAgentStepStatus | null {
  return ["pending", "running", "awaiting_confirmation", "completed", "failed", "skipped"].includes(String(value))
    ? value as VideoAgentStepStatus
    : null;
}

export function createVideoAgentTimelineState(): VideoAgentTimelineState {
  return { plans: {} };
}

export function reduceVideoAgentEvent(
  state: VideoAgentTimelineState,
  event: VideoAgentPublicEvent,
): VideoAgentTimelineState {
  if (event.type === "agent.plan.created") {
    const planId = asText(event.payload.plan_id);
    const workspaceId = asText(event.payload.workspace_id);
    const status = asPlanStatus(event.payload.status);
    if (!planId || !workspaceId || !status) return state;
    const plan: VideoAgentPlanState = {
      planId,
      workspaceId,
      status,
      publicGoal: asText(event.payload.public_goal),
      steps: state.plans[planId]?.steps ?? {},
    };
    return { plans: { ...state.plans, [planId]: plan } };
  }

  if (!event.type.startsWith("agent.step.")) return state;
  const planId = asText(event.payload.plan_id);
  const stepId = asText(event.payload.step_id);
  const plan = planId ? state.plans[planId] : undefined;
  const status = asStepStatus(event.payload.status);
  const sequence = asPositiveInteger(event.payload.sequence);
  const title = asText(event.payload.title);
  if (!planId || !plan || !stepId || !status || !sequence || !title) return state;

  const resolvedPlanId = planId;
  const previous = plan.steps[stepId];
  const step: VideoAgentStepState = {
    ...previous,
    stepId,
    sequence,
    title,
    status,
    publicSummary: asText(event.payload.public_summary) ?? previous?.publicSummary ?? null,
    artifactRefs: asArtifactRefs(event.payload.artifact_refs).length > 0
      ? asArtifactRefs(event.payload.artifact_refs)
      : previous?.artifactRefs ?? [],
    startedAt: asText(event.payload.started_at) ?? previous?.startedAt ?? null,
    completedAt: asText(event.payload.completed_at) ?? previous?.completedAt ?? null,
    durationMs: asDuration(event.payload.duration_ms) ?? previous?.durationMs ?? null,
  };
  return {
    plans: {
      ...state.plans,
      [resolvedPlanId]: { ...plan, steps: { ...plan.steps, [stepId]: step } },
    },
  };
}
