import type {
  AgentEventEnvelope,
  ExternalJobRef,
  JsonObject,
  JsonValue,
  WorkflowRecord,
} from "./contracts.js";
import {
  EXTERNAL_JOB_STATUS_VALUES,
  WORKFLOW_KIND_VALUES,
  WORKFLOW_STATUS_VALUES,
} from "./contracts.js";
import type { SupervisorRuntimeProjection } from "./reducer.js";
import {
  projectVideoAgentConfirmationSnapshot,
  projectVideoAgentPlanSnapshot,
  projectVideoAgentQuotaSnapshot,
} from "../../features/video-agent/state/reducer.js";
import {
  applyVideoWorkspaceSnapshot,
  createVideoWorkspaceProjectionState,
  projectVideoWorkspaceSnapshot,
} from "../../features/video-agent/state/workspace.js";
import type { WorkflowProgressSnapshot } from "../workflowTaskBoard.js";

type SupervisorArtifactType = (typeof ARTIFACT_TYPE_VALUES)[number];

export interface SupervisorChatArtifact {
  type: SupervisorArtifactType;
  title: string;
  description: string;
  actionLabel: string;
  [key: string]: unknown;
}

export interface SupervisorChatMessage {
  id: string;
  conversationId: string;
  runId?: string;
  workflowId?: string;
  artifactRef?: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  materials?: JsonObject[];
  artifact?: SupervisorChatArtifact;
}

export interface PendingSupervisorMessageProjection {
  id: string;
  conversationId: string;
  content: string;
  materials?: JsonObject[];
}

export interface SupervisorInterruptProjection {
  interruptId: string;
  conversationId: string;
  payload: JsonObject;
}

export interface SupervisorWorkspaceProjection {
  messages: SupervisorChatMessage[];
  workflows: WorkflowRecord[];
  interrupt: SupervisorInterruptProjection | null;
}

const WORKSPACE_PROJECTION_ERROR = "Supervisor 工作区投影状态不合法";
const ARTIFACT_TYPE_VALUES = [
  "brief",
  "results",
  "segments",
  "edit",
  "qc",
  "directions",
  "plan",
  "image_prepare",
  "image_edit_options",
  "scene_asset_model_options",
  "image_result",
  "video_scene_packages",
  "video_quality_review",
  "video_analysis_result",
  "video_result",
  "jianying_draft",
  "ppt_outline",
  "ppt_images",
  "ppt_file",
] as const;

function fail(): never {
  throw new TypeError(WORKSPACE_PROJECTION_ERROR);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}

function isIsoTime(value: unknown): value is string {
  return isNonEmptyString(value) && Number.isFinite(Date.parse(value));
}

function includesValue<TValue extends string>(
  values: readonly TValue[],
  value: unknown,
): value is TValue {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

function cloneJsonValue(value: unknown, seen = new Set<object>()): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "object") return fail();
  if (seen.has(value)) return fail();
  seen.add(value);
  try {
    if (Array.isArray(value)) return value.map((item) => cloneJsonValue(item, seen));
    const cloned: JsonObject = {};
    for (const [key, item] of Object.entries(value)) {
      cloned[key] = cloneJsonValue(item, seen);
    }
    return cloned;
  } finally {
    seen.delete(value);
  }
}

function cloneJsonObject(value: unknown): JsonObject {
  const cloned = cloneJsonValue(value);
  return isRecord(cloned) ? cloned as JsonObject : fail();
}

function jsonValuesEqual(left: JsonValue, right: JsonValue): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function readArtifact(
  message: Record<string, unknown>,
  payload: JsonObject,
): SupervisorChatArtifact | null {
  const direct = message.artifact === undefined || message.artifact === null
    ? null
    : cloneJsonObject(message.artifact);
  const nested = payload.artifact === undefined || payload.artifact === null
    ? null
    : cloneJsonObject(payload.artifact);
  if (direct && nested && !jsonValuesEqual(direct, nested)) return fail();
  const artifact = direct ?? nested;
  if (!artifact) return null;
  if (!includesValue(ARTIFACT_TYPE_VALUES, artifact.type)
    || typeof artifact.title !== "string"
    || typeof artifact.description !== "string"
    || typeof artifact.actionLabel !== "string") {
    return fail();
  }
  return artifact as unknown as SupervisorChatArtifact;
}

function readMaterials(message: Record<string, unknown>, payload: JsonObject): JsonObject[] {
  const value = message.materials ?? payload.materials ?? [];
  if (!Array.isArray(value)) return fail();
  return value.map((item) => cloneJsonObject(item));
}

function projectMessage(value: unknown, conversationId: string): SupervisorChatMessage | null {
  if (!isRecord(value)) return fail();
  if (value.role === "system") return null;
  if (value.role !== "user" && value.role !== "assistant") return fail();
  for (const owner of [value.conversation_id, value.conversationId]) {
    if (owner !== undefined && owner !== conversationId) return fail();
  }
  const payload = value.payload === undefined ? {} : cloneJsonObject(value.payload);
  const messageId = isNonEmptyString(payload.client_message_id)
    ? payload.client_message_id
    : value.message_id ?? value.id;
  const time = value.created_at ?? value.time ?? "";
  if (!isNonEmptyString(messageId) || typeof value.content !== "string" || typeof time !== "string") {
    return fail();
  }
  const materials = readMaterials(value, payload);
  const artifact = readArtifact(value, payload);
  const runId = value.run_id ?? value.runId;
  const workflowId = payload.workflow_id ?? value.workflowId;
  const artifactRef = payload.artifact_ref ?? value.artifactRef;
  const hasWorkflowIdentity = isNonEmptyString(runId)
    && isNonEmptyString(workflowId)
    && runId === workflowId
    && isNonEmptyString(artifactRef)
    && /^artifact:\S+$/u.test(artifactRef);
  if (artifact && value.role === "assistant" && !hasWorkflowIdentity) {
    // VideoAgent「脚本方案待确认」等本地卡片完全没有 workflow 身份：保留卡片，勿让 Snapshot fatal。
    // 若只给了残缺/冲突身份，仍视为投影非法。
    if (
      runId === undefined
      && workflowId === undefined
      && artifactRef === undefined
    ) {
      return {
        id: messageId,
        conversationId,
        role: value.role,
        content: value.content,
        time,
        ...(materials.length > 0 ? { materials } : {}),
        artifact,
      };
    }
    return fail();
  }
  if (!artifact && (
    (runId !== undefined && !isNonEmptyString(runId))
    || (workflowId !== undefined && !isNonEmptyString(workflowId))
    || (artifactRef !== undefined && !isNonEmptyString(artifactRef))
  )) return fail();
  return {
    id: messageId,
    conversationId,
    ...(isNonEmptyString(runId) ? { runId } : {}),
    ...(isNonEmptyString(workflowId) ? { workflowId } : {}),
    ...(isNonEmptyString(artifactRef) ? { artifactRef } : {}),
    role: value.role,
    content: value.content,
    time,
    ...(materials.length > 0 ? { materials } : {}),
    ...(artifact ? { artifact } : {}),
  };
}

export function selectSupervisorArtifactMessage(
  messages: readonly SupervisorChatMessage[],
  target: {
    workflowId: string;
    artifactRef: string | null;
    allowedTypes: readonly string[];
  },
): SupervisorChatMessage | null {
  if (
    !isNonEmptyString(target.workflowId)
    || !isNonEmptyString(target.artifactRef)
    || !target.allowedTypes.every((item) => includesValue(ARTIFACT_TYPE_VALUES, item))
  ) return null;
  const allowed = new Set(target.allowedTypes);
  return [...messages].reverse().find((message) => (
    message.runId === target.workflowId
    && message.workflowId === target.workflowId
    && message.artifactRef === target.artifactRef
    && Boolean(message.artifact && allowed.has(message.artifact.type))
  )) ?? null;
}

function upsertMessage(
  messages: readonly SupervisorChatMessage[],
  message: SupervisorChatMessage,
): SupervisorChatMessage[] {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index < 0) return [...messages, message];
  const next = [...messages];
  next[index] = message;
  return next;
}

export function mergeSupervisorMessagesWithPending(
  authoritativeMessages: readonly SupervisorChatMessage[],
  pendingMessages: readonly PendingSupervisorMessageProjection[],
  conversationId: string,
): SupervisorChatMessage[] {
  if (!isNonEmptyString(conversationId)) return fail();
  let messages = [...authoritativeMessages];
  for (const pendingMessage of pendingMessages) {
    // 路由切换期间旧会话的本地 pending 可能晚一拍到达，只保留当前会话数据。
    if (pendingMessage.conversationId !== conversationId) continue;
    if (!isNonEmptyString(pendingMessage.id) || typeof pendingMessage.content !== "string") return fail();
    if (messages.some((message) => message.id === pendingMessage.id)) continue;
    const materials = (pendingMessage.materials || []).map((material) => cloneJsonObject(material));
    messages = [...messages, {
      id: pendingMessage.id,
      conversationId,
      role: "user",
      content: pendingMessage.content,
      time: "",
      ...(materials.length > 0 ? { materials } : {}),
    }];
  }
  return messages;
}

function projectMessages(value: unknown, conversationId: string): SupervisorChatMessage[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return fail();
  let messages: SupervisorChatMessage[] = [];
  for (const item of value) {
    const message = projectMessage(item, conversationId);
    if (message) messages = upsertMessage(messages, message);
  }
  return messages;
}

function projectExternalJob(value: unknown, workflowId: string): ExternalJobRef | null {
  if (value === null) return null;
  if (!isRecord(value)
    || !isNonEmptyString(value.job_id)
    || (value.provider_job_id !== null && !isNonEmptyString(value.provider_job_id))
    || value.workflow_id !== workflowId
    || !isNonEmptyString(value.stage)
    || !includesValue(EXTERNAL_JOB_STATUS_VALUES, value.status)
    || !isPositiveInteger(value.attempt)
    || !isNonEmptyString(value.idempotency_key)
    || (value.next_poll_at !== null && !isIsoTime(value.next_poll_at))
    || (value.lease_owner !== null && !isNonEmptyString(value.lease_owner))
    || (value.lease_expires_at !== null && !isIsoTime(value.lease_expires_at))) {
    return fail();
  }
  return {
    job_id: value.job_id,
    provider_job_id: value.provider_job_id,
    workflow_id: value.workflow_id,
    stage: value.stage,
    status: value.status,
    attempt: value.attempt,
    idempotency_key: value.idempotency_key,
    next_poll_at: value.next_poll_at,
    lease_owner: value.lease_owner,
    lease_expires_at: value.lease_expires_at,
  };
}

function projectWorkflow(value: unknown, conversationId: string): WorkflowRecord {
  if (!isRecord(value)
    || !isNonEmptyString(value.workflow_id)
    || value.conversation_id !== conversationId
    || !includesValue(WORKFLOW_KIND_VALUES, value.kind)
    || !includesValue(WORKFLOW_STATUS_VALUES, value.status)
    || !isNonEmptyString(value.current_stage)
    || !isPositiveInteger(value.stage_version)
    || !Array.isArray(value.latest_artifact_refs)
    || !value.latest_artifact_refs.every(isNonEmptyString)
    || !isPositiveInteger(value.context_version)
    || !isIsoTime(value.created_at)
    || !isIsoTime(value.updated_at)) {
    return fail();
  }
  return {
    workflow_id: value.workflow_id,
    conversation_id: conversationId,
    kind: value.kind,
    status: value.status,
    current_stage: value.current_stage,
    stage_version: value.stage_version,
    creation_contract_snapshot: cloneJsonObject(value.creation_contract_snapshot),
    pending_external_job: projectExternalJob(value.pending_external_job, value.workflow_id),
    latest_artifact_refs: [...value.latest_artifact_refs],
    context_version: value.context_version,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

function upsertWorkflow(
  workflows: readonly WorkflowRecord[],
  workflow: WorkflowRecord,
): WorkflowRecord[] {
  const index = workflows.findIndex((item) => item.workflow_id === workflow.workflow_id);
  if (index < 0) return [...workflows, workflow];
  const current = workflows[index];
  if (workflow.stage_version < current.stage_version) return [...workflows];
  if (workflow.stage_version === current.stage_version) {
    const currentTime = Date.parse(current.updated_at);
    const nextTime = Date.parse(workflow.updated_at);
    if (nextTime < currentTime) return [...workflows];
    if (nextTime === currentTime
      && !jsonValuesEqual(
        cloneJsonValue(current as unknown as Record<string, unknown>),
        cloneJsonValue(workflow as unknown as Record<string, unknown>),
      )) {
      return fail();
    }
  }
  const next = [...workflows];
  next[index] = workflow;
  return next;
}

function projectWorkflows(value: unknown, conversationId: string): WorkflowRecord[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return fail();
  let workflows: WorkflowRecord[] = [];
  for (const item of value) workflows = upsertWorkflow(workflows, projectWorkflow(item, conversationId));
  return workflows;
}

function projectInterrupt(value: unknown, conversationId: string): SupervisorInterruptProjection | null {
  if (value === undefined || value === null) return null;
  if (!isRecord(value)) return fail();
  const interruptId = value.interrupt_id ?? value.interruptId;
  const owner = value.conversation_id ?? value.conversationId;
  if (!isNonEmptyString(interruptId)) return fail();
  if (owner !== undefined && owner !== conversationId) return fail();
  return {
    interruptId,
    conversationId,
    payload: value.payload === undefined
      ? cloneJsonObject(value)
      : cloneJsonObject(value.payload),
  };
}

export function cloneSupervisorWorkspaceProjection(
  value: unknown,
  conversationId: string,
): SupervisorWorkspaceProjection {
  if (!isRecord(value)) return fail();
  return {
    messages: projectMessages(value.messages, conversationId),
    workflows: projectWorkflows(value.workflows, conversationId),
    interrupt: projectInterrupt(value.interrupt, conversationId),
  };
}

export function projectSupervisorSnapshot(
  value: JsonValue,
  conversationId: string,
): SupervisorRuntimeProjection {
  if (!isRecord(value) || value.conversationId !== conversationId) return fail();
  const workspace = cloneSupervisorWorkspaceProjection(value, conversationId);
  const videoAgentWorkspace = createVideoWorkspaceProjectionState(conversationId);
  let projectedVideoAgentWorkspace = videoAgentWorkspace;
  let videoAgentPlan = null;
  let videoAgentPlans: Record<string, NonNullable<ReturnType<typeof projectVideoAgentPlanSnapshot>>> = {};
  let videoAgentPlanOrder: string[] = [];
  let videoAgentConfirmation = null;
  let videoAgentQuota = null;
  let agentThinkingHistory: SupervisorRuntimeProjection["agentThinkingHistory"] = [];
  const rawThinkingHistory = Array.isArray(value.thinkingHistory)
    ? value.thinkingHistory
    : (
      isRecord(value.videoAgent) && Array.isArray(value.videoAgent.thinkingHistory)
        ? value.videoAgent.thinkingHistory
        : []
    );
  for (const item of rawThinkingHistory) {
    if (!isRecord(item)) continue;
    const turnId = typeof item.turnId === "string" ? item.turnId.trim() : "";
    if (!turnId) continue;
    const status = item.status === "streaming" ? "streaming" : "completed";
    agentThinkingHistory.push({
      turnId,
      title: typeof item.title === "string" && item.title.trim()
        ? item.title
        : "思考中",
      subtitle: typeof item.subtitle === "string" && item.subtitle.trim()
        ? item.subtitle
        : "",
      text: typeof item.text === "string" ? item.text : "",
      answer: typeof item.answer === "string" ? item.answer : "",
      startedAt: typeof item.startedAt === "string" ? item.startedAt : null,
      status,
      afterMessageId: typeof item.afterMessageId === "string" && item.afterMessageId.trim()
        ? item.afterMessageId.trim()
        : (
          typeof item.clientInputId === "string" && item.clientInputId.trim()
            ? item.clientInputId.trim()
            : null
        ),
      clientInputId: typeof item.clientInputId === "string" && item.clientInputId.trim()
        ? item.clientInputId.trim()
        : null,
    });
  }
  if (value.videoAgent !== null && value.videoAgent !== undefined) {
    if (!isRecord(value.videoAgent)) return fail();
    projectedVideoAgentWorkspace = applyVideoWorkspaceSnapshot(
      videoAgentWorkspace,
      projectVideoWorkspaceSnapshot(value.videoAgent.workspace, conversationId),
    );
    videoAgentPlan = projectVideoAgentPlanSnapshot(
      value.videoAgent.plan,
      value.videoAgent.steps,
    );
    const history = Array.isArray(value.videoAgent.plans) ? value.videoAgent.plans : [];
    for (const item of history) {
      if (!isRecord(item)) return fail();
      const historical = projectVideoAgentPlanSnapshot(item.plan, item.steps);
      if (!historical) continue;
      videoAgentPlans[historical.planId] = historical;
      if (!videoAgentPlanOrder.includes(historical.planId)) {
        videoAgentPlanOrder.push(historical.planId);
      }
    }
    if (videoAgentPlan) {
      videoAgentPlans[videoAgentPlan.planId] = videoAgentPlan;
      if (!videoAgentPlanOrder.includes(videoAgentPlan.planId)) {
        videoAgentPlanOrder.push(videoAgentPlan.planId);
      }
    }
    videoAgentConfirmation = projectVideoAgentConfirmationSnapshot(
      value.videoAgent.confirmation,
    );
    videoAgentQuota = projectVideoAgentQuotaSnapshot(value.videoAgent.quota);
    if (
      videoAgentPlan
      && videoAgentPlan.workspaceId !== projectedVideoAgentWorkspace.current?.workspaceId
    ) return fail();
    const waitingSteps = videoAgentPlan
      ? Object.values(videoAgentPlan.steps).filter(
        (step) => step.status === "awaiting_confirmation",
      )
      : [];
    // 旧 Plan 步骤确认：必须与 confirmation 一一对应。
    // 原生 native_pending：Snapshot 有 confirmation、Plan 可无 waiting step（tool_call_id ≠ step_id）。
    if (waitingSteps.length > 1) return fail();
    if (waitingSteps.length === 1) {
      if (!videoAgentConfirmation) return fail();
      if (
        !videoAgentPlan
        || videoAgentConfirmation.planId !== videoAgentPlan.planId
        || waitingSteps[0]?.stepId !== videoAgentConfirmation.stepId
      ) return fail();
    } else if (
      videoAgentConfirmation
      && videoAgentPlan
      && videoAgentConfirmation.planId !== videoAgentPlan.planId
    ) {
      return fail();
    }
    if (
      videoAgentQuota
      && (
        !videoAgentPlan
        || videoAgentQuota.planId !== videoAgentPlan.planId
        || videoAgentPlan.steps[videoAgentQuota.stepId]?.status !== "running"
      )
    ) return fail();
  }
  // V2.1 批次 D：有 VideoAgent 投影时不吸收 Workflow 影子状态。
  const workflows = value.videoAgent !== null && value.videoAgent !== undefined
    ? []
    : workspace.workflows;
  return {
    conversationId,
    run: value.run as unknown as SupervisorRuntimeProjection["run"],
    compression: value.compression as unknown as SupervisorRuntimeProjection["compression"],
    inputQueue: value.inputQueue as unknown as SupervisorRuntimeProjection["inputQueue"],
    resume: value.resume as unknown as SupervisorRuntimeProjection["resume"],
    videoAgentWorkspace: projectedVideoAgentWorkspace,
    videoAgentPlan,
    videoAgentPlans,
    videoAgentPlanOrder,
    videoAgentConfirmation,
    videoAgentQuota,
    agentThinkingHistory,
    messages: workspace.messages,
    workflows,
    interrupt: workspace.interrupt,
  };
}

export function applySupervisorWorkspaceEvent(
  workspace: SupervisorWorkspaceProjection,
  event: AgentEventEnvelope,
): SupervisorWorkspaceProjection {
  switch (event.type) {
    case "message.upserted": {
      const rawMessage = event.payload.message ?? event.payload;
      const message = projectMessage(rawMessage, event.conversation_id);
      return message ? { ...workspace, messages: upsertMessage(workspace.messages, message) } : workspace;
    }
    case "workflow.progressed": {
      const rawWorkflow = event.payload.workflow ?? event.payload;
      const workflow = projectWorkflow(rawWorkflow, event.conversation_id);
      return { ...workspace, workflows: upsertWorkflow(workspace.workflows, workflow) };
    }
    case "interrupt.opened": {
      const rawInterrupt = event.payload.interrupt ?? event.payload;
      const interrupt = projectInterrupt(rawInterrupt, event.conversation_id);
      if (!interrupt) return fail();
      return { ...workspace, interrupt };
    }
    case "interrupt.closed": {
      const interruptId = event.payload.interrupt_id;
      if (!isNonEmptyString(interruptId)) return fail();
      if (!workspace.interrupt || workspace.interrupt.interruptId !== interruptId) return workspace;
      return { ...workspace, interrupt: null };
    }
    default:
      return workspace;
  }
}

function normalizedVideoPhase(stage: string): string {
  const phases: Record<string, string> = {
    intake: "intake_running",
    direction_generation: "creative_directions_running",
    direction_review: "creative_directions_ready",
    plan_generation: "plan_generation_running",
    plan_review: "plan_review_pending",
    plan_approved: "plan_approved",
    prepare_scene_packages: "scene_package_running",
    generate_scene_assets: "scene_asset_generation_running",
    scene_package_review: "scene_package_ready",
    generate_scene_videos: "video_generation_running",
    scene_video_review: "video_review_pending",
    merge_video: "video_merge_running",
    quality_review: "video_quality_review_running",
    video_review: "video_review_pending",
    completed: "video_accepted",
  };
  return phases[stage] ?? stage;
}

function phaseForWorkflow(workflow: WorkflowRecord): string {
  let phase = workflow.kind === "video"
    ? normalizedVideoPhase(workflow.current_stage)
    : workflow.current_stage;
  if (workflow.status === "cancelled") return "form_cancelled";
  if (workflow.status === "paused_quota" && !phase.includes("quota_paused")) {
    phase = `${phase}_quota_paused`;
  }
  if (workflow.status === "failed" && !phase.includes("failed")) phase = `${phase}_failed`;
  if (workflow.status === "completed" && workflow.kind === "image") return "image_accepted";
  if (workflow.status === "completed" && workflow.kind === "ppt") return "ppt_done";
  return phase;
}

function latestWorkflow(workflows: readonly WorkflowRecord[]): WorkflowRecord | null {
  return [...workflows].sort((left, right) => (
    Date.parse(right.updated_at) - Date.parse(left.updated_at)
    || right.stage_version - left.stage_version
    || right.workflow_id.localeCompare(left.workflow_id)
  ))[0] ?? null;
}

export function projectSupervisorWorkflowProgress(
  workflows: readonly WorkflowRecord[],
): WorkflowProgressSnapshot | null {
  const workflow = latestWorkflow(workflows);
  if (!workflow || workflow.kind === "video_analysis") return null;
  const sourceMessageId = workflow.creation_contract_snapshot.source_message_id;
  const flowKind = workflow.creation_contract_snapshot.flow_kind === "direct_image_edit"
    ? "direct_image_edit"
    : "standard";
  const scenePackageStage = workflow.current_stage === "prepare_scene_packages"
    || workflow.current_stage === "generate_scene_assets"
    ? workflow.current_stage
    : workflow.current_stage === "scene_package_review"
      ? "completed"
      : null;
  return {
    version: 1,
    intent: workflow.kind,
    flow_kind: flowKind,
    source_message_id: isNonEmptyString(sourceMessageId) ? sourceMessageId : workflow.workflow_id,
    last_phase: phaseForWorkflow(workflow),
    scene_package_stage: scenePackageStage,
    updated_at: workflow.updated_at,
  };
}
