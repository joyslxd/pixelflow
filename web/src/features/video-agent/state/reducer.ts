import type {
  VideoAgentConfirmationState,
  VideoAgentPlanState,
  VideoAgentPlanStatus,
  VideoAgentPublicEvent,
  VideoAgentQuotaState,
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

function asNonnegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function asDuration(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function asArtifactRefs(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asTextList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(asText).filter((item): item is string => item !== null)
    : [];
}

function asPlanStatus(value: unknown): VideoAgentPlanStatus | null {
  return [
    "planning",
    "running",
    "awaiting_confirmation",
    "waiting_for_input",
    "completed",
    "failed",
    "cancelled",
  ].includes(String(value))
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

export function projectVideoAgentPlanSnapshot(
  value: unknown,
  rawSteps: unknown,
): VideoAgentPlanState | null {
  if (value === null) {
    if (Array.isArray(rawSteps) && rawSteps.length === 0) return null;
    throw new TypeError("VideoAgent计划快照不合法");
  }
  if (typeof value !== "object" || Array.isArray(value) || value === null || !Array.isArray(rawSteps)) {
    throw new TypeError("VideoAgent计划快照不合法");
  }
  const planValue = value as Record<string, unknown>;
  const planId = asText(planValue.plan_id);
  const workspaceId = asText(planValue.workspace_id);
  const status = asPlanStatus(planValue.status);
  if (!planId || !workspaceId || !status) throw new TypeError("VideoAgent计划快照不合法");
  const steps: Record<string, VideoAgentStepState> = {};
  for (const rawStep of rawSteps) {
    if (typeof rawStep !== "object" || rawStep === null || Array.isArray(rawStep)) {
      throw new TypeError("VideoAgent步骤快照不合法");
    }
    const stepValue = rawStep as Record<string, unknown>;
    const stepId = asText(stepValue.step_id);
    const stepPlanId = asText(stepValue.plan_id);
    const sequence = asPositiveInteger(stepValue.sequence);
    const title = asText(stepValue.title);
    const stepStatus = asStepStatus(stepValue.status);
    if (!stepId || stepPlanId !== planId || !sequence || !title || !stepStatus || steps[stepId]) {
      throw new TypeError("VideoAgent步骤快照不合法");
    }
    steps[stepId] = {
      stepId,
      sequence,
      title,
      status: stepStatus,
      publicSummary: asText(stepValue.public_summary),
      progressLog: (() => {
        const fromList = asTextList(stepValue.progress_log);
        if (fromList.length > 0) return fromList;
        const summary = asText(stepValue.public_summary);
        return summary ? [summary] : [];
      })(),
      progressPhase: asText(stepValue.progress_phase),
      artifactRefs: asArtifactRefs(stepValue.artifact_refs),
      startedAt: asText(stepValue.started_at),
      completedAt: asText(stepValue.completed_at),
      durationMs: asDuration(stepValue.duration_ms),
    };
  }
  if (new Set(Object.values(steps).map((step) => step.sequence)).size !== Object.keys(steps).length) {
    throw new TypeError("VideoAgent步骤sequence重复");
  }
  return {
    planId,
    workspaceId,
    status,
    publicGoal: asText(planValue.public_goal),
    steps,
  };
}

export function cloneVideoAgentPlanState(value: unknown): VideoAgentPlanState | null {
  if (value === null) return null;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("VideoAgent计划投影不合法");
  }
  const plan = value as Partial<VideoAgentPlanState>;
  if (!plan.steps || typeof plan.steps !== "object" || Array.isArray(plan.steps)) {
    throw new TypeError("VideoAgent计划投影不合法");
  }
  return projectVideoAgentPlanSnapshot(
    {
      plan_id: plan.planId,
      workspace_id: plan.workspaceId,
      status: plan.status,
      public_goal: plan.publicGoal,
    },
    Object.values(plan.steps).map((step) => ({
      step_id: step.stepId,
      plan_id: plan.planId,
      sequence: step.sequence,
      title: step.title,
      status: step.status,
      public_summary: step.publicSummary,
      progress_log: step.progressLog,
      progress_phase: step.progressPhase,
      artifact_refs: step.artifactRefs,
      started_at: step.startedAt,
      completed_at: step.completedAt,
      duration_ms: step.durationMs,
    })),
  );
}

export function projectVideoAgentConfirmationSnapshot(
  value: unknown,
): VideoAgentConfirmationState | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("VideoAgent确认快照不合法");
  }
  const record = value as Record<string, unknown>;
  const confirmationId = asText(record.confirmation_id);
  const planId = asText(record.plan_id);
  const stepId = asText(record.step_id);
  const title = asText(record.title);
  const costSummary = asText(record.cost_summary);
  const affectedSceneIds = asTextList(record.affected_scene_ids);
  if (
    !confirmationId
    || !planId
    || !stepId
    || !title
    || !costSummary
    || typeof record.submittable !== "boolean"
  ) throw new TypeError("VideoAgent确认快照不合法");
  const unavailableReason = asText(record.unavailable_reason);
  if (!record.submittable && !unavailableReason) {
    throw new TypeError("VideoAgent不可提交确认必须说明原因");
  }
  return {
    confirmationId,
    planId,
    stepId,
    title,
    costSummary,
    affectedSceneIds,
    submittable: record.submittable,
    unavailableReason,
  };
}

export function cloneVideoAgentConfirmationState(
  value: unknown,
): VideoAgentConfirmationState | null {
  if (value === null) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("VideoAgent确认投影不合法");
  }
  const confirmation = value as Partial<VideoAgentConfirmationState>;
  return projectVideoAgentConfirmationSnapshot({
    confirmation_id: confirmation.confirmationId,
    plan_id: confirmation.planId,
    step_id: confirmation.stepId,
    title: confirmation.title,
    cost_summary: confirmation.costSummary,
    affected_scene_ids: confirmation.affectedSceneIds,
    submittable: confirmation.submittable,
    unavailable_reason: confirmation.unavailableReason,
  });
}

export function projectVideoAgentQuotaSnapshot(
  value: unknown,
): VideoAgentQuotaState | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("VideoAgent额度快照不合法");
  }
  const record = value as Record<string, unknown>;
  const quotaInterruptId = asText(record.quota_interrupt_id);
  const planId = asText(record.plan_id);
  const stepId = asText(record.step_id);
  const quotaPauseRevision = asNonnegativeInteger(record.quota_pause_revision);
  const phase = record.phase;
  if (
    !quotaInterruptId
    || !planId
    || !stepId
    || quotaPauseRevision === null
    || (phase !== "start" && phase !== "status")
    || record.reason_code !== "provider_quota_insufficient"
    || typeof record.submittable !== "boolean"
  ) throw new TypeError("VideoAgent额度快照不合法");
  const unavailableReason = asText(record.unavailable_reason);
  if (!record.submittable && !unavailableReason) {
    throw new TypeError("VideoAgent不可提交额度卡必须说明原因");
  }
  return {
    quotaInterruptId,
    planId,
    stepId,
    quotaPauseRevision,
    phase,
    reasonCode: "provider_quota_insufficient",
    submittable: record.submittable,
    unavailableReason,
  };
}

export function cloneVideoAgentQuotaState(
  value: unknown,
): VideoAgentQuotaState | null {
  if (value === null) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("VideoAgent额度投影不合法");
  }
  const quota = value as Partial<VideoAgentQuotaState>;
  return projectVideoAgentQuotaSnapshot({
    quota_interrupt_id: quota.quotaInterruptId,
    plan_id: quota.planId,
    step_id: quota.stepId,
    quota_pause_revision: quota.quotaPauseRevision,
    phase: quota.phase,
    reason_code: quota.reasonCode,
    submittable: quota.submittable,
    unavailable_reason: quota.unavailableReason,
  });
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
    const previous = state.plans[planId];
    const nextSteps: Record<string, VideoAgentStepState> = { ...(previous?.steps ?? {}) };
    const rawSteps = Array.isArray(event.payload.steps) ? event.payload.steps : [];
    for (const item of rawSteps) {
      if (!item || typeof item !== "object" || Array.isArray(item)) continue;
      const stepId = asText((item as Record<string, unknown>).step_id);
      const sequence = asPositiveInteger((item as Record<string, unknown>).sequence);
      const title = asText((item as Record<string, unknown>).title);
      const stepStatus = asStepStatus((item as Record<string, unknown>).status) ?? "pending";
      if (!stepId || !sequence || !title) continue;
      const existing = nextSteps[stepId];
      nextSteps[stepId] = {
        stepId,
        sequence,
        title,
        status: existing?.status && existing.status !== "pending" ? existing.status : stepStatus,
        publicSummary: existing?.publicSummary ?? null,
        progressLog: existing?.progressLog ?? [],
        progressPhase: existing?.progressPhase ?? null,
        artifactRefs: existing?.artifactRefs ?? [],
        startedAt: existing?.startedAt ?? null,
        completedAt: existing?.completedAt ?? null,
        durationMs: existing?.durationMs ?? null,
      };
    }
    const plan: VideoAgentPlanState = {
      planId,
      workspaceId,
      status,
      publicGoal: asText(event.payload.public_goal),
      steps: nextSteps,
    };
    return { plans: { ...state.plans, [planId]: plan } };
  }

  if (event.type === "agent.plan.updated") {
    const planId = asText(event.payload.plan_id);
    const workspaceId = asText(event.payload.workspace_id);
    const status = asPlanStatus(event.payload.status);
    if (!planId || !workspaceId || !status) return state;
    const previous = state.plans[planId];
    const nextSteps: Record<string, VideoAgentStepState> = { ...(previous?.steps ?? {}) };
    const rawSteps = Array.isArray(event.payload.steps) ? event.payload.steps : [];
    for (const item of rawSteps) {
      if (!item || typeof item !== "object" || Array.isArray(item)) continue;
      const stepId = asText((item as Record<string, unknown>).step_id);
      const sequence = asPositiveInteger((item as Record<string, unknown>).sequence);
      const title = asText((item as Record<string, unknown>).title);
      const stepStatus = asStepStatus((item as Record<string, unknown>).status) ?? "pending";
      if (!stepId || !sequence || !title) continue;
      const existing = nextSteps[stepId];
      nextSteps[stepId] = {
        stepId,
        sequence,
        title,
        status: existing?.status && existing.status !== "pending" ? existing.status : stepStatus,
        publicSummary: existing?.publicSummary ?? null,
        progressLog: existing?.progressLog ?? [],
        progressPhase: existing?.progressPhase ?? null,
        artifactRefs: existing?.artifactRefs ?? [],
        startedAt: existing?.startedAt ?? null,
        completedAt: existing?.completedAt ?? null,
        durationMs: existing?.durationMs ?? null,
      };
    }
    const plan: VideoAgentPlanState = {
      planId,
      workspaceId,
      status,
      publicGoal: asText(event.payload.public_goal) ?? previous?.publicGoal ?? null,
      steps: nextSteps,
    };
    return { plans: { ...state.plans, [planId]: plan } };
  }

  if (event.type === "agent.confirmation.requested") {
    const planId = asText(event.payload.plan_id);
    const stepId = asText(event.payload.step_id);
    const plan = planId ? state.plans[planId] : undefined;
    if (!planId || !stepId || !plan) return state;
    const previous = plan.steps[stepId];
    const title = asText(event.payload.title) ?? previous?.title ?? "待确认步骤";
    const sequence = asPositiveInteger(event.payload.sequence) ?? previous?.sequence ?? (
      Object.keys(plan.steps).length + 1
    );
    // 原生观察 Plan 常以 0 步创建；确认闸门必须 upsert 步骤，否则 UI 卡在「规划中」。
    return {
      plans: {
        ...state.plans,
        [planId]: {
          ...plan,
          status: "awaiting_confirmation",
          steps: {
            ...plan.steps,
            [stepId]: {
              stepId,
              sequence,
              title,
              status: "awaiting_confirmation",
              publicSummary: previous?.publicSummary ?? null,
              progressLog: previous?.progressLog ?? [],
              progressPhase: previous?.progressPhase ?? null,
              artifactRefs: previous?.artifactRefs ?? [],
              startedAt: previous?.startedAt ?? null,
              completedAt: previous?.completedAt ?? null,
              durationMs: previous?.durationMs ?? null,
            },
          },
        },
      },
    };
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
  const nextSummary = asText(event.payload.public_summary) ?? previous?.publicSummary ?? null;
  const nextPhase = asText(event.payload.progress_phase);
  let progressLog = previous?.progressLog ?? [];
  if (event.type === "agent.step.started") {
    progressLog = [];
  } else if (
    event.type === "agent.step.progressed"
    && nextSummary
    && progressLog[progressLog.length - 1] !== nextSummary
  ) {
    progressLog = [...progressLog, nextSummary];
  } else if (
    event.type === "agent.step.completed"
    && nextSummary
    && progressLog.length === 0
  ) {
    progressLog = [nextSummary];
  }
  const step: VideoAgentStepState = {
    ...previous,
    stepId,
    sequence,
    title,
    status,
    publicSummary: nextSummary,
    progressLog,
    progressPhase: event.type === "agent.step.progressed"
      ? nextPhase
      : event.type === "agent.step.started"
        ? null
        : previous?.progressPhase ?? nextPhase,
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
