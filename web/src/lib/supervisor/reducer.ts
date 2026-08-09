import type {
  AgentEventEnvelope,
  JsonObject,
  JsonValue,
} from "./contracts.js";
import {
  applySupervisorWorkspaceEvent,
  cloneSupervisorWorkspaceProjection,
  type SupervisorWorkspaceProjection,
} from "./workspaceProjection.js";
import type {
  VideoAgentConfirmationState,
  VideoAgentPlanState,
  VideoAgentQuotaState,
} from "../../features/video-agent/state/contracts.js";
import {
  cloneVideoAgentConfirmationState,
  cloneVideoAgentPlanState,
  cloneVideoAgentQuotaState,
  reduceVideoAgentEvent,
} from "../../features/video-agent/state/reducer.js";
import {
  applyVideoWorkspaceSnapshot,
  cloneVideoWorkspaceProjectionState,
  createVideoWorkspaceProjectionState,
  type VideoWorkspaceProjectionState,
} from "../../features/video-agent/state/workspace.js";

export const SUPERVISOR_CONNECTION_STATUS_VALUES = [
  "idle",
  "connecting",
  "connected",
  "reconnecting",
  "fatal",
] as const;

export type SupervisorConnectionStatus =
  (typeof SUPERVISOR_CONNECTION_STATUS_VALUES)[number];

export const SUPERVISOR_RUN_STATUS_VALUES = [
  "idle",
  "running",
  "waiting_user",
  "paused",
  "failed",
  "completed",
] as const;

export type SupervisorRunStatus = (typeof SUPERVISOR_RUN_STATUS_VALUES)[number];

export const SUPERVISOR_COMPRESSION_STATUS_VALUES = [
  "idle",
  "compacting",
  "blocked",
] as const;

export type SupervisorCompressionStatus =
  (typeof SUPERVISOR_COMPRESSION_STATUS_VALUES)[number];

export const SUPERVISOR_INPUT_STATUS_VALUES = [
  "sending",
  "queued",
  "processing",
  "accepted",
  "failed",
] as const;

export type SupervisorInputStatus = (typeof SUPERVISOR_INPUT_STATUS_VALUES)[number];

export type SupervisorCompressionOutcome = "completed" | "failed" | null;

export interface SupervisorConnectionState {
  status: SupervisorConnectionStatus;
  error: string | null;
}

export interface SupervisorRunState {
  runId: string | null;
  status: SupervisorRunStatus;
  updatedAt: string | null;
}

export interface SupervisorCompressionState {
  status: SupervisorCompressionStatus;
  progressPercent: number | null;
  queuedInputCount: number;
  lastOutcome: SupervisorCompressionOutcome;
  updatedAt: string | null;
}

export interface SupervisorInputQueueItem {
  clientInputId: string;
  turnId: string | null;
  status: SupervisorInputStatus;
  queuePosition: number | null;
  updatedAt: string | null;
}

export interface SupervisorResumePoint {
  cursor: string | null;
  sequence: number;
}

export interface SupervisorRuntimeProjection extends SupervisorWorkspaceProjection {
  conversationId: string;
  run: SupervisorRunState;
  compression: SupervisorCompressionState;
  inputQueue: SupervisorInputQueueItem[];
  resume: SupervisorResumePoint;
  videoAgentWorkspace: VideoWorkspaceProjectionState;
  videoAgentPlan: VideoAgentPlanState | null;
  /** Snapshot / 事件恢复出的会话内全部执行方案（服务端持久化）。 */
  videoAgentPlans: Record<string, VideoAgentPlanState>;
  videoAgentPlanOrder: string[];
  videoAgentConfirmation: VideoAgentConfirmationState | null;
  videoAgentQuota: VideoAgentQuotaState | null;
}

export interface SupervisorRuntimeState extends SupervisorRuntimeProjection {
  connection: SupervisorConnectionState;
}

export type SupervisorRuntimeAction =
  | {
    type: "conversation.reset";
    conversationId: string;
  }
  | {
    type: "connection.state_changed";
    status: SupervisorConnectionStatus;
  }
  | {
    type: "input.sending";
    clientInputId: string;
  }
  | {
    type: "input.submit_failed";
    clientInputId: string;
  }
  | {
    type: "snapshot.hydrated";
    snapshot: SupervisorRuntimeProjection;
  }
  | {
    type: "event.received";
    event: AgentEventEnvelope;
  };

const CONNECTION_TRANSITIONS: Record<
  SupervisorConnectionStatus,
  readonly SupervisorConnectionStatus[]
> = {
  idle: ["idle", "connecting", "fatal"],
  connecting: ["idle", "connecting", "connected", "reconnecting", "fatal"],
  connected: ["idle", "connected", "reconnecting", "fatal"],
  reconnecting: ["idle", "connecting", "connected", "reconnecting", "fatal"],
  fatal: ["idle", "connecting", "fatal"],
};

const RUN_TRANSITIONS: Record<SupervisorRunStatus, readonly SupervisorRunStatus[]> = {
  idle: ["idle", "running", "waiting_user", "paused", "failed", "completed"],
  running: ["running", "waiting_user", "paused", "failed", "completed"],
  waiting_user: ["running", "waiting_user", "paused", "failed", "completed"],
  paused: ["running", "paused", "failed", "completed"],
  failed: ["running", "failed", "completed"],
  completed: ["completed"],
};

const EVENT_STATE_ERROR = "Supervisor 事件状态不合法";
const EVENT_SEQUENCE_RECOVERY_ERROR = "Supervisor 事件序列需要恢复";

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isNullableNonEmptyString(value: unknown): value is string | null {
  return value === null || isNonEmptyString(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 1;
}

function isPercentage(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;
}

function includesValue<TValue extends string>(
  values: readonly TValue[],
  value: unknown,
): value is TValue {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

function optionalNonNegativeInteger(
  payload: JsonObject,
  key: string,
  fallback: number,
): { valid: boolean; value: number } {
  const value = payload[key];
  if (value === undefined) return { valid: true, value: fallback };
  return isNonNegativeInteger(value)
    ? { valid: true, value }
    : { valid: false, value: fallback };
}

function optionalNullableString(
  payload: JsonObject,
  key: string,
  fallback: string | null,
): { valid: boolean; value: string | null } {
  const value = payload[key];
  if (value === undefined) return { valid: true, value: fallback };
  return isNullableNonEmptyString(value)
    ? { valid: true, value }
    : { valid: false, value: fallback };
}

function optionalPositiveInteger(
  payload: JsonObject,
  key: string,
  fallback: number | null,
): { valid: boolean; value: number | null } {
  const value = payload[key];
  if (value === undefined) return { valid: true, value: fallback };
  if (value === null) return { valid: true, value: null };
  return isPositiveInteger(value)
    ? { valid: true, value }
    : { valid: false, value: fallback };
}

function mapRunStatus(value: JsonValue | undefined): SupervisorRunStatus | null {
  switch (value) {
    case "accepted":
    case "queued":
    case "processing":
    case "running":
      return "running";
    case "waiting_user":
      return "waiting_user";
    case "paused":
      return "paused";
    case "failed":
      return "failed";
    case "completed":
      return "completed";
    default:
      return null;
  }
}

function mapInputStatus(value: JsonValue | undefined): SupervisorInputStatus | null {
  switch (value) {
    case "accepted":
    case "waiting_user":
    case "completed":
      return "accepted";
    case "queued":
      return "queued";
    case "processing":
      return "processing";
    case "failed":
      return "failed";
    default:
      return null;
  }
}

function withEventResumePoint(
  state: SupervisorRuntimeState,
  event: AgentEventEnvelope,
): SupervisorRuntimeState {
  return {
    ...state,
    resume: {
      cursor: event.cursor,
      sequence: event.sequence,
    },
  };
}

function withInvalidEvent(
  state: SupervisorRuntimeState,
): SupervisorRuntimeState {
  return {
    ...state,
    connection: {
      status: "fatal",
      error: EVENT_STATE_ERROR,
    },
  };
}

function upsertInputItem(
  items: readonly SupervisorInputQueueItem[],
  nextItem: SupervisorInputQueueItem,
): SupervisorInputQueueItem[] {
  const index = items.findIndex((item) => item.clientInputId === nextItem.clientInputId);
  if (index < 0) return [...items, nextItem];
  const nextItems = [...items];
  nextItems[index] = nextItem;
  return nextItems;
}

function inputStatusAfterServerEvent(
  current: SupervisorInputStatus | undefined,
  wireStatus: JsonValue | undefined,
): SupervisorInputStatus | null {
  const mapped = mapInputStatus(wireStatus);
  if (mapped === null) return null;
  if (wireStatus === "accepted" && (current === "queued" || current === "processing")) {
    return current;
  }
  if (wireStatus === "queued" && current === "processing") return current;
  return mapped;
}

function applyRunEvent(
  state: SupervisorRuntimeState,
  event: AgentEventEnvelope,
): SupervisorRuntimeState {
  const nextStatus = mapRunStatus(event.payload.status);
  if (nextStatus === null) return withInvalidEvent(state);
  const sameRun = state.run.runId === event.run_id;
  if (sameRun && !RUN_TRANSITIONS[state.run.status].includes(nextStatus)) {
    return withEventResumePoint(state, event);
  }
  return {
    ...withEventResumePoint(state, event),
    run: {
      runId: event.run_id,
      status: nextStatus,
      updatedAt: event.occurred_at,
    },
  };
}

function applyCompressionStarted(
  state: SupervisorRuntimeState,
  event: AgentEventEnvelope,
): SupervisorRuntimeState {
  const queued = optionalNonNegativeInteger(
    event.payload,
    "queued_input_count",
    0,
  );
  if (!queued.valid) return withInvalidEvent(state);
  return {
    ...withEventResumePoint(state, event),
    compression: {
      status: "compacting",
      progressPercent: null,
      queuedInputCount: queued.value,
      lastOutcome: null,
      updatedAt: event.occurred_at,
    },
  };
}

function applyCompressionProgressed(
  state: SupervisorRuntimeState,
  event: AgentEventEnvelope,
): SupervisorRuntimeState {
  const progress = event.payload.progress_percent;
  const queued = optionalNonNegativeInteger(
    event.payload,
    "queued_input_count",
    state.compression.queuedInputCount,
  );
  if ((progress !== undefined && !isPercentage(progress)) || !queued.valid) {
    return withInvalidEvent(state);
  }
  if (state.compression.status !== "compacting") return withEventResumePoint(state, event);
  return {
    ...withEventResumePoint(state, event),
    compression: {
      ...state.compression,
      progressPercent: progress === undefined
        ? state.compression.progressPercent
        : Math.max(state.compression.progressPercent ?? 0, progress),
      queuedInputCount: queued.value,
      updatedAt: event.occurred_at,
    },
  };
}

function applyCompressionTerminal(
  state: SupervisorRuntimeState,
  event: AgentEventEnvelope,
  outcome: Exclude<SupervisorCompressionOutcome, null>,
): SupervisorRuntimeState {
  const queued = optionalNonNegativeInteger(event.payload, "queued_input_count", 0);
  if (!queued.valid) return withInvalidEvent(state);
  if (state.compression.status !== "compacting") return withEventResumePoint(state, event);
  return {
    ...withEventResumePoint(state, event),
    compression: {
      status: outcome === "completed" ? "idle" : "blocked",
      progressPercent: outcome === "completed"
        ? 100
        : state.compression.progressPercent,
      queuedInputCount: 0,
      lastOutcome: outcome,
      updatedAt: event.occurred_at,
    },
  };
}

function applyInputEvent(
  state: SupervisorRuntimeState,
  event: AgentEventEnvelope,
): SupervisorRuntimeState {
  const clientInputId = event.payload.client_input_id;
  if (!isNonEmptyString(clientInputId)) return withInvalidEvent(state);
  const current = state.inputQueue.find((item) => item.clientInputId === clientInputId);
  const nextStatus = inputStatusAfterServerEvent(current?.status, event.payload.status);
  const turnId = optionalNullableString(event.payload, "turn_id", current?.turnId ?? null);
  const queuePosition = optionalPositiveInteger(
    event.payload,
    "queue_position",
    current?.queuePosition ?? null,
  );
  if (nextStatus === null || !turnId.valid || !queuePosition.valid) {
    return withInvalidEvent(state);
  }
  if (current?.turnId !== null && current?.turnId !== undefined
    && event.payload.turn_id !== undefined
    && event.payload.turn_id !== current.turnId) {
    return withInvalidEvent(state);
  }
  if ((nextStatus === "queued" || nextStatus === "processing" || nextStatus === "accepted")
    && turnId.value === null) {
    return withInvalidEvent(state);
  }
  if (turnId.value !== null && state.inputQueue.some(
    (item) => item.clientInputId !== clientInputId && item.turnId === turnId.value,
  )) {
    return withInvalidEvent(state);
  }
  return {
    ...withEventResumePoint(state, event),
    inputQueue: upsertInputItem(state.inputQueue, {
      clientInputId,
      turnId: turnId.value,
      status: nextStatus,
      queuePosition: nextStatus === "queued"
        ? queuePosition.value
        : null,
      updatedAt: event.occurred_at,
    }),
  };
}

function applyAgentEvent(
  state: SupervisorRuntimeState,
  event: AgentEventEnvelope,
): SupervisorRuntimeState {
  if (event.conversation_id !== state.conversationId) return state;
  if (!Number.isSafeInteger(event.sequence) || event.sequence < 1) {
    return withInvalidEvent(state);
  }
  if (event.sequence <= state.resume.sequence) return state;
  if (event.sequence !== state.resume.sequence + 1) {
    return {
      ...state,
      connection: {
        status: "reconnecting",
        error: EVENT_SEQUENCE_RECOVERY_ERROR,
      },
    };
  }

  switch (event.type) {
    case "run.state_changed":
      return applyRunEvent(state, event);
    case "context.compression_started":
      return applyCompressionStarted(state, event);
    case "context.compression_progressed":
      return applyCompressionProgressed(state, event);
    case "context.compression_completed":
      return applyCompressionTerminal(state, event, "completed");
    case "context.compression_failed":
      return applyCompressionTerminal(state, event, "failed");
    case "input.state_changed":
      return applyInputEvent(state, event);
    case "external_job.quota_state_changed": {
      const quotaState = event.payload.quota_state;
      const planId = typeof event.payload.workflow_id === "string"
        ? event.payload.workflow_id
        : null;
      const revision = typeof event.payload.quota_pause_revision === "number"
        && Number.isSafeInteger(event.payload.quota_pause_revision)
        && event.payload.quota_pause_revision > 0
        ? event.payload.quota_pause_revision
        : null;
      const quotaPlan = planId
        ? state.videoAgentPlans[planId]
          ?? (state.videoAgentPlan?.planId === planId ? state.videoAgentPlan : null)
        : null;
      const runningSteps = quotaPlan
        ? Object.values(quotaPlan.steps).filter((step) => step.status === "running")
        : [];
      const runningStep = runningSteps.length === 1 ? runningSteps[0] : undefined;
      if (quotaState === "resumed") {
        return {
          ...withEventResumePoint(state, event),
          videoAgentQuota: state.videoAgentQuota?.planId === planId
            ? null
            : state.videoAgentQuota,
        };
      }
      if (
        quotaState !== "paused"
        || !planId
        || !revision
        || !runningStep
        || event.payload.reason_code !== "provider_quota_insufficient"
      ) return withEventResumePoint(state, event);
      return {
        ...withEventResumePoint(state, event),
        videoAgentQuota: {
          quotaInterruptId: event.event_id,
          planId,
          stepId: runningStep.stepId,
          quotaPauseRevision: revision,
          phase: "status",
          reasonCode: "provider_quota_insufficient",
          submittable: true,
          unavailableReason: null,
        },
      };
    }
    case "agent.plan.created":
    case "agent.step.started":
    case "agent.step.progressed":
    case "agent.step.completed":
    case "agent.step.failed":
    case "agent.confirmation.requested": {
      const timeline = reduceVideoAgentEvent(
        {
          plans: { ...state.videoAgentPlans },
        },
        event,
      );
      const eventPlanId = typeof event.payload.plan_id === "string"
        ? event.payload.plan_id
        : null;
      const nextOrder = event.type === "agent.plan.created" && eventPlanId
        && !state.videoAgentPlanOrder.includes(eventPlanId)
        ? [...state.videoAgentPlanOrder, eventPlanId]
        : state.videoAgentPlanOrder;
      const nextCurrent = event.type === "agent.plan.created" && eventPlanId
        ? timeline.plans[eventPlanId] ?? state.videoAgentPlan
        : state.videoAgentPlan?.planId
          ? timeline.plans[state.videoAgentPlan.planId] ?? state.videoAgentPlan
          : state.videoAgentPlan;
      return {
        ...withEventResumePoint(state, event),
        videoAgentPlans: timeline.plans,
        videoAgentPlanOrder: nextOrder,
        videoAgentPlan: nextCurrent,
        videoAgentConfirmation: (
          event.type === "agent.step.started"
          || event.type === "agent.step.completed"
          || event.type === "agent.step.failed"
        ) && event.payload.step_id === state.videoAgentConfirmation?.stepId
          ? null
          : state.videoAgentConfirmation,
      };
    }
    case "message.upserted":
    case "workflow.progressed":
    case "interrupt.opened":
    case "interrupt.closed": {
      try {
        const workspace = applySupervisorWorkspaceEvent({
          messages: state.messages,
          workflows: state.workflows,
          interrupt: state.interrupt,
        }, event);
        return {
          ...withEventResumePoint(state, event),
          ...workspace,
        };
      } catch {
        return withInvalidEvent(state);
      }
    }
    default:
      return withEventResumePoint(state, event);
  }
}

function isRunState(value: unknown): value is SupervisorRunState {
  if (value === null || typeof value !== "object") return false;
  const state = value as Partial<SupervisorRunState>;
  return isNullableNonEmptyString(state.runId)
    && includesValue(SUPERVISOR_RUN_STATUS_VALUES, state.status)
    && isNullableNonEmptyString(state.updatedAt);
}

function isCompressionState(value: unknown): value is SupervisorCompressionState {
  if (value === null || typeof value !== "object") return false;
  const state = value as Partial<SupervisorCompressionState>;
  return includesValue(SUPERVISOR_COMPRESSION_STATUS_VALUES, state.status)
    && (state.progressPercent === null || isPercentage(state.progressPercent))
    && isNonNegativeInteger(state.queuedInputCount)
    && (state.lastOutcome === null
      || state.lastOutcome === "completed"
      || state.lastOutcome === "failed")
    && isNullableNonEmptyString(state.updatedAt);
}

function isInputQueueItem(value: unknown): value is SupervisorInputQueueItem {
  if (value === null || typeof value !== "object") return false;
  const item = value as Partial<SupervisorInputQueueItem>;
  return isNonEmptyString(item.clientInputId)
    && isNullableNonEmptyString(item.turnId)
    && includesValue(SUPERVISOR_INPUT_STATUS_VALUES, item.status)
    && (item.queuePosition === null || isPositiveInteger(item.queuePosition))
    && isNullableNonEmptyString(item.updatedAt);
}

function isResumePoint(value: unknown): value is SupervisorResumePoint {
  if (value === null || typeof value !== "object") return false;
  const point = value as Partial<SupervisorResumePoint>;
  return isNullableNonEmptyString(point.cursor) && isNonNegativeInteger(point.sequence);
}

function isProjectionStateConsistent(
  projection: SupervisorRuntimeProjection,
): boolean {
  const runIdMatchesStatus = projection.run.status === "idle"
    ? projection.run.runId === null
    : projection.run.runId !== null;
  if (!runIdMatchesStatus) return false;

  const compression = projection.compression;
  if (compression.status === "compacting" && compression.lastOutcome !== null) {
    return false;
  }
  if (compression.status === "blocked" && compression.lastOutcome !== "failed") {
    return false;
  }
  if (compression.status === "idle") {
    const isInitial = compression.lastOutcome === null
      && compression.progressPercent === null;
    const isCompleted = compression.lastOutcome === "completed"
      && compression.progressPercent === 100;
    if (!isInitial && !isCompleted) return false;
  }

  const turnIds = new Set<string>();
  for (const item of projection.inputQueue) {
    const requiresTurnId = item.status === "queued"
      || item.status === "processing"
      || item.status === "accepted";
    if (requiresTurnId && item.turnId === null) return false;
    if (item.status !== "queued" && item.queuePosition !== null) return false;
    if (item.turnId !== null) {
      if (turnIds.has(item.turnId)) return false;
      turnIds.add(item.turnId);
    }
  }
  return true;
}

function readProjectionConversationId(value: unknown): string | null {
  if (value === null || typeof value !== "object") return null;
  const conversationId = (value as Partial<SupervisorRuntimeProjection>).conversationId;
  return isNonEmptyString(conversationId) ? conversationId : null;
}

function cloneProjection(value: unknown): SupervisorRuntimeProjection | null {
  if (value === null || typeof value !== "object") return null;
  const projection = value as Partial<SupervisorRuntimeProjection>;
  if (!isNonEmptyString(projection.conversationId)
    || !isRunState(projection.run)
    || !isCompressionState(projection.compression)
    || !Array.isArray(projection.inputQueue)
    || !projection.inputQueue.every(isInputQueueItem)
    || new Set(projection.inputQueue.map((item) => item.clientInputId)).size !== projection.inputQueue.length
    || !isResumePoint(projection.resume)) {
    return null;
  }
  let workspace: SupervisorWorkspaceProjection;
  try {
    workspace = cloneSupervisorWorkspaceProjection(value, projection.conversationId);
  } catch {
    return null;
  }
  let videoAgentWorkspace: VideoWorkspaceProjectionState;
  let videoAgentPlan: VideoAgentPlanState | null;
  let videoAgentConfirmation: VideoAgentConfirmationState | null;
  let videoAgentQuota: VideoAgentQuotaState | null;
  let videoAgentPlans: Record<string, VideoAgentPlanState>;
  let videoAgentPlanOrder: string[];
  try {
    videoAgentWorkspace = projection.videoAgentWorkspace === undefined
      ? createVideoWorkspaceProjectionState(projection.conversationId)
      : cloneVideoWorkspaceProjectionState(
        projection.videoAgentWorkspace,
        projection.conversationId,
      );
    videoAgentPlan = projection.videoAgentPlan === undefined
      ? null
      : cloneVideoAgentPlanState(projection.videoAgentPlan);
    videoAgentConfirmation = projection.videoAgentConfirmation === undefined
      ? null
      : cloneVideoAgentConfirmationState(projection.videoAgentConfirmation);
    videoAgentQuota = projection.videoAgentQuota === undefined
      ? null
      : cloneVideoAgentQuotaState(projection.videoAgentQuota);
    videoAgentPlans = {};
    videoAgentPlanOrder = [];
    if (projection.videoAgentPlans && typeof projection.videoAgentPlans === "object") {
      for (const [planId, plan] of Object.entries(projection.videoAgentPlans)) {
        const clonedPlan = cloneVideoAgentPlanState(plan);
        if (clonedPlan) videoAgentPlans[planId] = clonedPlan;
      }
    }
    if (Array.isArray(projection.videoAgentPlanOrder)) {
      videoAgentPlanOrder = projection.videoAgentPlanOrder.filter(
        (planId): planId is string => typeof planId === "string" && Boolean(videoAgentPlans[planId]),
      );
    }
    for (const planId of Object.keys(videoAgentPlans)) {
      if (!videoAgentPlanOrder.includes(planId)) videoAgentPlanOrder.push(planId);
    }
  } catch {
    return null;
  }
  const cloned = {
    conversationId: projection.conversationId,
    run: { ...projection.run },
    compression: { ...projection.compression },
    inputQueue: projection.inputQueue.map((item) => ({ ...item })),
    resume: { ...projection.resume },
    videoAgentWorkspace,
    videoAgentPlan,
    videoAgentPlans,
    videoAgentPlanOrder,
    videoAgentConfirmation,
    videoAgentQuota,
    ...workspace,
  };
  return isProjectionStateConsistent(cloned) ? cloned : null;
}

export function createSupervisorRuntimeState(conversationId: string): SupervisorRuntimeState {
  if (!isNonEmptyString(conversationId)) throw new TypeError("对话 ID 不能为空");
  return {
    conversationId,
    connection: {
      status: "idle",
      error: null,
    },
    run: {
      runId: null,
      status: "idle",
      updatedAt: null,
    },
    compression: {
      status: "idle",
      progressPercent: null,
      queuedInputCount: 0,
      lastOutcome: null,
      updatedAt: null,
    },
    inputQueue: [],
    messages: [],
    workflows: [],
    interrupt: null,
    videoAgentWorkspace: createVideoWorkspaceProjectionState(conversationId),
    videoAgentPlan: null,
    videoAgentPlans: {},
    videoAgentPlanOrder: [],
    videoAgentConfirmation: null,
    videoAgentQuota: null,
    resume: {
      cursor: null,
      sequence: 0,
    },
  };
}

export function supervisorRuntimeReducer(
  state: SupervisorRuntimeState,
  action: SupervisorRuntimeAction,
): SupervisorRuntimeState {
  switch (action.type) {
    case "conversation.reset":
      return createSupervisorRuntimeState(action.conversationId);
    case "connection.state_changed": {
      if (!includesValue(SUPERVISOR_CONNECTION_STATUS_VALUES, action.status)
        || !CONNECTION_TRANSITIONS[state.connection.status].includes(action.status)) {
        return state;
      }
      const error = action.status === "fatal" ? "Supervisor 连接无法恢复" : null;
      if (state.connection.status === action.status && state.connection.error === error) return state;
      return {
        ...state,
        connection: {
          status: action.status,
          error,
        },
      };
    }
    case "input.sending": {
      if (!isNonEmptyString(action.clientInputId)) return state;
      const current = state.inputQueue.find(
        (item) => item.clientInputId === action.clientInputId,
      );
      if (current && current.status !== "failed") return state;
      return {
        ...state,
        inputQueue: upsertInputItem(state.inputQueue, {
          clientInputId: action.clientInputId,
          turnId: current?.turnId ?? null,
          status: "sending",
          queuePosition: null,
          updatedAt: null,
        }),
      };
    }
    case "input.submit_failed": {
      const current = state.inputQueue.find(
        (item) => item.clientInputId === action.clientInputId,
      );
      if (!current || current.status !== "sending") return state;
      return {
        ...state,
        inputQueue: upsertInputItem(state.inputQueue, {
          ...current,
          status: "failed",
          queuePosition: null,
        }),
      };
    }
    case "snapshot.hydrated": {
      const conversationId = readProjectionConversationId(action.snapshot);
      if (conversationId !== null && conversationId !== state.conversationId) return state;
      const projection = cloneProjection(action.snapshot);
      if (!projection) {
        return {
          ...state,
          connection: {
            status: "fatal",
            error: "Supervisor Snapshot 状态不合法",
          },
        };
      }
      if (projection.resume.sequence < state.resume.sequence) {
        return state;
      }
      const incomingWorkspace = projection.videoAgentWorkspace.current;
      const videoAgentWorkspace = incomingWorkspace
        ? applyVideoWorkspaceSnapshot(state.videoAgentWorkspace, incomingWorkspace)
        : state.videoAgentWorkspace.current
          ? state.videoAgentWorkspace
          : projection.videoAgentWorkspace;
      const sameWorkspacePlan = state.videoAgentPlan
        && incomingWorkspace
        && state.videoAgentPlan.workspaceId === incomingWorkspace.workspaceId
        ? state.videoAgentPlan
        : null;
      const preferRicherPlan = (
        local: typeof state.videoAgentPlan,
        incoming: typeof projection.videoAgentPlan,
      ) => {
        if (!local) return incoming;
        if (!incoming) return local;
        if (local.planId !== incoming.planId) return incoming;
        const localSteps = Object.keys(local.steps).length;
        const incomingSteps = Object.keys(incoming.steps).length;
        return localSteps > incomingSteps ? local : incoming;
      };
      const resolvedPlan = preferRicherPlan(
        sameWorkspacePlan,
        projection.videoAgentPlan
          ?? (incomingWorkspace === null && state.videoAgentWorkspace.current
            ? state.videoAgentPlan
            : null),
      ) ?? sameWorkspacePlan;
      const nextPlans = { ...state.videoAgentPlans };
      // Snapshot 带回的历史 plans 是服务端权威；本地仅补齐步骤更丰富的同 id 版本。
      for (const [planId, incomingPlan] of Object.entries(projection.videoAgentPlans || {})) {
        nextPlans[planId] = preferRicherPlan(nextPlans[planId] ?? null, incomingPlan) ?? incomingPlan;
      }
      for (const plan of Object.values(state.videoAgentPlans)) {
        const incomingSame = resolvedPlan?.planId === plan.planId ? resolvedPlan : null;
        nextPlans[plan.planId] = preferRicherPlan(plan, incomingSame) ?? plan;
      }
      if (resolvedPlan) {
        nextPlans[resolvedPlan.planId] = preferRicherPlan(
          nextPlans[resolvedPlan.planId] ?? null,
          resolvedPlan,
        ) ?? resolvedPlan;
      }
      const nextOrder = [
        ...(projection.videoAgentPlanOrder?.length
          ? projection.videoAgentPlanOrder
          : state.videoAgentPlanOrder),
      ];
      for (const planId of Object.keys(nextPlans)) {
        if (!nextOrder.includes(planId)) nextOrder.push(planId);
      }
      return {
        ...projection,
        videoAgentWorkspace,
        videoAgentPlan: resolvedPlan,
        videoAgentPlans: nextPlans,
        videoAgentPlanOrder: nextOrder,
        videoAgentConfirmation: projection.videoAgentConfirmation
          ?? (incomingWorkspace === null && state.videoAgentWorkspace.current
            ? state.videoAgentConfirmation
            : null),
        videoAgentQuota: projection.videoAgentQuota
          ?? (incomingWorkspace === null && state.videoAgentWorkspace.current
            ? state.videoAgentQuota
            : null),
        connection: state.connection,
      };
    }
    case "event.received":
      return applyAgentEvent(state, action.event);
  }
}
