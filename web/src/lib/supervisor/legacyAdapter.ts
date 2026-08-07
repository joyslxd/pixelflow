import {
  ORCHESTRATION_MODE_VALUES,
  type JsonObject,
  type JsonValue,
  type OrchestrationMode,
} from "./contracts.js";

export const LEGACY_PENDING_FIELD_PAIRS = [
  { camel: "pendingMessageJob", snake: "pending_message_job" },
  { camel: "pendingIntakeJob", snake: "pending_intake_job" },
  { camel: "pendingDirectionJob", snake: "pending_direction_job" },
  { camel: "pendingPlanJob", snake: "pending_plan_job" },
  { camel: "pendingPlanRevisionRequest", snake: "pending_plan_revision_request" },
  { camel: "pendingPlanRevisionChoice", snake: "pending_plan_revision_choice" },
  { camel: "pendingImageEditRequest", snake: "pending_image_edit_request" },
  { camel: "pendingImageJob", snake: "pending_image_job" },
  { camel: "pendingImageRevision", snake: "pending_image_revision" },
  { camel: "pendingScenePackageJob", snake: "pending_scene_package_job" },
  { camel: "pendingVideoJob", snake: "pending_video_job" },
  { camel: "pendingVideoRevision", snake: "pending_video_revision" },
  { camel: "pendingPptJob", snake: "pending_ppt_job" },
  { camel: "pendingJianyingDraftJob", snake: "pending_jianying_draft_job" },
] as const;

export type LegacyPendingField = (typeof LEGACY_PENDING_FIELD_PAIRS)[number]["camel"];

export type LegacyPendingProjection = Record<LegacyPendingField, JsonObject | null>;

export interface LegacyMessageViewModel {
  id: string;
  conversationId: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  materials: JsonObject[];
  artifact: JsonObject | null;
}

export interface LegacyArtifactViewModel {
  messageId: string;
  type: string;
  artifact: JsonObject;
}

export interface LegacyConversationViewModel {
  conversationId: string;
  orchestrationMode: OrchestrationMode;
  orchestrationVersion: 1;
  agentRuntimeMode: WorkspaceAgentRuntimeMode;
  title: string;
  currentTaskId: string | null;
  lastPhase: string;
  context: JsonObject;
  pending: LegacyPendingProjection;
  hasPendingWork: boolean;
  messages: LegacyMessageViewModel[];
  artifacts: LegacyArtifactViewModel[];
}

const INVALID_SNAPSHOT = "历史对话 Snapshot 状态不合法";
const INVALID_PENDING = "历史对话 pending 状态不合法";
const CONFLICTING_PENDING = "历史对话 pending 状态不一致";
const INVALID_PENDING_OWNER = "历史对话 pending 状态归属不合法";
const CONFLICTING_ARTIFACT = "历史对话 artifact 状态不一致";

export type WorkspaceAgentRuntimeMode = "off" | "shadow" | "assist" | "primary";
export interface ConversationWriteSequencer {
  run<T>(conversationId: string, operation: () => Promise<T>): Promise<T>;
}

export function createConversationWriteSequencer(): ConversationWriteSequencer {
  const tails = new Map<string, Promise<void>>();
  return {
    async run<T>(conversationId: string, operation: () => Promise<T>): Promise<T> {
      const previous = tails.get(conversationId) || Promise.resolve();
      let releaseCurrent: () => void = () => {};
      const current = new Promise<void>((resolve) => {
        releaseCurrent = resolve;
      });
      tails.set(conversationId, current);
      await previous;
      try {
        return await operation();
      } finally {
        releaseCurrent();
        if (tails.get(conversationId) === current) tails.delete(conversationId);
      }
    },
  };
}

function fail(message = INVALID_SNAPSHOT): never {
  throw new TypeError(message);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function cloneJsonValue(value: unknown, ancestors = new WeakSet<object>()): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "object" || ancestors.has(value)) return fail();

  ancestors.add(value);
  const cloned: JsonValue = Array.isArray(value)
    ? value.map((item) => cloneJsonValue(item, ancestors))
    : Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneJsonValue(item, ancestors)]),
    );
  ancestors.delete(value);
  return cloned;
}

function cloneJsonObject(value: unknown, message = INVALID_SNAPSHOT): JsonObject {
  if (!isRecord(value)) return fail(message);
  const cloned = cloneJsonValue(value);
  if (!isRecord(cloned)) return fail(message);
  return cloned as JsonObject;
}

function jsonValuesEqual(left: JsonValue, right: JsonValue): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((item, index) => jsonValuesEqual(item, right[index]));
  }
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && jsonValuesEqual(left[key] as JsonValue, right[key] as JsonValue));
}

function readNullableObject(value: unknown, message: string): JsonObject | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return cloneJsonObject(value, message);
}

function validatePendingOwner(value: JsonObject, conversationId: string): void {
  const snakeOwner = value.conversation_id;
  const camelOwner = value.conversationId;
  for (const owner of [snakeOwner, camelOwner]) {
    if (owner === undefined) continue;
    if (!isNonEmptyString(owner) || owner !== conversationId) fail(INVALID_PENDING_OWNER);
  }
}

function resolvePending(
  context: Record<string, unknown>,
  conversationId: string,
): LegacyPendingProjection {
  return Object.fromEntries(LEGACY_PENDING_FIELD_PAIRS.map(({ camel, snake }) => {
    const camelValue = readNullableObject(context[camel], INVALID_PENDING);
    const snakeValue = readNullableObject(context[snake], INVALID_PENDING);
    if (camelValue && snakeValue && !jsonValuesEqual(camelValue, snakeValue)) {
      fail(CONFLICTING_PENDING);
    }
    const value = camelValue ?? snakeValue ?? null;
    if (value) validatePendingOwner(value, conversationId);
    return [camel, value];
  })) as LegacyPendingProjection;
}

function resolveOrchestration(
  conversation: Record<string, unknown>,
): { mode: OrchestrationMode; version: 1 } {
  const rawMode = conversation.orchestration_mode;
  const rawVersion = conversation.orchestration_version;
  if (rawMode === undefined && rawVersion === undefined) {
    return { mode: "frontend_v2", version: 1 };
  }
  if (typeof rawMode !== "string"
    || !(ORCHESTRATION_MODE_VALUES as readonly string[]).includes(rawMode)
    || rawVersion !== 1) {
    return fail();
  }
  return { mode: rawMode as OrchestrationMode, version: 1 };
}

/**
 * 解析 Workspace 当前会话的编排归属。
 *
 * 服务端归属是唯一权威；缺失、非法或仍有旧 pending 任务时，必须回退到旧
 * frontend_v2，避免新旧运行时同时推进同一个计费流程。
 */
export function resolveWorkspaceOrchestrationMode(value: unknown): OrchestrationMode {
  if (!isRecord(value)) return "frontend_v2";
  const conversation = isRecord(value.conversation) ? value.conversation : value;
  if (conversation.context !== undefined && !isRecord(conversation.context)) return "frontend_v2";
  const context = isRecord(conversation.context) ? conversation.context : {};
  try {
    const orchestration = resolveOrchestration(conversation);
    if (orchestration.mode !== "video_agent_v2") return orchestration.mode;
    const conversationId = conversation.conversation_id;
    if (!isNonEmptyString(conversationId)) return "frontend_v2";
    const pending = resolvePending(context, conversationId);
    return Object.values(pending).some((item) => item !== null)
      ? "frontend_v2"
      : orchestration.mode;
  } catch {
    return "frontend_v2";
  }
}

/**
 * 只读取服务端保留的 Runtime 命名空间，普通业务 context 字段不能伪造接入状态。
 */
export function resolveWorkspaceAgentRuntimeMode(
  value: unknown,
): WorkspaceAgentRuntimeMode {
  if (!isRecord(value)) return "off";
  const conversation = isRecord(value.conversation) ? value.conversation : value;
  if (!isRecord(conversation.context)) return "off";
  const runtime = conversation.context.__agent_runtime;
  if (!isRecord(runtime)) return "off";
  const mode = runtime.mode;
  const contextVersion = runtime.context_version;
  if (
    !["shadow", "assist", "primary"].includes(String(mode))
    || !Array.isArray(runtime.enabled_intents)
    || typeof runtime.context_compaction_enabled !== "boolean"
    || !Number.isSafeInteger(contextVersion)
    || Number(contextVersion) < 0
  ) {
    return "off";
  }
  return mode as WorkspaceAgentRuntimeMode;
}

/**
 * 只信任服务端 Runtime 命名空间冻结的本会话 live Handler 就绪事实。
 */
export function resolveWorkspacePrimaryExecutionReady(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const conversation = isRecord(value.conversation) ? value.conversation : value;
  if (!isRecord(conversation.context)) return false;
  const runtime = conversation.context.__agent_runtime;
  return isRecord(runtime) && runtime.primary_execution_ready === true;
}

export interface WorkspaceRuntimePolicy {
  supervisorEnabled: boolean;
  legacyRunnerEnabled: boolean;
  legacyArtifactActionsEnabled: boolean;
}

export type AssistHandoffAction =
  | "register"
  | "wait"
  | "continue_legacy"
  | "acknowledge"
  | "failed"
  | "unavailable";

export interface AssistHandoffPolicyInput {
  orchestrationMode?: OrchestrationMode;
  primaryExecutionReady?: boolean;
  registrationStatus: "pending" | "registered";
  serverInputStatus?: "sending" | "queued" | "processing" | "accepted" | "failed";
  serverRunStatus?: "idle" | "running" | "waiting_user" | "paused" | "failed" | "completed";
  continueLegacy: boolean;
  legacyBusy: boolean;
  dialogOpen: boolean;
  pendingPlanRevision: boolean;
}

export type UnavailableSupervisorRecoveryAction =
  | "none"
  | "persist_notice"
  | "finalize";

export interface UnavailableSupervisorRecoveryInput {
  orchestrationMode?: OrchestrationMode;
  primaryExecutionReady?: boolean;
  connectionStatus: "idle" | "connecting" | "connected" | "reconnecting" | "fatal";
  pendingCount: number;
  hasActiveInput: boolean;
  markerVersion: number;
  noticePersisted: boolean;
}

/**
 * 历史未接线 Supervisor 只按会话收敛一次。
 *
 * 提示已经落库但 marker 尚未写回时返回 finalize，用于修复进程在两次写入之间
 * 中断的窗口；没有服务端活跃输入时不主动污染历史会话。
 */
export function resolveUnavailableSupervisorRecovery(
  input: UnavailableSupervisorRecoveryInput,
): UnavailableSupervisorRecoveryAction {
  if (
    input.orchestrationMode !== "video_agent_v2"
    || input.primaryExecutionReady === true
    || input.connectionStatus !== "connected"
    || (input.pendingCount <= 0 && !input.hasActiveInput)
  ) {
    return "none";
  }
  if (
    input.markerVersion >= 1
    && input.noticePersisted
    && input.pendingCount <= 0
  ) {
    return "none";
  }
  return input.noticePersisted ? "finalize" : "persist_notice";
}

/**
 * assist 只在服务端确认当前 Turn 可执行后接力旧流程。
 *
 * queued/sending 以及尚未从 Snapshot 找到的 Turn 必须等待，刷新恢复时也不会
 * 重新提交旧流程；不需要旧流程的 Turn 则只确认交接完成。
 */
export function resolveAssistHandoffAction(
  input: AssistHandoffPolicyInput,
): AssistHandoffAction {
  if (
    input.orchestrationMode === "video_agent_v2" &&
    input.primaryExecutionReady !== true
  ) {
    return "unavailable";
  }
  if (input.registrationStatus === "pending") return "register";
  if (input.orchestrationMode === "video_agent_v2") {
    if (
      input.serverInputStatus === "failed" ||
      input.serverRunStatus === "failed"
    ) {
      return "failed";
    }
    if (
      input.serverInputStatus === undefined &&
      input.serverRunStatus === "completed"
    ) {
      return "acknowledge";
    }
    // 活跃 Turn 必须由真实 Graph 执行器推进，assist 接力层只等待权威终态。
    return "wait";
  }
  if (
    input.serverInputStatus === undefined
    || input.serverInputStatus === "queued"
    || input.serverInputStatus === "sending"
    || input.legacyBusy
    || input.dialogOpen
    || input.pendingPlanRevision
  ) {
    return "wait";
  }
  if (input.serverInputStatus === "failed") return "failed";
  return input.continueLegacy ? "continue_legacy" : "acknowledge";
}

/**
 * 把会话归属转换为互斥的页面运行策略。
 *
 * Supervisor 只有在已取得对话 ID 时才能挂载；旧 runner 和旧产物动作始终
 * 同开同关，避免前端绕过 Supervisor 直接启动供应商阶段。
 */
export function resolveWorkspaceRuntimePolicy(
  mode: OrchestrationMode,
  conversationId: string,
  agentRuntimeMode: WorkspaceAgentRuntimeMode = "off",
): WorkspaceRuntimePolicy {
  const hasConversation = conversationId.trim().length > 0;
  const supervisorEnabled = hasConversation && (
    mode === "video_agent_v2"
    || agentRuntimeMode === "assist"
    || agentRuntimeMode === "shadow"
    || agentRuntimeMode === "primary"
  );
  const legacyRunnerEnabled = mode === "frontend_v2";
  return {
    supervisorEnabled,
    legacyRunnerEnabled,
    legacyArtifactActionsEnabled: legacyRunnerEnabled,
  };
}

export interface WorkspaceInteractionPolicyInput {
  mode: OrchestrationMode;
  conversationId: string;
  orchestrationResolved: boolean;
  legacyBusy: boolean;
  dialogOpen: boolean;
  pendingPlanRevision: boolean;
  supervisorConnection?: "idle" | "connecting" | "connected" | "reconnecting" | "fatal";
  supervisorRun?: "idle" | "running" | "waiting_user" | "paused" | "failed" | "completed";
  supervisorCompression?: "idle" | "compacting" | "blocked";
  pendingSupervisorTurns?: number;
}

export interface WorkspaceInteractionPolicy {
  composer: {
    disabled: boolean;
    canQueue: boolean;
  };
  artifact: {
    actionsDisabled: boolean;
  };
  runtime: {
    busy: boolean;
    mode: OrchestrationMode;
  };
}

/**
 * 将页面交互拆成输入框、产物动作和运行时三条独立策略。
 *
 * 旧 frontend_v2 仍然保持“业务处理中不能继续输入或操作产物”的兼容行为；
 * supervisor_v1 则允许输入先进入服务端 Turn 队列，同时不把预览、下载等只读
 * 产物入口误锁死。真正会启动旧供应商任务的 handler 仍由运行时归属单独裁剪。
 */
export function resolveWorkspaceInteractionPolicy(
  input: WorkspaceInteractionPolicyInput,
): WorkspaceInteractionPolicy {
  const hasConversation = input.conversationId.trim().length > 0;
  const legacyInteractionBlocked = input.legacyBusy || input.dialogOpen || input.pendingPlanRevision;
  const supervisorConnection = input.supervisorConnection || "idle";
  const supervisorRuntimeBusy = (input.pendingSupervisorTurns ?? 0) > 0
    || input.supervisorRun === "running"
    || input.supervisorCompression === "compacting"
    || supervisorConnection === "connecting"
    || supervisorConnection === "reconnecting";
  const runtimeBusy = !input.orchestrationResolved
    || (input.mode === "frontend_v2" ? input.legacyBusy : supervisorRuntimeBusy);

  if (!input.orchestrationResolved || (input.mode === "video_agent_v2" && !hasConversation)) {
    return {
      composer: { disabled: true, canQueue: false },
      artifact: { actionsDisabled: true },
      runtime: { busy: true, mode: input.mode },
    };
  }

  if (input.mode === "video_agent_v2") {
    return {
      composer: {
        disabled: supervisorConnection === "fatal",
        canQueue: supervisorConnection !== "fatal",
      },
      artifact: { actionsDisabled: false },
      runtime: { busy: runtimeBusy, mode: input.mode },
    };
  }

  return {
    composer: { disabled: legacyInteractionBlocked, canQueue: false },
    artifact: { actionsDisabled: legacyInteractionBlocked },
    runtime: { busy: runtimeBusy, mode: input.mode },
  };
}

function resolveArtifact(message: Record<string, unknown>): JsonObject | null {
  const payload = message.payload === undefined
    ? null
    : cloneJsonObject(message.payload);
  const directArtifact = readNullableObject(message.artifact, INVALID_SNAPSHOT);
  const payloadArtifact = readNullableObject(payload?.artifact, INVALID_SNAPSHOT);
  if (directArtifact && payloadArtifact && !jsonValuesEqual(directArtifact, payloadArtifact)) {
    fail(CONFLICTING_ARTIFACT);
  }
  const artifact = directArtifact ?? payloadArtifact ?? null;
  if (artifact && !isNonEmptyString(artifact.type)) fail();
  return artifact;
}

function resolveMaterials(message: Record<string, unknown>): JsonObject[] {
  const payload = message.payload === undefined
    ? null
    : cloneJsonObject(message.payload);
  const rawMaterials = message.materials ?? payload?.materials ?? [];
  if (!Array.isArray(rawMaterials)) return fail();
  return rawMaterials.map((material) => cloneJsonObject(material));
}

function projectMessage(
  value: unknown,
  conversationId: string,
): LegacyMessageViewModel | null {
  if (!isRecord(value)) return fail();
  const role = value.role;
  if (role === "system") return null;
  if (role !== "user" && role !== "assistant") return fail();

  for (const messageConversationId of [value.conversationId, value.conversation_id]) {
    if (messageConversationId !== undefined
      && (!isNonEmptyString(messageConversationId) || messageConversationId !== conversationId)) {
      return fail();
    }
  }
  const payload = value.payload === undefined
    ? null
    : cloneJsonObject(value.payload);
  const payloadClientId = payload?.client_message_id;
  const id = isNonEmptyString(payloadClientId)
    ? payloadClientId
    : value.id ?? value.message_id;
  if (!isNonEmptyString(id) || typeof value.content !== "string") return fail();

  const rawTime = value.time ?? value.created_at ?? "";
  if (typeof rawTime !== "string") return fail();
  return {
    id,
    conversationId,
    role,
    content: value.content,
    time: rawTime,
    materials: resolveMaterials(value),
    artifact: resolveArtifact(value),
  };
}

function makeMessageIdsUnique(messages: LegacyMessageViewModel[]): LegacyMessageViewModel[] {
  const usedIds = new Set<string>();
  return messages.map((message) => {
    const baseId = message.id;
    let id = baseId;
    let suffix = 2;
    while (usedIds.has(id)) {
      id = `${baseId}-${suffix}`;
      suffix += 1;
    }
    usedIds.add(id);
    return id === message.id ? message : { ...message, id };
  });
}

export function projectLegacyConversationSnapshot(value: unknown): LegacyConversationViewModel {
  if (!isRecord(value) || !isRecord(value.conversation) || !Array.isArray(value.messages)) {
    return fail();
  }
  const conversation = value.conversation;
  const conversationId = conversation.conversation_id;
  const context = cloneJsonObject(conversation.context ?? {});
  if (!isNonEmptyString(conversationId)
    || typeof conversation.title !== "string"
    || typeof conversation.last_phase !== "string") {
    return fail();
  }
  const currentTaskId = conversation.current_task_id;
  if (currentTaskId !== null && currentTaskId !== undefined && !isNonEmptyString(currentTaskId)) {
    return fail();
  }

  const pending = resolvePending(context, conversationId);
  const messages = makeMessageIdsUnique(value.messages
    .map((message) => projectMessage(message, conversationId))
    .filter((message): message is LegacyMessageViewModel => message !== null));
  const orchestration = resolveOrchestration(conversation);
  const agentRuntimeMode = resolveWorkspaceAgentRuntimeMode(conversation);
  const artifacts = messages.flatMap((message): LegacyArtifactViewModel[] => {
    if (!message.artifact) return [];
    return [{
      messageId: message.id,
      type: message.artifact.type as string,
      artifact: cloneJsonObject(message.artifact),
    }];
  });

  return {
    conversationId,
    orchestrationMode: orchestration.mode === "video_agent_v2" && Object.values(pending).some(Boolean)
      ? "frontend_v2"
      : orchestration.mode,
    orchestrationVersion: orchestration.version,
    agentRuntimeMode,
    title: conversation.title,
    currentTaskId: currentTaskId ?? null,
    lastPhase: conversation.last_phase,
    context,
    pending,
    hasPendingWork: Object.values(pending).some((item) => item !== null),
    messages,
    artifacts,
  };
}
