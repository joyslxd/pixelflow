/**
 * LegacyWorkspace 纯函数与模块级常量（从 LegacyWorkspace.tsx 提取，行为不变）。
 */
import type { GenParamsForm } from "@/components/composer/GenParamsDialog";
import {
  api,
  type ConversationMessageResponse,
  type CreativeDirectionResponse,
  type ImageAssetEditResponse,
  type ImageAssetFusionResponse,
  type ImageEditModelSelection,
  type ImageModelParamConfig,
  type ImagePrepareResponse,
  type IntakeIntentResponse,
  type PlanMarkdownResponse,
  type PrepareScenePackagesResponse,
  type PptContentJsonResult,
  type PptImagesResult,
  type VideoCreationContract,
  type GenerateSceneVideosResponse,
} from "@/lib/api";
import type { ChatMessage } from "@/lib/chat";
import { messageConversationId } from "@/lib/conversationRouting";
import { formatMessageTime } from "@/lib/time";
import type { Brief, BriefShot } from "@/lib/chat";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "@/lib/types";
import type { TaskEvent } from "@/lib/api";
import type { CreationIntent } from "@/components/composer/GenParamsDialog";
import { buildSupervisorWorkflowAction } from "@/lib/supervisor/actions";
import type { AgentAction, ExplicitActionSignal, JsonObject, JsonValue } from "@/lib/supervisor/contracts";
import {
  DEFAULT_TARGET_DURATION_MS,
  type GlobalSceneAssetGroup,
  type SceneGlobalAssetReference,
} from "@/lib/scenePackages";
import {
  imageModelCapabilities,
  preferredImageRatio,
  preferredImageSize,
  resolveImageModel,
} from "@/lib/videoRequirementConfig";
import {
  hasMediaResultMessage,
  isSceneAssetGenerationMaterialized,
  mediaResultClientMessageId,
  preferredVideoScenePackagesMessageIndex,
  resolveVideoScenePackagesForRestore,
  scenePackageHasGeneratedImages,
} from "@/lib/scenePackageAssetUi";
import { canAcceptImageResult } from "@/lib/imageReview";
import type {
  ChatArtifact,
  FlowDraft,
  FlowDraftStage,
  PendingImageEditRequest,
  PendingImageJob,
  PendingMessageJob,
  PendingPlanJob,
  PendingPlanJobContext,
  PendingScenePackageJob,
  RestoredSupervisorVideoUi,
  WorkspaceSnapshot,
} from "./legacyWorkspaceTypes";
import {
  LEGACY_VIDEO_JOB_CONTINUE_TIP,
  LEGACY_VIDEO_JOB_HTTP_REMOVED,
} from "./legacyWorkspaceLegacyVideoJobs";

export { LEGACY_VIDEO_JOB_CONTINUE_TIP, LEGACY_VIDEO_JOB_HTTP_REMOVED };

const uid = (): string => crypto.randomUUID();
const UNAVAILABLE_SUPERVISOR_NOTICE_VERSION = 1;
const UNAVAILABLE_SUPERVISOR_NOTICE =
  "该历史会话由未接线的 R2 候选创建，已停止自动重试。请新建对话继续，原消息和素材仍保留。";
const unavailableSupervisorNoticeId = (conversationId: string): string =>
  `agent-runtime-unavailable:${conversationId}:v1`;
const failedSupervisorNoticeId = (
  conversationId: string,
  clientInputId: string,
): string => `agent-runtime-failed:${conversationId}:${clientInputId}:v1`;
const now = () => formatMessageTime(new Date().toISOString());

const isJsonObject = (value: JsonValue | undefined): value is JsonObject =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const parseExplicitAction = (value: unknown): ExplicitActionSignal | null => {
  if (value === undefined || value === null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const action = value as Record<string, unknown>;
  if (typeof action.action !== "string") return null;
  try {
    return buildSupervisorWorkflowAction({
      action: action.action as AgentAction,
      intent: action.intent === "video" ? "video" : null,
      workflowId: typeof action.workflow_id === "string" ? action.workflow_id : null,
      stage: typeof action.stage === "string" ? action.stage : null,
      artifactRef: typeof action.artifact_ref === "string" ? action.artifact_ref : null,
      patch: action.patch && typeof action.patch === "object" && !Array.isArray(action.patch)
        ? action.patch as Readonly<Record<string, unknown>>
        : {},
    });
  } catch {
    return null;
  }
};

function restoreSupervisorVideoUi(
  payload: JsonObject | undefined,
): RestoredSupervisorVideoUi | null {
  if (!payload) return null;
  const workflowId = typeof payload.workflow_id === "string" ? payload.workflow_id.trim() : "";
  const stage = typeof payload.stage === "string" ? payload.stage.trim() : "";
  const artifactRef = typeof payload.artifact_ref === "string" ? payload.artifact_ref.trim() : null;
  if (!workflowId || !stage || (artifactRef !== null && !/^artifact:\S+$/u.test(artifactRef))) return null;
  const formValues = isJsonObject(payload.form_values) ? payload.form_values : {};
  const coreMessage = typeof payload.core_message === "string" ? payload.core_message : "";
  const materials = Array.isArray(payload.materials)
    ? payload.materials.filter(isJsonObject)
    : [];
  const intakeRounds = Number.isSafeInteger(payload.intake_rounds)
    && Number(payload.intake_rounds) >= 0
    ? Number(payload.intake_rounds)
    : 0;
  const authorizationAction = parseExplicitAction(payload.authorization_action);
  switch (payload.ui_kind) {
    case "video_intake_form":
    case "video_direction_review":
    case "video_plan_review":
    case "video_scene_package_review":
    case "video_result_review":
      return {
        kind: payload.ui_kind,
        workflowId,
        stage,
        artifactRef,
        formValues,
        coreMessage,
        materials,
        intakeRounds,
        authorizationAction: null,
      };
    case "authorization_required":
      if (
        !authorizationAction
        || authorizationAction.workflow_id !== workflowId
        || authorizationAction.stage !== stage
        || authorizationAction.artifact_ref !== artifactRef
      ) return null;
      return {
        kind: payload.ui_kind,
        workflowId,
        stage,
        artifactRef,
        formValues,
        coreMessage,
        materials,
        intakeRounds,
        authorizationAction,
      };
    default:
      return null;
  }
}

const parsePendingSupervisorTurn = (
  value: unknown,
  conversationId: string,
): PendingSupervisorTurn | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (
    item.conversationId !== conversationId
    || typeof item.clientInputId !== "string"
    || typeof item.content !== "string"
    || !Array.isArray(item.materials)
    || (
      item.explicitAction !== undefined
      && item.explicitAction !== null
      && parseExplicitAction(item.explicitAction) === null
    )
    || (item.continueLegacy !== undefined && typeof item.continueLegacy !== "boolean")
    || (
      item.registrationStatus !== undefined
      && item.registrationStatus !== "pending"
      && item.registrationStatus !== "registered"
    )
    || (item.runId !== undefined && typeof item.runId !== "string")
    || (
      item.replyToMessageId !== undefined
      && item.replyToMessageId !== null
      && typeof item.replyToMessageId !== "string"
    )
    || (
      item.interruptId !== undefined
      && item.interruptId !== null
      && typeof item.interruptId !== "string"
    )
    || (
      item.artifactRefs !== undefined
      && (
        !Array.isArray(item.artifactRefs)
        || !item.artifactRefs.every((artifactRef) => typeof artifactRef === "string")
      )
    )
  ) return null;
  return {
    conversationId,
    clientInputId: item.clientInputId,
    content: item.content,
    materials: item.materials as Array<Record<string, unknown>>,
    replyToMessageId: typeof item.replyToMessageId === "string" ? item.replyToMessageId : null,
    artifactRefs: Array.isArray(item.artifactRefs) ? item.artifactRefs as string[] : [],
    interruptId: typeof item.interruptId === "string" ? item.interruptId : null,
    explicitAction: parseExplicitAction(item.explicitAction),
    continueLegacy: item.continueLegacy === true,
    registrationStatus: item.registrationStatus === "registered" ? "registered" : "pending",
    ...(typeof item.runId === "string" ? { runId: item.runId } : {}),
  };
};

const parseRegisteredSupervisorTurn = (
  value: JsonValue,
): RegisteredSupervisorTurn => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("Agent Turn 返回格式不合法");
  }
  const runId = value.run_id;
  const status = value.status;
  const orchestrationMode = value.orchestration_mode;
  const routeDecision = value.route_decision;
  const routeIntent = isJsonObject(routeDecision)
    && ["video", "image", "ppt", "video_analysis", "unknown"].includes(String(routeDecision.intent))
    ? routeDecision.intent as RegisteredSupervisorTurn["routeIntent"]
    : null;
  if (
    typeof runId !== "string"
    || !runId
    || (status !== "accepted" && status !== "queued")
    || (orchestrationMode !== "frontend_v2" && orchestrationMode !== "video_agent_v2")
  ) {
    throw new TypeError("Agent Turn 返回格式不合法");
  }
  return { runId, status, orchestrationMode, routeIntent };
};

const isCreationIntent = (value: unknown): value is CreationIntent => value === "video" || value === "image" || value === "ppt";

const browserSessionStorage = (): Storage | null => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};
const workflowIntentFromPhase = (phase: string): CreationIntent | null => {
  if (phase.startsWith("ppt_")) return "ppt";
  if (phase.startsWith("scene_") || (phase.startsWith("video_") && !phase.startsWith("video_analysis"))) return "video";
  if (phase.startsWith("image_") && !phase.startsWith("scene_global_asset")) return "image";
  return null;
};
const AUTO_CONFIRM_TIMEOUT_MS = 60_000;
const AUTO_CONFIRM_TIMEOUT_SECONDS = AUTO_CONFIRM_TIMEOUT_MS / 1000;
const JIANYING_DRAFT_CLIENT_TIMEOUT_MS = 30 * 60 * 1000;
const CONTENT_APP_CONVERSATIONS_UPDATED_MESSAGE_TYPE = "PIXELFLOW_CONVERSATIONS_UPDATED";
const SCENE_GLOBAL_ASSET_DELETE_PROMPT = (assetName: string) => `删除分镜故事板中的内容「${assetName}」。请只删除对应内容，并保持其他内容不变。`;

const PHASE_MSG: Record<string, string> = {
  intake: "正在理解商品与需求…",
  creative: "正在策划分镜 Brief…",
  brief_review: "Brief 已就绪,请在右侧确认或修改。",
  generate: "正在生成分镜片段…",
  edit: "正在剪辑合成…",
  segment_review: "分镜片段已生成,请在画布确认。",
  edit_review: "剪辑结果已生成,请在画布确认。",
  qc: "正在质检…",
  qc_review: "质检完成,请在画布确认。",
  done: "全部完成 🎉",
};

function notifyContentAppConversationsUpdated(conversationId: string): void {
  if (typeof window === "undefined" || window.parent === window || !conversationId) return;
  let targetOrigin = "*";
  try {
    targetOrigin = document.referrer ? new URL(document.referrer).origin : "*";
  } catch {
    targetOrigin = "*";
  }
  window.parent.postMessage(
    {
      type: CONTENT_APP_CONVERSATIONS_UPDATED_MESSAGE_TYPE,
      conversation_id: conversationId,
      conversationId,
    },
    targetOrigin,
  );
}

const REVIEW_ARTIFACT: Partial<Record<TaskPhase, NonNullable<ChatMessage["artifact"]>>> = {
  segment_review: {
    type: "segments",
    title: "分镜片段",
    description: "查看生成片段并确认是否进入剪辑",
    actionLabel: "查看",
  },
  edit_review: {
    type: "edit",
    title: "剪辑结果",
    description: "查看剪辑成片并确认是否质检",
    actionLabel: "查看",
  },
  qc_review: {
    type: "qc",
    title: "质检结果",
    description: "查看质检结果并确认是否完成",
    actionLabel: "查看",
  },
};

const EXPLAINABLE_EVENT_NAMES = new Set<FlowTimelineEntry["event"]>([
  "step_started",
  "step_finished",
  "llm_summary",
  "vendor_call_started",
  "vendor_call_finished",
  "asset_ready",
]);

const EVENT_FALLBACK_TITLE: Record<FlowTimelineEntry["event"], string> = {
  step_started: "步骤开始",
  step_finished: "步骤完成",
  llm_summary: "思考摘要",
  vendor_call_started: "外部能力调用开始",
  vendor_call_finished: "外部能力调用完成",
  asset_ready: "资产已就绪",
};

function toBrief(raw: Record<string, unknown>): Brief {
  // 后端 Brief DTO 使用 snake_case；前端画布组件使用 camelCase 展示模型。
  // 这个函数就是两者之间的适配器，类似 Java 里 DO/DTO -> VO 的转换。
  const shots = Array.isArray(raw.shots) ? (raw.shots as Record<string, unknown>[]) : [];
  return {
    title: String(raw.brief_id ?? "视频 Brief"),
    platform: String(raw.platform ?? ""),
    durationSec: Number(raw.duration_sec ?? 0),
    ratio: String(raw.ratio ?? "9:16"),
    shots: shots.map(
      (s, i): BriefShot => ({
        shotId: String(s.shot_id ?? `s${i}`),
        timeRange: String(s.time_range ?? ""),
        sceneType: String(s.scene_type ?? ""),
        durationSec: Number(s.duration ?? 0),
        narration: String(s.narration_text ?? ""),
        onscreen: String(s.onscreen_text ?? ""),
      }),
    ),
  };
}

const EMPTY_CANVAS: CanvasState = { phase: "idle", results: [], timeline: [] };

function toTimelineEntry(event: TaskEvent): FlowTimelineEntry | null {
  // 后端的可解释事件 payload 是面向前端展示的 VO；这里只做轻量字段适配。
  // 普通业务事件仍走 onEvent switch，不进入时间线，避免画布噪声过多。
  if (!EXPLAINABLE_EVENT_NAMES.has(event.event as FlowTimelineEntry["event"])) return null;
  const type = event.event as FlowTimelineEntry["event"];
  const data = event.data || {};
  return {
    id: event.id ? `event-${event.id}` : `${type}-${Date.now()}`,
    event: type,
    title: String(data.title || EVENT_FALLBACK_TITLE[type]),
    summary: String(data.summary || ""),
    phase: data.phase ? String(data.phase) : undefined,
    status: data.status ? String(data.status) : undefined,
    time: formatMessageTime(event.created_at, "zh-CN", undefined, now()),
  };
}

function pendingPlanJobRunningPhase(kind: PendingPlanJob["kind"]): string {
  if (kind === "plan_revision") return "plan_revision_running";
  if (kind === "plan_manual_edit") return "plan_manual_edit_running";
  return "plan_generation_running";
}

function pendingPlanJobPersistenceContext(kind: PendingPlanJob["kind"]): Record<string, unknown> {
  if (kind === "plan_revision") {
    return {
        pendingPlanRevisionChoice: null,
        pending_plan_revision_choice: null,
      };
  }
  return kind === "plan_manual_edit" ? { plan_approved: false } : {};
}

function isRecoverablePendingPlanJob(value: unknown): value is PendingPlanJob {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const pendingPlanJob = value as Partial<PendingPlanJob>;
  const context = pendingPlanJob.context as Partial<PendingPlanJobContext> | undefined;
  return (
    typeof pendingPlanJob.job_id === "string"
    && Boolean(pendingPlanJob.job_id)
    && typeof pendingPlanJob.conversation_id === "string"
    && Boolean(pendingPlanJob.conversation_id)
    && typeof pendingPlanJob.source_message_id === "string"
    && (
      pendingPlanJob.kind === "plan_generation"
      || pendingPlanJob.kind === "plan_revision"
      || pendingPlanJob.kind === "plan_manual_edit"
    )
    && typeof pendingPlanJob.started_at === "string"
    && Boolean(pendingPlanJob.request)
    && typeof pendingPlanJob.request === "object"
    && Boolean(context)
    && isCreationIntent(context?.intent)
    && Boolean(context?.formValues)
    && typeof context?.formValues === "object"
    && Boolean(context?.selectedDirection)
    && typeof context?.selectedDirection === "object"
    && typeof context?.coreMessage === "string"
    && Array.isArray(context?.materials)
  );
}

const DIRECTION_SUCCESSOR_ARTIFACT_TYPES = new Set<ChatArtifact["type"]>([
  "plan",
  "image_prepare",
  "image_edit_options",
  "image_result",
  "video_scene_packages",
  "video_result",
  "ppt_outline",
  "ppt_images",
  "ppt_file",
]);

const REQUIREMENT_COLLECTION_SUCCESSOR_ARTIFACT_TYPES = new Set<ChatArtifact["type"]>([
  "directions",
  "brief",
  "results",
  "segments",
  "edit",
  "qc",
  "video_quality_review",
  "video_analysis_result",
  ...DIRECTION_SUCCESSOR_ARTIFACT_TYPES,
]);

const REQUIREMENT_COLLECTION_SUCCESSOR_CONTEXT_KEYS = [
  "creative_directions",
  "selected_direction",
  "plan_markdown",
  "plan_approved",
  "plan_revision_requested",
  "image_prepare",
  "image_result",
  "image_edit_done",
  "image_accepted",
  "image_revision_feedback",
  "video_scene_packages",
  "global_assets",
  "scene_packages",
  "sceneAssetFailures",
  "scene_asset_failures",
  "generated_scene_videos",
  "merged_video",
  "video_accepted",
  "video_revision_requested",
  "video_revision_feedback",
  "ppt_summary",
  "ppt_content_json",
  "ppt_images",
  "ppt_file",
  "ppt_outline_feedback",
  "ppt_done",
] as const;

const REQUIREMENT_COLLECTION_PENDING_JOB_KEYS = [
  "pendingMessageJob",
  "pending_message_job",
  "pendingIntakeJob",
  "pending_intake_job",
  "pendingDirectionJob",
  "pending_direction_job",
  "pendingPlanJob",
  "pending_plan_job",
  "pendingImageEditRequest",
  "pending_image_edit_request",
  "pendingImageJob",
  "pending_image_job",
  "pendingScenePackageJob",
  "pending_scene_package_job",
  "pendingVideoJob",
  "pending_video_job",
  "pendingPptJob",
  "pending_ppt_job",
] as const;

function stableWorkflowValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableWorkflowValue);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(Object.keys(record).sort().map((key) => [key, stableWorkflowValue(record[key])]));
  }
  return value ?? null;
}

function stableWorkflowJson(value: unknown): string {
  return JSON.stringify(stableWorkflowValue(value));
}

function creativeDirectionIdentity(direction: CreativeDirectionResponse | undefined): string {
  if (!direction) return "";
  return stableWorkflowJson({
    direction_id: direction.direction_id,
    title: direction.title,
    description: direction.description,
  });
}

function creativeDirectionsFingerprint(directions: CreativeDirectionResponse[] | undefined): string {
  return stableWorkflowJson((directions || []).map((direction) => creativeDirectionIdentity(direction)));
}

function selectedDirectionMatchesDirections(
  selectedDirection: CreativeDirectionResponse | undefined,
  directions: CreativeDirectionResponse[] | undefined,
): boolean {
  const selectedIdentity = creativeDirectionIdentity(selectedDirection);
  if (!selectedIdentity) return false;
  return (directions || []).some((direction) => creativeDirectionIdentity(direction) === selectedIdentity);
}

function isDirectionSuccessorArtifact(artifact: ChatArtifact | undefined): artifact is ChatArtifact {
  return Boolean(artifact && DIRECTION_SUCCESSOR_ARTIFACT_TYPES.has(artifact.type));
}

function hasWorkflowValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "string") return value.trim().length > 0;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value);
  if (value && typeof value === "object") return Object.keys(value as Record<string, unknown>).length > 0;
  return false;
}

function lastPhasePassedRequirementCollection(lastPhase: string | null | undefined): boolean {
  const phase = String(lastPhase || "").trim().toLowerCase();
  if (
    !phase ||
    phase === "idle" ||
    phase === "intake_form_pending" ||
    phase === "ppt_form_pending" ||
    phase === "form_cancelled" ||
    phase === "intake_unknown" ||
    phase === "image_edit_waiting_source_image" ||
    phase.endsWith("_form_pending")
  ) {
    return false;
  }
  return /(directions|plan|image|video|scene|ppt|brief|generation|review|accepted|failed|running|blocked|resume)/.test(phase);
}

function hasPassedRequirementCollection(
  messages: ChatMessage[],
  targetConversationId: string,
  snapshot: Partial<WorkspaceSnapshot> & Record<string, unknown>,
  lastPhase: string | null | undefined,
): boolean {
  const draftStage = snapshot.flowDraft?.stage;
  if (draftStage === "directions_running" || draftStage === "directions_ready") return true;
  if (REQUIREMENT_COLLECTION_PENDING_JOB_KEYS.some((key) => hasWorkflowValue(snapshot[key]))) return true;
  if (
    messages.some(
      (message) =>
        messageConversationId(message, targetConversationId) === targetConversationId &&
        Boolean(message.artifact && REQUIREMENT_COLLECTION_SUCCESSOR_ARTIFACT_TYPES.has(message.artifact.type)),
    )
  ) {
    return true;
  }
  if (REQUIREMENT_COLLECTION_SUCCESSOR_CONTEXT_KEYS.some((key) => hasWorkflowValue(snapshot[key]))) return true;
  return lastPhasePassedRequirementCollection(lastPhase);
}

function artifactMatchesDirectionContext(
  artifact: ChatArtifact | undefined,
  context: PendingDirectionJobContext,
): boolean {
  if (!isDirectionSuccessorArtifact(artifact)) return false;
  if (artifact.intent !== context.intent) return false;
  if (stableWorkflowJson(artifact.formValues || {}) !== stableWorkflowJson(context.formValues || {})) return false;
  const artifactCore = String(artifact.coreMessage || "").trim();
  const contextCore = String(context.coreMessage || "").trim();
  return !artifactCore || !contextCore || artifactCore === contextCore;
}

function hasPostDirectionArtifactForContext(
  messages: ChatMessage[],
  targetConversationId: string,
  context: PendingDirectionJobContext,
): boolean {
  return messages.some(
    (message) =>
      messageConversationId(message, targetConversationId) === targetConversationId &&
      artifactMatchesDirectionContext(message.artifact, context),
  );
}

function hasPostDirectionArtifactForDirections(
  messages: ChatMessage[],
  targetConversationId: string,
  directions: CreativeDirectionResponse[] | undefined,
): boolean {
  return messages.some((message) => {
    if (messageConversationId(message, targetConversationId) !== targetConversationId) return false;
    const artifact = message.artifact;
    return isDirectionSuccessorArtifact(artifact) && selectedDirectionMatchesDirections(artifact.selectedDirection, directions);
  });
}

function hasLaterDirectionSuccessor(
  messages: ChatMessage[],
  targetConversationId: string,
  directionMessage: ChatMessage,
): boolean {
  const directionMessageIndex = messages.findIndex(
    (message) =>
      message.id === directionMessage.id &&
      messageConversationId(message, targetConversationId) === targetConversationId &&
      message.artifact?.type === "directions",
  );
  if (directionMessageIndex < 0) return true;
  return messages.slice(directionMessageIndex + 1).some((message) => {
    if (messageConversationId(message, targetConversationId) !== targetConversationId) return false;
    const artifact = message.artifact;
    if (!artifact) return false;
    if (artifact.type === "directions") return true;
    return isDirectionSuccessorArtifact(artifact);
  });
}

function hasMaterializedPlanJob(
  messages: ChatMessage[],
  targetConversationId: string,
  pendingPlanJob: PendingPlanJob,
): boolean {
  return messages.some(
    (message) =>
      messageConversationId(message, targetConversationId) === targetConversationId &&
      message.artifact?.type === "plan" &&
      message.artifact.planJobId === pendingPlanJob.job_id,
  );
}

const DEFAULT_IMAGE_EDIT_MODEL_CONFIG: ImageModelParamConfig = {
  modelType: "gpt-image-2",
  modelCategoryType: "image_generate",
  paramConfig: {
    sizeList: ["4K"],
    aspectRatioList: ["1:1", "9:16", "16:9"],
  },
  isEnabled: true,
};

function valuesFromForm(form: GenParamsForm): Record<string, unknown> {
  if (form.intent === "video") {
    return {
      product_info: form.product_info,
      product_category: form.product_category,
      target_audience: form.target_audience,
      conversion_goal: form.conversion_goal,
      video_duration_sec: form.video_duration_sec,
      video_ratio: form.video_ratio,
      video_model_mode: form.video_model_mode,
      video_model: form.video_model,
      video_model_capabilities: form.video_model_capabilities,
      video_size: form.video_size,
      video_sound: form.video_sound,
      image_model: form.image_model,
      image_model_capabilities: form.image_model_capabilities,
      video_usage: form.video_usage,
      visual_style: form.visual_style,
    };
  }
  if (form.intent === "ppt") {
    return {
      ppt_topic: form.ppt_topic,
      ppt_style: form.ppt_style,
      attachments: form.attachments,
    };
  }
  return {
    image_goal: form.image_goal,
    image_type: form.image_type,
    image_usage: form.image_usage,
    image_style: form.image_style,
    image_size: form.image_size,
    image_count: form.image_count,
  };
}

function formatSceneIndexesForMessage(
  scenes: Array<Pick<PrepareScenePackagesResponse["scene_packages"][number], "scene_id" | "scene_index">>,
  sceneIds: Set<string>,
): string {
  const indexes = scenes
    .filter((scene) => sceneIds.has(scene.scene_id))
    .map((scene) => `第${scene.scene_index}个分镜`);
  return indexes.length ? indexes.join("、") : "未定位到具体分镜";
}

function failedSceneIdsFromGeneratedSceneVideos(
  generatedSceneVideos: ChatArtifact["generatedSceneVideos"] | undefined,
  scenes: Array<Pick<PrepareScenePackagesResponse["scene_packages"][number], "scene_id" | "scene_index">> = [],
): Set<string> {
  const sceneIds = new Set<string>();
  const sceneIdByIndex = new Map(scenes.map((scene) => [Number(scene.scene_index), scene.scene_id]));
  for (const failedScene of generatedSceneVideos?.failed_scenes || []) {
    const sceneId = String(failedScene.scene_id || failedScene.sceneId || "");
    if (sceneId) {
      sceneIds.add(sceneId);
      continue;
    }
    const sceneIndex = Number(failedScene.scene_index || failedScene.sceneIndex);
    const sceneIdFromIndex = Number.isFinite(sceneIndex) ? sceneIdByIndex.get(sceneIndex) : "";
    if (sceneIdFromIndex) sceneIds.add(sceneIdFromIndex);
  }
  return sceneIds;
}

function mergeMaterials(...groups: Array<Array<Record<string, unknown>> | undefined>): Array<Record<string, unknown>> {
  const seen = new Set<string>();
  const merged: Array<Record<string, unknown>> = [];
  for (const group of groups) {
    for (const item of group || []) {
      const key = String(item.url || item.path || item.image_url || item.imageUrl || item.filename || JSON.stringify(item));
      if (!key || seen.has(key)) continue;
      seen.add(key);
      merged.push(item);
    }
  }
  return merged;
}

function materialUrl(item: Record<string, unknown>): string {
  return String(item.url || item.image_url || item.imageUrl || item.download_url || item.downloadUrl || item.path || item.src || "");
}

/** 统计全局资产里已有的参考图 URL 数，用于判断 V2 工作区投影是否需要刷新卡片。 */
function countGlobalAssetImageUrls(globalAssets: unknown): number {
  if (!globalAssets || typeof globalAssets !== "object") return 0;
  let count = 0;
  for (const group of Object.values(globalAssets as Record<string, unknown>)) {
    if (!Array.isArray(group)) continue;
    for (const item of group) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      for (const field of ["three_view_images", "images", "image_urls"] as const) {
        const value = record[field];
        if (Array.isArray(value)) {
          count += value.filter((url) => typeof url === "string" && url.trim()).length;
        } else if (typeof value === "string" && value.trim()) {
          count += 1;
        }
      }
      const single = materialUrl(record);
      if (single) count += 1;
    }
  }
  return count;
}

/**
 * 场景包结构就绪后，用户表示无参考图、继续生图。
 * 产品话术：「没有参考图，直接生成」及其短回复变体。
 */
function isNoRefImageContinueRequest(text: string): boolean {
  const normalized = String(text || "").trim().replace(/\s+/g, "");
  if (!normalized || normalized.length > 80) return false;
  if (/确认并生成视频|生成视频|生成成片/.test(normalized)) return false;
  if (/没有参考图|无参考图|不需要参考图|无需参考图|没有引用参考/.test(normalized)) {
    return true;
  }
  return /直接生成/.test(normalized) && /参考图/.test(normalized);
}

function isImageMaterial(item: Record<string, unknown>): boolean {
  const url = materialUrl(item);
  const kind = String(item.type || item.kind || item.media_type || item.mediaType || item.mime_type || item.mimeType || "").toLowerCase();
  return Boolean(
    url
    && (
      kind === ""
      || kind === "image"
      || kind === "picture"
      || kind.startsWith("image")
      || /\.(png|jpe?g|webp)(?:$|\?)/i.test(url)
    ),
  );
}

function hasImageMaterial(materials: Array<Record<string, unknown>>): boolean {
  return materials.some(isImageMaterial);
}

function uniqueStringArray(value: unknown): string[] {
  return Array.from(new Set((Array.isArray(value) ? value : []).filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean)));
}

function imageModelType(config: ImageModelParamConfig): string {
  const record = config as unknown as Record<string, unknown>;
  return recordTextValue(record, "modelType") || recordTextValue(record, "model_type") || recordTextValue(record, "model");
}

function imageModelParamConfig(config?: ImageModelParamConfig): Record<string, unknown> {
  const record = (config || {}) as unknown as Record<string, unknown>;
  const raw = record.paramConfig || record.param_config || {};
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

function imageModelOptions(config?: ImageModelParamConfig): { ratios: string[]; sizes: string[] } {
  const params = imageModelParamConfig(config);
  const ratios = uniqueStringArray(params.aspectRatioList || params.aspect_ratio_list);
  const sizes = uniqueStringArray(params.sizeList || params.size_list);
  return {
    ratios: ratios.length > 0 ? ratios : ["1:1", "9:16", "16:9"],
    sizes: sizes.length > 0 ? sizes : ["4K"],
  };
}

function preferredImageEditConfig(configs: ImageModelParamConfig[], preferredModel = "gpt-image-2"): ImageModelParamConfig {
  return configs.find((config) => imageModelType(config) === preferredModel) || configs[0] || DEFAULT_IMAGE_EDIT_MODEL_CONFIG;
}

function globalSceneAssetRecord(
  packages: PrepareScenePackagesResponse | undefined,
  reference: SceneGlobalAssetReference,
): Record<string, unknown> | undefined {
  const groupRecords = packages?.global_assets?.[reference.asset_group];
  if (!Array.isArray(groupRecords)) return undefined;
  return groupRecords.find((record) => String(record.asset_id || record.id || "") === reference.asset_id);
}

function imageNaturalSize(url: string): Promise<{ width: number; height: number } | null> {
  if (!url || typeof window === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => {
      const width = image.naturalWidth || image.width;
      const height = image.naturalHeight || image.height;
      resolve(width > 0 && height > 0 ? { width, height } : null);
    };
    image.onerror = () => resolve(null);
    image.src = url;
  });
}

function recordTextValue(record: Record<string, unknown> | undefined, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function displayIndustryType(value: string): string {
  const normalized = value.trim();
  if (!normalized || ["general", "other", "unknown"].includes(normalized.toLowerCase()) || ["其他", "其他品类", "其他类目", "未知", "未分类"].includes(normalized)) {
    return "其他品类";
  }
  return normalized;
}

function initialValuesFromIntake(intake: IntakeIntentResponse): Record<string, unknown> {
  const values = { ...(intake.values || {}) };
  if (intake.intent === "video") {
    const industryType = recordTextValue(intake.intake_context, "industry_type");
    if (industryType) values.product_category = displayIndustryType(industryType);
  }
  return values;
}

function looksLikeImageEditPrompt(prompt: string): boolean {
  const text = prompt.trim();
  if (!text) return false;
  const referencesExistingImage = /上传的?(图片|图)|这张(图片|图|照片)|这幅图|原图|当前(图片|图)|图片中|图中|照片中|素材图|参考图/i.test(text);
  const editAction = /编辑|修改|改成|改为|变成|变为|换成|换为|替换|调整|调成|去掉|删除|移除|增加|添加|换背景|改背景|换色|改色|上色|修复|修图|抠图|去水印|image_edit|edit/i.test(text);
  return editAction && referencesExistingImage;
}

function isImageEditIntake(intake: IntakeIntentResponse, prompt: string): boolean {
  if (intake.intent !== "image") return false;
  const operation = (
    recordTextValue(intake.intake_context, "image_operation")
    || recordTextValue(intake.values, "image_operation")
    || recordTextValue(intake.values, "operation")
  ).toLowerCase();
  if (operation === "image_edit" || operation === "edit") return true;
  return /编辑|修改|改图|修图|换背景|替换背景|去水印|抠图|image_edit|edit/i.test(prompt) || looksLikeImageEditPrompt(prompt);
}

function directImageEditFormValues(
  prompt: string,
  values: Record<string, unknown>,
  intakeContext: Record<string, unknown>,
  selection?: ImageEditModelSelection,
): Record<string, unknown> {
  const requestedCount = intakeContext.requested_output_count || values.image_count || 1;
  const imageSize = selection?.ratio || values.image_size || intakeContext.image_size || "自动适配";
  const imageQuality = selection?.size || values.image_quality || intakeContext.image_quality || "4K";
  const imageModel = selection?.model || values.image_model || intakeContext.image_model || "gpt-image-2";
  return {
    image_goal: String(values.image_goal || intakeContext.creation_goal || prompt || "图片编辑"),
    image_type: String(values.image_type || "其他"),
    image_usage: String(values.image_usage || "社媒发布"),
    image_style: String(values.image_style || "真实摄影"),
    image_size: String(imageSize),
    image_quality: String(imageQuality),
    image_model: String(imageModel),
    image_count: requestedCount,
    image_operation: "image_edit",
  };
}

function imageEditRatioFromPrepareParams(params: Record<string, unknown> | undefined): string {
  const width = Number(params?.width);
  const height = Number(params?.height);
  if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) return `${width}:${height}`;
  return "";
}

function imageEditRequestFromArtifact(
  artifact: ChatArtifact,
  conversationId: string,
): PendingImageEditRequest {
  const storedRequest = (artifact.imageEditRequest || {}) as Partial<PendingImageEditRequest>;
  const params = (artifact.imagePrepare?.params || {}) as Record<string, unknown>;
  const formValues = (storedRequest.formValues || artifact.formValues || {}) as Record<string, unknown>;
  const intakeContext = (storedRequest.intakeContext || artifact.intakeContext || {}) as Record<string, unknown>;
  const selection = artifact.imageEditConfirmedSelection || storedRequest.selection || {
    model: String(formValues.image_model || intakeContext.image_model || params.model || "gpt-image-2"),
    ratio: String(formValues.image_size || intakeContext.image_size || imageEditRatioFromPrepareParams(params) || "1:1"),
    size: String(formValues.image_quality || intakeContext.image_quality || params.imageSize || "4K"),
  };
  return {
    conversationId,
    prompt: String(storedRequest.prompt || artifact.selectedDirection?.description || artifact.imagePrepare?.prompt || artifact.coreMessage || "图片编辑"),
    formValues,
    intakeContext,
    materials: (storedRequest.materials || artifact.materials || []) as Array<Record<string, unknown>>,
    selection,
  };
}

function applyImageEditConfirmedSelectionsToMessages(
  messages: ChatMessage[],
  imageEditConfirmedSelections: Record<string, ImageEditModelSelection>,
): ChatMessage[] {
  if (Object.keys(imageEditConfirmedSelections).length === 0) return messages;
  return messages.map((message) => {
    const selection = imageEditConfirmedSelections[message.id];
    if (!selection || message.artifact?.type !== "image_edit_options") return message;
    return {
      ...message,
      artifact: {
        ...message.artifact,
        imageEditConfirmedSelection: selection,
        imageEditRequest: {
          ...(message.artifact.imageEditRequest || {}),
          selection,
        },
      },
    };
  });
}

function numericValue(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function pptProjectId(artifact: ChatArtifact): number | null {
  return (
    numericValue(artifact.smartPptProjectId)
    ?? numericValue(artifact.pptSummary?.smart_ppt_project_id)
    ?? numericValue(artifact.pptImages?.smart_ppt_project_id)
    ?? numericValue(artifact.pptFile?.smart_ppt_project_id)
  );
}

function pptImageFileUrls(artifact: ChatArtifact): string[] {
  return (artifact.pptImages?.pages || [])
    .map((page) => (typeof page.image_url === "string" ? page.image_url : ""))
    .filter((url) => url.trim().length > 0);
}

function pptPageJson(artifact: ChatArtifact, pageIndex: number): Record<string, unknown> | null {
  const page = artifact.pptImages?.pages?.find((item) => item.page_index === pageIndex);
  if (page?.json_content && typeof page.json_content === "object") return page.json_content;
  const pages = artifact.pptContentJson?.pages;
  const fallback = Array.isArray(pages) ? pages[pageIndex - 1] : null;
  return fallback && typeof fallback === "object" ? fallback as Record<string, unknown> : null;
}

function pptContentPages(contentJson: PptContentJsonResult): Array<Record<string, unknown>> {
  if (Array.isArray(contentJson.pages)) return contentJson.pages;
  const raw = contentJson.content_json;
  if (Array.isArray(raw)) return raw.filter((page): page is Record<string, unknown> => Boolean(page && typeof page === "object"));
  if (raw && typeof raw === "object") {
    const record = raw as Record<string, unknown>;
    const maybePages = record.pages || record.slides;
    if (Array.isArray(maybePages)) return maybePages.filter((page): page is Record<string, unknown> => Boolean(page && typeof page === "object"));
    return [record];
  }
  return [];
}

function pendingPptImagesFromContentJson(contentJson: PptContentJsonResult, projectId: number): PptImagesResult {
  const pages = pptContentPages(contentJson);
  return {
    ok: false,
    smart_ppt_project_id: numericValue(contentJson.smart_ppt_project_id) || projectId,
    pages: pages.map((page, index) => ({
      page_index: index + 1,
      title: String(page.title || page.name || `第 ${index + 1} 页`),
      json_content: page,
      status: "running",
      image_url: null,
    })),
    message: "PPT 图片生成中。",
  };
}

function sceneGlobalAssetMaterialKey(item: Record<string, unknown>): string {
  return String(item.asset_id || item.source_image_url || item.url || item.filename || JSON.stringify(item));
}

function sceneGlobalAssetReferenceFromMaterials(materials: Array<Record<string, unknown>>): SceneGlobalAssetReference | null {
  const material = materials.find((item) => item.source === "scene_global_asset");
  if (!material) return null;
  const assetId = String(material.asset_id || "");
  const assetGroup = String(material.asset_group || "");
  const sourceImageUrl = String(material.source_image_url || material.url || "");
  const action = material.scene_global_asset_action === "delete" ? "delete" : "edit";
  if (!assetId || !isGlobalSceneAssetGroup(assetGroup) || (action !== "delete" && !sourceImageUrl)) return null;
  return {
    ...material,
    source: "scene_global_asset",
    asset_id: assetId,
    asset_group: assetGroup,
    scene_global_asset_action: action,
    name: String(material.name || material.asset_name || assetId),
    source_image_url: sourceImageUrl,
    url: sourceImageUrl,
    type: "image",
    filename: String(material.filename || `${assetId}.png`),
    description: typeof material.description === "string" ? material.description : undefined,
    storyboard_message_id: typeof material.storyboard_message_id === "string" ? material.storyboard_message_id : undefined,
  };
}

function isGlobalSceneAssetGroup(value: string): value is GlobalSceneAssetGroup {
  return value === "characters" || value === "scenes" || value === "props";
}

function editedImageUrl(result: ImageAssetEditResponse): string {
  const image = result.edited_image;
  if (!image || typeof image !== "object") return "";
  return String(image.url || image.download_url || "");
}

function fusedImageUrl(result: ImageAssetFusionResponse): string {
  const image = result.fused_image;
  if (!image || typeof image !== "object") return "";
  return String(image.url || image.download_url || "");
}

function assetUpdateImage(result: ImageAssetEditResponse | ImageAssetFusionResponse): { asset_id?: string; url?: string; download_url?: string } {
  return "fused_image" in result ? result.fused_image : result.edited_image;
}

function assetUpdateImageUrl(result: ImageAssetEditResponse | ImageAssetFusionResponse): string {
  return "fused_image" in result ? fusedImageUrl(result) : editedImageUrl(result);
}

function isQuotaInsufficientPayload(value: unknown): boolean {
  if (!value) return false;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (record.quota_insufficient === true) return true;
    if (record.status_code === 402) return true;
    return Object.values(record).some((item) => isQuotaInsufficientPayload(item));
  }
  const text = String(value).toLowerCase();
  return ["额度不足", "余额不足", "没有有效的额度", "有效的额度", "剩余额度", "充值", "quota insufficient", "insufficient quota", "payment required"].some((keyword) =>
    text.includes(keyword.toLowerCase()),
  );
}

function secondsFromMilliseconds(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.ceil(value / 1000) : undefined;
}

function videoResultsFromGeneratedScenes(
  mergedVideoUrl: string,
  mergedVideoTaskId: string | null | undefined,
  generatedSceneVideos: NonNullable<ChatMessage["artifact"]>["generatedSceneVideos"],
  targetDurationMs?: number,
  mergedVideoOk = true,
): VideoResult[] {
  const finalVideo: VideoResult = {
    id: mergedVideoTaskId || "merged-video",
    title: "final_video.mp4",
    url: mergedVideoUrl,
    assetType: "final_video",
    durationSec: secondsFromMilliseconds(targetDurationMs),
    status: mergedVideoOk ? "success" : "failed",
  };
  const sceneVideos: VideoResult[] = (generatedSceneVideos?.scene_videos || []).map((scene, index) => ({
    id: scene.scene_id || `scene-${index + 1}`,
    title: `scene_${String(scene.scene_index || index + 1).padStart(2, "0")}.mp4`,
    url: scene.video_url,
    assetType: "generated_video",
    durationSec: secondsFromMilliseconds(scene.duration_ms),
    status: "success",
  }));
  return [finalVideo, ...sceneVideos].filter((video) => Boolean(video.url));
}

function sceneVideoForPackageScene(
  scene: Pick<PrepareScenePackagesResponse["scene_packages"][number], "scene_id" | "scene_index">,
  sceneVideos: NonNullable<NonNullable<ChatMessage["artifact"]>["generatedSceneVideos"]>["scene_videos"],
) {
  return (
    sceneVideos.find((video) => video.scene_id === scene.scene_id) ||
    sceneVideos.find((video) => Number(video.scene_index) === Number(scene.scene_index))
  );
}

function mergePartialGeneratedSceneVideos(
  previous: GenerateSceneVideosResponse | undefined,
  partial: GenerateSceneVideosResponse,
  affectedSceneIds?: string[],
): GenerateSceneVideosResponse {
  if (!previous?.scene_videos.length || !affectedSceneIds?.length) {
    return {
      ...partial,
      scene_videos: [...(partial.scene_videos || [])].sort((a, b) => Number(a.scene_index) - Number(b.scene_index)),
    };
  }
  const affected = new Set(affectedSceneIds);
  const kept = previous.scene_videos.filter((scene) => !affected.has(scene.scene_id));
  const nextVideos = [...kept, ...(partial.scene_videos || [])].sort(
    (a, b) => Number(a.scene_index) - Number(b.scene_index),
  );
  const keptFailed = (previous.failed_scenes || []).filter((scene) => !affected.has(String(scene.scene_id || "")));
  return {
    ...partial,
    scene_videos: nextVideos,
    failed_scenes: [...keptFailed, ...(partial.failed_scenes || [])],
  };
}

function canReuseUneditedSceneVideos(
  videoScenePackages: PrepareScenePackagesResponse,
  generatedSceneVideos: NonNullable<ChatMessage["artifact"]>["generatedSceneVideos"] | undefined,
  dirtySceneIds: Set<string>,
): boolean {
  const sceneVideos = generatedSceneVideos?.scene_videos || [];
  if (!sceneVideos.length || dirtySceneIds.size === 0) return false;
  return videoScenePackages.scene_packages.every((scene) =>
    dirtySceneIds.has(scene.scene_id) || Boolean(sceneVideoForPackageScene(scene, sceneVideos)),
  );
}

function latestVideoResultArtifactForConversation(messages: ChatMessage[], conversationId = ""): ChatArtifact | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (conversationId && messageConversationId(message, conversationId) !== conversationId) continue;
    const artifact = message.artifact;
    if (artifact?.type === "video_result" && (artifact.generatedSceneVideos || artifact.mergedVideo)) return artifact;
  }
  return undefined;
}

function latestImageResultArtifactForConversation(messages: ChatMessage[], conversationId = ""): ChatArtifact | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (conversationId && messageConversationId(message, conversationId) !== conversationId) continue;
    const artifact = message.artifact;
    if (artifact?.type === "image_result" && artifact.imageResult) return artifact;
  }
  return undefined;
}

function latestPptRevisionRequestedForConversation(
  messages: ChatMessage[],
  conversationId: string,
  content: string,
): boolean {
  if (!/(大纲|页|结构|封面|市场痛点|产品卖点|营销计划|总结)/u.test(content)) return false;
  return messages.some(
    (message) => messageConversationId(message, conversationId) === conversationId
      && Boolean(message.artifact?.pptSummary),
  );
}

function quotaMessage(fallback: string) {
  return `${fallback} 当前操作已暂停，充值后回到本对话可以继续执行。`;
}

function isTransientFetchAbort(err: unknown): boolean {
  const name = err && typeof err === "object" && "name" in err ? String((err as { name?: unknown }).name || "") : "";
  const message = err instanceof Error ? err.message : String(err || "");
  return (
    name === "AbortError" ||
    message === "Failed to fetch" ||
    message.includes("NetworkError") ||
    message.includes("Load failed") ||
    message.includes("The user aborted a request")
  );
}

function processedArtifactKey(message: Pick<ChatMessage, "id">, conversationId: string): string {
  return `${conversationId || "local"}:${message.id}`;
}

function scenePackageJobMessageId(job: Pick<PendingScenePackageJob, "kind" | "job_id">): string {
  return `scene-package-job:${job.kind}:${job.job_id}`;
}

function inferVideoDurationSecFromScript(markdown: string, fallback = 30): number {
  const text = markdown.trim();
  const patterns = [
    // 兼容 **时长**：60秒 / 总时长: 60s；秒后勿用 \b（中文边界会匹配失败）
    /总?时长[*\s]*[：:]\s*[*\s]*(\d+)\s*(?:秒|s)/iu,
    /(\d+)\s*秒\s*(?:成片|视频|短剧|竖屏|广告)/u,
    /duration\s*[:=]\s*(\d+)\s*s\b/iu,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (!match) continue;
    const value = Number(match[1]);
    if (Number.isInteger(value) && value >= 4 && value <= 300) return value;
  }
  // 从镜头时间轴末尾推断，例如 00:51-00:53 → 53；00:55-01:00 → 60
  let maxEndSec = 0;
  for (const match of text.matchAll(/(\d{1,2}):(\d{2}):(\d{2})\s*[-–—~]\s*(\d{1,2}):(\d{2}):(\d{2})/gu)) {
    const total = Number(match[4]) * 3600 + Number(match[5]) * 60 + Number(match[6]);
    if (Number.isInteger(total) && total > maxEndSec) maxEndSec = total;
  }
  for (const match of text.matchAll(/(?<![:\d])(\d{1,2}):(\d{2})\s*[-–—~]\s*(\d{1,2}):(\d{2})(?![:\d])/gu)) {
    const total = Number(match[3]) * 60 + Number(match[4]);
    if (Number.isInteger(total) && total > maxEndSec) maxEndSec = total;
  }
  if (maxEndSec >= 4 && maxEndSec <= 300) return maxEndSec;
  return fallback;
}


function buildVideoAgentCreationContract(markdown: string): VideoCreationContract {
  return {
    version: 1,
    intent: "video",
    video_duration_sec: inferVideoDurationSecFromScript(markdown),
    video_ratio: "9:16",
    video_model_mode: "system_recommended",
    video_model: "seedance-2.0",
    video_model_capabilities: {
      generation_types: ["文生视频", "首尾帧", "全能参考"],
      upload_file_types: ["JPG", "PNG", "MP4"],
      aspect_ratios: ["9:16", "16:9", "1:1"],
      sizes: ["480p", "720p", "1080p"],
      sound_options: ["on", "off"],
      durations_sec: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    },
    video_size: "1080p",
    video_sound: "on",
    image_model: "gpt-image-2",
    image_model_capabilities: {
      aspect_ratios: ["1:1", "16:9", "9:16"],
      // 对齐 Borg Skill：gpt-image-2 默认 4K；1080p 在 content-app 常无价格配置。
      sizes: ["4K", "2K", "1080p"],
    },
    video_usage: "宣传片",
    visual_style: "",
    confirmed_by_user: true,
    scene_image_ratio: "9:16",
    scene_image_size: "4K",
    scene_image_spec_source: "deterministic_fallback",
  };
}

async function resolveVideoAgentCreationContract(markdown: string): Promise<VideoCreationContract> {
  const fallback = buildVideoAgentCreationContract(markdown);
  try {
    const configs = await api.listImageGenerateModelConfigs();
    const selected = resolveImageModel(configs as ImageModelParamConfig[], fallback.image_model || "gpt-image-2");
    const caps = imageModelCapabilities(selected);
    const sceneImageSize = preferredImageSize(caps.sizes, fallback.scene_image_size || "4K");
    const sceneImageRatio = preferredImageRatio(
      caps.aspect_ratios,
      fallback.scene_image_ratio || fallback.video_ratio || "9:16",
    );
    return {
      ...fallback,
      image_model: String(selected.modelType || fallback.image_model || "gpt-image-2"),
      image_model_capabilities: caps,
      scene_image_ratio: sceneImageRatio,
      scene_image_size: sceneImageSize,
      scene_image_spec_source: "deterministic_fallback",
    };
  } catch {
    return fallback;
  }
}

/** 权威 Snapshot 消息与本地卡片合并：已有 id 以 Snapshot 为准，本地独有卡片（回执/资产包等）保留。 */
function mergeProjectedMessagesWithLocalCards(
  projectedMessages: ChatMessage[],
  localMessages: ChatMessage[],
  conversationId: string,
): ChatMessage[] {
  const existing = localMessages.filter(
    (message) => messageConversationId(message, conversationId) === conversationId,
  );
  if (existing.length === 0) return projectedMessages;
  const projectedById = new Map(projectedMessages.map((message) => [message.id, message]));
  const merged: ChatMessage[] = [];
  const seen = new Set<string>();
  for (const local of existing) {
    const projected = projectedById.get(local.id);
    merged.push(projected ?? local);
    seen.add(local.id);
  }
  for (const projected of projectedMessages) {
    if (seen.has(projected.id)) continue;
    merged.push(projected);
  }
  return merged;
}

function hasMaterializedScenePackageJob(messages: ChatMessage[], job: PendingScenePackageJob): boolean {
  const expectedMessageId = scenePackageJobMessageId(job);
  if (job.kind === "scene_asset_revision") {
    return messages.some(
      (message) =>
        message.artifact?.type === "video_scene_packages" &&
        Boolean(message.artifact.videoScenePackages) &&
        (message.id === expectedMessageId || message.id.startsWith(`${expectedMessageId}-`)),
    );
  }
  if (job.kind === "scene_asset_generation") {
    // 结构 early card / generating spinner 不算完成；必须有参考图。
    if (isSceneAssetGenerationMaterialized(messages, job.kind)) return true;
    if (hasMediaResultMessage(messages, "scene_assets", job.job_id)) {
      const mediaResult = messages.find((message) => message.id === mediaResultClientMessageId("scene_assets", job.job_id));
      if (
        mediaResult?.artifact?.type === "video_scene_packages"
        && scenePackageHasGeneratedImages(mediaResult.artifact.videoScenePackages)
      ) {
        return true;
      }
    }
    return messages.some(
      (message) =>
        message.id === expectedMessageId
        && message.artifact?.type === "video_scene_packages"
        && Boolean(message.artifact.videoScenePackages)
        && !message.artifact.sceneAssetsGenerating
        && scenePackageHasGeneratedImages(message.artifact.videoScenePackages),
    );
  }
  const sourceMessageIndex = messages.findIndex((message) => message.id === job.source_message_id);
  return messages.some((message, index) => {
    if (message.artifact?.type !== "video_scene_packages" || !message.artifact.videoScenePackages) return false;
    if (message.id === expectedMessageId || message.id.startsWith(`${expectedMessageId}-`)) return true;
    return sourceMessageIndex >= 0 && index > sourceMessageIndex;
  });
}

function scenePackageMessageFingerprint(message: ChatMessage): string {
  const artifact = message.artifact;
  if (artifact?.type !== "video_scene_packages" || !artifact.videoScenePackages) return "";
  return JSON.stringify({
    content: message.content,
    videoScenePackages: artifact.videoScenePackages,
    sceneAssetFailures: artifact.sceneAssetFailures || [],
  });
}

function dedupeRestoredScenePackageMessages(messages: ChatMessage[]): ChatMessage[] {
  const latestIndexByFingerprint = new Map<string, number>();
  messages.forEach((message, index) => {
    const fingerprint = scenePackageMessageFingerprint(message);
    if (fingerprint) latestIndexByFingerprint.set(fingerprint, index);
  });
  return messages.filter((message, index) => {
    const fingerprint = scenePackageMessageFingerprint(message);
    return !fingerprint || latestIndexByFingerprint.get(fingerprint) === index;
  });
}

function messageFromResponse(message: ConversationMessageResponse, conversationId: string): ChatMessage | null {
  if (message.role === "system") return null;
  const artifact = message.payload.artifact as ChatMessage["artifact"] | undefined;
  const materials = Array.isArray(message.payload.materials) ? (message.payload.materials as Array<Record<string, unknown>>) : undefined;
  const clientMessageId = typeof message.payload.client_message_id === "string" ? message.payload.client_message_id : "";
  return {
    id: clientMessageId || message.message_id,
    conversationId,
    role: message.role,
    content: message.content,
    materials,
    time: formatMessageTime(message.created_at),
    artifact,
  };
}

function hasMaterializedMessageJob(messages: ChatMessage[], job: PendingMessageJob | null | undefined): boolean {
  if (!job?.source_message_id) return false;
  const clientMessageId = typeof job.request.payload?.client_message_id === "string" ? job.request.payload.client_message_id : "";
  return messages.some((message) => {
    if (messageConversationId(message, job.conversation_id) !== job.conversation_id) return false;
    return message.id === job.source_message_id || Boolean(clientMessageId && message.id === clientMessageId);
  });
}

function restorePendingMessageJobMessage(messages: ChatMessage[], job: PendingMessageJob | null | undefined): ChatMessage[] {
  if (!job?.message || hasMaterializedMessageJob(messages, job)) return messages;
  return [
    ...messages,
    {
      ...job.message,
      id: job.source_message_id,
      conversationId: job.conversation_id,
      time: job.message.time || now(),
    },
  ];
}

function normalizeRestoredMessageReferences(messages: ChatMessage[]): ChatMessage[] {
  const latestStoryboardIdsByAsset = new Map<string, string>();
  for (const message of messages) {
    const videoScenePackages = message.artifact?.videoScenePackages;
    const globalAssets = videoScenePackages?.global_assets;
    for (const group of ["characters", "scenes", "props"] as const) {
      const records = globalAssets?.[group];
      if (!Array.isArray(records)) continue;
      records.forEach((record) => {
        const assetId = String(record.asset_id || record.id || "");
        if (assetId) latestStoryboardIdsByAsset.set(assetId, message.id);
      });
    }
  }
  return messages.map((message) => {
    const materials = normalizeMaterialStoryboardReferences(message.artifact?.materials, latestStoryboardIdsByAsset);
    if (!materials) return message;
    return {
      ...message,
      artifact: message.artifact
        ? {
            ...message.artifact,
            materials,
          }
        : message.artifact,
    };
  });
}

function restoreLatestVideoScenePackagesFromContext(
  messages: ChatMessage[],
  context: Partial<Record<string, unknown>>,
): ChatMessage[] {
  const globalAssets = context.global_assets;
  const scenePackages = context.scene_packages;
  const latestVideoResultArtifact = latestVideoResultArtifactForConversation(messages);
  const contextGeneratedSceneVideos = Array.isArray(context.generated_scene_videos)
    ? {
        ok: true,
        endpoint: "/api/video/reference-mode-video",
        scene_videos: context.generated_scene_videos as NonNullable<ChatArtifact["generatedSceneVideos"]>["scene_videos"],
        failed_scenes: [],
        message: "已恢复生成后的分镜视频。",
      }
    : undefined;
  const generatedSceneVideos = contextGeneratedSceneVideos || latestVideoResultArtifact?.generatedSceneVideos;
  const mergedVideo = context.merged_video && typeof context.merged_video === "object"
    ? context.merged_video as NonNullable<ChatArtifact["mergedVideo"]>
    : latestVideoResultArtifact?.mergedVideo;
  const editedSceneIds = Array.isArray(context.video_scene_package_edited_scene_ids)
    ? context.video_scene_package_edited_scene_ids.map((item) => String(item)).filter(Boolean)
    : latestVideoResultArtifact?.videoScenePackageEditedSceneIds;
  const contextHasScenePackages = globalAssets && Array.isArray(scenePackages);
  const restoredVideoScenePackages = contextHasScenePackages
    ? ({
        ok: true,
        message: "已从对话上下文恢复视频场景包。",
        requires_confirmation: true,
        review_timeout_sec: null,
        target_duration_ms: typeof context.target_duration_ms === "number" ? context.target_duration_ms : DEFAULT_TARGET_DURATION_MS,
        global_assets: globalAssets as PrepareScenePackagesResponse["global_assets"],
        scene_packages: scenePackages as PrepareScenePackagesResponse["scene_packages"],
        creation_contract: context.creation_contract as VideoCreationContract | null | undefined,
      } satisfies PrepareScenePackagesResponse)
    : null;
  const messageIndex = preferredVideoScenePackagesMessageIndex(messages);
  if (messageIndex < 0) {
    if (!restoredVideoScenePackages) return messages;
    return [
      ...messages,
      {
        id: "restored-video-scene-packages",
        role: "assistant",
        content: "已从历史对话恢复视频场景包，请继续确认或生成分镜视频。",
        time: "",
        artifact: {
          type: "video_scene_packages",
          title: "视频场景包",
          description: `${restoredVideoScenePackages.scene_packages.length} 个场景片段，生成视频前必须确认。`,
          actionLabel: "确认",
          videoScenePackages: restoredVideoScenePackages,
          originalVideoScenePackages: restoredVideoScenePackages,
          sceneAssetFailures: Array.isArray(context.scene_asset_failures) ? context.scene_asset_failures as Array<Record<string, unknown>> : [],
          intent: "video",
          generatedSceneVideos,
          mergedVideo,
          videoScenePackageEditedSceneIds: editedSceneIds || [],
          sceneAssetsGenerating: false,
          sceneAssetsAwaitingModel: false,
        },
      },
    ];
  }
  return messages.map((message, index) => {
    const videoScenePackages = message.artifact?.videoScenePackages;
    if (index !== messageIndex || !message.artifact || !videoScenePackages) return message;
    const nextPackages = resolveVideoScenePackagesForRestore(videoScenePackages, restoredVideoScenePackages) || videoScenePackages;
    const preferredHasImages = scenePackageHasGeneratedImages(nextPackages);
    return {
      ...message,
      artifact: {
        ...message.artifact,
        videoScenePackages: nextPackages,
        originalVideoScenePackages: message.artifact.originalVideoScenePackages || nextPackages,
        sceneAssetsGenerating: preferredHasImages ? false : Boolean(message.artifact.sceneAssetsGenerating),
        sceneAssetsAwaitingModel: preferredHasImages ? false : Boolean(message.artifact.sceneAssetsAwaitingModel),
        generatedSceneVideos:
          latestVideoResultArtifact?.generatedSceneVideos ||
          message.artifact.generatedSceneVideos ||
          contextGeneratedSceneVideos,
        mergedVideo:
          latestVideoResultArtifact?.mergedVideo ||
          message.artifact.mergedVideo ||
          mergedVideo,
        videoScenePackageEditedSceneIds:
          message.artifact.videoScenePackageEditedSceneIds ??
          latestVideoResultArtifact?.videoScenePackageEditedSceneIds ??
          editedSceneIds ??
          [],
      },
    };
  });
}

function markLatestPptFileDoneFromContext(messages: ChatMessage[], context: Partial<Record<string, unknown>>): ChatMessage[] {
  if (context.ppt_done !== true) return messages;
  const latestIndex = [...messages]
    .reverse()
    .findIndex((message) => message.artifact?.type === "ppt_file" && Boolean(message.artifact.pptFile));
  if (latestIndex < 0) return messages;
  const messageIndex = messages.length - 1 - latestIndex;
  return messages.map((message, index) => {
    if (index !== messageIndex || !message.artifact) return message;
    return {
      ...message,
      artifact: {
        ...message.artifact,
        pptDone: true,
      },
    };
  });
}

function markLatestImageResultAcceptedFromContext(messages: ChatMessage[], context: Partial<Record<string, unknown>>): ChatMessage[] {
  if (context.image_accepted !== true) return messages;
  const latestIndex = [...messages]
    .reverse()
    .findIndex((message) => message.artifact?.type === "image_result" && Boolean(message.artifact.imageResult && canAcceptImageResult(message.artifact.imageResult)));
  if (latestIndex < 0) return messages;
  const messageIndex = messages.length - 1 - latestIndex;
  return messages.map((message, index) => {
    if (index !== messageIndex || !message.artifact) return message;
    return {
      ...message,
      artifact: {
        ...message.artifact,
        imageAccepted: true,
      },
    };
  });
}

function markLatestVideoResultAcceptedFromContext(messages: ChatMessage[], context: Partial<Record<string, unknown>>): ChatMessage[] {
  if (context.video_accepted !== true) return messages;
  const latestIndex = [...messages]
    .reverse()
    .findIndex((message) => message.artifact?.type === "video_result" && Boolean(message.artifact.mergedVideo?.ok));
  if (latestIndex < 0) return messages;
  const messageIndex = messages.length - 1 - latestIndex;
  return messages.map((message, index) => {
    if (index !== messageIndex || !message.artifact) return message;
    return {
      ...message,
      artifact: {
        ...message.artifact,
        videoAccepted: true,
      },
    };
  });
}

function latestOriginalVideoScenePackagesForConversation(messages: ChatMessage[], conversationId: string): PrepareScenePackagesResponse | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (messageConversationId(message, conversationId) !== conversationId) continue;
    const artifact = message.artifact;
    if (artifact?.type === "video_scene_packages" && artifact.videoScenePackages) {
      return artifact.originalVideoScenePackages || artifact.videoScenePackages;
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (messageConversationId(message, conversationId) !== conversationId) continue;
    const baseline = message.artifact?.originalVideoScenePackages;
    if (baseline) return baseline;
  }
  return undefined;
}

function latestScenePackageSnapshotForConversation(messages: ChatMessage[], conversationId: string): Partial<WorkspaceSnapshot> {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (messageConversationId(message, conversationId) !== conversationId) continue;
    const artifact = message.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    if (artifact?.type !== "video_scene_packages" && artifact?.type !== "video_result") continue;
    if (!videoScenePackages) continue;
    return {
      global_assets: videoScenePackages.global_assets,
      scene_packages: videoScenePackages.scene_packages,
      generated_scene_videos: artifact.generatedSceneVideos?.scene_videos,
      merged_video: artifact.mergedVideo,
      video_scene_package_edited_scene_ids: artifact.videoScenePackageEditedSceneIds || [],
    };
  }
  return {};
}

function normalizeMaterialStoryboardReferences(
  materials: Array<Record<string, unknown>> | undefined,
  latestStoryboardIdsByAsset: Map<string, string>,
): Array<Record<string, unknown>> | undefined {
  if (!Array.isArray(materials)) return materials;
  let changed = false;
  const nextMaterials = materials.map((material) => {
    if (material.source !== "scene_global_asset") return material;
    const assetId = String(material.asset_id || "");
    const storyboardMessageId = assetId ? latestStoryboardIdsByAsset.get(assetId) : "";
    if (!storyboardMessageId || material.storyboard_message_id === storyboardMessageId) return material;
    changed = true;
    return { ...material, storyboard_message_id: storyboardMessageId };
  });
  return changed ? nextMaterials : materials;
}

// AUTO-EXPORTS-START
export {
  applyImageEditConfirmedSelectionsToMessages,
  AUTO_CONFIRM_TIMEOUT_MS,
  AUTO_CONFIRM_TIMEOUT_SECONDS,
  browserSessionStorage,
  buildVideoAgentCreationContract,
  canReuseUneditedSceneVideos,
  CONTENT_APP_CONVERSATIONS_UPDATED_MESSAGE_TYPE,
  creativeDirectionsFingerprint,
  dedupeRestoredScenePackageMessages,
  DEFAULT_IMAGE_EDIT_MODEL_CONFIG,
  directImageEditFormValues,
  EMPTY_CANVAS,
  EVENT_FALLBACK_TITLE,
  EXPLAINABLE_EVENT_NAMES,
  failedSceneIdsFromGeneratedSceneVideos,
  formatSceneIndexesForMessage,
  hasLaterDirectionSuccessor,
  hasMaterializedMessageJob,
  hasMaterializedPlanJob,
  hasMaterializedScenePackageJob,
  hasPassedRequirementCollection,
  hasPostDirectionArtifactForContext,
  hasPostDirectionArtifactForDirections,
  imageEditRequestFromArtifact,
  inferVideoDurationSecFromScript,
  isCreationIntent,
  isDirectionSuccessorArtifact,
  isImageEditIntake,
  isJsonObject,
  isRecoverablePendingPlanJob,
  isTransientFetchAbort,
  JIANYING_DRAFT_CLIENT_TIMEOUT_MS,
  latestImageResultArtifactForConversation,
  latestOriginalVideoScenePackagesForConversation,
  latestPptRevisionRequestedForConversation,
  latestScenePackageSnapshotForConversation,
  latestVideoResultArtifactForConversation,
  markLatestImageResultAcceptedFromContext,
  markLatestPptFileDoneFromContext,
  markLatestVideoResultAcceptedFromContext,
  mergeMaterials,
  mergePartialGeneratedSceneVideos,
  mergeProjectedMessagesWithLocalCards,
  messageFromResponse,
  normalizeMaterialStoryboardReferences,
  normalizeRestoredMessageReferences,
  now,
  parseExplicitAction,
  parsePendingSupervisorTurn,
  parseRegisteredSupervisorTurn,
  pendingPlanJobPersistenceContext,
  pendingPlanJobRunningPhase,
  PHASE_MSG,
  pptContentPages,
  pptImageFileUrls,
  pptPageJson,
  pptProjectId,
  processedArtifactKey,
  quotaMessage,
  restoreLatestVideoScenePackagesFromContext,
  restorePendingMessageJobMessage,
  restoreSupervisorVideoUi,
  resolveVideoAgentCreationContract,
  REVIEW_ARTIFACT,
  SCENE_GLOBAL_ASSET_DELETE_PROMPT,
  sceneGlobalAssetReferenceFromMaterials,
  scenePackageJobMessageId,
  sceneVideoForPackageScene,
  selectedDirectionMatchesDirections,
  toBrief,
  toTimelineEntry,
  uid,
  UNAVAILABLE_SUPERVISOR_NOTICE,
  UNAVAILABLE_SUPERVISOR_NOTICE_VERSION,
  unavailableSupervisorNoticeId,
  failedSupervisorNoticeId,
  valuesFromForm,
  videoResultsFromGeneratedScenes,
  workflowIntentFromPhase,
  notifyContentAppConversationsUpdated,
  countGlobalAssetImageUrls,
  isNoRefImageContinueRequest,
};
// AUTO-EXPORTS-END
