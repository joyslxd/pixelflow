import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { GenParamsDialog, type CreationIntent, type GenParamsForm } from "@/components/composer/GenParamsDialog";
import { PlanRevisionDialog, type PlanRevisionMode } from "@/components/composer/PlanRevisionDialog";
import {
  ApiError,
  api,
  setActiveConversationId as setActiveConversationIdForTrace,
  subscribeTaskEvents,
  type ConversationDetailResponse,
  type ConversationMessageResponse,
  type ConversationMessageJobStatusResponse,
  type CreativeDirectionResponse,
  type CreativeDirectionsResponse,
  type ImageEditModelSelection,
  type ImageAssetEditResponse,
  type ImageAssetFusionResponse,
  type ImageGenerateResponse,
  type ImageModelParamConfig,
  type ImagePrepareResponse,
  type IntakeIntentResponse,
  type JianyingDraftCapability,
  type JianyingDraftJobResponse,
  type JianyingDraftStartRequest,
  type MergeSceneVideosResponse,
  type PlanManualEditRequest,
  type PlanMarkdownResponse,
  type PptFileResult,
  type PptImagesResult,
  type PptJobStatusResponse,
  type PptPageImage,
  type PptContentJsonResult,
  type PptSummaryResult,
  type GenerateSceneAssetsResponse,
  type PrepareScenePackagesJobResult,
  type PrepareScenePackagesJobStatusResponse,
  type PrepareScenePackagesResponse,
  type ScenePackageAssetRevisionRequest,
  type ScenePackageAssetRevisionResponse,
  type SceneGenerationPayload,
  type SceneVideoPayload,
  type TaskEvent,
  type VideoCreationContract,
} from "@/lib/api";
import type { ChatMessage, CanvasState, Brief, BriefShot, JianyingDraftRecordMap } from "@/lib/chat";
import type { AgentUserMessagePayload } from "@/lib/authStorage";
import { activePlanSnapshotForConversation } from "@/lib/activePlanSnapshot";
import {
  preferredSceneAssetImageSize,
  SCENE_ASSET_PREFERRED_MODELS,
  sceneAssetModelLabel,
} from "@/lib/sceneAssetModelSelection";
import {
  remapMessageAnchorId,
  resolveAssetPackageProgressAnchorId,
} from "@/lib/assetPackageProgressAnchor";
import {
  classifyScenePackageJobResume,
  scenePackageJobResumeDelayMs,
} from "@/lib/scenePackageJobResume";
import {
  isSceneAssetGenerationMaterialized,
  reconcileStaleSceneAssetUiFlags,
  scenePackageHasGeneratedImages,
} from "@/lib/scenePackageAssetUi";
import {
  isPendingPlanSaveForConversation,
  isSameMessageJobGeneration,
  planContextFromSavedMessage,
  planMessageResumeDelayMs,
  resumePlanMessageJobStep,
} from "@/lib/planMessageRecovery";
import {
  classifyPlanJobResume,
  clearPendingPlanJobRecovery,
  continueStartedPlanJob,
  loadPendingPlanJobRecovery,
  planJobResumeDelayMs,
  savePendingPlanJobRecovery,
  shouldRetryPlanJobPersistence,
} from "@/lib/planJobRecovery";

const PlanMarkdownEditor = lazy(() =>
  import("@/components/canvas/PlanMarkdownEditor").then((module) => ({ default: module.PlanMarkdownEditor })),
);
import {
  appendVisibleConversationMessage,
  messageConversationId,
  replaceMessageById,
  restoredConversationMessages,
  shouldApplyVisibleConversationSideEffect,
} from "@/lib/conversationRouting";
import { useSupervisorConversation } from "@/hooks/useSupervisorConversation";
import { AgentPlanTimeline } from "@/features/video-agent/AgentPlanTimeline";
import {
  AgentPipelineProgress,
  applyAssetPackageAssetProgress,
  applyAssetPackageJobStage,
  createAssetPackageProgressSteps,
  type AgentPipelineProgressStep,
} from "@/features/video-agent/AgentPipelineProgress";
import {
  AgentConfirmationCard,
  type AgentConfirmationSubmission,
} from "@/features/video-agent/AgentConfirmationCard";
import {
  AgentQuotaCard,
  type AgentQuotaSubmission,
} from "@/features/video-agent/AgentQuotaCard";
import { AgentScriptPreviewPanel } from "@/features/video-agent/AgentScriptPreviewPanel";
import { SceneEvidencePanel } from "@/features/video-agent/SceneEvidencePanel";
import { VideoAgentStoryboardSurface } from "@/features/video-agent/VideoAgentStoryboardSurface";
import { useVideoAgent } from "@/features/video-agent/hooks/useVideoAgent";
import {
  emptyVideoAgentPlanHistory,
  loadVideoAgentPlanHistory,
  mergeVideoAgentPlanHistory,
  saveVideoAgentPlanHistory,
  type VideoAgentPlanHistory,
} from "@/features/video-agent/planHistory";
import { stageIdFromStep, isContinueVideoGenerationRequest, isConfirmScriptPlanRequest, isRedesignTaskPlanRequest, isMajorRequirementChangeRequest, isRegenerateVideoAssetPackageRequest, isReviseVideoAssetPackageRequest, isConfirmGenerateVideoFromPackagesRequest, resolveGeneratableScriptMarkdown, buildAssetPackagePlanMarkdown, extractConcreteProductHint, workspaceHasGeneratableScript, workspaceHasExportReady, analyzeScriptCharacterReadiness, scriptNeedsFullCharacterPlan } from "@/features/video-agent/scriptSkillStages";
import { buildImageRevisionPreparePayload, canAcceptImageResult, imageResultSummary } from "@/lib/imageReview";
import { isReviewExpired, reviewExpiresAt, timeoutReviewMessage } from "@/lib/reviewWindow";
import {
  addGlobalSceneAssetReference,
  DEFAULT_TARGET_DURATION_MS,
  defaultGlobalSceneAssetRatio,
  globalSceneAssetRatioFromMetadata,
  inferGlobalSceneAssetRatioFromMetadata,
  mergeSceneAssetRetryFailures,
  nearestSupportedAspectRatio,
  sceneAssetRetryTargets,
  sceneGenerationPayloadFromPackage,
  sceneIdsForRevision,
  scenePackagesWithRevisionContract,
  scenePackagesWithoutRevisionContract,
  updateScenePackageField,
  uploadedReferenceMaterials,
  type GlobalSceneAssetGroup,
  type SceneGlobalAssetReference,
  type SceneGlobalAssetReplacement,
  type SceneAssetRetryTarget,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import {
  imageModelCapabilities,
  preferredImageRatio,
  preferredImageSize,
  resolveImageModel,
} from "@/lib/videoRequirementConfig";
import { formatMessageTime } from "@/lib/time";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "@/lib/types";
import type { SceneGlobalAssetEditReview } from "@/lib/chat";
import {
  isJianyingDraftSucceededResultValid,
  jianyingDraftPublicErrorMessage,
  JianyingDraftStartGuard,
  patchJianyingDraftTargetConversation,
  storyboardVersionId,
  type JianyingDraftScene,
} from "@/lib/jianyingDraft";
import {
  deriveWorkflowTaskBoard,
  deriveWorkflowTaskBoardFromAgentPlan,
  type WorkflowFlowKind,
  type WorkflowProgressSnapshot,
} from "@/lib/workflowTaskBoard";
import type {
  AgentAction,
  ExplicitActionSignal,
  JsonObject,
  JsonValue,
  OrchestrationMode,
  TurnStartRequest,
  WorkflowRecord,
} from "@/lib/supervisor/contracts";
import { buildSupervisorWorkflowAction } from "@/lib/supervisor/actions";
import {
  createConversationWriteSequencer,
  resolveAssistHandoffAction,
  resolveUnavailableSupervisorRecovery,
  resolveWorkspaceAgentRuntimeMode,
  resolveWorkspaceOrchestrationMode,
  resolveWorkspacePrimaryExecutionReady,
  resolveWorkspaceInteractionPolicy,
  resolveWorkspaceRuntimePolicy,
  type WorkspaceAgentRuntimeMode,
} from "@/lib/supervisor/legacyAdapter";
import { resolveSupervisorRuntimeNotice } from "@/lib/supervisor/runtimeNotice";
import { buildSupervisorSubmission } from "@/lib/supervisor/turnSubmission";
import { supervisorApi } from "@/lib/supervisor/api";
import {
  mergeSupervisorMessagesWithPending,
  projectSupervisorWorkflowProgress,
  selectSupervisorArtifactMessage,
} from "@/lib/supervisor/workspaceProjection";

interface ConversationOwnership {
  conversationId: string;
  orchestrationMode: OrchestrationMode;
  agentRuntimeMode: WorkspaceAgentRuntimeMode;
}

interface PendingSupervisorTurn {
  conversationId: string;
  clientInputId: string;
  content: string;
  materials: Array<Record<string, unknown>>;
  replyToMessageId: string | null;
  artifactRefs: string[];
  interruptId: string | null;
  explicitAction: ExplicitActionSignal | null;
  continueLegacy: boolean;
  registrationStatus: "pending" | "registered";
  runId?: string;
}

type SupervisorVideoUiKind =
  | "video_intake_form"
  | "video_direction_review"
  | "video_plan_review"
  | "video_scene_package_review"
  | "video_result_review"
  | "authorization_required";

interface RestoredSupervisorVideoUi {
  kind: SupervisorVideoUiKind;
  workflowId: string;
  stage: string;
  artifactRef: string | null;
  formValues: JsonObject;
  coreMessage: string;
  materials: JsonObject[];
  intakeRounds: number;
  authorizationAction: ExplicitActionSignal | null;
}

interface SupervisorVideoTarget {
  ui: RestoredSupervisorVideoUi;
  workflow: WorkflowRecord;
  stage: string;
  artifactRef: string | null;
}

interface RegisteredSupervisorTurn {
  runId: string;
  status: "accepted" | "queued";
  orchestrationMode: OrchestrationMode;
  routeIntent: "video" | "image" | "ppt" | "video_analysis" | "unknown" | null;
}

interface DeferredOwnershipInput {
  routeConversationId: string;
  input: string | AgentUserMessagePayload;
}

interface SendRuntimeOptions {
  skipRuntimeRegistration?: boolean;
  clientInputId?: string;
}

interface SubmitSupervisorActionOptions {
  materials?: JsonObject[];
  replyToMessageId?: string | null;
  artifactRefs?: string[];
}

// 统一使用 UUID 作为消息 ID 与 Runtime client_input_id，旧消息 API 会映射到同一稳定主键。
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

interface WorkspaceSnapshot {
  taskId: string;
  messages?: ChatMessage[];
  pendingMaterials: Array<Record<string, unknown>>;
  flowDraft?: FlowDraft | null;
  pendingMessageJob?: PendingMessageJob | null;
  pending_message_job?: PendingMessageJob | null;
  pendingAgentRuntimeTurns?: PendingSupervisorTurn[];
  pending_agent_runtime_turns?: PendingSupervisorTurn[];
  agentRuntimeUnavailableNoticeVersion?: number;
  agent_runtime_unavailable_notice_version?: number;
  pendingIntakeJob?: PendingIntakeJob | null;
  pending_intake_job?: PendingIntakeJob | null;
  pendingDirectionJob?: PendingDirectionJob | null;
  pending_direction_job?: PendingDirectionJob | null;
  pendingPlanJob?: PendingPlanJob | null;
  pending_plan_job?: PendingPlanJob | null;
  pendingPlanRevisionRequest?: PendingConversationArtifact | null;
  pending_plan_revision_request?: PendingConversationArtifact | null;
  pendingPlanRevisionChoice?: PendingPlanRevisionChoice | null;
  pending_plan_revision_choice?: PendingPlanRevisionChoice | null;
  pendingImageEditRequest?: PendingImageEditRequest | null;
  imageEditConfirmedSelections?: Record<string, ImageEditModelSelection>;
  pendingImageJob?: PendingImageJob | null;
  pending_image_job?: PendingImageJob | null;
  pendingImageRevision?: PendingConversationArtifact | null;
  pending_image_revision?: PendingConversationArtifact | null;
  pendingPptOutlineRevision?: PendingConversationArtifact | null;
  pending_ppt_outline_revision?: PendingConversationArtifact | null;
  pendingScenePackageJob?: PendingScenePackageJob | null;
  pending_scene_package_job?: PendingScenePackageJob | null;
  pendingVideoJob?: PendingVideoJob | null;
  pending_video_job?: PendingVideoJob | null;
  pendingVideoRevision?: PendingConversationArtifact | null;
  pending_video_revision?: PendingConversationArtifact | null;
  pendingPptJob?: PendingPptJob | null;
  pending_ppt_job?: PendingPptJob | null;
  pendingJianyingDraftJob?: PendingJianyingDraftJob | null;
  pending_jianying_draft_job?: PendingJianyingDraftJob | null;
  jianyingDraftRecords?: JianyingDraftRecordMap;
  jianying_draft_records?: JianyingDraftRecordMap;
  ppt_done?: boolean;
  image_accepted?: boolean;
  video_accepted?: boolean;
  canvas: CanvasState;
  canvasOpen: boolean;
  briefConfirmed: boolean;
  lastEventId: number;
  announcedPhases: string[];
  briefReadyShown: boolean;
  global_assets?: PrepareScenePackagesResponse["global_assets"];
  scene_packages?: PrepareScenePackagesResponse["scene_packages"];
  generated_scene_videos?: NonNullable<ChatArtifact["generatedSceneVideos"]>["scene_videos"];
  merged_video?: NonNullable<ChatArtifact["mergedVideo"]>;
  video_scene_package_edited_scene_ids?: string[];
  selected_direction?: unknown;
  plan_markdown?: string;
  plan_version?: number;
  plan_history?: PlanMarkdownResponse["plan_history"];
  creation_contract?: Record<string, unknown>;
  scene_durations_sec?: number[];
  scene_blueprints?: PlanMarkdownResponse["scene_blueprints"];
  asset_manifest?: PlanMarkdownResponse["asset_manifest"];
  restored_from_version?: number | null;
  workflowProgress?: WorkflowProgressSnapshot | null;
  workflow_progress?: WorkflowProgressSnapshot | null;
  /** 执行方案卡片锚点：planId → 用户消息 id（会话 context 持久化） */
  videoAgentPlanAnchors?: Record<string, string>;
  video_agent_plan_anchors?: Record<string, string>;
  /** 资产包进度卡锚点：脚本确认后的回执消息 id */
  assetPackageAnchorMessageId?: string;
  asset_package_anchor_message_id?: string;
}

type ChatArtifact = NonNullable<ChatMessage["artifact"]>;

interface PendingConversationArtifact {
  conversationId: string;
  artifact: ChatArtifact;
  sourceMessageId?: string;
  processedKey?: string;
}

interface PendingPlanRevisionChoice {
  conversationId: string;
  artifact: ChatArtifact;
  feedback: string;
  materials: Array<Record<string, unknown>>;
  sourceMessageId: string;
  processedKey?: string;
}

interface PendingDialogContext {
  conversationId: string;
  coreMessage: string;
  materials: Array<Record<string, unknown>>;
  intakeContext?: Record<string, unknown>;
}

type FlowDraftStage = "intake_analyzed" | "form_pending" | "directions_running" | "directions_ready" | "form_cancelled";

interface FlowDraft {
  version: 1;
  stage: FlowDraftStage;
  intent?: CreationIntent | "video_analysis" | "unknown";
  coreMessage?: string;
  materials?: Array<Record<string, unknown>>;
  intakeIntent?: IntakeIntentResponse;
  intakeContext?: Record<string, unknown>;
  formValues?: Record<string, unknown>;
  form?: GenParamsForm;
  creativeDirections?: CreativeDirectionResponse[];
  updatedAt: string;
}

interface ConversationMessageJobRequest {
  role: "user" | "assistant" | "system";
  content: string;
  payload?: Record<string, unknown>;
}

interface PendingHandleSendMessageContinuation {
  type: "handle_send";
  content: string;
  materials: Array<Record<string, unknown>>;
}

interface PendingPlanSaveMessageContinuation {
  type: "plan_save";
  last_phase: string;
  context: Record<string, unknown>;
  success_message?: string;
  processed_key?: string;
}

type PendingMessageJobContinuation = PendingHandleSendMessageContinuation | PendingPlanSaveMessageContinuation;

function createVideoAgentPlanResponse({
  planMarkdown,
  creationContract = {},
  sceneBlueprints = [],
  assetManifest,
}: {
  planMarkdown: string;
  creationContract?: Record<string, unknown>;
  sceneBlueprints?: PlanMarkdownResponse["scene_blueprints"];
  assetManifest?: PlanMarkdownResponse["asset_manifest"];
}): PlanMarkdownResponse {
  return {
    output_type: "video",
    plan_markdown: planMarkdown,
    template_path: "",
    consistency_issues: [],
    review_timeout_sec: null,
    plan_version: 1,
    plan_history: [],
    creation_contract: creationContract,
    scene_durations_sec: [],
    scene_blueprints: sceneBlueprints,
    asset_manifest: assetManifest ?? { characters: [], scenes: [], props: [] },
    llm_used: false,
    model_name: "",
    error: null,
    restored_from_version: null,
  };
}

interface PendingMessageJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: "conversation_message";
  started_at: string;
  request: ConversationMessageJobRequest;
  message: ChatMessage;
  continue_after_save?: PendingMessageJobContinuation;
  restart_count?: number;
}

interface IntakeAnalyzeJobRequest {
  prompt: string;
  materials?: Array<Record<string, unknown>>;
}

interface PendingIntakeJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: "intake_analyze";
  started_at: string;
  request: IntakeAnalyzeJobRequest;
}

interface CreativeDirectionsJobRequest {
  intent: CreationIntent;
  values: Record<string, unknown>;
  intake_rounds?: number;
  product_creative_profile?: Record<string, unknown>;
  intake_context?: Record<string, unknown>;
  materials?: Array<Record<string, unknown>>;
}

interface PendingDirectionJobContext {
  intent: CreationIntent;
  formValues: Record<string, unknown>;
  coreMessage: string;
  materials?: Array<Record<string, unknown>>;
  intakeContext?: Record<string, unknown>;
  form?: GenParamsForm;
  revisionFeedback?: string;
}

interface PendingDirectionJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: "creative_directions";
  started_at: string;
  request: CreativeDirectionsJobRequest;
  context: PendingDirectionJobContext;
}

interface PlanGenerationJobRequest {
  intent: CreationIntent;
  form_values: Record<string, unknown>;
  selected_direction: Record<string, unknown>;
  product_creative_profile?: Record<string, unknown>;
  intake_context?: Record<string, unknown>;
  materials?: Array<Record<string, unknown>>;
}

interface PlanRevisionJobRequest extends PlanGenerationJobRequest {
  current_plan_markdown: string;
  current_plan_version: number;
  plan_history: PlanMarkdownResponse["plan_history"];
  revision_feedback: string;
  creation_contract?: Record<string, unknown>;
  scene_blueprints?: PlanMarkdownResponse["scene_blueprints"];
  asset_manifest?: PlanMarkdownResponse["asset_manifest"];
}

type PlanManualEditJobRequest = PlanManualEditRequest;

interface PendingPlanJobContext {
  intent: CreationIntent;
  formValues: Record<string, unknown>;
  selectedDirection: CreativeDirectionResponse;
  coreMessage: string;
  materials: Array<Record<string, unknown>>;
  intakeContext?: Record<string, unknown>;
  processedKey?: string;
}

interface PendingPlanJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: "plan_generation" | "plan_revision" | "plan_manual_edit";
  started_at: string;
  request: PlanGenerationJobRequest | PlanRevisionJobRequest | PlanManualEditJobRequest;
  context: PendingPlanJobContext;
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

interface PendingImageEditRequest {
  conversationId: string;
  prompt: string;
  formValues: Record<string, unknown>;
  intakeContext: Record<string, unknown>;
  materials: Array<Record<string, unknown>>;
  selection?: ImageEditModelSelection;
  mode?: "direct_image_edit" | "scene_global_asset_edit" | "scene_global_asset_fusion";
  sceneGlobalAssetReference?: SceneGlobalAssetReference;
  storyboardMessageId?: string;
}

type PendingImageJobKind = "image_generation" | "image_regeneration" | "direct_image_edit" | "scene_global_asset_edit" | "scene_global_asset_fusion";
type PendingImageJobApi = "generate" | "edit_asset" | "fuse_asset";

interface ImageGenerationJobRequest {
  method: ImagePrepareResponse["method"];
  prompt: string;
  negative_prompt?: string;
  params: Record<string, unknown>;
}

interface ImageAssetEditJobRequest {
  asset_id: string;
  asset_name?: string;
  asset_group: string;
  source_image_url: string;
  prompt: string;
  materials?: Array<Record<string, unknown>>;
  reference_image_urls?: string[];
  ratio?: string;
  size?: string;
  model?: string | null;
}

type ImageAssetFusionJobRequest = ImageAssetEditJobRequest;

interface PendingImageJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: PendingImageJobKind;
  job_api: PendingImageJobApi;
  started_at: string;
  request: ImageGenerationJobRequest | ImageAssetEditJobRequest | ImageAssetFusionJobRequest;
  artifact: ChatArtifact;
  imagePrepare?: ImagePrepareResponse;
  sceneGlobalAssetReference?: SceneGlobalAssetReference;
  storyboard_message_id?: string;
  revision_feedback?: string;
}

type PendingScenePackageJobKind = "scene_package_generation" | "scene_asset_generation" | "scene_asset_revision";

interface PrepareScenePackagesJobRequest {
  form_values: Record<string, unknown>;
  plan_markdown: string;
  selected_direction: Record<string, unknown>;
  materials?: Array<Record<string, unknown>>;
  target_duration_ms?: number;
  creation_contract?: VideoCreationContract;
  scene_blueprints?: PlanMarkdownResponse["scene_blueprints"];
  asset_manifest?: PlanMarkdownResponse["asset_manifest"];
  generate_images?: boolean;
}

interface SceneAssetsJobRequest {
  global_assets?: Record<string, unknown>;
  scene_packages: PrepareScenePackagesResponse["scene_packages"];
  materials?: Array<Record<string, unknown>>;
  image_ratio?: string;
  image_size?: string;
  model?: string | null;
  creation_contract?: VideoCreationContract;
  target_assets?: SceneAssetRetryTarget[];
}

interface PendingScenePackageJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: PendingScenePackageJobKind;
  started_at: string;
  request: PrepareScenePackagesJobRequest | SceneAssetsJobRequest | ScenePackageAssetRevisionRequest;
  artifact: ChatArtifact;
  review_message_id?: string;
  restart_count?: number;
}

type PendingVideoJobKind = "scene_generation" | "scene_regeneration" | "scene_failed_retry" | "video_merge";

interface SceneVideosJobRequest {
  scenes: SceneGenerationPayload[];
  ratio?: string;
  size?: string;
  model?: string | null;
  sound?: string;
  creation_contract?: VideoCreationContract;
}

interface MergeSceneVideosJobRequest {
  scene_videos: SceneVideoPayload[];
  duration?: number;
  size?: string;
}

interface PendingVideoJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: PendingVideoJobKind;
  started_at: string;
  request: SceneVideosJobRequest | MergeSceneVideosJobRequest;
  artifact: ChatArtifact;
  affected_scene_ids?: string[];
  use_quality_review?: boolean;
  merge_purpose?: "generation" | "regeneration";
}

type PendingPptJobKind =
  | "summary_generation"
  | "summary_update"
  | "content_json_generation"
  | "image_generation"
  | "image_regeneration"
  | "file_generation"
  | "file_regeneration";

interface PptSummaryJobRequest {
  ppt_topic: string;
  ppt_style: string;
  attachments: Array<Record<string, unknown>>;
  smart_ppt_project_id?: number | null;
}

interface PptSummaryUpdateJobRequest {
  original_outline: string;
  modification_opinion: string;
  smart_ppt_project_id: number;
}

interface PptContentJsonJobRequest {
  original_outline: string;
  ppt_style: string;
  smart_ppt_project_id: number;
}

interface PptImagesJobRequest {
  content_json: unknown;
  smart_ppt_project_id: number;
}

interface PptImageRegenerationJobRequest {
  page_index: number;
  page_json: Record<string, unknown>;
  smart_ppt_project_id: number;
}

interface PptFileJobRequest {
  file_urls: string[];
  smart_ppt_project_id: number;
}

interface PendingPptJobContext {
  formValues: Record<string, unknown>;
  coreMessage: string;
  materials?: Array<Record<string, unknown>>;
  intakeContext?: Record<string, unknown>;
  pptStyle: string;
}

interface PendingPptJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: PendingPptJobKind;
  started_at: string;
  request:
    | PptSummaryJobRequest
    | PptSummaryUpdateJobRequest
    | PptContentJsonJobRequest
    | PptImagesJobRequest
    | PptImageRegenerationJobRequest
    | PptFileJobRequest;
  artifact?: ChatArtifact;
  context?: PendingPptJobContext;
  image_message_id?: string;
}

interface PendingJianyingDraftJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  storyboard_version_id: string;
  started_at: string;
  request: JianyingDraftStartRequest;
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
  const latestIndex = [...messages]
    .reverse()
    .findIndex((message) => message.artifact?.type === "video_scene_packages" && Boolean(message.artifact.videoScenePackages));
  if (latestIndex < 0) {
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
        },
      },
    ];
  }
  const messageIndex = messages.length - 1 - latestIndex;
  return messages.map((message, index) => {
    const videoScenePackages = message.artifact?.videoScenePackages;
    if (index !== messageIndex || !message.artifact || !videoScenePackages) return message;
    return {
      ...message,
      artifact: {
        ...message.artifact,
        videoScenePackages: {
          ...videoScenePackages,
          global_assets: videoScenePackages.global_assets,
          scene_packages: videoScenePackages.scene_packages,
        },
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

export function WorkspacePage() {
  const navigate = useNavigate();
  const { conversationId } = useParams<{ conversationId?: string }>();
  // 页面可渲染状态：聊天消息、右侧画布、参数弹窗、旧运行时忙碌态和 Brief 确认态。
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvas, setCanvas] = useState<CanvasState>(EMPTY_CANVAS);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [selectedStoryboardMessageId, setSelectedStoryboardMessageId] = useState("");
  const [selectedPlanEditorMessageId, setSelectedPlanEditorMessageId] = useState("");
  const [savingPlanEdit, setSavingPlanEdit] = useState(false);
  const [savingVideoAgentScript, setSavingVideoAgentScript] = useState(false);
  const [agentRevisionSourceMessageId, setAgentRevisionSourceMessageId] = useState("");
  const [assetPackageAnchorMessageId, setAssetPackageAnchorMessageId] = useState("");
  const assetPackageAnchorMessageIdRef = useRef("");
  const [assetPackageProgressSteps, setAssetPackageProgressSteps] = useState<AgentPipelineProgressStep[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [pendingCore, setPendingCore] = useState("");
  const [pendingIntent, setPendingIntent] = useState<CreationIntent>("video");
  const [pendingFormValues, setPendingFormValues] = useState<Record<string, unknown>>({});
  const [pendingMaterials, setPendingMaterials] = useState<Array<Record<string, unknown>>>([]);
  const [referencedMaterials, setReferencedMaterials] = useState<SceneGlobalAssetReference[]>([]);
  const [composerPrefillRequest, setComposerPrefillRequest] = useState<{ id: string; content: string } | null>(null);
  const [legacyBusy, setBusy] = useState(false);
  const [briefConfirmed, setBriefConfirmed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState("");
  const [pendingPlanRevisionChoice, setPendingPlanRevisionChoice] = useState<PendingPlanRevisionChoice | null>(null);
  const [jianyingDraftCapability, setJianyingDraftCapability] = useState<JianyingDraftCapability>({
    available: false,
    reason: "剪映草稿服务待接入",
    poll_interval_seconds: 2,
  });
  const [, setJianyingDraftUiRevision] = useState(0);
  const [workflowProgress, setWorkflowProgress] = useState<WorkflowProgressSnapshot | null>(null);
  const [orchestrationMode, setOrchestrationMode] = useState<OrchestrationMode>("frontend_v2");
  const [agentRuntimeMode, setAgentRuntimeMode] = useState<WorkspaceAgentRuntimeMode>("off");
  const [orchestrationResolved, setOrchestrationResolved] = useState(true);
  const [pendingSupervisorTurns, setPendingSupervisorTurns] = useState<PendingSupervisorTurn[]>([]);
  const pendingSupervisorTurnsRef = useRef<PendingSupervisorTurn[]>([]);
  const pendingSupervisorTurnsByConversationRef = useRef(
    new Map<string, PendingSupervisorTurn[]>(),
  );
  const pendingSupervisorTurnWritesRef = useRef(
    createConversationWriteSequencer(),
  );
  const orchestrationModeRef = useRef<OrchestrationMode | null>("frontend_v2");
  const agentRuntimeModeRef = useRef<WorkspaceAgentRuntimeMode | null>("off");
  const primaryExecutionReadyRef = useRef(false);
  const deferredOwnershipInputsRef = useRef<DeferredOwnershipInput[]>([]);
  const supervisorTurnInFlightRef = useRef<Set<string>>(new Set());
  // 旧 v2 接力可能持续数秒；在接力完成并写回上下文前，禁止同一 Turn 被
  // Runtime effect 因状态刷新重复执行，避免重复计费或重复启动供应商任务。
  const supervisorLegacyHandoffClaimedRef = useRef<Set<string>>(new Set());
  const unavailableSupervisorNoticeVersionsRef = useRef(
    new Map<string, number>(),
  );
  const unavailableSupervisorRecoveryInFlightRef = useRef(
    new Set<string>(),
  );
  const resolvedRuntimePolicy = resolveWorkspaceRuntimePolicy(
    orchestrationMode,
    currentConversationId,
    agentRuntimeMode,
  );
  const runtimePolicy = orchestrationResolved
    ? resolvedRuntimePolicy
    : {
      supervisorEnabled: false,
      legacyRunnerEnabled: false,
      legacyArtifactActionsEnabled: false,
    };
  const primaryExecutionUnavailable = orchestrationResolved
    && orchestrationMode === "video_agent_v2"
    && !primaryExecutionReadyRef.current;
  // 新旧运行时共用同一个页面入口；R1 assist 只挂载会话基础设施，业务仍由旧 runner 推进。
  // 空会话使用稳定占位符，保证 Hook 顺序不变且不会向后端发起请求。
  const supervisorRuntime = useSupervisorConversation(currentConversationId || "workspace-pending", {
    enabled: runtimePolicy.supervisorEnabled,
  });
  const [videoAgentConfirmationSubmitting, setVideoAgentConfirmationSubmitting] = useState(false);
  const [videoAgentConfirmationError, setVideoAgentConfirmationError] = useState<string | null>(null);
  const [selectedVideoAgentStepId, setSelectedVideoAgentStepId] = useState<string | null>(null);
  const [videoAgentPlanAnchors, setVideoAgentPlanAnchors] = useState<Record<string, string>>({});
  const videoAgentPlanAnchorsRef = useRef<Record<string, string>>({});
  const [videoAgentPlanHistory, setVideoAgentPlanHistory] = useState<VideoAgentPlanHistory>(emptyVideoAgentPlanHistory);
  const restoringRef = useRef(false);
  const [confirmingVideoAgentScript, setConfirmingVideoAgentScript] = useState(false);
  const visibleVideoAgentConfirmationId = supervisorRuntime.state.videoAgentConfirmation?.confirmationId ?? null;
  useEffect(() => {
    setVideoAgentConfirmationSubmitting(false);
    setVideoAgentConfirmationError(null);
  }, [currentConversationId, visibleVideoAgentConfirmationId]);
  const handleVideoAgentConfirmation = (submission: AgentConfirmationSubmission) => {
    if (
      videoAgentConfirmationSubmitting
      || submission.confirmationId !== visibleVideoAgentConfirmationId
    ) return;
    setVideoAgentConfirmationSubmitting(true);
    setVideoAgentConfirmationError(null);
    void supervisorRuntime.respondToVideoAgentConfirmation(
      submission.confirmationId,
      {
        step_id: submission.stepId,
        decision: submission.decision,
      },
    ).catch(() => {
      setVideoAgentConfirmationError("确认请求未完成，请刷新后重试。");
    }).finally(() => {
      setVideoAgentConfirmationSubmitting(false);
    });
  };
  const [videoAgentQuotaSubmitting, setVideoAgentQuotaSubmitting] = useState(false);
  const [videoAgentQuotaError, setVideoAgentQuotaError] = useState<string | null>(null);
  const visibleVideoAgentQuotaId = supervisorRuntime.state.videoAgentQuota?.quotaInterruptId ?? null;
  useEffect(() => {
    setVideoAgentQuotaSubmitting(false);
    setVideoAgentQuotaError(null);
  }, [currentConversationId, visibleVideoAgentQuotaId]);
  const handleVideoAgentQuota = (submission: AgentQuotaSubmission) => {
    if (
      videoAgentQuotaSubmitting
      || submission.quotaInterruptId !== visibleVideoAgentQuotaId
    ) return;
    setVideoAgentQuotaSubmitting(true);
    setVideoAgentQuotaError(null);
    void supervisorRuntime.respondToVideoAgentQuota(
      submission.quotaInterruptId,
      { decision: submission.decision },
    ).catch(() => {
      setVideoAgentQuotaError("额度恢复请求未完成，请刷新后重试。");
    }).finally(() => {
      setVideoAgentQuotaSubmitting(false);
    });
  };
  const restoredSupervisorUi = useMemo(
    () => restoreSupervisorVideoUi(supervisorRuntime.state.interrupt?.payload),
    [supervisorRuntime.state.interrupt?.payload],
  );
  const videoAgentView = useVideoAgent(
    supervisorRuntime.state.videoAgentWorkspace,
  );
  const videoAgentCompletedStepKey = useMemo(() => {
    const plans = supervisorRuntime.state.videoAgentPlanOrder
      .map((planId) => supervisorRuntime.state.videoAgentPlans[planId])
      .filter((plan): plan is NonNullable<typeof plan> => Boolean(plan));
    if (plans.length === 0 && supervisorRuntime.state.videoAgentPlan) {
      return Object.values(supervisorRuntime.state.videoAgentPlan.steps)
        .filter((step) => step.status === "completed")
        .map((step) => step.stepId)
        .sort()
        .join(",");
    }
    return plans
      .flatMap((plan) => Object.values(plan.steps))
      .filter((step) => step.status === "completed")
      .map((step) => step.stepId)
      .sort()
      .join(",");
  }, [
    supervisorRuntime.state.videoAgentPlan,
    supervisorRuntime.state.videoAgentPlanOrder,
    supervisorRuntime.state.videoAgentPlans,
  ]);
  useEffect(() => {
    if (
      orchestrationMode !== "video_agent_v2"
      || !currentConversationId
      || !videoAgentCompletedStepKey
    ) return;
    // 步骤完成事件不含 workspace 全文；刷新 Snapshot 才能露出脚本/分镜预览。
    void supervisorRuntime.refreshSnapshot().catch(() => {});
  }, [
    currentConversationId,
    orchestrationMode,
    videoAgentCompletedStepKey,
  ]);
  useEffect(() => {
    if (!currentConversationId) {
      setVideoAgentPlanAnchors({});
      videoAgentPlanAnchorsRef.current = {};
      setVideoAgentPlanHistory(emptyVideoAgentPlanHistory());
      lastPlanAnchorUserMessageIdRef.current = "";
      return;
    }
    // 切会话先恢复热缓存；随后 restoreConversation 会用服务端 context / Snapshot 覆盖。
    let next: Record<string, string> = {};
    try {
      const raw = sessionStorage.getItem(`pixelflow:video-agent-plan-anchors:${currentConversationId}`);
      const parsed = raw ? JSON.parse(raw) as Record<string, string> : {};
      next = parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      next = {};
    }
    videoAgentPlanAnchorsRef.current = next;
    setVideoAgentPlanAnchors(next);
    // 先读热缓存，避免空历史抢写覆盖；权威仍以 Snapshot.plans 为准。
    setVideoAgentPlanHistory(loadVideoAgentPlanHistory(currentConversationId));
    lastPlanAnchorUserMessageIdRef.current = "";
    scriptPlanConfirmedRef.current = false;
    characterSupplementNoticeRef.current = "";
    durableScriptPlanMessageIdsRef.current = new Set();
  }, [currentConversationId]);
  useEffect(() => {
    assetPackageAnchorMessageIdRef.current = assetPackageAnchorMessageId;
  }, [assetPackageAnchorMessageId]);
  useEffect(() => {
    videoAgentPlanAnchorsRef.current = videoAgentPlanAnchors;
  }, [videoAgentPlanAnchors]);
  useEffect(() => {
    if (!currentConversationId) return;
    setVideoAgentPlanHistory((previous) => {
      const runtimePlans = supervisorRuntime.state.videoAgentPlans;
      const runtimeOrder = supervisorRuntime.state.videoAgentPlanOrder;
      const runtimeEmpty = Object.keys(runtimePlans || {}).length === 0
        && !supervisorRuntime.state.videoAgentPlan;
      // Snapshot 尚未带回 plans 时，保留热缓存，避免空合并把执行方案历史冲掉。
      if (runtimeEmpty && previous.order.length > 0) {
        return previous;
      }
      const merged = mergeVideoAgentPlanHistory(
        previous,
        runtimePlans,
        runtimeOrder,
        supervisorRuntime.state.videoAgentPlan,
      );
      if (
        merged.order.length === previous.order.length
        && merged.order.every((planId, index) => planId === previous.order[index])
        && merged.order.every((planId) => merged.plans[planId] === previous.plans[planId])
      ) {
        return previous;
      }
      // 热缓存仅加速同页往返；权威来源是 agent-snapshot.plans（DB）。
      saveVideoAgentPlanHistory(currentConversationId, merged);
      return merged;
    });
  }, [
    currentConversationId,
    supervisorRuntime.state.videoAgentPlan,
    supervisorRuntime.state.videoAgentPlanOrder,
    supervisorRuntime.state.videoAgentPlans,
  ]);
  useEffect(() => {
    const order = videoAgentPlanHistory.order.length > 0
      ? videoAgentPlanHistory.order
      : supervisorRuntime.state.videoAgentPlanOrder;
    if (order.length === 0) return;
    const userMessages = messages.filter((message) => message.role === "user");
    if (userMessages.length === 0) return;
    setVideoAgentPlanAnchors((previous) => {
      let changed = false;
      const next = { ...previous };
      order.forEach((planId, planIndex) => {
        const existing = next[planId];
        if (existing && userMessages.some((message) => message.id === existing)) return;
        // 锚点失效/新建：按 plan 序号对齐用户消息；最新 plan 优先锚到刚发出的用户消息。
        const isLatestPlan = planIndex === order.length - 1;
        const preferredId = lastPlanAnchorUserMessageIdRef.current;
        const preferred = preferredId
          ? userMessages.find((message) => message.id === preferredId)
          : undefined;
        const byIndex = userMessages[Math.min(planIndex, userMessages.length - 1)];
        next[planId] = (
          (!existing && isLatestPlan && preferred)
          || byIndex
          || preferred
          || userMessages[userMessages.length - 1]
        ).id;
        changed = true;
      });
      return changed ? next : previous;
    });
  }, [
    messages,
    supervisorRuntime.state.videoAgentPlanOrder,
    videoAgentPlanHistory.order,
  ]);
  useEffect(() => {
    if (!currentConversationId) return;
    try {
      sessionStorage.setItem(
        `pixelflow:video-agent-plan-anchors:${currentConversationId}`,
        JSON.stringify(videoAgentPlanAnchors),
      );
    } catch {
      // ignore quota / private mode
    }
    // 恢复会话期间不回写，避免热缓存抢写覆盖服务端锚点。
    if (restoringRef.current) return;
    // 写入会话 context，换设备/清缓存后仍可从服务端恢复锚点。
    const timer = window.setTimeout(() => {
      if (restoringRef.current) return;
      void updateConversationWithProgress(currentConversationId, {
        context: {
          ...makeSnapshot(currentConversationId),
          videoAgentPlanAnchors,
          video_agent_plan_anchors: videoAgentPlanAnchors,
        },
      }).catch(() => {});
    }, 800);
    return () => window.clearTimeout(timer);
  }, [currentConversationId, videoAgentPlanAnchors]);
  const activeSupervisorVideoTarget = useMemo<SupervisorVideoTarget | null>(() => {
    if (
      orchestrationMode !== "video_agent_v2"
      || !restoredSupervisorUi
      || supervisorRuntime.state.conversationId !== currentConversationId
    ) return null;
    const workflow = supervisorRuntime.state.workflows.find(
      (workflow) => workflow.workflow_id === restoredSupervisorUi.workflowId
        && workflow.conversation_id === currentConversationId
        && workflow.kind === "video"
        && workflow.current_stage === restoredSupervisorUi.stage,
    );
    if (!workflow) return null;
    if (
      restoredSupervisorUi.artifactRef
      && !workflow.latest_artifact_refs.includes(restoredSupervisorUi.artifactRef)
    ) return null;
    return {
      ui: restoredSupervisorUi,
      workflow,
      stage: restoredSupervisorUi.stage,
      artifactRef: restoredSupervisorUi.artifactRef,
    };
  }, [
    currentConversationId,
    orchestrationMode,
    restoredSupervisorUi,
    supervisorRuntime.state.conversationId,
    supervisorRuntime.state.workflows,
  ]);
  const activeSupervisorVideoMessage = useMemo<ChatMessage | null>(() => {
    if (!activeSupervisorVideoTarget) return null;
    const allowedTypes = activeSupervisorVideoTarget.ui.kind === "video_direction_review"
      ? new Set(["directions"])
      : activeSupervisorVideoTarget.ui.kind === "video_plan_review"
        ? new Set(["plan"])
        : activeSupervisorVideoTarget.ui.kind === "video_scene_package_review"
          ? new Set(["video_scene_packages"])
          : activeSupervisorVideoTarget.ui.kind === "video_result_review"
            ? new Set(["video_scene_packages", "video_quality_review", "video_result", "jianying_draft"])
            : new Set<string>();
    const projected = selectSupervisorArtifactMessage(
      supervisorRuntime.state.messages,
      {
        workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
        artifactRef: activeSupervisorVideoTarget.artifactRef,
        allowedTypes: [...allowedTypes],
      },
    );
    if (!projected) return null;
    return messages.find(
      (message) => message.id === projected.id
        && messageConversationId(message, currentConversationId) === currentConversationId,
    ) ?? null;
  }, [
    activeSupervisorVideoTarget,
    currentConversationId,
    messages,
    supervisorRuntime.state.messages,
  ]);
  const interactionPolicy = resolveWorkspaceInteractionPolicy({
    mode: orchestrationMode,
    conversationId: currentConversationId,
    orchestrationResolved,
    legacyBusy,
    dialogOpen,
    pendingPlanRevision: Boolean(pendingPlanRevisionChoice),
    supervisorConnection: primaryExecutionUnavailable
      ? "fatal"
      : supervisorRuntime.state.connection.status,
    supervisorRun: supervisorRuntime.state.run.status,
    supervisorCompression: supervisorRuntime.state.compression.status,
    pendingSupervisorTurns: pendingSupervisorTurns.length,
  });
  const runtimeNotice = resolveSupervisorRuntimeNotice({
    enabled: runtimePolicy.supervisorEnabled && !primaryExecutionUnavailable,
    runStatus: supervisorRuntime.state.run.status,
    runUpdatedAt: supervisorRuntime.state.run.updatedAt,
    compression: supervisorRuntime.state.compression,
    inputQueue: supervisorRuntime.state.inputQueue,
  });

  // 接收来自 content-app 的用户消息（通过 postMessage + CustomEvent）
  useEffect(() => {
    // 先检查是否已有等待消费的消息
    if (window.__CONTENT_APP_USER_MESSAGE__) {
      const msg = window.__CONTENT_APP_USER_MESSAGE__;
      window.__CONTENT_APP_USER_MESSAGE__ = undefined;
      handleSend(msg);
      return;
    }
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string | AgentUserMessagePayload>).detail;
      if (detail) handleSend(detail);
    };
    window.addEventListener("contentAppUserMessage", handler);
    return () => window.removeEventListener("contentAppUserMessage", handler);
  }, [agentRuntimeMode, orchestrationMode]);

  // 运行中上下文：这些值主要给异步 SSE 回调读取，不需要每次变化都触发 React 重渲染。
  // 可以类比后端 Service 内部字段，保存当前 taskId、事件去重集合和取消订阅函数。
  const [currentTaskId, setCurrentTaskId] = useState("");
  const messagesRef = useRef<ChatMessage[]>([]);
  const lastPlanAnchorUserMessageIdRef = useRef("");
  const scriptPlanConfirmedRef = useRef(false);
  const characterSupplementNoticeRef = useRef("");
  const durableScriptPlanMessageIdsRef = useRef<Set<string>>(new Set());
  const pendingPlanMessagePersistenceIdsRef = useRef(new Set<string>());
  const conversationIdRef = useRef<string>("");
  const routeConversationIdRef = useRef<string>("");
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const processedArtifactIdsRef = useRef(new Set<string>());
  const pendingDialogContextRef = useRef<PendingDialogContext | null>(null);
  const flowDraftRef = useRef<FlowDraft | null>(null);
  const pendingMessageJobRef = useRef<PendingMessageJob | null>(null);
  const activeMessageJobPollsRef = useRef(new Set<string>());
  const pendingIntakeJobRef = useRef<PendingIntakeJob | null>(null);
  const activeIntakeJobPollsRef = useRef(new Set<string>());
  const pendingDirectionJobRef = useRef<PendingDirectionJob | null>(null);
  const activeDirectionJobPollsRef = useRef(new Set<string>());
  const pendingPlanJobRef = useRef<PendingPlanJob | null>(null);
  const activePlanJobPollsRef = useRef(new Set<string>());
  const planJobResumeAttemptsRef = useRef(new Map<string, number>());
  const planJobRecoveryNoticesRef = useRef(new Set<string>());
  const planJobPersistenceAttemptsRef = useRef(new Map<string, number>());
  const planJobPersistenceNoticesRef = useRef(new Set<string>());
  const scheduledPlanJobPersistenceRef = useRef(new Set<string>());
  const pendingImageEditRequestRef = useRef<PendingImageEditRequest | null>(null);
  const imageEditConfirmedSelectionsRef = useRef<Record<string, ImageEditModelSelection>>({});
  const pendingImageJobRef = useRef<PendingImageJob | null>(null);
  const activeImageJobPollsRef = useRef(new Set<string>());
  const planRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const pptOutlineRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const imageRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const videoRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const pendingScenePackageJobRef = useRef<PendingScenePackageJob | null>(null);
  const [pendingScenePackageResumeVersion, setPendingScenePackageResumeVersion] = useState(0);
  const activeScenePackageJobPollsRef = useRef(new Set<string>());
  const pendingVideoJobRef = useRef<PendingVideoJob | null>(null);
  const activeVideoJobPollsRef = useRef(new Set<string>());
  const pendingPptJobRef = useRef<PendingPptJob | null>(null);
  const workflowProgressRef = useRef<WorkflowProgressSnapshot | null>(null);
  const workflowProgressConversationIdRef = useRef("");
  const activePptJobPollsRef = useRef(new Set<string>());
  const pendingJianyingDraftJobRef = useRef<PendingJianyingDraftJob | null>(null);
  const activeJianyingDraftJobPollsRef = useRef(new Set<string>());
  const jianyingDraftStartGuardRef = useRef(new JianyingDraftStartGuard());
  const jianyingDraftRecordsByConversationRef = useRef(new Map<string, JianyingDraftRecordMap>());
  const pptDoneConversationIdsRef = useRef(new Set<string>());
  const briefReadyShownRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const pageVisibleRef = useRef(true);
  const saveTimerRef = useRef<number | undefined>(undefined);
  const skipRouteRestoreConversationRef = useRef("");
  const unsubRef = useRef<() => void>(() => {});
  routeConversationIdRef.current = conversationId || "";

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    pendingSupervisorTurnsRef.current = pendingSupervisorTurns;
    if (currentConversationId) {
      pendingSupervisorTurnsByConversationRef.current.set(
        currentConversationId,
        pendingSupervisorTurns,
      );
    }
  }, [currentConversationId, pendingSupervisorTurns]);

  useEffect(() => {
    if (
      !runtimePolicy.supervisorEnabled
      || supervisorRuntime.state.connection.status !== "connected"
      || supervisorRuntime.state.conversationId !== currentConversationId
    ) return;
    const projectedMessages = mergeSupervisorMessagesWithPending(
      supervisorRuntime.state.messages,
      pendingSupervisorTurns.map((pendingTurn) => ({
        id: pendingTurn.clientInputId,
        conversationId: pendingTurn.conversationId,
        content: pendingTurn.content,
        materials: pendingTurn.materials as JsonObject[],
      })),
      currentConversationId,
    ).map((message) => ({
      ...message,
      // R1/Supervisor 会在连接建立后覆盖 REST 恢复消息，必须在这一投影边界再次转换时间。
      time: formatMessageTime(message.time, "zh-CN", undefined, message.time),
    }));
    // video_agent_v2：脚本保存等 refreshSnapshot 不能抹掉本地回执/时间线旁的聊天卡片。
    const nextMessages = orchestrationModeRef.current === "video_agent_v2"
      ? mergeProjectedMessagesWithLocalCards(
        projectedMessages,
        messagesRef.current,
        currentConversationId,
      )
      : projectedMessages;
    messagesRef.current = nextMessages;
    setMessages(nextMessages);
    if (orchestrationModeRef.current === "video_agent_v2") {
      const nextProgress = projectSupervisorWorkflowProgress(supervisorRuntime.state.workflows);
      workflowProgressConversationIdRef.current = currentConversationId;
      workflowProgressRef.current = nextProgress;
      setWorkflowProgress(nextProgress);
    }
  }, [
    currentConversationId,
    orchestrationMode,
    pendingSupervisorTurns,
    runtimePolicy.supervisorEnabled,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.conversationId,
    supervisorRuntime.state.messages,
    supervisorRuntime.state.workflows,
  ]);

  useEffect(() => {
    let disposed = false;
    void api.getJianyingDraftCapability()
      .then((capability) => {
        if (!disposed) setJianyingDraftCapability(capability);
      })
      .catch(() => {
        if (!disposed) setJianyingDraftCapability({ available: false, reason: "剪映草稿服务待接入", poll_interval_seconds: 2 });
      });
    return () => {
      disposed = true;
    };
  }, []);

  const isPptDoneForConversation = (targetConversationId: string) => {
    return Boolean(targetConversationId && pptDoneConversationIdsRef.current.has(targetConversationId));
  };

  const setPptDoneForConversation = (targetConversationId: string, value: boolean) => {
    if (!targetConversationId) return;
    const nextIds = new Set(pptDoneConversationIdsRef.current);
    if (value) nextIds.add(targetConversationId);
    else nextIds.delete(targetConversationId);
    pptDoneConversationIdsRef.current = nextIds;
  };

  const setActiveTaskId = (taskId: string) => {
    taskIdRef.current = taskId;
    setCurrentTaskId(taskId);
  };

  const setActiveConversationId = (id: string) => {
    const previousId = conversationIdRef.current;
    conversationIdRef.current = id;
    setCurrentConversationId(id);
    setActiveConversationIdForTrace(id || null);
    // 仅切换会话时清空资产包进度；同 id 重入会误抹掉正在展示的分步卡片。
    if (previousId !== id) {
      setAssetPackageAnchorMessageId("");
      setAssetPackageProgressSteps([]);
    }
  };

  const setResolvedOrchestrationMode = (mode: OrchestrationMode) => {
    orchestrationModeRef.current = mode;
    setOrchestrationMode(mode);
    setOrchestrationResolved(true);
  };

  const setResolvedAgentRuntimeMode = (
    mode: WorkspaceAgentRuntimeMode,
  ) => {
    agentRuntimeModeRef.current = mode;
    setAgentRuntimeMode(mode);
  };

  const isCurrentConversation = (targetConversationId: string) => {
    const activeConversationId = conversationIdRef.current || routeConversationIdRef.current;
    return shouldApplyVisibleConversationSideEffect(activeConversationId, targetConversationId);
  };

  const isVisibleConversation = (targetConversationId: string) => {
    return pageVisibleRef.current && isCurrentConversation(targetConversationId);
  };

  const advanceWorkflowProgress = (
    targetConversationId: string,
    lastPhase: string,
    patch: Partial<Omit<WorkflowProgressSnapshot, "version" | "last_phase" | "updated_at">> = {},
  ): WorkflowProgressSnapshot | null => {
    if (!targetConversationId) return null;
    const belongsToTarget = workflowProgressConversationIdRef.current === targetConversationId;
    const current = belongsToTarget ? workflowProgressRef.current : null;
    const contextIntent = workflowIntentFromPhase(lastPhase);
    const inferredFlowKind: WorkflowFlowKind = lastPhase.startsWith("image_edit_") ? "direct_image_edit" : "standard";
    const next: WorkflowProgressSnapshot = {
      version: 1,
      intent: patch.intent !== undefined ? patch.intent : current?.intent || contextIntent,
      flow_kind: patch.flow_kind || current?.flow_kind || inferredFlowKind,
      source_message_id: patch.source_message_id !== undefined ? patch.source_message_id : current?.source_message_id || "",
      last_phase: lastPhase,
      scene_package_stage: patch.scene_package_stage !== undefined ? patch.scene_package_stage : current?.scene_package_stage || null,
      updated_at: new Date().toISOString(),
    };
    if (isCurrentConversation(targetConversationId)) {
      workflowProgressConversationIdRef.current = targetConversationId;
      workflowProgressRef.current = next;
      setWorkflowProgress(next);
    }
    return next;
  };

  const updateConversationWithProgress = async (
    targetConversationId: string,
    body: { title?: string; current_task_id?: string | null; last_phase?: string; context?: Record<string, unknown> },
    progressPatch: Partial<Omit<WorkflowProgressSnapshot, "version" | "last_phase" | "updated_at">> = {},
  ) => {
    const nextProgress = body.last_phase
      ? advanceWorkflowProgress(targetConversationId, body.last_phase, progressPatch)
      : workflowProgressConversationIdRef.current === targetConversationId
        ? workflowProgressRef.current
        : null;
    const updated = await api.updateConversation(targetConversationId, {
      ...body,
      context: body.context
        ? {
            ...body.context,
            workflowProgress: nextProgress,
            workflow_progress: nextProgress,
          }
        : body.context,
    });
    // 会话阶段写入成功后刷新左侧列表，避免异步 Job 完成后仍展示旧的运行态。
    window.dispatchEvent(new Event("pixelflow-conversations-updated"));
    return updated;
  };

  const setBusyForConversation = (targetConversationId: string, value: boolean) => {
    if (!isCurrentConversation(targetConversationId)) return;
    if (value && !pageVisibleRef.current) return;
    setBusy(value);
  };

  const setCanvasOpenForConversation = (targetConversationId: string, value: boolean) => {
    if (isVisibleConversation(targetConversationId)) {
      setCanvasOpen(value);
      if (!value) setSelectedStoryboardMessageId("");
    }
  };

  const setCanvasForConversation = (
    targetConversationId: string,
    updater: CanvasState | ((current: CanvasState) => CanvasState),
  ) => {
    if (!isVisibleConversation(targetConversationId)) return;
    setCanvas(updater);
  };

  const beginArtifactAction = (msg: ChatMessage, targetConversationId: string): string => {
    const pendingMessageJob = pendingMessageJobRef.current;
    if (isPendingPlanSaveForConversation(pendingMessageJob, targetConversationId)) {
      return "";
    }
    const key = processedArtifactKey(msg, targetConversationId);
    if (processedArtifactIdsRef.current.has(key)) return "";
    processedArtifactIdsRef.current.add(key);
    return key;
  };

  const releaseArtifactAction = (key: string) => {
    if (key) processedArtifactIdsRef.current.delete(key);
  };

  const markImageResultAccepted = (messageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "image_result") {
          return message;
        }
        return {
          ...message,
          artifact: {
            ...message.artifact,
            imageAccepted: true,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const markVideoResultAccepted = (messageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "video_result") {
          return message;
        }
        return {
          ...message,
          artifact: {
            ...message.artifact,
            videoAccepted: true,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const recordArtifactDownload = async (msg: ChatMessage, url: string) => {
    if (!url || !msg.artifact) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const currentMessage = messagesRef.current.find(
      (message) => message.id === msg.id && messageConversationId(message, targetConversationId) === targetConversationId,
    ) || msg;
    if (!currentMessage.artifact || currentMessage.artifact.deliveryDownloadedAt) return;
    const artifact: ChatArtifact = {
      ...currentMessage.artifact,
      deliveryDownloadedAt: new Date().toISOString(),
      deliveryDownloadedUrl: url,
    };
    try {
      await api.updateConversationMessage(targetConversationId, currentMessage.id, {
        payload: {
          artifact,
          materials: currentMessage.materials || artifact.materials || [],
          client_message_id: currentMessage.id,
        } as unknown as Record<string, unknown>,
      });
      if (!isCurrentConversation(targetConversationId)) return;
      setMessages((items) => {
        const nextItems = items.map((message) =>
          message.id === currentMessage.id && messageConversationId(message, targetConversationId) === targetConversationId
            ? { ...message, artifact }
            : message,
        );
        messagesRef.current = nextItems;
        return nextItems;
      });
    } catch (err) {
      pushAssistant(`下载已开始，但交付状态保存失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    }
  };

  const hasPendingImageRevisionForResult = (msg: ChatMessage, targetConversationId: string): boolean => {
    const pendingRevision = imageRevisionArtifactRef.current;
    if (pendingRevision?.conversationId !== targetConversationId || !pendingRevision.artifact.imageResult || !msg.artifact?.imageResult) {
      return false;
    }
    const pendingImage = pendingRevision.artifact.imageResult;
    const currentImage = msg.artifact.imageResult;
    return Boolean(
      (pendingImage.task_id && pendingImage.task_id === currentImage.task_id) ||
        (pendingImage.images[0]?.url && pendingImage.images[0]?.url === currentImage.images[0]?.url),
    );
  };

  const shouldAutoAcceptImageResult = (msg: ChatMessage, targetConversationId: string): boolean => {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult) || msg.artifact.imageAccepted) return false;
    if (hasPendingImageRevisionForResult(msg, targetConversationId)) return false;
    return messagesRef.current.some((message) => {
      if (message.id !== msg.id || messageConversationId(message, targetConversationId) !== targetConversationId) return false;
      return message.artifact?.type === "image_result" && Boolean(message.artifact.imageResult && canAcceptImageResult(message.artifact.imageResult)) && !message.artifact.imageAccepted;
    });
  };

  const persistChatMessage = async (conversation: string, message: ChatMessage): Promise<ChatMessage> => {
    const request: ConversationMessageJobRequest = {
      role: message.role,
      content: message.content,
      payload: { artifact: message.artifact, materials: message.materials || [], client_message_id: message.id },
    };
    const started = await api.startConversationMessageJob(conversation, request);
    const saved = await api.pollConversationMessageJob(conversation, started.job_id);
    if (!saved) throw new Error("对话消息保存已暂停");
    return {
      ...message,
      id: message.id,
      conversationId: conversation,
      time: formatMessageTime(saved.created_at),
    };
  };

  const replaceOptimisticMessage = (
    optimisticMessageId: string,
    savedMessage: ChatMessage,
    targetConversationId: string,
    preferSavedArtifact = false,
  ) => {
    // 乐观消息落库后 id 会变；同步重映射进度卡 / 执行方案锚点，避免卡片漂到错误位置或变 orphan。
    if (optimisticMessageId && savedMessage.id && optimisticMessageId !== savedMessage.id) {
      if (assetPackageAnchorMessageIdRef.current === optimisticMessageId) {
        assetPackageAnchorMessageIdRef.current = savedMessage.id;
        setAssetPackageAnchorMessageId(savedMessage.id);
      }
      if (lastPlanAnchorUserMessageIdRef.current === optimisticMessageId) {
        lastPlanAnchorUserMessageIdRef.current = savedMessage.id;
      }
      const remappedAnchors = remapMessageAnchorId(
        videoAgentPlanAnchorsRef.current,
        optimisticMessageId,
        savedMessage.id,
      );
      if (remappedAnchors !== videoAgentPlanAnchorsRef.current) {
        videoAgentPlanAnchorsRef.current = remappedAnchors;
        setVideoAgentPlanAnchors(remappedAnchors);
      }
    }
    setMessages((items) => {
      const currentMessage = items.find((item) => item.id === optimisticMessageId);
      if (!currentMessage) {
        const nextItems = appendVisibleConversationMessage(items, {
          activeConversationId: conversationIdRef.current,
          targetConversationId,
          message: { ...savedMessage, conversationId: targetConversationId },
        });
        messagesRef.current = nextItems;
        return nextItems;
      }
      const nextItems = replaceMessageById(items, optimisticMessageId, {
        ...savedMessage,
        artifact: preferSavedArtifact ? savedMessage.artifact : currentMessage?.artifact || savedMessage.artifact,
        materials: preferSavedArtifact ? savedMessage.materials : currentMessage?.materials || savedMessage.materials,
        conversationId: targetConversationId,
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const appendOptimisticMessageForConversation = (message: ChatMessage, targetConversationId: string): ChatMessage => {
    const optimisticMessage = { ...message, conversationId: targetConversationId, time: message.time || now() };
    setMessages((items) => {
      const nextItems = appendVisibleConversationMessage(items, {
        activeConversationId: conversationIdRef.current,
        targetConversationId,
        message: optimisticMessage,
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    return optimisticMessage;
  };

  const ensurePendingSupervisorTurnVisible = (pendingTurn: PendingSupervisorTurn) => {
    const alreadyVisible = messagesRef.current.some(
      (item) => item.id === pendingTurn.clientInputId
        && messageConversationId(item, pendingTurn.conversationId) === pendingTurn.conversationId,
    );
    if (alreadyVisible) return;
    appendOptimisticMessageForConversation({
      id: pendingTurn.clientInputId,
      conversationId: pendingTurn.conversationId,
      role: "user",
      content: pendingTurn.content,
      materials: pendingTurn.materials,
      time: "",
    }, pendingTurn.conversationId);
  };

  const appendMessageForConversation = async (message: ChatMessage, targetConversationId: string): Promise<ChatMessage> => {
    if (targetConversationId) {
      const optimisticMessage = appendOptimisticMessageForConversation(message, targetConversationId);
      try {
        const savedMessage = await persistChatMessage(targetConversationId, optimisticMessage);
        replaceOptimisticMessage(optimisticMessage.id, savedMessage, targetConversationId);
        return savedMessage;
      } catch {
        return optimisticMessage;
      }
    }
    const localMessage = { ...message, time: message.time || now() };
    setMessages((items) => {
      const nextItems = [...items, localMessage];
      messagesRef.current = nextItems;
      return nextItems;
    });
    return localMessage;
  };

  const removeOptimisticMessage = (optimisticMessageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.filter(
        (item) => item.id !== optimisticMessageId || messageConversationId(item, targetConversationId) !== targetConversationId,
      );
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const schedulePendingMessageJobResume = (pendingMessageJob: PendingMessageJob, delayMs?: number) => {
    const delay = delayMs ?? planMessageResumeDelayMs(pendingMessageJob.restart_count);
    window.setTimeout(() => {
      const current = pendingMessageJobRef.current;
      if (!isSameMessageJobGeneration(current, pendingMessageJob)) return;
      if (!current) return;
      void resumePendingMessageJob(current).catch(() => {});
    }, delay);
  };

  const persistPlanArtifactForConversation = async (
    message: ChatMessage,
    targetConversationId: string,
    continuation: PendingPlanSaveMessageContinuation,
  ): Promise<void> => {
    pendingPlanMessagePersistenceIdsRef.current.add(message.id);
    try {
      if (!targetConversationId) throw new Error("无法保存 plan.md：对话 ID 不存在");
      const pendingMessageJob = await startConversationMessageJobForConversation(
        message,
        targetConversationId,
        continuation,
      );
      if (!pendingMessageJob) throw new Error("无法保存 plan.md：消息任务未启动");
      await resumePendingMessageJob(pendingMessageJob);
    } catch (err) {
      const pendingMessageJob = pendingMessageJobRef.current;
      if (
        pendingMessageJob?.source_message_id === message.id
        && pendingMessageJob.continue_after_save?.type === "plan_save"
      ) {
        schedulePendingMessageJobResume(pendingMessageJob, 0);
        return;
      }
      pendingPlanMessagePersistenceIdsRef.current.delete(message.id);
      removeOptimisticMessage(message.id, targetConversationId);
      throw err;
    }
  };

  const startConversationMessageJobForConversation = async (
    message: ChatMessage,
    targetConversationId: string,
    continuation?: PendingMessageJobContinuation,
  ): Promise<PendingMessageJob | null> => {
    const existingPendingMessageJob = pendingMessageJobRef.current;
    if (
      continuation?.type !== "plan_save"
      && existingPendingMessageJob
      && isPendingPlanSaveForConversation(existingPendingMessageJob, targetConversationId)
    ) {
      schedulePendingMessageJobResume(existingPendingMessageJob, 0);
      throw new Error("当前 plan.md 仍在保存，请等待保存完成后再发送新消息");
    }
    if (!targetConversationId) {
      appendOptimisticMessageForConversation(message, targetConversationId);
      return null;
    }
    const optimisticMessage = appendOptimisticMessageForConversation(message, targetConversationId);
    const request: ConversationMessageJobRequest = {
      role: optimisticMessage.role,
      content: optimisticMessage.content,
      payload: {
        artifact: optimisticMessage.artifact,
        materials: optimisticMessage.materials || [],
        client_message_id: optimisticMessage.id,
      },
    };
    const started = await api.startConversationMessageJob(targetConversationId, request);
    const pendingMessageJob: PendingMessageJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: optimisticMessage.id,
      kind: "conversation_message",
      started_at: new Date().toISOString(),
      request,
      message: optimisticMessage,
      continue_after_save: continuation,
    };
    if (continuation?.type === "handle_send") {
      advanceWorkflowProgress(targetConversationId, "message_save_running", {
        intent: null,
        flow_kind: "standard",
        source_message_id: optimisticMessage.id,
        scene_package_stage: null,
      });
    }
    await persistPendingMessageJob(
      pendingMessageJob,
      targetConversationId,
      continuation?.type === "plan_save" ? "plan_message_save_running" : "message_save_running",
    );
    return pendingMessageJob;
  };

  const pushAssistant = (content: string, targetConversationId = conversationIdRef.current) => {
    const message: ChatMessage = { id: uid(), conversationId: targetConversationId || undefined, role: "assistant", content, time: "" };
    void appendMessageForConversation(message, targetConversationId);
    return message.id;
  };

  const pushArtifact = (
    content: string,
    artifact: ChatArtifact,
    targetConversationId = conversationIdRef.current,
    messageId = uid(),
  ) => {
    const message: ChatMessage = { id: messageId, conversationId: targetConversationId || undefined, role: "assistant", content, time: "", artifact };
    void appendMessageForConversation(message, targetConversationId);
    return message;
  };

  const makeFlowDraft = (
    stage: FlowDraftStage,
    data: Omit<FlowDraft, "version" | "stage" | "updatedAt">,
  ): FlowDraft => ({
    version: 1,
    stage,
    ...data,
    updatedAt: new Date().toISOString(),
  });

  const hasDirectionsArtifactForDraft = (items: ChatMessage[], targetConversationId: string, draft: FlowDraft | null | undefined): boolean => {
    if (!draft?.creativeDirections?.length) return false;
    const draftFingerprint = creativeDirectionsFingerprint(draft.creativeDirections);
    return items.some((message) => {
      if (messageConversationId(message, targetConversationId) !== targetConversationId) return false;
      const artifact = message.artifact;
      if (artifact?.type !== "directions" || artifact.intent !== draft.intent) return false;
      return Boolean(draftFingerprint && creativeDirectionsFingerprint(artifact.directions) === draftFingerprint);
    });
  };

  const restoreFormDraft = (draft: FlowDraft, targetConversationId: string) => {
    if (!isCreationIntent(draft.intent)) return;
    const materials = draft.materials || [];
    const formValues = draft.form ? valuesFromForm(draft.form) : draft.formValues || {};
    setPendingCore(draft.coreMessage || "");
    setPendingIntent(draft.intent);
    setPendingFormValues(formValues);
    setPendingMaterials(materials);
    pendingDialogContextRef.current = {
      conversationId: targetConversationId,
      coreMessage: draft.coreMessage || "",
      materials,
      intakeContext: draft.intakeContext || draft.intakeIntent?.intake_context || {},
    };
    if (isVisibleConversation(targetConversationId)) setDialogOpen(true);
  };

  const recordImageEditConfirmedSelection = (
    messageId: string,
    targetConversationId: string,
    selection: ImageEditModelSelection,
  ) => {
    imageEditConfirmedSelectionsRef.current = {
      ...imageEditConfirmedSelectionsRef.current,
      [messageId]: selection,
    };
    setMessages((items) =>
      items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "image_edit_options") {
          return message;
        }
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
      }),
    );
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(),
            imageEditConfirmedSelections: imageEditConfirmedSelectionsRef.current,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const buildPptImagesMessageContent = (pptImages: PptImagesResult) =>
    pptImages.ok ? "PPT 页面图片已生成，请确认后生成 PPT 附件。" : "PPT 页面图片生成中，请查看每页状态。";

  const buildPptImagesArtifact = (
    pptImages: PptImagesResult,
    sourceArtifact: ChatArtifact,
    pptContentJson?: PptContentJsonResult,
  ): ChatArtifact => ({
    ...sourceArtifact,
    type: "ppt_images",
    title: "PPT页面图片",
    description: String(pptImages.message || `${pptImages.pages.length} 页 PPT 图片`),
    actionLabel: "生成附件",
    intent: "ppt",
    materials: sourceArtifact.materials || [],
    pptContentJson: pptContentJson || sourceArtifact.pptContentJson,
    pptImages,
    smartPptProjectId:
      pptProjectId(sourceArtifact) ?? numericValue(pptImages.smart_ppt_project_id) ?? numericValue(pptContentJson?.smart_ppt_project_id),
  });

  const updatePptImagesArtifactInMessage = (
    messageId: string,
    targetConversationId: string,
    pptImages: PptImagesResult,
    sourceArtifact?: ChatArtifact,
    pptContentJson?: PptContentJsonResult,
  ) => {
    const visibleMessage = messagesRef.current.find(
      (message) =>
        message.id === messageId &&
        messageConversationId(message, targetConversationId) === targetConversationId &&
        message.artifact?.type === "ppt_images",
    );
    const baseArtifact = visibleMessage?.artifact || sourceArtifact;
    if (!baseArtifact) return;

    const content = buildPptImagesMessageContent(pptImages);
    const artifact = buildPptImagesArtifact(pptImages, baseArtifact, pptContentJson);
    if (isVisibleConversation(targetConversationId)) {
      setMessages((items) => {
        const nextItems = items.map((message) => {
          if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "ppt_images") {
            return message;
          }
          return {
            ...message,
            content,
            artifact,
          };
        });
        messagesRef.current = nextItems;
        return nextItems;
      });
    }
    void api
      .updateConversationMessage(targetConversationId, messageId, {
        content,
        payload: {
          artifact,
          materials: artifact.materials || [],
          client_message_id: messageId,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {});
  };

  const markPptFileDoneInMessage = (messageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "ppt_file") {
          return message;
        }
        return {
          ...message,
          artifact: {
            ...message.artifact,
            pptDone: true,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const updateVideoScenePackagesInMessage = (
    messageId: string,
    updater: (scenePackages: ScenePackageRecord[]) => ScenePackageRecord[],
    editedSceneId?: string,
  ) => {
    let targetConversationId = "";
    let updatedPackages: PrepareScenePackagesResponse | undefined;
    let editedSceneIdsForContext: string[] = [];
    setMessages((items) => {
      const nextItems = items.map((message) => {
        const artifact = message.artifact;
        const videoScenePackages = artifact?.videoScenePackages;
        if (message.id !== messageId || !artifact || !videoScenePackages) return message;
        targetConversationId = messageConversationId(message, conversationIdRef.current);
        const editedSceneIds = new Set(artifact.videoScenePackageEditedSceneIds || []);
        if (editedSceneId) editedSceneIds.add(editedSceneId);
        const nextScenePackages = updater(videoScenePackages.scene_packages as ScenePackageRecord[]) as typeof videoScenePackages.scene_packages;
        editedSceneIdsForContext = Array.from(editedSceneIds);
        updatedPackages = {
          ...videoScenePackages,
          scene_packages: nextScenePackages,
        };
        return {
          ...message,
          artifact: {
            ...artifact,
            videoScenePackageEditedSceneIds: editedSceneIdsForContext,
            videoScenePackages: updatedPackages,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    if (targetConversationId && updatedPackages) {
      void updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(),
            global_assets: updatedPackages.global_assets,
            scene_packages: updatedPackages.scene_packages,
            video_scene_package_edited_scene_ids: editedSceneIdsForContext,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const updateVideoScenePackageArtifactInMessage = (
    messageId: string,
    updater: (videoScenePackages: PrepareScenePackagesResponse) => PrepareScenePackagesResponse,
  ): PrepareScenePackagesResponse | undefined => {
    let updatedPackages: PrepareScenePackagesResponse | undefined;
    setMessages((items) => {
      const nextItems = items.map((message) => {
        const artifact = message.artifact;
        const videoScenePackages = artifact?.videoScenePackages;
        if (message.id !== messageId || !artifact || !videoScenePackages) return message;
        updatedPackages = updater(videoScenePackages);
        return {
          ...message,
          artifact: {
            ...artifact,
            videoScenePackages: updatedPackages,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    return updatedPackages;
  };

  const persistScenePackageSnapshot = (
    targetConversationId: string,
    packages: PrepareScenePackagesResponse,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    if (!targetConversationId) return;
    void updateConversationWithProgress(targetConversationId, {
        last_phase: lastPhase,
        context: {
          ...makeSnapshot(targetConversationId),
          global_assets: packages.global_assets,
          scene_packages: packages.scene_packages,
          ...extraContext,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {});
  };

  const updateOriginalScenePackageMessageWithVideoResult = (
    sourceMessageId: string,
    targetConversationId: string,
    videoScenePackages: PrepareScenePackagesResponse,
    generatedSceneVideos: NonNullable<ChatArtifact["generatedSceneVideos"]>,
    mergedVideo: NonNullable<ChatArtifact["mergedVideo"]>,
  ) => {
    setMessages((items) => {
      const sourceIndex = items.findIndex(
        (message) =>
          message.id === sourceMessageId &&
          messageConversationId(message, targetConversationId) === targetConversationId &&
          message.artifact?.type === "video_scene_packages" &&
          Boolean(message.artifact.videoScenePackages),
      );
      const targetIndex = sourceIndex >= 0
        ? sourceIndex
        : items
            .map((message, index) => ({ message, index }))
            .reverse()
            .find(
              ({ message }) =>
                messageConversationId(message, targetConversationId) === targetConversationId &&
                message.artifact?.type === "video_scene_packages" &&
                Boolean(message.artifact.videoScenePackages),
            )?.index ?? -1;
      if (targetIndex < 0) {
        messagesRef.current = items;
        return items;
      }
      const nextItems = items.map((message, index) => {
        if (index !== targetIndex || !message.artifact?.videoScenePackages) return message;
        return {
          ...message,
          artifact: {
            ...message.artifact,
            videoScenePackages,
            generatedSceneVideos,
            mergedVideo,
            videoScenePackageEditedSceneIds: [],
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(),
            global_assets: videoScenePackages.global_assets,
            scene_packages: videoScenePackages.scene_packages,
            generated_scene_videos: generatedSceneVideos.scene_videos,
            merged_video: mergedVideo,
            video_scene_package_edited_scene_ids: [],
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const storyboardMessageHasGlobalAsset = (message: ChatMessage | undefined, reference: SceneGlobalAssetReference): boolean => {
    const globalAssets = message?.artifact?.videoScenePackages?.global_assets;
    const records = globalAssets?.[reference.asset_group];
    if (!Array.isArray(records)) return false;
    return records.some((record) => String(record.asset_id || record.id || "") === reference.asset_id);
  };

  const findStoryboardMessageForGlobalAsset = (
    reference: SceneGlobalAssetReference,
    targetConversationId: string,
    preferredMessageIds: string[] = [],
  ): ChatMessage | undefined => {
    const currentMessages = messagesRef.current;
    const preferredIds = [
      ...preferredMessageIds,
      reference.storyboard_message_id,
      selectedStoryboardMessageId,
    ].filter((id): id is string => Boolean(id));
    for (const id of preferredIds) {
      const candidate = currentMessages.find(
        (item) =>
          item.id === id &&
          messageConversationId(item, targetConversationId) === targetConversationId &&
          Boolean(item.artifact?.videoScenePackages),
      );
      if (storyboardMessageHasGlobalAsset(candidate, reference)) return candidate;
    }
    const selectedCandidate = selectedStoryboardMessageId
      ? currentMessages.find((item) => item.id === selectedStoryboardMessageId && item.artifact?.videoScenePackages)
      : undefined;
    if (storyboardMessageHasGlobalAsset(selectedCandidate, reference)) return selectedCandidate;
    const latestCandidate = [...currentMessages]
      .reverse()
      .find(
        (item) =>
          messageConversationId(item, targetConversationId) === targetConversationId &&
          Boolean(item.artifact?.videoScenePackages) &&
          storyboardMessageHasGlobalAsset(item, reference),
      );
    if (latestCandidate) return latestCandidate;
    const referencedCandidate = reference.storyboard_message_id
      ? currentMessages.find((item) => item.id === reference.storyboard_message_id && item.artifact?.videoScenePackages)
      : undefined;
    return storyboardMessageHasGlobalAsset(referencedCandidate, reference) ? referencedCandidate : undefined;
  };

  const handleUpdateVideoScenePackage = (msg: ChatMessage, sceneId: string, patch: ScenePackagePatch) => {
    updateVideoScenePackagesInMessage(msg.id, (scenePackages) => updateScenePackageField(scenePackages, sceneId, patch), sceneId);
  };

  const handleReferenceGlobalAsset = (asset: SceneGlobalAssetReference) => {
    const material = selectedStoryboardMessageId
      ? { ...asset, conversation_id: currentConversationId, scene_global_asset_action: "edit" as const, storyboard_message_id: selectedStoryboardMessageId }
      : { ...asset, conversation_id: currentConversationId, scene_global_asset_action: "edit" as const };
    setReferencedMaterials((items) => {
      const next = items.filter((item) => item.asset_id !== material.asset_id);
      return [material, ...next].slice(0, 1);
    });
  };

  const handleDeleteGlobalAsset = (asset: SceneGlobalAssetReference) => {
    if (activeSupervisorVideoTarget?.ui.kind === "video_scene_package_review") {
      void submitSupervisorAction(
        "删除视频全局素材",
        buildSupervisorWorkflowAction({
          action: "modify_workflow",
          intent: "video",
          workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
          stage: activeSupervisorVideoTarget.stage,
          artifactRef: activeSupervisorVideoTarget.artifactRef,
          patch: {
            asset_action: "delete",
            asset_group: asset.asset_group,
            asset_id: asset.asset_id,
          },
        }),
        {
          artifactRefs: activeSupervisorVideoTarget.artifactRef
            ? [activeSupervisorVideoTarget.artifactRef]
            : [],
        },
      );
      return;
    }
    const material = selectedStoryboardMessageId
      ? { ...asset, conversation_id: currentConversationId, storyboard_message_id: selectedStoryboardMessageId }
      : { ...asset, conversation_id: currentConversationId };
    const deleteMaterial = { ...material, scene_global_asset_action: "delete" as const };
    setReferencedMaterials((items) => {
      const next = items.filter((item) => item.asset_id !== deleteMaterial.asset_id);
      return [deleteMaterial, ...next].slice(0, 1);
    });
    setComposerPrefillRequest({ id: uid(), content: SCENE_GLOBAL_ASSET_DELETE_PROMPT(deleteMaterial.name) });
  };

  const startSceneGlobalAssetRevision = async (
    reference: SceneGlobalAssetReference,
    operation: "replace" | "delete",
    replacement?: SceneGlobalAssetReplacement,
    options: { processedKey?: string; reviewMessageId?: string; targetConversationId?: string } = {},
  ): Promise<boolean> => {
    const targetConversationId = options.targetConversationId || currentConversationId || conversationIdRef.current;
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId);
    if (!storyboardMessage?.artifact?.videoScenePackages) {
      if (options.processedKey) releaseArtifactAction(options.processedKey);
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后重试。", targetConversationId);
      return false;
    }
    const request: ScenePackageAssetRevisionRequest = {
      operation,
      asset_id: reference.asset_id,
      asset_group: reference.asset_group,
      asset_name: replacement?.assetName || reference.name,
      source_image_url: reference.source_image_url,
      new_image_url: replacement?.displayImageUrl || null,
      generation_reference_url: replacement?.generationReferenceUrl || null,
      replacement_metadata: replacement
        ? {
            replacement_source: replacement.source,
            third_asset_id: replacement.thirdAssetId,
            replacement_asset_type: replacement.assetType,
            replacement_asset_id: replacement.contentAssetId,
            replacement_asset_name: replacement.assetName,
          }
        : {},
      global_assets: storyboardMessage.artifact.videoScenePackages.global_assets,
      scene_packages: storyboardMessage.artifact.videoScenePackages.scene_packages,
    };

    try {
      setBusyForConversation(targetConversationId, true);
      pushAssistant(
        operation === "replace"
          ? "正在分析新素材，并同步更新受影响的分镜内容…"
          : "正在移除素材，并同步清理各分镜中的引用和相关描述…",
        targetConversationId,
      );
      const started = await api.startScenePackageAssetRevisionJob(request);
      const pendingScenePackageJob: PendingScenePackageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: storyboardMessage.id,
        kind: "scene_asset_revision",
        started_at: new Date().toISOString(),
        request,
        artifact: storyboardMessage.artifact,
        review_message_id: options.reviewMessageId,
      };
      await persistPendingScenePackageJob(pendingScenePackageJob, targetConversationId, "scene_asset_revision_running", {
        scene_global_asset_revision: {
          operation,
          asset_id: reference.asset_id,
          asset_group: reference.asset_group,
        },
      });
      await resumePendingScenePackageJob(pendingScenePackageJob, options.processedKey || "");
      return true;
    } catch (err) {
      if (options.processedKey) releaseArtifactAction(options.processedKey);
      pushAssistant(`分镜素材修改失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      return false;
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleReplaceGlobalAsset = (asset: SceneGlobalAssetReference, replacement: SceneGlobalAssetReplacement) => {
    const reference = selectedStoryboardMessageId ? { ...asset, storyboard_message_id: selectedStoryboardMessageId } : asset;
    void startSceneGlobalAssetRevision(reference, "replace", replacement);
  };

  const handleAddGlobalAsset = (
    msg: ChatMessage,
    assetGroup: GlobalSceneAssetGroup,
    replacement: SceneGlobalAssetReplacement,
  ) => {
    const targetConversationId = messageConversationId(msg, currentConversationId || conversationIdRef.current);
    const storyboardMessage = messagesRef.current.find(
      (item) =>
        item.id === msg.id &&
        messageConversationId(item, targetConversationId) === targetConversationId &&
        Boolean(item.artifact?.videoScenePackages),
    ) || msg;
    const artifact = storyboardMessage.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    if (!artifact || !videoScenePackages) {
      pushAssistant("当前没有找到可添加素材的场景包，请先打开最新的场景包卡片。", targetConversationId);
      return;
    }

    const added = addGlobalSceneAssetReference(videoScenePackages.global_assets, {
      assetGroup,
      manualId: uid(),
      replacement,
    });
    const updatedPackages: PrepareScenePackagesResponse = {
      ...videoScenePackages,
      global_assets: added.global_assets,
    };
    const updatedArtifact: ChatArtifact = {
      ...artifact,
      videoScenePackages: updatedPackages,
    };

    updateVideoScenePackageArtifactInMessage(storyboardMessage.id, () => updatedPackages);
    if (targetConversationId) {
      void api.updateConversationMessage(targetConversationId, storyboardMessage.id, {
        content: storyboardMessage.content,
        payload: {
          artifact: updatedArtifact,
          materials: updatedArtifact.materials || [],
          client_message_id: storyboardMessage.id,
        } as unknown as Record<string, unknown>,
      }).catch(() => {});
    }
    persistScenePackageSnapshot(targetConversationId, updatedPackages, "scene_global_asset_added", {
      scene_global_asset_addition: {
        asset_id: added.added_asset.asset_id,
        asset_group: assetGroup,
        name: added.added_asset.name,
        replacement_source: replacement.source,
        manual_added: true,
      },
    });
  };

  const handleRemoveReferencedMaterial = (key: string) => {
    setReferencedMaterials((items) => items.filter((item) => sceneGlobalAssetMaterialKey(item) !== key));
  };

  const sceneGlobalAssetEditRatio = async (
    reference: SceneGlobalAssetReference,
    videoScenePackages: PrepareScenePackagesResponse,
    modelConfigs: ImageModelParamConfig[],
  ): Promise<string> => {
    const options = imageModelOptions(preferredImageEditConfig(modelConfigs));
    const assetRecord = globalSceneAssetRecord(videoScenePackages, reference) || reference;
    const metadataRatio = globalSceneAssetRatioFromMetadata(assetRecord, options.ratios);
    if (metadataRatio) return metadataRatio;
    const fallbackRatio = inferGlobalSceneAssetRatioFromMetadata(assetRecord, reference.asset_group, options.ratios);
    const naturalSize = await imageNaturalSize(reference.source_image_url);
    if (naturalSize) return nearestSupportedAspectRatio(naturalSize.width, naturalSize.height, options.ratios, fallbackRatio);
    return fallbackRatio;
  };

  const pushSceneGlobalAssetEditOptions = async (
    reference: SceneGlobalAssetReference,
    prompt: string,
    targetConversationId: string,
    materials: Array<Record<string, unknown>> = [],
  ): Promise<boolean> => {
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId);
    const videoScenePackages = storyboardMessage?.artifact?.videoScenePackages;
    if (!storyboardMessage?.artifact || !videoScenePackages) {
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后再编辑。", targetConversationId);
      return true;
    }
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      pushAssistant("已引用素材，请在输入框里写清楚要怎么修改这张图片。", targetConversationId);
      return true;
    }

    const uploadedReferences = uploadedReferenceMaterials(materials);
    const mode = uploadedReferences.length >= 1 ? "scene_global_asset_fusion" : "scene_global_asset_edit";
    setReferencedMaterials((items) => items.filter((item) => item.asset_id !== reference.asset_id));
    setBusyForConversation(targetConversationId, true);
    pushAssistant("已引用场景包素材，正在读取可用图片模型和参数配置…", targetConversationId);
    let modelConfigs: ImageModelParamConfig[] = [];
    try {
      modelConfigs = await api.listImageGenerateModelConfigs();
    } catch (err) {
      modelConfigs = [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
      pushAssistant(`图片模型配置读取失败，已使用默认模型 image-2。${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }

    const normalizedConfigs = modelConfigs.length > 0 ? modelConfigs : [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
    const preferredConfig = preferredImageEditConfig(normalizedConfigs);
    const options = imageModelOptions(preferredConfig);
    const selection: ImageEditModelSelection = {
      model: imageModelType(preferredConfig) || "gpt-image-2",
      ratio: await sceneGlobalAssetEditRatio(reference, videoScenePackages, normalizedConfigs),
      size: options.sizes[0] || "4K",
    };
    const request: PendingImageEditRequest = {
      conversationId: targetConversationId,
      prompt: cleanPrompt,
      formValues: {
        image_goal: reference.name,
        image_operation: "image_edit",
        image_model: selection.model,
        image_size: selection.ratio,
        image_quality: selection.size,
      },
      intakeContext: {
        image_operation: "image_edit",
        image_model: selection.model,
        image_size: selection.ratio,
        image_quality: selection.size,
        scene_global_asset_reference: reference,
      },
      materials: uploadedReferences,
      selection,
      mode,
      sceneGlobalAssetReference: reference,
      storyboardMessageId: storyboardMessage.id,
    };
    pendingImageEditRequestRef.current = request;
    pushArtifact("全局素材图片编辑参数已准备好，请确认后开始编辑。", {
      type: "image_edit_options",
      title: mode === "scene_global_asset_fusion" ? "全局素材融合参数确认" : "全局素材编辑参数确认",
      description: "选择图片编辑模型、原图比例和清晰度。编辑结果成功后，需要你确认才会替换场景包素材。",
      actionLabel: "确认",
      intent: "image",
      formValues: request.formValues,
      intakeContext: request.intakeContext,
      materials: [{ ...reference, source_image_url: reference.source_image_url, url: reference.source_image_url }, ...uploadedReferences],
      imageEditRequest: request as unknown as Record<string, unknown>,
      imageEditModelConfigs: normalizedConfigs,
      imageEditRequestedParams: {
        ratio: selection.ratio,
        size: selection.size,
      },
    }, targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "scene_global_asset_edit_model_pending",
          context: {
            ...makeSnapshot(),
            pendingImageEditRequest: request,
            pending_image_edit_request: request,
            scene_global_asset_reference: reference,
            scene_global_asset_edit_prompt: cleanPrompt,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
    return true;
  };

  const handleEditReferencedGlobalAsset = async (
    reference: SceneGlobalAssetReference,
    prompt: string,
    targetConversationId: string,
    materials: Array<Record<string, unknown>> = [],
  ): Promise<boolean> => {
    return pushSceneGlobalAssetEditOptions(reference, prompt, targetConversationId, materials);
  };

  const executeSceneGlobalAssetEdit = async (request: PendingImageEditRequest): Promise<void> => {
    const targetConversationId = request.conversationId;
    const reference = request.sceneGlobalAssetReference;
    if (!reference) return;
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId, [request.storyboardMessageId || ""]);
    if (!storyboardMessage?.artifact?.videoScenePackages) {
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后再编辑。", targetConversationId);
      return;
    }
    const uploadedReferences = uploadedReferenceMaterials(request.materials);
    const shouldFuseAsset = request.mode === "scene_global_asset_fusion" || uploadedReferences.length >= 1;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(
      shouldFuseAsset
        ? `正在融合引用素材和 ${uploadedReferences.length} 张上传图片，生成「${reference.name}」的候选新图…`
        : `正在调用图片编辑接口生成「${reference.name}」的候选新图…`,
      targetConversationId,
    );
    try {
      const jobRequest: ImageAssetEditJobRequest | ImageAssetFusionJobRequest = {
        asset_id: reference.asset_id,
        asset_name: reference.name,
        asset_group: reference.asset_group,
        source_image_url: reference.source_image_url,
        prompt: request.prompt,
        materials: uploadedReferences,
        ratio: request.selection?.ratio,
        size: request.selection?.size,
        model: request.selection?.model,
      };
      const started = shouldFuseAsset ? await api.startImageAssetFusionJob(jobRequest) : await api.startImageAssetEditJob(jobRequest);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: storyboardMessage.id,
        kind: shouldFuseAsset ? "scene_global_asset_fusion" : "scene_global_asset_edit",
        job_api: shouldFuseAsset ? "fuse_asset" : "edit_asset",
        started_at: new Date().toISOString(),
        request: jobRequest,
        artifact: storyboardMessage.artifact,
        sceneGlobalAssetReference: reference,
        storyboard_message_id: storyboardMessage.id,
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, shouldFuseAsset ? "scene_global_asset_fusion_running" : "scene_global_asset_edit_running", {
        scene_global_asset_reference: reference,
        scene_global_asset_edit_prompt: request.prompt,
        scene_global_asset_fusion: shouldFuseAsset,
        pendingImageEditRequest: null,
        pending_image_edit_request: null,
      });
      pendingImageEditRequestRef.current = null;
      await resumePendingImageJob(pendingImageJob);
    } catch (err) {
      pushAssistant(`全局素材图片编辑失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleDeleteReferencedGlobalAsset = async (
    reference: SceneGlobalAssetReference,
    targetConversationId: string,
  ): Promise<boolean> => {
    return startSceneGlobalAssetRevision(reference, "delete", undefined, { targetConversationId });
  };

  const showImageEditOptions = async (request: PendingImageEditRequest): Promise<void> => {
    const targetConversationId = request.conversationId;
    const flowMaterials = request.materials || [];
    if (!hasImageMaterial(flowMaterials)) {
      pendingImageEditRequestRef.current = request;
      pushAssistant("我识别到这是图片编辑需求，请上传需要编辑的图片后提交，我会先让你确认图片编辑模型和参数。", targetConversationId);
      if (targetConversationId) {
        void updateConversationWithProgress(targetConversationId, {
            last_phase: "image_edit_waiting_source_image",
            context: {
              ...makeSnapshot(),
              intent: "image",
              pendingImageEditRequest: pendingImageEditRequestRef.current,
              pending_image_edit_request: pendingImageEditRequestRef.current,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
      return;
    }

    setBusyForConversation(targetConversationId, true);
    pushAssistant("已识别为图片编辑需求，正在读取可用图片模型和参数配置…", targetConversationId);
    let modelConfigs: ImageModelParamConfig[] = [];
    try {
      modelConfigs = await api.listImageGenerateModelConfigs();
    } catch (err) {
      modelConfigs = [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
      pushAssistant(`图片模型配置读取失败，已使用默认模型 image-2。${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
    const normalizedConfigs = modelConfigs.length > 0 ? modelConfigs : [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
    pendingImageEditRequestRef.current = request;
    pushArtifact("图片编辑模型和参数已准备好，请确认后开始编辑。", {
      type: "image_edit_options",
      title: "图片编辑参数确认",
      description: "选择图片编辑模型。若需求里明确了尺寸或清晰度，所选模型必须支持后才能提交。",
      actionLabel: "确认",
      intent: "image",
      formValues: request.formValues,
      intakeContext: request.intakeContext,
      materials: flowMaterials,
      imageEditRequest: request as unknown as Record<string, unknown>,
      imageEditModelConfigs: normalizedConfigs,
      imageEditRequestedParams: {
        ratio: request.selection?.ratio || request.formValues.image_size || request.intakeContext.image_size || "",
        size: request.selection?.size || request.formValues.image_quality || request.intakeContext.image_quality || "",
      },
    }, targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "image_edit_model_pending",
          context: {
            ...makeSnapshot(),
            intent: "image",
            materials: flowMaterials,
            pendingImageEditRequest: pendingImageEditRequestRef.current,
            pending_image_edit_request: pendingImageEditRequestRef.current,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const executeDirectImageEdit = async (request: PendingImageEditRequest): Promise<void> => {
    const targetConversationId = request.conversationId;
    const flowMaterials = request.materials || [];
    const formValues = directImageEditFormValues(request.prompt, request.formValues, request.intakeContext, request.selection);
    const intakeContext = {
      ...request.intakeContext,
      image_operation: "image_edit",
      image_model: request.selection?.model || request.intakeContext.image_model || formValues.image_model,
      image_size: request.selection?.ratio || request.intakeContext.image_size || formValues.image_size,
      image_quality: request.selection?.size || request.intakeContext.image_quality || formValues.image_quality,
      requested_output_count: request.intakeContext.requested_output_count || formValues.image_count || 1,
    };
    const executableRequest: PendingImageEditRequest = { ...request, formValues, intakeContext };
    pendingImageEditRequestRef.current = executableRequest;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "image_edit_generation_running", {
      intent: "image",
      flow_kind: "direct_image_edit",
    });
    pushAssistant("已识别为图片编辑需求，正在直接调用图片编辑接口生成结果…", targetConversationId);
    try {
      const imagePrepare = await api.prepareImageGeneration({
        form_values: formValues,
        plan_markdown: "",
        selected_direction: {
          title: "图片编辑",
          description: request.prompt,
          operation: "image_edit",
        },
        materials: flowMaterials,
        intake_context: intakeContext,
      });
      if (!imagePrepare.ok) {
        pendingImageEditRequestRef.current = { ...request, formValues, intakeContext };
        pushArtifact("图片编辑需要先补充原图，请上传后我会继续。", {
          type: "image_prepare",
          title: "图片编辑准备",
          description: imagePrepare.message,
          actionLabel: "查看",
          imagePrepare,
          intent: "image",
          formValues,
          intakeContext,
          materials: flowMaterials,
          selectedDirection: {
            direction_id: "image_edit",
            title: "图片编辑",
            description: request.prompt,
            recommended: true,
            tags: ["图片编辑"],
            data: { operation: "image_edit" },
          },
        }, targetConversationId);
        void updateConversationWithProgress(targetConversationId, {
            last_phase: "image_edit_waiting_source_image",
            context: {
              ...makeSnapshot(),
              intent: "image",
              pendingImageEditRequest: pendingImageEditRequestRef.current,
              pending_image_edit_request: pendingImageEditRequestRef.current,
              image_prepare: imagePrepare,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
        return;
      }
      pushAssistant(`正在调用 ${imagePrepare.endpoint} 编辑图片…`, targetConversationId);
      const jobRequest: ImageGenerationJobRequest = {
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
      };
      const selectedDirection = {
        direction_id: "image_edit",
        title: "图片编辑",
        description: request.prompt,
        recommended: true,
        tags: ["图片编辑"],
        data: { operation: "image_edit" },
      };
      const started = await api.startImageGenerationJob(jobRequest);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: "",
        kind: "direct_image_edit",
        job_api: "generate",
        started_at: new Date().toISOString(),
        request: jobRequest,
        imagePrepare,
        artifact: {
          type: "image_result",
          title: "图片编辑结果",
          description: "图片编辑生成中。",
          actionLabel: "查看",
          imagePrepare,
          imageEditRequest: executableRequest as unknown as Record<string, unknown>,
          imageEditConfirmedSelection: request.selection,
          intent: "image",
          formValues,
          intakeContext,
          materials: flowMaterials,
          selectedDirection,
        },
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, "image_edit_generation_running", {
        intent: "image",
        image_edit_request: executableRequest,
        intake_context: intakeContext,
        materials: flowMaterials,
        image_prepare: imagePrepare,
        pendingImageEditRequest: pendingImageEditRequestRef.current,
        pending_image_edit_request: pendingImageEditRequestRef.current,
      });
      await resumePendingImageJob(pendingImageJob);
    } catch (err) {
      pendingImageEditRequestRef.current = null;
      pushAssistant(`图片编辑失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleConfirmImageEditOptions = async (msg: ChatMessage, selection: ImageEditModelSelection) => {
    const artifact = msg.artifact;
    if (artifact?.type !== "image_edit_options" || !artifact.imageEditRequest) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const storedRequest = artifact.imageEditRequest as Partial<PendingImageEditRequest>;
    const request: PendingImageEditRequest = {
      conversationId: targetConversationId,
      prompt: String(storedRequest.prompt || artifact.coreMessage || msg.content || ""),
      formValues: (storedRequest.formValues || artifact.formValues || {}) as Record<string, unknown>,
      intakeContext: (storedRequest.intakeContext || artifact.intakeContext || {}) as Record<string, unknown>,
      materials: (storedRequest.materials || artifact.materials || []) as Array<Record<string, unknown>>,
      selection,
      mode: storedRequest.mode,
      sceneGlobalAssetReference: storedRequest.sceneGlobalAssetReference as SceneGlobalAssetReference | undefined,
      storyboardMessageId: typeof storedRequest.storyboardMessageId === "string" ? storedRequest.storyboardMessageId : undefined,
    };
    recordImageEditConfirmedSelection(msg.id, targetConversationId, selection);
    pendingImageEditRequestRef.current = null;
    if (request.mode === "scene_global_asset_edit" || request.mode === "scene_global_asset_fusion") {
      await executeSceneGlobalAssetEdit(request);
      return;
    }
    await executeDirectImageEdit(request);
  };

  const pushDirectionsArtifact = (
    directions: CreativeDirectionResponse[],
    context: {
      intent: CreationIntent;
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
    },
    targetConversationId = conversationIdRef.current,
  ) => {
    return pushArtifact("已根据表单生成 3 个创意方向，请选择一个进入 plan.md 策划。", {
      type: "directions",
      title: "创意方向",
      description: `${directions.length} 个方向，第一项为推荐方向`,
      actionLabel: "查看",
      directions,
      intent: context.intent,
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
    }, targetConversationId);
  };

  const createPlanArtifactMessage = (
    plan: PlanMarkdownResponse,
    selectedDirection: CreativeDirectionResponse,
    context: {
      intent: CreationIntent;
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
    },
    targetConversationId = conversationIdRef.current,
    planJobId?: string,
  ) => ({
    id: uid(),
    conversationId: targetConversationId || undefined,
    role: "assistant" as const,
    content: "plan.md 创作方案已生成，请审核后点击「同意方案」继续。",
    time: "",
    artifact: {
      type: "plan" as const,
      title: "plan.md 创作方案",
      description: `基于「${selectedDirection.title}」生成，当前版本 v${plan.plan_version || 1}`,
      actionLabel: "审核",
      plan,
      planVersion: plan.plan_version || 1,
      planHistory: plan.plan_history || [],
      planJobId,
      creationContract: plan.creation_contract || {},
      restoredFromVersion: plan.restored_from_version,
      selectedDirection,
      intent: context.intent,
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
    },
  });

  const pushPptOutlineArtifact = (
    pptSummary: PptSummaryResult,
    context: {
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
      pptStyle: string;
    },
    targetConversationId = conversationIdRef.current,
  ) => {
    const projectId = numericValue(pptSummary.smart_ppt_project_id);
    return pushArtifact(pptSummary.ok ? "PPT 大纲已生成，请确认是否需要修改。" : "PPT 大纲生成失败，请查看错误信息。", {
      type: "ppt_outline",
      title: "PPT大纲",
      description: String(pptSummary.message || (pptSummary.ok ? "确认后将生成 PPT 页面结构和页面图片。" : "可充值或调整附件后继续。")),
      actionLabel: "审核",
      intent: "ppt",
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
      pptSummary,
      pptStyle: context.pptStyle,
      smartPptProjectId: projectId,
    }, targetConversationId);
  };

  const pushPptImagesArtifact = (
    pptImages: PptImagesResult,
    pptContentJson: PptContentJsonResult,
    sourceArtifact: ChatArtifact,
    targetConversationId = conversationIdRef.current,
  ) =>
    pushArtifact(buildPptImagesMessageContent(pptImages), buildPptImagesArtifact(pptImages, sourceArtifact, pptContentJson), targetConversationId);

  const pushPptFileArtifact = (
    pptFile: PptFileResult,
    sourceArtifact: ChatArtifact,
    targetConversationId = conversationIdRef.current,
  ) =>
    pushArtifact(pptFile.ok ? "PPT 附件已生成，请下载确认。" : "PPT 附件生成失败，请查看原因。", {
      type: "ppt_file",
      title: "PPT附件",
      description: String(pptFile.message || (pptFile.ok ? "文件生成完成。" : "文件生成失败。")),
      actionLabel: "下载",
      intent: "ppt",
      formValues: sourceArtifact.formValues,
      intakeContext: sourceArtifact.intakeContext,
      materials: sourceArtifact.materials || [],
      coreMessage: sourceArtifact.coreMessage,
      pptSummary: sourceArtifact.pptSummary,
      pptContentJson: sourceArtifact.pptContentJson,
      pptImages: sourceArtifact.pptImages,
      pptFile,
      pptStyle: sourceArtifact.pptStyle,
      smartPptProjectId: pptProjectId(sourceArtifact) ?? numericValue(pptFile.smart_ppt_project_id),
    }, targetConversationId);

  const persistPendingSupervisorTurns = async (
    updater: (
      current: PendingSupervisorTurn[],
    ) => PendingSupervisorTurn[],
    targetConversationId: string,
  ) => {
    await pendingSupervisorTurnWritesRef.current.run(
      targetConversationId,
      async () => {
        const current = (
          pendingSupervisorTurnsByConversationRef.current.get(
            targetConversationId,
          )
          || pendingSupervisorTurnsRef.current.filter(
            (item) => item.conversationId === targetConversationId,
          )
        );
        const normalized = updater([...current]).filter(
          (item) => item.conversationId === targetConversationId,
        );
        const completedLegacyTurn = current.find(
          (item) => item.conversationId === targetConversationId
            && item.continueLegacy
            && item.registrationStatus === "registered"
            && !normalized.some((next) => next.clientInputId === item.clientInputId),
        );
        await updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(targetConversationId),
            pendingAgentRuntimeTurns: normalized,
            pending_agent_runtime_turns: normalized,
            ...(completedLegacyTurn
              ? {
                  legacy_handoff: {
                    source: "frontend_v2",
                    client_input_id: completedLegacyTurn.clientInputId,
                  },
                }
              : {}),
          } as unknown as Record<string, unknown>,
        });
        // 只有服务端恢复上下文落库后才向注册 effect 暴露 Turn；
        // PUT 失败时保持原 ref/state，避免出现已注册但刷新不可恢复的半状态。
        pendingSupervisorTurnsByConversationRef.current.set(
          targetConversationId,
          normalized,
        );
        if (isCurrentConversation(targetConversationId)) {
          pendingSupervisorTurnsRef.current = normalized;
          setPendingSupervisorTurns(normalized);
        }
      },
    );
  };

  const pendingSupervisorTurnsForConversation = (
    targetConversationId: string,
  ): PendingSupervisorTurn[] => (
    pendingSupervisorTurnsByConversationRef.current.get(
      targetConversationId,
    )
    || pendingSupervisorTurnsRef.current.filter(
      (item) => item.conversationId === targetConversationId,
    )
  );

  const persistPendingMessageJob = async (
    pendingMessageJob: PendingMessageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    if (!targetConversationId) {
      pendingMessageJobRef.current = pendingMessageJob;
      return;
    }
    await pendingSupervisorTurnWritesRef.current.run(
      targetConversationId,
      async () => {
        const currentRuntimeTurns = pendingSupervisorTurnsForConversation(
          targetConversationId,
        );
        const runtimeTurns = (
          pendingMessageJob?.continue_after_save?.type === "handle_send"
            ? currentRuntimeTurns.filter(
                (item) => (
                  item.clientInputId
                  !== pendingMessageJob.source_message_id
                ),
              )
            : currentRuntimeTurns
        );
        await updateConversationWithProgress(targetConversationId, {
          last_phase: lastPhase,
          context: {
            ...makeSnapshot(targetConversationId),
            ...extraContext,
            pendingMessageJob,
            pending_message_job: pendingMessageJob,
            pendingAgentRuntimeTurns: runtimeTurns,
            pending_agent_runtime_turns: runtimeTurns,
          } as unknown as Record<string, unknown>,
        });
        pendingMessageJobRef.current = pendingMessageJob;
        pendingSupervisorTurnsByConversationRef.current.set(
          targetConversationId,
          runtimeTurns,
        );
        if (isCurrentConversation(targetConversationId)) {
          pendingSupervisorTurnsRef.current = runtimeTurns;
          setPendingSupervisorTurns(runtimeTurns);
        }
      },
    );
  };

  const clearPendingMessageJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingMessageJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingIntakeJob = async (
    pendingIntakeJob: PendingIntakeJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingIntakeJobRef.current = pendingIntakeJob;
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        pendingIntakeJob,
        pending_intake_job: pendingIntakeJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingIntakeJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingIntakeJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingImageJob = async (
    pendingImageJob: PendingImageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingImageJobRef.current = pendingImageJob;
    if (!targetConversationId) return;
    const baseSnapshot = makeSnapshot(targetConversationId);
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...baseSnapshot,
        ...extraContext,
        pendingImageJob,
        pending_image_job: pendingImageJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingImageJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingImageJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingVideoJob = async (
    pendingVideoJob: PendingVideoJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingVideoJobRef.current = pendingVideoJob;
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        pendingVideoJob,
        pending_video_job: pendingVideoJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingVideoJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingVideoJob(null, targetConversationId, lastPhase, extraContext);
  };

  const jianyingDraftRecordsForConversation = (targetConversationId: string): JianyingDraftRecordMap =>
    jianyingDraftRecordsByConversationRef.current.get(targetConversationId) || {};

  const setJianyingDraftRecordsForConversation = (targetConversationId: string, records: JianyingDraftRecordMap) => {
    if (!targetConversationId) return;
    jianyingDraftRecordsByConversationRef.current.set(targetConversationId, records);
    setJianyingDraftUiRevision((revision) => revision + 1);
  };

  const patchJianyingDraftConversationContextForTarget = async (
    targetConversationId: string,
    lastPhase: string,
    expectedJobId: string,
    pendingJianyingDraftJob: PendingJianyingDraftJob | null,
    jianyingDraftRecordUpdates: JianyingDraftRecordMap,
    jianyingDraftJobResumeError?: string | null,
  ) => {
    let resolvedPendingJob = pendingJianyingDraftJob;
    let resolvedRecords = {
      ...jianyingDraftRecordsForConversation(targetConversationId),
      ...jianyingDraftRecordUpdates,
    };
    await patchJianyingDraftTargetConversation({
      targetConversationId,
      expectedJobId,
      isCurrentConversation: (conversationId) => conversationIdRef.current === conversationId,
      syncCurrentConversation: () => {
        pendingJianyingDraftJobRef.current = resolvedPendingJob;
        setJianyingDraftRecordsForConversation(targetConversationId, resolvedRecords);
      },
      patchTargetConversation: async (conversationId, expectedJobId) => {
        const updated = await api.patchJianyingDraftConversationContext(conversationId, {
          last_phase: lastPhase,
          expected_job_id: expectedJobId,
          pendingJianyingDraftJob,
          jianyingDraftRecords: jianyingDraftRecordUpdates,
          ...(jianyingDraftJobResumeError === undefined
            ? {}
            : { jianying_draft_job_resume_error: jianyingDraftJobResumeError }),
        });
        const context = updated.context as Partial<WorkspaceSnapshot>;
        resolvedPendingJob = context.pendingJianyingDraftJob ?? context.pending_jianying_draft_job ?? null;
        resolvedRecords = context.jianyingDraftRecords || context.jianying_draft_records || {};
        setJianyingDraftRecordsForConversation(conversationId, resolvedRecords);
        return updated;
      },
    });
  };

  const persistPendingJianyingDraftJob = async (
    pendingJianyingDraftJob: PendingJianyingDraftJob | null,
    targetConversationId: string,
    lastPhase: string,
    expectedJobId: string,
    jianyingDraftRecordUpdates: JianyingDraftRecordMap = {},
    jianyingDraftJobResumeError?: string | null,
  ) => {
    if (pendingJianyingDraftJob && pendingJianyingDraftJob.conversation_id !== targetConversationId) return;
    if (!targetConversationId) return;
    if (conversationIdRef.current === targetConversationId) {
      pendingJianyingDraftJobRef.current = pendingJianyingDraftJob;
      setJianyingDraftUiRevision((revision) => revision + 1);
    }
    await patchJianyingDraftConversationContextForTarget(
      targetConversationId,
      lastPhase,
      expectedJobId,
      pendingJianyingDraftJob,
      jianyingDraftRecordUpdates,
      pendingJianyingDraftJob ? null : jianyingDraftJobResumeError,
    );
  };

  const persistPendingScenePackageJob = async (
    pendingScenePackageJob: PendingScenePackageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    // 启动时立即暴露恢复句柄；结束时必须等权威 context 成功落库后再清空，
    // 防止消息/画布自动保存拿着旧 running 阶段覆盖已经完成的任务。
    if (pendingScenePackageJob) pendingScenePackageJobRef.current = pendingScenePackageJob;
    if (!targetConversationId) {
      if (!pendingScenePackageJob) pendingScenePackageJobRef.current = null;
      return;
    }
    const baseSnapshot = makeSnapshot(targetConversationId);
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...baseSnapshot,
        ...extraContext,
        pendingScenePackageJob,
        pending_scene_package_job: pendingScenePackageJob,
      } as unknown as Record<string, unknown>,
    });
    if (
      !pendingScenePackageJob
      && pendingScenePackageJobRef.current?.conversation_id === targetConversationId
    ) {
      pendingScenePackageJobRef.current = null;
    }
  };

  const clearPendingScenePackageJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingScenePackageJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingPptJob = async (
    pendingPptJob: PendingPptJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingPptJobRef.current = pendingPptJob;
    if (!targetConversationId) return;
    const baseSnapshot = makeSnapshot(targetConversationId);
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...baseSnapshot,
        ...extraContext,
        pendingPptJob,
        pending_ppt_job: pendingPptJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingPptJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingPptJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingDirectionJob = async (
    pendingDirectionJob: PendingDirectionJob | null,
    targetConversationId: string,
    lastPhase: string,
    flowDraft: FlowDraft | null = flowDraftRef.current,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingDirectionJobRef.current = pendingDirectionJob;
    flowDraftRef.current = flowDraft;
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        flowDraft,
        pendingDirectionJob,
        pending_direction_job: pendingDirectionJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingDirectionJob = async (
    targetConversationId: string,
    lastPhase: string,
    flowDraft: FlowDraft | null = flowDraftRef.current,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingDirectionJob(null, targetConversationId, lastPhase, flowDraft, extraContext);
  };

  const planJobPersistenceKey = (pendingPlanJob: PendingPlanJob) =>
    `${pendingPlanJob.conversation_id}:${pendingPlanJob.kind}:${pendingPlanJob.job_id}`;

  const clearPlanJobPersistenceState = (pendingPlanJob: PendingPlanJob) => {
    const key = planJobPersistenceKey(pendingPlanJob);
    planJobPersistenceAttemptsRef.current.delete(key);
    planJobPersistenceNoticesRef.current.delete(key);
    scheduledPlanJobPersistenceRef.current.delete(key);
  };

  const notifyPlanJobPersistenceRecovery = (pendingPlanJob: PendingPlanJob) => {
    const key = planJobPersistenceKey(pendingPlanJob);
    if (planJobPersistenceNoticesRef.current.has(key)) return;
    planJobPersistenceNoticesRef.current.add(key);
    pushAssistant(
      "Plan 任务已经启动，但恢复句柄暂未同步到对话；正在继续查询原任务并重试同步，不会重复生成。",
      pendingPlanJob.conversation_id,
    );
  };

  const persistPendingPlanJob = async (
    pendingPlanJob: PendingPlanJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    const previousPendingPlanJob = pendingPlanJobRef.current;
    pendingPlanJobRef.current = pendingPlanJob;
    if (
      !pendingPlanJob
      && previousPendingPlanJob?.conversation_id === targetConversationId
    ) {
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        targetConversationId,
        previousPendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(previousPendingPlanJob);
    }
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        pendingPlanJob,
        pending_plan_job: pendingPlanJob,
      } as unknown as Record<string, unknown>,
    });
    if (pendingPlanJob) {
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        targetConversationId,
        pendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(pendingPlanJob);
    }
  };

  const schedulePendingPlanJobPersistence = (
    pendingPlanJob: PendingPlanJob,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ): void => {
    if (!shouldRetryPlanJobPersistence({
      hidden: !pageVisibleRef.current,
      startedAt: pendingPlanJob.started_at,
    })) {
      return;
    }
    const key = planJobPersistenceKey(pendingPlanJob);
    if (scheduledPlanJobPersistenceRef.current.has(key)) return;
    const attempt = planJobPersistenceAttemptsRef.current.get(key) || 0;
    planJobPersistenceAttemptsRef.current.set(key, attempt + 1);
    scheduledPlanJobPersistenceRef.current.add(key);
    window.setTimeout(() => {
      scheduledPlanJobPersistenceRef.current.delete(key);
      if (!shouldRetryPlanJobPersistence({
        hidden: !pageVisibleRef.current,
        startedAt: pendingPlanJob.started_at,
      })) {
        return;
      }
      const current = pendingPlanJobRef.current;
      if (
        current?.job_id !== pendingPlanJob.job_id
        || current.conversation_id !== pendingPlanJob.conversation_id
      ) {
        return;
      }
      void persistPendingPlanJob(
        pendingPlanJob,
        pendingPlanJob.conversation_id,
        lastPhase,
        extraContext,
      ).catch(() => {
        schedulePendingPlanJobPersistence(pendingPlanJob, lastPhase, extraContext);
      });
    }, planJobResumeDelayMs(attempt));
  };

  const clearPendingPlanJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingPlanJob(null, targetConversationId, lastPhase, extraContext);
  };

  const handleCompletedDirectionJob = async (
    pendingDirectionJob: PendingDirectionJob,
    directionResult: CreativeDirectionsResponse,
  ) => {
    const targetConversationId = pendingDirectionJob.conversation_id;
    const context = pendingDirectionJob.context;
    if (!directionResult.validation.is_complete) {
      pushAssistant(directionResult.validation.message || "表单信息还不完整，请补充后再提交。", targetConversationId);
      const draft = makeFlowDraft("form_pending", {
        intent: context.intent,
        coreMessage: context.coreMessage,
        materials: context.materials || [],
        intakeContext: context.intakeContext,
        formValues: context.formValues,
        form: context.form,
      });
      await clearPendingDirectionJob(targetConversationId, "intake_form_pending", draft, {
        intent: context.intent,
        form_values: context.formValues,
        intake_context: context.intakeContext,
        materials: context.materials || [],
      }).catch(() => {});
      return;
    }
    if (hasPostDirectionArtifactForContext(messagesRef.current, targetConversationId, context)) {
      await clearPendingDirectionJob(targetConversationId, "plan_review", null, {
        intent: context.intent,
        form_values: context.formValues,
        intake_context: context.intakeContext,
        materials: context.materials || [],
      }).catch(() => {});
      return;
    }
    const intakeContext = directionResult.intake_context || context.intakeContext || {};
    const draft = makeFlowDraft("directions_ready", {
      intent: context.intent,
      coreMessage: context.coreMessage,
      materials: context.materials || [],
      intakeContext,
      formValues: context.formValues,
      form: context.form,
      creativeDirections: directionResult.creative_directions,
    });
    if (context.revisionFeedback || !hasDirectionsArtifactForDraft(messagesRef.current, targetConversationId, draft)) {
      pushDirectionsArtifact(directionResult.creative_directions, {
        intent: context.intent,
        formValues: context.formValues,
        materials: context.materials || [],
        coreMessage: context.coreMessage,
        intakeContext,
      }, targetConversationId);
    }
    await clearPendingDirectionJob(targetConversationId, context.revisionFeedback ? "creative_directions_revised" : `${context.intent}_directions`, draft, {
      intent: context.intent,
      [`${context.intent}_form`]: context.form,
      creative_directions: directionResult.creative_directions,
      form_values: context.formValues,
      intake_context: intakeContext,
      materials: context.materials || [],
      revision_feedback: context.revisionFeedback,
    }).catch(() => {});
  };

  const resumePendingDirectionJob = async (pendingDirectionJob: PendingDirectionJob) => {
    const pollKey = `${pendingDirectionJob.conversation_id}:${pendingDirectionJob.job_id}`;
    if (activeDirectionJobPollsRef.current.has(pollKey)) return;
    activeDirectionJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingDirectionJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingDirectionJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      const status = await api.getCreativeDirectionsJob(pendingDirectionJob.job_id);
      if (stopIfHidden()) return;
      const result =
        status.status === "completed" && status.result
          ? status.result
          : await api.pollCreativeDirectionsJob(pendingDirectionJob.job_id, shouldContinuePolling);
      if (!result || stopIfHidden()) return;
      await handleCompletedDirectionJob(pendingDirectionJob, result);
    } catch (err) {
      if (stopIfHidden()) return;
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的创意方向生成任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从当前表单手动继续生成方向。"
          : `继续查询创意方向生成任务失败:${message}`,
        pendingDirectionJob.conversation_id,
      );
      const draft = flowDraftRef.current || makeFlowDraft("form_pending", {
        intent: pendingDirectionJob.context.intent,
        coreMessage: pendingDirectionJob.context.coreMessage,
        materials: pendingDirectionJob.context.materials || [],
        intakeContext: pendingDirectionJob.context.intakeContext,
        formValues: pendingDirectionJob.context.formValues,
        form: pendingDirectionJob.context.form,
      });
      await clearPendingDirectionJob(pendingDirectionJob.conversation_id, "direction_job_resume_failed", draft, {
        direction_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activeDirectionJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingDirectionJob.conversation_id, false);
      void pausedForHiddenConversation;
    }
  };

  const startDirectionJob = async (
    targetConversationId: string,
    request: CreativeDirectionsJobRequest,
    context: PendingDirectionJobContext,
    lastPhase = "directions_running",
    sourceMessageId = "",
  ) => {
    advanceWorkflowProgress(targetConversationId, lastPhase, {
      intent: context.intent,
      flow_kind: "standard",
    });
    const flowDraft = makeFlowDraft("directions_running", {
      intent: context.intent,
      coreMessage: context.coreMessage,
      materials: context.materials || [],
      intakeContext: context.intakeContext,
      formValues: context.formValues,
      form: context.form,
    });
    const started = await api.startCreativeDirectionsJob(request);
    const pendingDirectionJob: PendingDirectionJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: sourceMessageId,
      kind: "creative_directions",
      started_at: new Date().toISOString(),
      request,
      context,
    };
    await persistPendingDirectionJob(pendingDirectionJob, targetConversationId, lastPhase, flowDraft, {
      intent: context.intent,
      form_values: context.formValues,
      intake_context: context.intakeContext,
      materials: context.materials || [],
      revision_feedback: context.revisionFeedback,
    });
    await resumePendingDirectionJob(pendingDirectionJob);
  };

  const handleCompletedIntakeJob = async (pendingIntakeJob: PendingIntakeJob, intake: IntakeIntentResponse) => {
    const targetConversationId = pendingIntakeJob.conversation_id;
    const text = pendingIntakeJob.request.prompt;
    const materials = pendingIntakeJob.request.materials || [];
    if (intake.intent === "video_analysis") {
      advanceWorkflowProgress(targetConversationId, "video_analysis_running", { intent: null });
      pushAssistant("已识别为视频分析/拆解需求，正在识别媒体链接并调用视频分析 Skill…", targetConversationId);
      const videoAnalysis = await api.analyzeStoryboards({ prompt: text, materials });
      pushArtifact(videoAnalysis.ok ? "视频分析已完成，结果如下。" : "视频分析未完成，请查看原因后补充视频链接。", {
        type: "video_analysis_result",
        title: videoAnalysis.mode === "batch" ? "批量视频分析" : "视频分析",
        description: videoAnalysis.ok
          ? `${videoAnalysis.video_urls.length} 个视频，调用 ${videoAnalysis.endpoint}`
          : videoAnalysis.message,
        actionLabel: "查看",
        intent: "video_analysis",
        coreMessage: text,
        materials,
        videoAnalysis,
      }, targetConversationId);
      await clearPendingIntakeJob(targetConversationId, videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed", {
        intent: "video_analysis",
        materials,
        intake_intent: intake,
        video_analysis: videoAnalysis,
      }).catch(() => {});
      return;
    }
    if (intake.intent === "ppt") {
      advanceWorkflowProgress(targetConversationId, "ppt_form_pending", { intent: "ppt", flow_kind: "standard" });
      const flowDraft = makeFlowDraft("form_pending", {
        intent: "ppt",
        coreMessage: text,
        materials,
        intakeIntent: intake,
        intakeContext: intake.intake_context,
        formValues: initialValuesFromIntake(intake),
      });
      flowDraftRef.current = flowDraft;
      if (isVisibleConversation(targetConversationId)) {
        setPendingCore(text);
        setPendingIntent("ppt");
        setPendingFormValues(initialValuesFromIntake(intake));
        setPendingMaterials(materials);
        pendingDialogContextRef.current = {
          conversationId: targetConversationId,
          coreMessage: text,
          materials,
          intakeContext: intake.intake_context,
        };
      }
      pushAssistant("采集 Agent 判断这是PPT制作需求，已把能识别的信息自动填进表单。请补充确认并上传 Word、Excel 或 PDF 附件。", targetConversationId);
      await clearPendingIntakeJob(targetConversationId, "ppt_form_pending", {
        flowDraft,
        intent: "ppt",
        materials,
        intake_intent: intake,
        intake_context: intake.intake_context,
      }).catch(() => {});
      if (isVisibleConversation(targetConversationId)) setDialogOpen(true);
      return;
    }
    if (intake.intent === "image" && isImageEditIntake(intake, text)) {
      advanceWorkflowProgress(targetConversationId, "image_edit_options_pending", {
        intent: "image",
        flow_kind: "direct_image_edit",
      });
      const imageEditRequest: PendingImageEditRequest = {
        conversationId: targetConversationId,
        prompt: text,
        formValues: intake.values || {},
        intakeContext: intake.intake_context || {},
        materials,
      };
      if (!hasImageMaterial(materials)) {
        pendingImageEditRequestRef.current = imageEditRequest;
        pushAssistant("我识别到这是图片编辑需求，请上传需要编辑的图片后提交，我会先让你确认图片编辑模型和参数。", targetConversationId);
        await clearPendingIntakeJob(targetConversationId, "image_edit_waiting_source_image", {
          intent: "image",
          materials,
          intake_intent: intake,
          intake_context: intake.intake_context,
          pendingImageEditRequest: imageEditRequest,
          pending_image_edit_request: imageEditRequest,
        }).catch(() => {});
        return;
      }
      await clearPendingIntakeJob(targetConversationId, "image_edit_options_pending", {
        intent: "image",
        materials,
        intake_intent: intake,
        intake_context: intake.intake_context,
      }).catch(() => {});
      await showImageEditOptions(imageEditRequest);
      return;
    }
    if (isCreationIntent(intake.intent)) {
      advanceWorkflowProgress(targetConversationId, "intake_form_pending", {
        intent: intake.intent,
        flow_kind: "standard",
      });
      const flowDraft = makeFlowDraft("form_pending", {
        intent: intake.intent,
        coreMessage: text,
        materials,
        intakeIntent: intake,
        intakeContext: intake.intake_context,
        formValues: initialValuesFromIntake(intake),
      });
      flowDraftRef.current = flowDraft;
      if (isVisibleConversation(targetConversationId)) {
        setPendingCore(text);
        setPendingIntent(intake.intent);
        setPendingFormValues(initialValuesFromIntake(intake));
        setPendingMaterials(materials);
        pendingDialogContextRef.current = {
          conversationId: targetConversationId,
          coreMessage: text,
          materials,
          intakeContext: intake.intake_context,
        };
      }
      pushAssistant(`采集 Agent 判断这是${intake.intent === "video" ? "视频生成" : "图片生成"}需求，已把能识别的信息自动填进表单。请补充确认。`, targetConversationId);
      await clearPendingIntakeJob(targetConversationId, "intake_form_pending", {
        flowDraft,
        intent: intake.intent,
        materials,
        intake_intent: intake,
        intake_context: intake.intake_context,
      }).catch(() => {});
      if (isVisibleConversation(targetConversationId)) setDialogOpen(true);
      return;
    }
    pushAssistant(intake.reason || "我可以帮你生成图片、生成电商带货短视频，或分析已有视频。请再描述一下需求。", targetConversationId);
    advanceWorkflowProgress(targetConversationId, "intake_unknown", { intent: null });
    await clearPendingIntakeJob(targetConversationId, "intake_unknown", {
      intake_intent: intake,
    }).catch(() => {});
  };

  const resumePendingIntakeJob = async (pendingIntakeJob: PendingIntakeJob) => {
    const pollKey = `${pendingIntakeJob.conversation_id}:${pendingIntakeJob.job_id}`;
    if (activeIntakeJobPollsRef.current.has(pollKey)) return;
    activeIntakeJobPollsRef.current.add(pollKey);
    const shouldContinuePolling = () => isVisibleConversation(pendingIntakeJob.conversation_id);
    const stopIfHidden = () => !shouldContinuePolling();
    setBusyForConversation(pendingIntakeJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      const status = await api.getIntakeAnalyzeJob(pendingIntakeJob.job_id);
      if (stopIfHidden()) return;
      const result =
        status.status === "completed" && status.result
          ? status.result
          : await api.pollIntakeAnalyzeJob(pendingIntakeJob.job_id, shouldContinuePolling);
      if (!result || stopIfHidden()) return;
      await handleCompletedIntakeJob(pendingIntakeJob, result);
    } catch (err) {
      if (stopIfHidden()) return;
      if (isTransientFetchAbort(err)) return;
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的采集意图识别任务不存在或已过期。为避免重复推进流程，我没有自动重启任务，请重新发送需求。"
          : `继续查询采集意图识别任务失败:${message}`,
        pendingIntakeJob.conversation_id,
      );
      await clearPendingIntakeJob(pendingIntakeJob.conversation_id, "intake_job_resume_failed", {
        intake_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activeIntakeJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingIntakeJob.conversation_id, false);
    }
  };

  const startIntakeAnalyzeJob = async (
    targetConversationId: string,
    request: IntakeAnalyzeJobRequest,
    sourceMessageId = "",
    autoResume = true,
  ): Promise<PendingIntakeJob> => {
    advanceWorkflowProgress(targetConversationId, "intake_analyze_running", {
      intent: null,
      flow_kind: "standard",
      source_message_id: sourceMessageId,
      scene_package_stage: null,
    });
    const started = await api.startIntakeAnalyzeJob(request);
    const pendingIntakeJob: PendingIntakeJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: sourceMessageId,
      kind: "intake_analyze",
      started_at: new Date().toISOString(),
      request,
    };
    await persistPendingIntakeJob(pendingIntakeJob, targetConversationId, "intake_analyze_running", {
      materials: request.materials || [],
      intake_prompt: request.prompt,
    });
    if (autoResume) await resumePendingIntakeJob(pendingIntakeJob);
    return pendingIntakeJob;
  };

  const resumePendingMessageJob = async (pendingMessageJob: PendingMessageJob) => {
    const pollKey = `${pendingMessageJob.conversation_id}:${pendingMessageJob.job_id}`;
    if (activeMessageJobPollsRef.current.has(pollKey)) return;
    activeMessageJobPollsRef.current.add(pollKey);
    const shouldContinuePolling = () => isVisibleConversation(pendingMessageJob.conversation_id);
    const stopIfHidden = () => !shouldContinuePolling();
    const planContinuation =
      pendingMessageJob.continue_after_save?.type === "plan_save"
        ? pendingMessageJob.continue_after_save
        : null;
    setBusyForConversation(pendingMessageJob.conversation_id, true);
    if (planContinuation) {
      const failPendingPlanMessage = async (error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        pendingPlanMessagePersistenceIdsRef.current.delete(pendingMessageJob.source_message_id);
        removeOptimisticMessage(pendingMessageJob.source_message_id, pendingMessageJob.conversation_id);
        if (planContinuation.processed_key) releaseArtifactAction(planContinuation.processed_key);
        await clearPendingMessageJob(pendingMessageJob.conversation_id, "plan_message_save_failed", {
          message_job_resume_error: message,
        }).catch(() => {});
        pushAssistant(`plan.md 消息保存失败:${message}`, pendingMessageJob.conversation_id);
      };
      try {
        await persistPendingMessageJob(
          pendingMessageJob,
          pendingMessageJob.conversation_id,
          "plan_message_save_running",
        ).catch(() => {});
        const step = await resumePlanMessageJobStep(pendingMessageJob, {
          shouldContinue: shouldContinuePolling,
          getStatus: () =>
            api.getConversationMessageJob(pendingMessageJob.conversation_id, pendingMessageJob.job_id),
          pollStatus: async (onStatus) => {
            let latestStatus: ConversationMessageJobStatusResponse | null = null;
            const saved = await api.pollConversationMessageJob(
              pendingMessageJob.conversation_id,
              pendingMessageJob.job_id,
              shouldContinuePolling,
              (status) => {
                latestStatus = status;
                onStatus(status);
              },
            );
            if (saved) {
              return {
                ok: true,
                job_id: pendingMessageJob.job_id,
                status: "completed",
                result: saved,
                error: null,
                message: "Plan 消息已保存",
              };
            }
            return latestStatus;
          },
          restart: (request: ConversationMessageJobRequest) =>
            api.startConversationMessageJob(pendingMessageJob.conversation_id, request),
        });
        if (step.kind === "pending") {
          if (step.pending.job_id !== pendingMessageJob.job_id) {
            const restartedPendingMessageJob: PendingMessageJob = {
              ...step.pending,
              restart_count: (pendingMessageJob.restart_count || 0) + 1,
            };
            try {
              await persistPendingMessageJob(
                restartedPendingMessageJob,
                restartedPendingMessageJob.conversation_id,
                "plan_message_save_running",
              );
            } catch {
              pendingMessageJobRef.current = restartedPendingMessageJob;
            }
            if (shouldContinuePolling()) schedulePendingMessageJobResume(restartedPendingMessageJob);
            return;
          }
          if (!shouldContinuePolling()) return;
          const retryPendingMessageJob: PendingMessageJob = {
            ...step.pending,
            restart_count: (pendingMessageJob.restart_count || 0) + 1,
          };
          try {
            await persistPendingMessageJob(
              retryPendingMessageJob,
              retryPendingMessageJob.conversation_id,
              "plan_message_save_running",
            );
          } catch {
            pendingMessageJobRef.current = retryPendingMessageJob;
          }
          schedulePendingMessageJobResume(retryPendingMessageJob);
          return;
        }
        if (step.kind === "failed") {
          await failPendingPlanMessage(step.error);
          return;
        }

        const savedMessage = messageFromResponse(step.result, pendingMessageJob.conversation_id);
        if (!savedMessage) {
          await failPendingPlanMessage(new Error("服务端 Plan 消息无法恢复"));
          return;
        }
        let authoritativeContext: Record<string, unknown>;
        try {
          authoritativeContext = planContextFromSavedMessage(savedMessage, planContinuation.context);
        } catch (protocolError) {
          await failPendingPlanMessage(protocolError);
          return;
        }
        pendingPlanMessagePersistenceIdsRef.current.delete(pendingMessageJob.source_message_id);
        replaceOptimisticMessage(
          pendingMessageJob.source_message_id,
          savedMessage,
          pendingMessageJob.conversation_id,
          true,
        );
        try {
          await updateConversationWithProgress(pendingMessageJob.conversation_id, {
            last_phase: planContinuation.last_phase,
            context: {
              ...makeSnapshot(pendingMessageJob.conversation_id),
              ...authoritativeContext,
              pendingMessageJob: null,
              pending_message_job: null,
            } as unknown as Record<string, unknown>,
          });
        } catch (contextError) {
          const contextSyncPendingMessageJob: PendingMessageJob = {
            ...pendingMessageJob,
            restart_count: (pendingMessageJob.restart_count || 0) + 1,
          };
          try {
            await persistPendingMessageJob(
              contextSyncPendingMessageJob,
              contextSyncPendingMessageJob.conversation_id,
              "plan_context_sync_pending",
              {
                message_job_resume_error:
                  contextError instanceof Error ? contextError.message : String(contextError),
              },
            );
          } catch {
            pendingMessageJobRef.current = contextSyncPendingMessageJob;
          }
          if (shouldContinuePolling()) schedulePendingMessageJobResume(contextSyncPendingMessageJob);
          return;
        }
        pendingMessageJobRef.current = null;
        if (authoritativeContext.flowDraft === null) flowDraftRef.current = null;
        if (authoritativeContext.pendingDirectionJob === null) pendingDirectionJobRef.current = null;
        if (authoritativeContext.pendingPlanJob === null) pendingPlanJobRef.current = null;
        if (authoritativeContext.pendingPlanRevisionChoice === null) setPendingPlanRevisionChoice(null);
        if (planContinuation.success_message) {
          pushAssistant(planContinuation.success_message, pendingMessageJob.conversation_id);
        }
      } finally {
        activeMessageJobPollsRef.current.delete(pollKey);
        setBusyForConversation(pendingMessageJob.conversation_id, false);
      }
      return;
    }

    try {
      if (stopIfHidden()) return;
      const status = await api.getConversationMessageJob(pendingMessageJob.conversation_id, pendingMessageJob.job_id);
      if (stopIfHidden()) return;
      if (status.status === "failed") {
        throw new Error(status.error || status.message || "对话消息保存失败");
      }
      const saved =
        status.status === "completed" && status.result
          ? status.result
          : await api.pollConversationMessageJob(pendingMessageJob.conversation_id, pendingMessageJob.job_id, shouldContinuePolling);
      if (!saved || stopIfHidden()) return;
      const savedMessage = messageFromResponse(saved, pendingMessageJob.conversation_id);
      if (savedMessage) replaceOptimisticMessage(pendingMessageJob.source_message_id, savedMessage, pendingMessageJob.conversation_id);
      let nextIntakeJob =
        pendingIntakeJobRef.current?.conversation_id === pendingMessageJob.conversation_id &&
        pendingIntakeJobRef.current.source_message_id === pendingMessageJob.source_message_id
          ? pendingIntakeJobRef.current
          : null;
      if (pendingMessageJob.continue_after_save?.type === "handle_send" && !nextIntakeJob) {
        pushAssistant("正在调用采集 Agent 识别意图，并抽取可自动填充的表单字段…", pendingMessageJob.conversation_id);
        nextIntakeJob = await startIntakeAnalyzeJob(
          pendingMessageJob.conversation_id,
          {
            prompt: pendingMessageJob.continue_after_save.content,
            materials: pendingMessageJob.continue_after_save.materials,
          },
          pendingMessageJob.source_message_id,
          false,
        );
      }
      await clearPendingMessageJob(pendingMessageJob.conversation_id, nextIntakeJob ? "intake_analyze_running" : "message_saved", {
        user_message_saved: true,
      }).catch(() => {});
      if (nextIntakeJob) await resumePendingIntakeJob(nextIntakeJob);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (stopIfHidden()) return;
      if (isTransientFetchAbort(err)) return;
      pushAssistant(
        message.includes("404")
          ? "之前的对话消息保存任务不存在或已过期。请确认历史对话里是否已保存该消息，必要时重新发送。"
          : `继续查询对话消息保存任务失败:${message}`,
        pendingMessageJob.conversation_id,
      );
      await clearPendingMessageJob(pendingMessageJob.conversation_id, "message_job_resume_failed", {
        message_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activeMessageJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingMessageJob.conversation_id, false);
    }
  };

  const resumePendingPlanJob = async (pendingPlanJob: PendingPlanJob) => {
    const pollKey = `${pendingPlanJob.conversation_id}:${pendingPlanJob.kind}:${pendingPlanJob.job_id}`;
    if (activePlanJobPollsRef.current.has(pollKey)) return;
    activePlanJobPollsRef.current.add(pollKey);
    const targetConversationId = pendingPlanJob.conversation_id;
    const shouldContinuePolling = () => isVisibleConversation(targetConversationId);
    const stopIfHidden = () => !shouldContinuePolling();
    const clearRecoveryState = () => {
      planJobResumeAttemptsRef.current.delete(pollKey);
      planJobRecoveryNoticesRef.current.delete(pollKey);
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        targetConversationId,
        pendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(pendingPlanJob);
    };
    setBusyForConversation(targetConversationId, true);
    try {
      if (stopIfHidden()) return;
      if (hasMaterializedPlanJob(messagesRef.current, targetConversationId, pendingPlanJob)) {
        clearRecoveryState();
        await clearPendingPlanJob(targetConversationId, "plan_review").catch(() => {});
        if (pendingPlanJob.context.processedKey) releaseArtifactAction(pendingPlanJob.context.processedKey);
        return;
      }
      const status = pendingPlanJob.kind === "plan_manual_edit"
        ? await api.getPlanManualEditJob(pendingPlanJob.job_id)
        : pendingPlanJob.kind === "plan_revision"
          ? await api.getPlanRevisionJob(pendingPlanJob.job_id)
          : await api.getPlanMarkdownJob(pendingPlanJob.job_id);
      if (stopIfHidden()) return;
      const statusAction = classifyPlanJobResume({
        status: status.status,
        hasResult: Boolean(status.result),
      });
      if (statusAction === "clear_failed") {
        throw new ApiError(
          status.status === "failed" ? 409 : 422,
          status.error || status.message || "Plan job failed",
        );
      }
      const plan = statusAction === "complete"
        ? status.result!
        : pendingPlanJob.kind === "plan_manual_edit"
          ? await api.pollPlanManualEditJob(pendingPlanJob.job_id, shouldContinuePolling)
          : pendingPlanJob.kind === "plan_revision"
            ? await api.pollPlanRevisionJob(pendingPlanJob.job_id, shouldContinuePolling)
            : await api.pollPlanMarkdownJob(pendingPlanJob.job_id, shouldContinuePolling);
      if (!plan || stopIfHidden()) return;
      if (plan.error) {
        clearRecoveryState();
        if (pendingPlanJob.context.processedKey) releaseArtifactAction(pendingPlanJob.context.processedKey);
        const isRevision = pendingPlanJob.kind === "plan_revision";
        const isManualEdit = pendingPlanJob.kind === "plan_manual_edit";
        await clearPendingPlanJob(
          targetConversationId,
          isManualEdit ? "plan_manual_edit_failed" : isRevision ? "plan_revision_failed" : "plan_generation_failed",
          {
            plan_job_resume_error: plan.error,
          },
        ).catch(() => {});
        pushAssistant(
          isManualEdit
            ? `plan.md 编辑发布失败，已保留当前版本：${plan.error}`
            : isRevision
            ? `plan.md 修改失败，已保留当前版本：${plan.error}`
            : `plan.md 生成失败，未展示不符合创作合同的候选方案：${plan.error}`,
          targetConversationId,
        );
        return;
      }
      await persistPlanArtifactForConversation(
        createPlanArtifactMessage(
          plan,
          pendingPlanJob.context.selectedDirection,
          {
            intent: pendingPlanJob.context.intent,
            formValues: pendingPlanJob.context.formValues,
            materials: pendingPlanJob.context.materials,
            coreMessage: pendingPlanJob.context.coreMessage,
            intakeContext: pendingPlanJob.context.intakeContext,
          },
          targetConversationId,
          pendingPlanJob.job_id,
        ),
        targetConversationId,
        {
          type: "plan_save",
          last_phase: "plan_review",
          processed_key: pendingPlanJob.context.processedKey,
          success_message: pendingPlanJob.kind === "plan_manual_edit"
            ? `用户编辑内容已发布为 plan.md v${plan.plan_version}，请确认后继续。`
            : undefined,
          context: {
            flowDraft: null,
            pendingDirectionJob: null,
            pending_direction_job: null,
            pendingPlanJob: null,
            pending_plan_job: null,
            pendingPlanRevisionChoice: null,
            pending_plan_revision_choice: null,
            intent: pendingPlanJob.context.intent,
            form_values: pendingPlanJob.context.formValues,
            intake_context: pendingPlanJob.context.intakeContext,
            materials: pendingPlanJob.context.materials,
            ...(pendingPlanJob.kind === "plan_manual_edit" ? { plan_approved: false } : {}),
          },
        },
      );
      if (pendingPlanJob.kind === "plan_manual_edit" && isVisibleConversation(targetConversationId)) {
        setCanvasOpen(false);
        setSelectedPlanEditorMessageId("");
        setSavingPlanEdit(false);
      }
      clearRecoveryState();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const action = classifyPlanJobResume({
        hidden: stopIfHidden(),
        errorStatus: err instanceof ApiError ? err.status : undefined,
      });
      if (action === "retain_pending") {
        if (!stopIfHidden()) {
          const attempt = planJobResumeAttemptsRef.current.get(pollKey) || 0;
          planJobResumeAttemptsRef.current.set(pollKey, attempt + 1);
          if (!planJobRecoveryNoticesRef.current.has(pollKey)) {
            planJobRecoveryNoticesRef.current.add(pollKey);
            pushAssistant("Plan 查询暂时中断，正在使用原任务继续恢复…", targetConversationId);
          }
          window.setTimeout(() => {
            const current = pendingPlanJobRef.current;
            if (
              !document.hidden &&
              current?.job_id === pendingPlanJob.job_id &&
              current.conversation_id === targetConversationId
            ) {
              void resumePendingPlanJob(pendingPlanJob);
            }
          }, planJobResumeDelayMs(attempt));
        }
        return;
      }
      clearRecoveryState();
      if (pendingPlanJob.context.processedKey) releaseArtifactAction(pendingPlanJob.context.processedKey);
      const isManualEdit = pendingPlanJob.kind === "plan_manual_edit";
      pushAssistant(
        action === "clear_not_found"
          ? "之前的 plan.md 任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从最新创意方向或 Plan 卡片手动重试。"
          : isManualEdit
            ? `plan.md 编辑发布失败，已保留当前版本：${message}`
            : `继续查询 plan.md 任务失败：${message}`,
        targetConversationId,
      );
      await clearPendingPlanJob(targetConversationId, isManualEdit ? "plan_manual_edit_failed" : "plan_job_resume_failed", {
        plan_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activePlanJobPollsRef.current.delete(pollKey);
      if (pendingPlanJob.kind === "plan_manual_edit") setSavingPlanEdit(false);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const scenePackageContext = (
    artifact: ChatArtifact,
    videoScenePackages: PrepareScenePackagesResponse,
    sceneAssetFailures: Array<Record<string, unknown>>,
  ) => ({
    form_values: artifact.formValues,
    intake_context: artifact.intakeContext,
    materials: artifact.materials || [],
    selected_direction: artifact.selectedDirection,
    plan_markdown: artifact.plan?.plan_markdown,
    plan_approved: true,
    global_assets: videoScenePackages.global_assets,
    scene_packages: videoScenePackages.scene_packages,
    scene_asset_failures: sceneAssetFailures,
    creation_contract: videoScenePackages.creation_contract,
  });

  const handleCompletedScenePackageJob = async (
    pendingScenePackageJob: PendingScenePackageJob,
    result: PrepareScenePackagesJobResult,
    processedKey: string,
    stage = "completed",
  ) => {
    const targetConversationId = pendingScenePackageJob.conversation_id;
    const artifact = pendingScenePackageJob.artifact;
    const videoScenePackages = result.videoScenePackages;
    const sceneAssetFailures = result.sceneAssetFailures || [];
    const quotaPaused = Boolean(result.quota_insufficient) || isQuotaInsufficientPayload(result);
    if (!videoScenePackages) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频场景包准备失败:${result.message || "任务没有返回场景包结果"}`, targetConversationId);
      await clearPendingScenePackageJob(targetConversationId, "scene_package_failed", {
        scene_package_error: result.message || "任务没有返回场景包结果",
      }).catch(() => {});
      return;
    }

    if (stage === "awaiting_image_model" && videoScenePackages.ok && !quotaPaused) {
      setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        "awaiting_image_model",
      ));
      upsertEarlyScenePackageCard(pendingScenePackageJob, videoScenePackages, {
        generating: false,
        awaitingModel: true,
        tip: "场景包结构已就绪。请在下方选择生图模型后再生成参考图。",
      });
      await pushSceneAssetModelOptionsCard(pendingScenePackageJob, videoScenePackages);
      await clearPendingScenePackageJob(
        targetConversationId,
        "scene_package_awaiting_image_model",
        scenePackageContext(artifact, videoScenePackages, []),
      ).catch(() => {});
      return;
    }

    if (!videoScenePackages.ok || quotaPaused) releaseArtifactAction(processedKey);
    setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
      current.length > 0 ? current : createAssetPackageProgressSteps(),
      "completed",
    ));
    pushArtifact(quotaPaused ? "场景参考图生成因额度不足暂停，充值后可从本卡片继续。" : videoScenePackages.ok ? "视频场景包和参考图已准备好，请确认后生成视频。" : "视频场景包准备失败，请检查提示。", {
      type: "video_scene_packages",
      title: "视频场景包",
      description: quotaPaused
        ? quotaMessage(result.message || "场景参考图生成额度不足。")
        : videoScenePackages.ok
          ? `${videoScenePackages.scene_packages.length} 个场景片段，生成视频前必须确认。`
          : (result.message || videoScenePackages.message),
      actionLabel: quotaPaused ? "继续" : "确认",
      videoScenePackages,
      originalVideoScenePackages: videoScenePackages,
      sceneAssetFailures,
      sceneAssetsGenerating: false,
      sceneAssetsAwaitingModel: false,
      intent: "video",
      formValues: artifact.formValues,
      intakeContext: artifact.intakeContext,
      materials: artifact.materials || [],
      selectedDirection: artifact.selectedDirection,
      plan: artifact.plan,
    }, targetConversationId, scenePackageJobMessageId(pendingScenePackageJob));

    await clearPendingScenePackageJob(
      targetConversationId,
      videoScenePackages.ok ? quotaPaused ? "scene_asset_quota_paused" : "scene_package_ready" : "scene_package_failed",
      scenePackageContext(artifact, videoScenePackages, sceneAssetFailures),
    ).catch(() => {});
  };

  const upsertEarlyScenePackageCard = (
    pendingScenePackageJob: PendingScenePackageJob,
    videoScenePackages: PrepareScenePackagesResponse,
    options: {
      generating: boolean;
      awaitingModel?: boolean;
      sceneAssetFailures?: Array<Record<string, unknown>>;
      tip?: string;
    },
  ) => {
    const targetConversationId = pendingScenePackageJob.conversation_id;
    const artifact = pendingScenePackageJob.artifact;
    const awaitingModel = Boolean(options.awaitingModel);
    const tip = options.tip
      || (options.generating
        ? "场景包结构已就绪，参考图生成中。可先打开卡片查看设定与分镜。"
        : awaitingModel
          ? "场景包结构已就绪。请选择生图模型后再生成参考图。"
          : "视频场景包和参考图已准备好，请确认后生成视频。");
    pushArtifact(tip, {
      type: "video_scene_packages",
      title: "视频场景包",
      description: options.generating
        ? `${videoScenePackages.scene_packages.length} 个场景片段，参考图生成中，可先查看结构。`
        : awaitingModel
          ? `${videoScenePackages.scene_packages.length} 个场景片段，结构已就绪，待选择生图模型。`
          : `${videoScenePackages.scene_packages.length} 个场景片段，生成视频前必须确认。`,
      actionLabel: options.generating || awaitingModel ? "查看" : "确认",
      videoScenePackages,
      originalVideoScenePackages: videoScenePackages,
      sceneAssetFailures: options.sceneAssetFailures || [],
      sceneAssetsGenerating: options.generating,
      sceneAssetsAwaitingModel: awaitingModel,
      intent: "video",
      formValues: artifact.formValues,
      intakeContext: artifact.intakeContext,
      materials: artifact.materials || [],
      selectedDirection: artifact.selectedDirection,
      plan: artifact.plan,
    }, targetConversationId, scenePackageJobMessageId(pendingScenePackageJob));
  };

  const sceneAssetModelOptionsMessageId = (jobId: string) => `scene-asset-model-options:${jobId}`;

  const pushSceneAssetModelOptionsCard = async (
    pendingScenePackageJob: PendingScenePackageJob,
    videoScenePackages: PrepareScenePackagesResponse,
  ) => {
    const targetConversationId = pendingScenePackageJob.conversation_id;
    const artifact = pendingScenePackageJob.artifact;
    let configs: ImageModelParamConfig[] = [];
    try {
      const listed = await api.listImageGenerateModelConfigs();
      configs = Array.isArray(listed) ? listed : [];
    } catch {
      configs = [];
    }
    const preferredModels = [...SCENE_ASSET_PREFERRED_MODELS];
    const filtered = preferredModels
      .map((model) => configs.find((config) => {
        const record = config as unknown as Record<string, unknown>;
        const modelType = String(record.modelType || record.model_type || record.model || "");
        return modelType === model && record.isEnabled !== false;
      }))
      .filter((config): config is ImageModelParamConfig => Boolean(config));
    const modelConfigs = filtered.length > 0
      ? filtered
      : preferredModels.map((modelType) => ({
          modelType,
          modelCategoryType: "image_generate",
          paramConfig: {
            sizeList: modelType === "gpt-image-2" ? ["4K", "2K", "1080p"] : ["2K", "4K", "1080p"],
            aspectRatioList: ["1:1", "9:16", "16:9"],
          },
          isEnabled: true,
        } as ImageModelParamConfig));
    pushArtifact("场景包结构已就绪，请选择生图模型后再生成参考图。", {
      type: "scene_asset_model_options",
      title: "选择生图模型",
      description: "推荐 image-2 或 Seedream 5.0。确认后开始生成角色/场景/道具参考图。",
      actionLabel: "确认",
      intent: "video",
      formValues: artifact.formValues,
      intakeContext: artifact.intakeContext,
      materials: artifact.materials || [],
      selectedDirection: artifact.selectedDirection,
      plan: artifact.plan,
      videoScenePackages,
      originalVideoScenePackages: videoScenePackages,
      sceneAssetModelConfigs: modelConfigs,
      sceneAssetModelConfirmed: false,
      creationContract: videoScenePackages.creation_contract || artifact.plan?.creation_contract,
    }, targetConversationId, sceneAssetModelOptionsMessageId(pendingScenePackageJob.job_id));
  };

  const handleConfirmSceneAssetModel = async (
    msg: ChatMessage,
    selection: ImageEditModelSelection,
  ) => {
    const artifact = msg.artifact;
    if (artifact?.type !== "scene_asset_model_options" || artifact.sceneAssetModelConfirmed) return;
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages?.ok) {
      pushAssistant("缺少可用的场景包结构，无法开始生图。", messageConversationId(msg, conversationIdRef.current));
      return;
    }
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const existing = pendingScenePackageJobRef.current;
    if (existing?.conversation_id === targetConversationId) {
      pushAssistant("参考图仍在生成中，请稍候…", targetConversationId);
      return;
    }
    const model = String(selection.model || "").trim() || "gpt-image-2";
    const selectedConfig = (artifact.sceneAssetModelConfigs || []).find((config) => {
      const record = config as unknown as Record<string, unknown>;
      return String(record.modelType || record.model_type || record.model || "") === model;
    });
    const sizeOptions = (() => {
      const record = (selectedConfig || {}) as unknown as Record<string, unknown>;
      const params = (record.paramConfig || record.param_config || {}) as Record<string, unknown>;
      const list = params.sizeList || params.size_list;
      return Array.isArray(list) ? list.map((item) => String(item || "")).filter(Boolean) : [];
    })();
    const imageSize = preferredSceneAssetImageSize(model, sizeOptions);
    const imageRatio = String(
      (videoScenePackages.creation_contract as Record<string, unknown> | undefined)?.scene_image_ratio
      || artifact.formValues?.scene_image_ratio
      || "9:16",
    );
    const previousContract = (videoScenePackages.creation_contract || artifact.creationContract || {}) as Record<string, unknown>;
    const creationContract = {
      ...previousContract,
      image_model: model,
      scene_image_size: imageSize,
      scene_image_ratio: imageRatio,
    } as unknown as VideoCreationContract;
    const nextPackages: PrepareScenePackagesResponse = {
      ...videoScenePackages,
      creation_contract: creationContract as unknown as PrepareScenePackagesResponse["creation_contract"],
    };
    setMessages((items) => {
      const nextItems = items.map((item) => {
        if (item.id !== msg.id || messageConversationId(item, targetConversationId) !== targetConversationId) return item;
        return {
          ...item,
          artifact: {
            ...item.artifact!,
            sceneAssetModelConfirmed: true,
            videoScenePackages: nextPackages,
            creationContract: creationContract as unknown as Record<string, unknown>,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    upsertEarlyScenePackageCard(
      {
        job_id: msg.id.replace(/^scene-asset-model-options:/, "") || "scene-assets",
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "scene_asset_generation",
        started_at: new Date().toISOString(),
        request: {} as SceneAssetsJobRequest,
        artifact: {
          ...artifact,
          videoScenePackages: nextPackages,
          type: "video_scene_packages",
          title: "视频场景包",
          description: "",
          actionLabel: "查看",
        },
      },
      nextPackages,
      {
        generating: true,
        tip: `已选择生图模型，正在生成场景参考图（${sceneAssetModelLabel(model)}）…`,
      },
    );
    setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
      current.length > 0 ? current : createAssetPackageProgressSteps(),
      "generate_scene_assets",
    ));
    pushAssistant(`已选择 ${sceneAssetModelLabel(model)}，开始生成场景参考图…`, targetConversationId);
    try {
      const request: SceneAssetsJobRequest = {
        global_assets: nextPackages.global_assets,
        scene_packages: nextPackages.scene_packages,
        materials: artifact.materials || [],
        image_ratio: imageRatio,
        image_size: imageSize,
        model,
        creation_contract: creationContract,
      };
      const started = await api.startSceneAssetsJob(request);
      const pendingScenePackageJob: PendingScenePackageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "scene_asset_generation",
        started_at: new Date().toISOString(),
        request,
        artifact: {
          ...artifact,
          type: "video_scene_packages",
          title: "视频场景包",
          description: `${nextPackages.scene_packages.length} 个场景片段，参考图生成中。`,
          actionLabel: "查看",
          videoScenePackages: nextPackages,
          originalVideoScenePackages: nextPackages,
          sceneAssetsGenerating: true,
          sceneAssetsAwaitingModel: false,
          creationContract: creationContract as unknown as Record<string, unknown>,
        },
      };
      await persistPendingScenePackageJob(pendingScenePackageJob, targetConversationId, "scene_asset_generation_running", {
        intent: "video",
        scene_package_stage: "generate_scene_assets",
      });
      await resumePendingScenePackageJob(pendingScenePackageJob);
    } catch (err) {
      pushAssistant(
        `启动参考图生成失败:${err instanceof Error ? err.message : String(err)}`,
        targetConversationId,
      );
    }
  };

  const pushSceneAssetProgressTip = (
    pendingScenePackageJob: PendingScenePackageJob,
    progress: NonNullable<PrepareScenePackagesJobStatusResponse["asset_progress"]>,
  ) => {
    const typeLabel = progress.asset_type === "character"
      ? "角色"
      : progress.asset_type === "scene_image"
        ? "场景"
        : progress.asset_type === "prop_image"
          ? "道具"
          : "素材";
    const assetName = (progress.asset_name || "参考图").trim() || "参考图";
    const statusText = progress.ok === false ? "失败" : "已完成";
    const content = `参考图 ${progress.completed}/${progress.total}：${typeLabel}「${assetName}」${statusText}`;
    void appendMessageForConversation(
      {
        id: `scene-package-asset-tip:${pendingScenePackageJob.job_id}`,
        conversationId: pendingScenePackageJob.conversation_id,
        role: "assistant",
        content,
        time: "",
      },
      pendingScenePackageJob.conversation_id,
    );
  };

  const imageResultTitleForJob = (pendingImageJob: PendingImageJob): string =>
    pendingImageJob.kind === "direct_image_edit"
      ? "图片编辑结果"
      : pendingImageJob.kind === "image_regeneration"
        ? "图片重新生成结果"
        : "图片生成结果";

  const imageResultSuccessContentForJob = (pendingImageJob: PendingImageJob): string =>
    pendingImageJob.kind === "direct_image_edit"
      ? `图片编辑完成，请确认是否满意。${AUTO_CONFIRM_TIMEOUT_SECONDS} 秒未操作将默认满意并结束流程。`
      : pendingImageJob.kind === "image_regeneration"
        ? "图片已按修改意见重新生成，请查看结果。"
        : "图片生成完成，请查看结果。";

  const imageResultFailureContentForJob = (pendingImageJob: PendingImageJob): string =>
    pendingImageJob.kind === "direct_image_edit"
      ? "图片编辑失败，请查看错误信息。"
      : pendingImageJob.kind === "image_regeneration"
        ? "图片重新生成失败，请查看错误信息。"
        : "图片生成失败，请查看错误信息。";

  const imageResultLastPhaseForJob = (pendingImageJob: PendingImageJob, imageResult: ImageGenerateResponse): string => {
    const quotaPaused = isQuotaInsufficientPayload(imageResult);
    if (pendingImageJob.kind === "direct_image_edit") {
      return imageResult.ok ? "image_edit_done" : quotaPaused ? "image_edit_quota_paused" : "image_edit_failed";
    }
    if (pendingImageJob.kind === "image_regeneration") {
      return imageResult.ok ? "image_regenerated" : quotaPaused ? "image_regeneration_quota_paused" : "image_regeneration_failed";
    }
    return imageResult.ok ? "image_generated" : quotaPaused ? "image_generation_quota_paused" : "image_generation_failed";
  };

  const handleCompletedImageGenerationJob = async (
    pendingImageJob: PendingImageJob,
    imageResult: ImageGenerateResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingImageJob.conversation_id;
    const artifact = pendingImageJob.artifact;
    const imagePrepare = pendingImageJob.imagePrepare || artifact.imagePrepare;
    if (!imagePrepare) {
      releaseArtifactAction(processedKey);
      pushAssistant("图片生成任务完成，但没有找到对应的图片参数，请从最新卡片手动重试。", targetConversationId);
      await clearPendingImageJob(targetConversationId, "image_job_resume_failed", {
        image_job_resume_error: "缺少 imagePrepare",
      }).catch(() => {});
      return;
    }
    const imageQuotaInsufficient = isQuotaInsufficientPayload(imageResult);
    if (!imageResult.ok) releaseArtifactAction(processedKey);
    if (pendingImageJob.kind === "direct_image_edit") {
      pendingImageEditRequestRef.current = null;
    }
    const imageReviewStartedAt = Date.now();
    const imageReviewRequestedAt = new Date(imageReviewStartedAt).toISOString();
    const imageReviewExpiresAt = reviewExpiresAt(imageReviewStartedAt, AUTO_CONFIRM_TIMEOUT_MS);
    const imageResultMessage = pushArtifact(imageResult.ok ? imageResultSuccessContentForJob(pendingImageJob) : imageResultFailureContentForJob(pendingImageJob), {
      type: "image_result",
      title: imageResultTitleForJob(pendingImageJob),
      description: imageQuotaInsufficient
        ? quotaMessage(imageResult.message || (pendingImageJob.kind === "image_regeneration" ? "图片重新生成额度不足。" : "图片生成额度不足。"))
        : imageResultSummary(imageResult),
      actionLabel: "查看",
      imageResult,
      imagePrepare,
      imageEditRequest: artifact.imageEditRequest,
      imageEditConfirmedSelection: artifact.imageEditConfirmedSelection,
      imageRevisionFeedback: pendingImageJob.revision_feedback || artifact.imageRevisionFeedback,
      intent: "image",
      formValues: artifact.formValues,
      intakeContext: artifact.intakeContext,
      materials: artifact.materials || [],
      selectedDirection: artifact.selectedDirection,
      plan: artifact.plan,
      reviewRequestedAt: imageResult.ok ? imageReviewRequestedAt : undefined,
      reviewExpiresAt: canAcceptImageResult(imageResult) ? imageReviewExpiresAt : undefined,
    }, targetConversationId);
    if (canAcceptImageResult(imageResult)) {
      window.setTimeout(() => {
        if (shouldAutoAcceptImageResult(imageResultMessage, targetConversationId)) {
          void handleAcceptImageResult(imageResultMessage, true);
        }
      }, AUTO_CONFIRM_TIMEOUT_MS);
    }
    await clearPendingImageJob(targetConversationId, imageResultLastPhaseForJob(pendingImageJob, imageResult), {
      plan_approved: pendingImageJob.kind === "image_generation" ? true : undefined,
      plan_markdown: artifact.plan?.plan_markdown,
      image_revision_feedback: pendingImageJob.revision_feedback || artifact.imageRevisionFeedback,
      image_edit_done: pendingImageJob.kind === "direct_image_edit" ? imageResult.ok : undefined,
      intake_context: artifact.intakeContext,
      materials: artifact.materials || [],
      image_prepare: imagePrepare,
      image_result: imageResult,
      image_edit_request: artifact.imageEditRequest,
      pendingImageEditRequest: pendingImageEditRequestRef.current,
      pending_image_edit_request: pendingImageEditRequestRef.current,
    }).catch(() => {});
  };

  const handleCompletedImageAssetEditJob = async (
    pendingImageJob: PendingImageJob,
    editResult: ImageAssetEditResponse | ImageAssetFusionResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingImageJob.conversation_id;
    const reference = pendingImageJob.sceneGlobalAssetReference;
    const storyboardMessage = reference
      ? findStoryboardMessageForGlobalAsset(reference, targetConversationId, [
          pendingImageJob.source_message_id,
          pendingImageJob.storyboard_message_id || "",
        ])
      : undefined;
    const fallbackArtifact = pendingImageJob.artifact?.videoScenePackages ? pendingImageJob.artifact : undefined;
    const baseArtifact = storyboardMessage?.artifact?.videoScenePackages ? storyboardMessage.artifact : fallbackArtifact;
    const baseVideoScenePackages = baseArtifact?.videoScenePackages;
    if (!reference || !baseArtifact || !baseVideoScenePackages) {
      releaseArtifactAction(processedKey);
      pushAssistant("素材图片编辑任务完成，但没有找到对应的场景包卡片，请从当前场景包手动重试。", targetConversationId);
      await clearPendingImageJob(targetConversationId, "scene_global_asset_edit_failed", {
        scene_global_asset_edit_error: "缺少对应的场景包 artifact",
      }).catch(() => {});
      return;
    }
    const isFusion = "fused_image" in editResult;
    const nextUrl = assetUpdateImageUrl(editResult);
    const quotaInsufficient = isQuotaInsufficientPayload(editResult);
    if (!editResult.ok || !nextUrl) {
      releaseArtifactAction(processedKey);
      const failedRequest = pendingImageJob.request as ImageAssetEditJobRequest;
      const retryMaterials = [
        {
          ...reference,
          source_image_url: reference.source_image_url,
          url: reference.source_image_url,
          storyboard_message_id: storyboardMessage?.id || pendingImageJob.storyboard_message_id,
        },
        ...uploadedReferenceMaterials(failedRequest.materials || []),
      ];
      const retrySelection: ImageEditModelSelection = {
        model: String(failedRequest.model || "gpt-image-2"),
        ratio: String(failedRequest.ratio || defaultGlobalSceneAssetRatio(reference.asset_group)),
        size: String(failedRequest.size || "4K"),
      };
      const retryRequest: PendingImageEditRequest = {
        conversationId: targetConversationId,
        prompt: String(failedRequest.prompt || ""),
        formValues: {
          image_goal: reference.name,
          image_operation: "image_edit",
          image_model: retrySelection.model,
          image_size: retrySelection.ratio,
          image_quality: retrySelection.size,
        },
        intakeContext: {
          image_operation: "image_edit",
          image_model: retrySelection.model,
          image_size: retrySelection.ratio,
          image_quality: retrySelection.size,
          scene_global_asset_reference: reference,
        },
        materials: retryMaterials,
        selection: retrySelection,
        mode: isFusion ? "scene_global_asset_fusion" : "scene_global_asset_edit",
        sceneGlobalAssetReference: reference,
        storyboardMessageId: storyboardMessage?.id || pendingImageJob.storyboard_message_id,
      };
      pushArtifact("全局素材图片编辑失败，请查看错误信息。", {
        type: "image_result",
        title: "全局素材图片编辑结果",
        description: quotaInsufficient ? quotaMessage(editResult.message || "图片编辑额度不足。") : editResult.message,
        actionLabel: "查看",
        imageResult: {
          ok: false,
          method: editResult.method,
          endpoint: editResult.endpoint,
          task_id: null,
          images: nextUrl ? [assetUpdateImage(editResult)] : [],
          error: editResult.message,
          message: editResult.message,
          quota_insufficient: quotaInsufficient,
          raw: editResult.raw,
        },
        intent: "image",
        materials: retryMaterials,
        imageEditRequest: retryRequest as unknown as Record<string, unknown>,
        imageEditConfirmedSelection: retrySelection,
        imageRevisionFeedback: retryRequest.prompt,
      }, targetConversationId);
      await clearPendingImageJob(
        targetConversationId,
        quotaInsufficient
          ? isFusion
            ? "scene_global_asset_fusion_quota_paused"
            : "scene_global_asset_edit_quota_paused"
          : isFusion
            ? "scene_global_asset_fusion_failed"
            : "scene_global_asset_edit_failed",
        {
        scene_global_asset_edit: editResult,
        scene_global_asset_fusion: isFusion ? editResult : undefined,
        },
      ).catch(() => {});
      return;
    }

    const originalImageUrl = String(
      (reference as Record<string, unknown>).original_image_url ||
        (reference as Record<string, unknown>).originalImageUrl ||
        reference.source_image_url,
    );
    const review: SceneGlobalAssetEditReview = {
      asset_id: reference.asset_id,
      asset_group: reference.asset_group,
      asset_name: reference.name,
      original_image_url: originalImageUrl,
      source_image_url: reference.source_image_url,
      edited_image_url: nextUrl,
      source_message_id: storyboardMessage?.id || pendingImageJob.source_message_id,
      storyboard_message_id: storyboardMessage?.id || pendingImageJob.storyboard_message_id,
      videoScenePackages: baseVideoScenePackages,
      originalVideoScenePackages: baseArtifact.originalVideoScenePackages || baseVideoScenePackages,
      editResult,
      request: pendingImageJob.request as unknown as Record<string, unknown>,
      selection: {
        model: String((pendingImageJob.request as ImageAssetEditJobRequest).model || "gpt-image-2"),
        ratio: String((pendingImageJob.request as ImageAssetEditJobRequest).ratio || defaultGlobalSceneAssetRatio(reference.asset_group)),
        size: String((pendingImageJob.request as ImageAssetEditJobRequest).size || "4K"),
      },
      prompt: String((pendingImageJob.request as ImageAssetEditJobRequest).prompt || ""),
      is_fusion: isFusion,
    };
    pushArtifact("全局素材候选图已生成，请确认是否替换到当前场景包。", {
      type: "image_result",
      title: isFusion ? "全局素材图片融合结果" : "全局素材图片编辑结果",
      description: `候选新图已生成。确认后才会替换「${reference.name}」并同步更新分镜引用。`,
      actionLabel: "查看",
      imageResult: {
        ok: true,
        method: editResult.method,
        endpoint: editResult.endpoint,
        task_id: null,
        images: [assetUpdateImage(editResult)],
        error: null,
        message: editResult.message,
        quota_insufficient: false,
        raw: editResult.raw,
      },
      intent: "image",
      materials: [{
        ...reference,
        url: nextUrl,
        source_image_url: nextUrl,
        original_image_url: originalImageUrl,
        storyboard_message_id: review.storyboard_message_id,
      }],
      imageRevisionFeedback: review.prompt,
      imageEditConfirmedSelection: review.selection,
      sceneGlobalAssetEditReview: review,
      reviewRequestedAt: new Date().toISOString(),
    }, targetConversationId);

    await clearPendingImageJob(targetConversationId, isFusion ? "scene_global_asset_fusion_review_pending" : "scene_global_asset_edit_review_pending", {
      scene_global_asset_edit: editResult,
      scene_global_asset_fusion: isFusion ? editResult : undefined,
      scene_global_asset_edit_review: review,
      pendingImageEditRequest: null,
      pending_image_edit_request: null,
    }).catch(() => {});
    return;
  };

  const handleCompletedSceneAssetRevisionJob = async (
    pendingScenePackageJob: PendingScenePackageJob,
    result: ScenePackageAssetRevisionResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingScenePackageJob.conversation_id;
    const request = pendingScenePackageJob.request as ScenePackageAssetRevisionRequest;
    const sourceMessage = messagesRef.current.find(
      (message) =>
        message.id === pendingScenePackageJob.source_message_id &&
        messageConversationId(message, targetConversationId) === targetConversationId &&
        Boolean(message.artifact?.videoScenePackages),
    );
    const sourceArtifact = sourceMessage?.artifact?.videoScenePackages
      ? sourceMessage.artifact
      : pendingScenePackageJob.artifact;
    const sourcePackages = sourceArtifact.videoScenePackages;
    const quotaPaused = Boolean(result.quota_insufficient) || isQuotaInsufficientPayload(result);
    if (!result.ok || quotaPaused || !sourcePackages || !result.scene_packages.length) {
      releaseArtifactAction(processedKey);
      pushAssistant(
        quotaPaused
          ? quotaMessage(result.message || "图片分析额度不足，充值后可重新执行本次素材修改。")
          : `分镜素材修改失败:${result.message || "任务没有返回更新后的场景包"}`,
        targetConversationId,
      );
      await clearPendingScenePackageJob(
        targetConversationId,
        quotaPaused ? "scene_asset_revision_quota_paused" : "scene_asset_revision_failed",
        {
          scene_global_asset_revision_error: result.message || "任务没有返回更新后的场景包",
          scene_global_asset_revision_request: request,
        },
      ).catch(() => {});
      return;
    }

    const affectedSceneIds = Array.from(
      new Set([...(sourceArtifact.videoScenePackageEditedSceneIds || []), ...(result.affected_scene_ids || [])]),
    );
    const updatedPackages: PrepareScenePackagesResponse = {
      ...sourcePackages,
      global_assets: result.global_assets,
      scene_packages: result.scene_packages,
      message: result.message || sourcePackages.message,
    };
    const updatedSourceArtifact: ChatArtifact = {
      ...sourceArtifact,
      videoScenePackages: updatedPackages,
      videoScenePackageEditedSceneIds: affectedSceneIds,
    };

    if (sourceMessage) {
      await api.updateConversationMessage(targetConversationId, sourceMessage.id, {
        content: sourceMessage.content,
        payload: {
          artifact: updatedSourceArtifact,
          materials: updatedSourceArtifact.materials || [],
          client_message_id: sourceMessage.id,
        } as unknown as Record<string, unknown>,
      });
      if (isVisibleConversation(targetConversationId)) {
        setMessages((items) => {
          const nextItems = items.map((message) =>
            message.id === sourceMessage.id &&
            messageConversationId(message, targetConversationId) === targetConversationId
              ? { ...message, artifact: updatedSourceArtifact }
              : message,
          );
          messagesRef.current = nextItems;
          return nextItems;
        });
      }
    }

    if (pendingScenePackageJob.review_message_id) {
      markImageResultAccepted(pendingScenePackageJob.review_message_id, targetConversationId);
    }
    setReferencedMaterials((items) => items.filter((item) => item.asset_id !== request.asset_id));
    const completedMessage: ChatMessage = {
      id: scenePackageJobMessageId(pendingScenePackageJob),
      conversationId: targetConversationId,
      role: "assistant",
      content: request.operation === "replace"
        ? "素材分析完成，已仅更新引用该素材的分镜描述。最新场景包已生成，请打开「查看分镜」确认。"
        : "素材已删除，相关分镜中的引用和描述已同步清理。最新场景包已生成，请打开「查看分镜」确认。",
      time: "",
      artifact: {
        ...updatedSourceArtifact,
        type: "video_scene_packages",
        title: sourceArtifact.title || "视频场景包",
        description: `${updatedPackages.scene_packages.length} 个场景片段，${result.affected_scene_ids.length} 个分镜已同步更新。`,
        actionLabel: "确认",
        videoScenePackages: updatedPackages,
        originalVideoScenePackages: sourceArtifact.originalVideoScenePackages || sourcePackages,
        videoScenePackageEditedSceneIds: affectedSceneIds,
      },
    };
    // 终态场景包也通过可恢复消息 job 落库；刷新或切换对话时，后续恢复会继续保存同一条消息。
    const completionMessageJob = await startConversationMessageJobForConversation(
      completedMessage,
      targetConversationId,
    );
    if (completionMessageJob) await resumePendingMessageJob(completionMessageJob);
    if (isVisibleConversation(targetConversationId)) {
      setSelectedStoryboardMessageId(completedMessage.id);
      setCanvasOpen(true);
    }
    await clearPendingScenePackageJob(targetConversationId, "scene_asset_revision_completed", {
      global_assets: updatedPackages.global_assets,
      scene_packages: updatedPackages.scene_packages,
      generated_scene_videos: sourceArtifact.generatedSceneVideos?.scene_videos,
      merged_video: sourceArtifact.mergedVideo,
      video_scene_package_edited_scene_ids: affectedSceneIds,
      scene_global_asset_revision: {
        operation: request.operation,
        asset_id: request.asset_id,
        asset_group: request.asset_group,
        affected_scene_ids: result.affected_scene_ids,
      },
    }).catch(() => {});
  };

  const handleCompletedSceneAssetJob = async (
    pendingScenePackageJob: PendingScenePackageJob,
    sceneAssets: GenerateSceneAssetsResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingScenePackageJob.conversation_id;
    const artifact = pendingScenePackageJob.artifact;
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages) {
      releaseArtifactAction(processedKey);
      pushAssistant("场景参考图生成完成，但没有找到对应的场景包卡片，请从最新 plan 或场景包手动重试。", targetConversationId);
      await clearPendingScenePackageJob(targetConversationId, "scene_asset_failed", {
        scene_asset_error: "缺少对应的场景包 artifact",
      }).catch(() => {});
      return;
    }

    const nextPackages: PrepareScenePackagesResponse = {
      ...videoScenePackages,
      global_assets: sceneAssets.global_assets || videoScenePackages.global_assets,
      scene_packages: sceneAssets.scene_packages.length ? sceneAssets.scene_packages : videoScenePackages.scene_packages,
      message: sceneAssets.ok ? videoScenePackages.message : sceneAssets.message,
    };
    const quotaPaused = Boolean(sceneAssets.quota_insufficient) || isQuotaInsufficientPayload(sceneAssets);
    const retryTargets = (pendingScenePackageJob.request as SceneAssetsJobRequest).target_assets;
    const sceneAssetFailures = retryTargets
      ? mergeSceneAssetRetryFailures(artifact.sceneAssetFailures, sceneAssets.failed_assets, retryTargets)
      : sceneAssets.failed_assets;
    const retryCompleted = sceneAssets.ok && sceneAssetFailures.length === 0 && !quotaPaused;
    nextPackages.message = retryCompleted ? videoScenePackages.message : sceneAssets.message;
    if (!retryCompleted) releaseArtifactAction(processedKey);
    const isRetry = Boolean(retryTargets?.length);
    const successContent = isRetry
      ? "失败的场景参考图已重新生成完成，请确认后生成视频。"
      : "视频场景包和参考图已准备好，请确认后生成视频。";
    const failureContent = isRetry
      ? "场景参考图重试仍有失败项，请查看失败素材。"
      : "场景参考图生成未全部成功，请查看失败素材。";
    setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
      current.length > 0 ? current : createAssetPackageProgressSteps(),
      retryCompleted ? "completed" : "generate_scene_assets",
    ));
    pushArtifact(retryCompleted ? successContent : failureContent, {
      type: "video_scene_packages",
      title: "视频场景包",
      description: quotaPaused
        ? quotaMessage(sceneAssets.message || "场景参考图生成额度不足。")
        : `${nextPackages.scene_packages.length} 个场景片段，生成视频前必须确认。`,
      actionLabel: quotaPaused ? "继续" : "确认",
      videoScenePackages: nextPackages,
      originalVideoScenePackages: artifact.originalVideoScenePackages || videoScenePackages,
      sceneAssetFailures,
      sceneAssetsGenerating: false,
      sceneAssetsAwaitingModel: false,
      intent: "video",
      formValues: artifact.formValues,
      intakeContext: artifact.intakeContext,
      materials: artifact.materials || [],
      selectedDirection: artifact.selectedDirection,
      plan: artifact.plan,
    }, targetConversationId, scenePackageJobMessageId(pendingScenePackageJob));

    await clearPendingScenePackageJob(
      targetConversationId,
      retryCompleted ? "scene_package_ready" : quotaPaused ? "scene_asset_quota_paused" : "scene_asset_failed",
      scenePackageContext(artifact, nextPackages, sceneAssetFailures),
    ).catch(() => {});
  };

  const sceneVideoRequestFromPackages = (
    videoScenePackages: PrepareScenePackagesResponse,
    sceneIds?: Set<string>,
    editedSceneIds: Set<string> = sceneIds || new Set<string>(),
  ): SceneVideosJobRequest => {
    const creationContract = videoScenePackages.creation_contract || {
      video_duration_sec: videoScenePackages.target_duration_ms / 1000,
      video_ratio: "9:16",
      video_model: "seedance-2.0",
      video_size: "720p",
      video_sound: "on",
      image_model: "gpt-image-2",
    };
    return {
      scenes: videoScenePackages.scene_packages
        .filter((scene) => !sceneIds || sceneIds.has(scene.scene_id))
        .map((scene) =>
          sceneGenerationPayloadFromPackage(scene, videoScenePackages.global_assets, {
            edited: editedSceneIds.has(scene.scene_id),
          }) as SceneGenerationPayload,
        ),
      ratio: creationContract.video_ratio,
      size: creationContract.video_size,
      model: creationContract.video_model,
      sound: creationContract.video_sound,
      creation_contract: creationContract,
    };
  };

  const mergeRequestFromSceneVideos = (
    sceneVideos: NonNullable<ChatArtifact["generatedSceneVideos"]>["scene_videos"],
    targetDurationMs?: number,
  ): MergeSceneVideosJobRequest => ({
    scene_videos: sceneVideos.map((scene) => ({
      scene_id: scene.scene_id,
      scene_index: scene.scene_index,
      video_url: scene.video_url,
    })),
    duration: Math.max(1, Math.ceil((targetDurationMs || 1000) / 1000)),
    size: "1080p",
  });

  const handleCompletedVideoMergeJob = async (
    pendingVideoJob: PendingVideoJob,
    mergedVideo: MergeSceneVideosResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingVideoJob.conversation_id;
    const artifact = pendingVideoJob.artifact;
    const videoScenePackages = artifact.videoScenePackages;
    const generatedSceneVideos = artifact.generatedSceneVideos;
    if (!videoScenePackages || !generatedSceneVideos) return;
    const originalVideoScenePackages =
      artifact.originalVideoScenePackages ||
      latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId) ||
      videoScenePackages;
    const isRegeneration = pendingVideoJob.merge_purpose === "regeneration";
    const mergeQuotaInsufficient = isQuotaInsufficientPayload(mergedVideo);
    const videoReviewRequestedAt = new Date().toISOString();
    pushArtifact(
      mergedVideo.ok
        ? isRegeneration
          ? "视频已按修改意见重新生成，请查看新版本。"
          : "视频生成完成，请查看合并视频和场景视频。"
        : isRegeneration
          ? "视频重新合并失败，请查看错误信息。"
          : "视频合并失败，请查看错误信息。",
      {
        type: "video_result",
        title: isRegeneration ? "视频修改结果" : "视频生成结果",
        description: mergedVideo.ok
          ? isRegeneration
            ? "已复用未受影响场景，并合并新版本视频。"
            : "合并视频和每个场景视频已返回。"
          : mergeQuotaInsufficient
            ? quotaMessage(mergedVideo.message || "视频合并额度不足。")
            : mergedVideo.message,
        actionLabel: "查看",
        videoScenePackages,
        originalVideoScenePackages,
        generatedSceneVideos,
        mergedVideo,
        videoScenePackageEditedSceneIds: [],
        intent: "video",
        formValues: artifact.formValues,
        intakeContext: artifact.intakeContext,
        materials: artifact.materials || [],
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
        reviewRequestedAt: mergedVideo.ok ? videoReviewRequestedAt : undefined,
      },
      targetConversationId,
    );
    if (mergedVideo.ok) {
      updateOriginalScenePackageMessageWithVideoResult(
        pendingVideoJob.source_message_id,
        targetConversationId,
        videoScenePackages,
        generatedSceneVideos,
        mergedVideo,
      );
    }
    if (!mergedVideo.ok) releaseArtifactAction(processedKey);
    if (mergedVideo.merged_video_url) {
      const results = videoResultsFromGeneratedScenes(
        mergedVideo.merged_video_url,
        mergedVideo.task_id,
        generatedSceneVideos,
        videoScenePackages.target_duration_ms,
        mergedVideo.ok,
      );
      setCanvasForConversation(targetConversationId, (c) => ({ ...c, phase: mergedVideo.ok ? "done" : c.phase, results, selectedVideo: null }));
      setCanvasOpenForConversation(targetConversationId, true);
    }
    await clearPendingVideoJob(
      targetConversationId,
      mergedVideo.ok
        ? isRegeneration
          ? "video_regenerated"
          : "video_generated"
        : mergeQuotaInsufficient
          ? "video_merge_quota_paused"
          : isRegeneration
            ? "video_regeneration_merge_failed"
            : "video_merge_failed",
      {
        video_revision_feedback: artifact.videoRevisionFeedback,
        video_revision_use_quality_review: pendingVideoJob.use_quality_review,
        affected_scene_ids: pendingVideoJob.affected_scene_ids || [],
        global_assets: videoScenePackages.global_assets,
        intake_context: artifact.intakeContext,
        scene_packages: videoScenePackages.scene_packages,
        generated_scene_videos: generatedSceneVideos.scene_videos,
        merged_video: mergedVideo,
        video_accepted: false,
      },
    ).catch(() => {});
  };

  const startAndResumeVideoMergeJob = async ({
    targetConversationId,
    sourceMessageId,
    artifact,
    videoScenePackages,
    generatedSceneVideos,
    originalVideoScenePackages,
    processedKey,
    mergePurpose = "generation",
    affectedSceneIds = [],
  }: {
    targetConversationId: string;
    sourceMessageId: string;
    artifact: ChatArtifact;
    videoScenePackages: PrepareScenePackagesResponse;
    generatedSceneVideos: NonNullable<ChatArtifact["generatedSceneVideos"]>;
    originalVideoScenePackages?: PrepareScenePackagesResponse;
    processedKey: string;
    mergePurpose?: "generation" | "regeneration";
    affectedSceneIds?: string[];
  }) => {
    const request = mergeRequestFromSceneVideos(generatedSceneVideos.scene_videos, videoScenePackages.target_duration_ms);
    const started = await api.startMergeSceneVideosJob(request);
    const pendingVideoJob: PendingVideoJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: sourceMessageId,
      kind: "video_merge",
      started_at: new Date().toISOString(),
      request,
      artifact: {
        ...artifact,
        videoScenePackages,
        originalVideoScenePackages,
        generatedSceneVideos,
        videoScenePackageEditedSceneIds: [],
      },
      affected_scene_ids: affectedSceneIds,
      merge_purpose: mergePurpose,
    };
    await persistPendingVideoJob(pendingVideoJob, targetConversationId, mergePurpose === "regeneration" ? "video_regeneration_merge_running" : "video_merge_running", {
      affected_scene_ids: affectedSceneIds,
      global_assets: videoScenePackages.global_assets,
      intake_context: artifact.intakeContext,
      scene_packages: videoScenePackages.scene_packages,
      generated_scene_videos: generatedSceneVideos.scene_videos,
      merged_video: artifact.mergedVideo,
    });
    await resumePendingVideoJob(pendingVideoJob, processedKey);
  };

  const handleCompletedSceneGenerationJob = async (
    pendingVideoJob: PendingVideoJob,
    generatedSceneVideos: NonNullable<ChatArtifact["generatedSceneVideos"]>,
    processedKey: string,
  ) => {
    const targetConversationId = pendingVideoJob.conversation_id;
    const artifact = pendingVideoJob.artifact;
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages) return;
    const originalVideoScenePackages = artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId) || videoScenePackages;
    if (!generatedSceneVideos.ok) {
      const videoQuotaInsufficient = isQuotaInsufficientPayload(generatedSceneVideos);
      releaseArtifactAction(processedKey);
      pushArtifact("视频生成失败：部分场景视频生成失败，请展开失败场景查看原因。", {
        type: "video_result",
        title: "视频生成结果",
        description: videoQuotaInsufficient ? quotaMessage(generatedSceneVideos.message || "场景视频生成额度不足。") : (generatedSceneVideos.message || "部分场景视频生成失败，请查看失败场景。"),
        actionLabel: "查看",
        videoScenePackages,
        originalVideoScenePackages,
        generatedSceneVideos,
        intent: "video",
        formValues: artifact.formValues,
        intakeContext: artifact.intakeContext,
        materials: artifact.materials || [],
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
      }, targetConversationId);
      await clearPendingVideoJob(targetConversationId, videoQuotaInsufficient ? "video_generation_quota_paused" : "video_generation_failed", {
        global_assets: videoScenePackages.global_assets,
        intake_context: artifact.intakeContext,
        scene_packages: videoScenePackages.scene_packages,
        generated_scene_videos: generatedSceneVideos.scene_videos,
        failed_scenes: generatedSceneVideos.failed_scenes,
        video_quota_insufficient: videoQuotaInsufficient,
      }).catch(() => {});
      return;
    }

    pushAssistant("场景视频已生成，正在按场景顺序合并完整视频…", targetConversationId);
    await startAndResumeVideoMergeJob({
      targetConversationId,
      sourceMessageId: pendingVideoJob.source_message_id,
      artifact,
      videoScenePackages,
      generatedSceneVideos,
      originalVideoScenePackages,
      processedKey,
      mergePurpose: "generation",
    });
  };

  const handleCompletedSceneRegenerationJob = async (
    pendingVideoJob: PendingVideoJob,
    regenerated: NonNullable<ChatArtifact["generatedSceneVideos"]>,
    processedKey: string,
  ) => {
    const targetConversationId = pendingVideoJob.conversation_id;
    const artifact = pendingVideoJob.artifact;
    if (!artifact.videoScenePackages || !artifact.generatedSceneVideos || !artifact.mergedVideo) return;
    const previousGeneratedSceneVideos = artifact.generatedSceneVideos;
    const originalVideoScenePackages = artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId) || artifact.videoScenePackages;
    const displayVideoScenePackages = {
      ...artifact.videoScenePackages,
      scene_packages: scenePackagesWithoutRevisionContract(artifact.videoScenePackages.scene_packages as ScenePackageRecord[]) as typeof artifact.videoScenePackages.scene_packages,
    };
    if (!regenerated.ok) {
      releaseArtifactAction(processedKey);
      pushArtifact("视频修改重生成失败：部分受影响场景生成失败，请展开失败场景查看原因。", {
        type: "video_result",
        title: "视频修改结果",
        description: regenerated.message || "受影响场景重生成失败，请查看失败场景。",
        actionLabel: "查看",
        videoScenePackages: displayVideoScenePackages,
        originalVideoScenePackages,
        videoScenePackageEditedSceneIds: artifact.videoScenePackageEditedSceneIds || pendingVideoJob.affected_scene_ids || [],
        generatedSceneVideos: regenerated,
        mergedVideo: artifact.mergedVideo,
        intent: "video",
        formValues: artifact.formValues,
        intakeContext: artifact.intakeContext,
        materials: artifact.materials || [],
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
      }, targetConversationId);
      await clearPendingVideoJob(targetConversationId, isQuotaInsufficientPayload(regenerated) ? "video_regeneration_quota_paused" : "video_regeneration_failed", {
        video_revision_feedback: artifact.videoRevisionFeedback,
        video_revision_use_quality_review: pendingVideoJob.use_quality_review,
        affected_scene_ids: pendingVideoJob.affected_scene_ids || [],
        global_assets: artifact.videoScenePackages.global_assets,
        scene_packages: artifact.videoScenePackages.scene_packages,
        generated_scene_videos: artifact.generatedSceneVideos.scene_videos,
        failed_scenes: regenerated.failed_scenes,
      }).catch(() => {});
      return;
    }

    const nextSceneVideos = artifact.videoScenePackages.scene_packages
      .map((scene) =>
        sceneVideoForPackageScene(scene, regenerated.scene_videos) ||
        sceneVideoForPackageScene(scene, previousGeneratedSceneVideos.scene_videos),
      )
      .filter((scene): scene is NonNullable<typeof scene> => Boolean(scene));
    const generatedSceneVideos = {
      ...artifact.generatedSceneVideos,
      ok: regenerated.ok,
      scene_videos: nextSceneVideos,
      message: "已按修改意见更新受影响场景。",
    };
    await startAndResumeVideoMergeJob({
      targetConversationId,
      sourceMessageId: pendingVideoJob.source_message_id,
      artifact: {
        ...artifact,
        videoScenePackages: displayVideoScenePackages,
        generatedSceneVideos,
        videoScenePackageEditedSceneIds: [],
      },
      videoScenePackages: displayVideoScenePackages,
      generatedSceneVideos,
      originalVideoScenePackages,
      processedKey,
      mergePurpose: "regeneration",
      affectedSceneIds: pendingVideoJob.affected_scene_ids || [],
    });
  };

  const handleCompletedFailedSceneRetryJob = async (
    pendingVideoJob: PendingVideoJob,
    retried: NonNullable<ChatArtifact["generatedSceneVideos"]>,
    processedKey: string,
  ) => {
    const targetConversationId = pendingVideoJob.conversation_id;
    const artifact = pendingVideoJob.artifact;
    if (!artifact.videoScenePackages || !artifact.generatedSceneVideos) return;
    const previousGeneratedSceneVideos = artifact.generatedSceneVideos;
    const nextSceneVideos = artifact.videoScenePackages.scene_packages
      .map((scene) =>
        sceneVideoForPackageScene(scene, retried.scene_videos) ||
        sceneVideoForPackageScene(scene, previousGeneratedSceneVideos.scene_videos),
      )
      .filter((scene): scene is NonNullable<typeof scene> => Boolean(scene));
    const generatedSceneVideos = {
      ...retried,
      scene_videos: nextSceneVideos,
      failed_scenes: retried.failed_scenes || [],
    };

    if (!retried.ok) {
      const videoQuotaInsufficient = isQuotaInsufficientPayload(retried);
      releaseArtifactAction(processedKey);
      pushArtifact("视频生成失败：部分场景视频生成失败，请展开失败场景查看原因。", {
        type: "video_result",
        title: "视频生成结果",
        description: videoQuotaInsufficient ? quotaMessage(retried.message || "场景视频生成额度不足。") : (retried.message || "部分场景视频生成失败，请查看失败场景。"),
        actionLabel: "查看",
        videoScenePackages: artifact.videoScenePackages,
        generatedSceneVideos,
        intent: "video",
        formValues: artifact.formValues,
        intakeContext: artifact.intakeContext,
        materials: artifact.materials || [],
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
      }, targetConversationId);
      await clearPendingVideoJob(targetConversationId, videoQuotaInsufficient ? "video_generation_quota_paused" : "video_generation_failed", {
        affected_scene_ids: pendingVideoJob.affected_scene_ids || [],
        global_assets: artifact.videoScenePackages.global_assets,
        intake_context: artifact.intakeContext,
        scene_packages: artifact.videoScenePackages.scene_packages,
        generated_scene_videos: nextSceneVideos,
        failed_scenes: retried.failed_scenes,
        video_quota_insufficient: videoQuotaInsufficient,
      }).catch(() => {});
      return;
    }

    pushAssistant("失败分镜已补齐，正在按场景顺序合并完整视频…", targetConversationId);
    await startAndResumeVideoMergeJob({
      targetConversationId,
      sourceMessageId: pendingVideoJob.source_message_id,
      artifact: { ...artifact, generatedSceneVideos },
      videoScenePackages: artifact.videoScenePackages,
      generatedSceneVideos,
      originalVideoScenePackages: artifact.originalVideoScenePackages,
      processedKey,
      mergePurpose: "generation",
      affectedSceneIds: pendingVideoJob.affected_scene_ids || [],
    });
  };

  const resumePendingVideoJob = async (pendingVideoJob: PendingVideoJob, processedKey = "") => {
    const pollKey = `${pendingVideoJob.conversation_id}:${pendingVideoJob.job_id}`;
    if (activeVideoJobPollsRef.current.has(pollKey)) return;
    activeVideoJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingVideoJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingVideoJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      if (pendingVideoJob.kind === "video_merge") {
        const status = await api.getMergeSceneVideosJob(pendingVideoJob.job_id);
        if (stopIfHidden()) return;
        const mergedVideo =
          status.status === "completed" && status.result
            ? status.result
            : await api.pollMergeSceneVideoJob(pendingVideoJob.job_id, shouldContinuePolling);
        if (!mergedVideo || stopIfHidden()) return;
        await handleCompletedVideoMergeJob(pendingVideoJob, mergedVideo, processedKey);
        return;
      }
      const status = await api.getSceneVideosJob(pendingVideoJob.job_id);
      if (stopIfHidden()) return;
      const generatedSceneVideos =
        status.status === "completed" && status.result
          ? status.result
          : await api.pollSceneVideoJob(pendingVideoJob.job_id, shouldContinuePolling);
      if (!generatedSceneVideos || stopIfHidden()) return;
      if (pendingVideoJob.kind === "scene_regeneration") {
        await handleCompletedSceneRegenerationJob(pendingVideoJob, generatedSceneVideos, processedKey);
      } else if (pendingVideoJob.kind === "scene_failed_retry") {
        await handleCompletedFailedSceneRetryJob(pendingVideoJob, generatedSceneVideos, processedKey);
      } else {
        await handleCompletedSceneGenerationJob(pendingVideoJob, generatedSceneVideos, processedKey);
      }
    } catch (err) {
      if (stopIfHidden()) return;
      releaseArtifactAction(processedKey);
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的视频生成任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从当前场景包手动重新生成。"
          : `继续查询视频生成任务失败:${message}`,
        pendingVideoJob.conversation_id,
      );
      await clearPendingVideoJob(pendingVideoJob.conversation_id, "video_job_resume_failed", {
        video_job_resume_error: message,
      }).catch(() => {});
    } finally {
      if (pausedForHiddenConversation) releaseArtifactAction(processedKey);
      activeVideoJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingVideoJob.conversation_id, false);
    }
  };

  const jianyingDraftArtifactMessageId = (pendingJob: PendingJianyingDraftJob) =>
    `jianying-draft:${pendingJob.source_message_id}:${pendingJob.storyboard_version_id}:${pendingJob.job_id}`;

  const completeJianyingDraftJob = async (
    pendingJob: PendingJianyingDraftJob,
    result: JianyingDraftJobResponse,
  ) => {
    const targetConversationId = pendingJob.conversation_id;
    const safeResult: JianyingDraftJobResponse = result.status === "succeeded" && !isJianyingDraftSucceededResultValid(result)
      ? {
          ...result,
          status: "failed",
          provider_task_id: null,
          download_url: null,
          file_name: null,
          expire_at: null,
          message: "剪映草稿生成失败，请重新生成。",
        }
      : result;
    const boundResult: JianyingDraftJobResponse = {
      ...safeResult,
      job_id: pendingJob.job_id,
      conversation_id: targetConversationId,
      storyboard_version_id: pendingJob.storyboard_version_id,
    };
    const existingRecords = jianyingDraftRecordsForConversation(targetConversationId);
    const existingRecord = existingRecords[pendingJob.storyboard_version_id];
    const records: JianyingDraftRecordMap = {
      ...existingRecords,
      [pendingJob.storyboard_version_id]: boundResult,
    };
    setJianyingDraftRecordsForConversation(targetConversationId, records);

    if (boundResult.status === "not_configured") {
      pushAssistant("剪映草稿服务待接入", targetConversationId);
    } else if (!existingRecord || existingRecord.status !== boundResult.status || existingRecord.job_id !== boundResult.job_id) {
      const succeeded = boundResult.status === "succeeded";
      pushArtifact(
        succeeded ? "剪映草稿已生成，可在消息卡片中下载。" : `剪映草稿生成${boundResult.status === "timeout" ? "超时" : "失败"}，可从结果卡片重新生成。`,
        {
          type: "jianying_draft",
          title: succeeded ? "剪映草稿已生成" : "剪映草稿生成失败",
          description: boundResult.message || (succeeded ? "剪映草稿已生成。" : "剪映草稿生成失败。"),
          actionLabel: succeeded ? "下载" : "重新生成",
          jianyingDraft: boundResult,
          pendingJianyingDraftJob: pendingJob,
          jianyingDraftSceneCount: pendingJob.request.scenes.length,
        },
        targetConversationId,
        jianyingDraftArtifactMessageId(pendingJob),
      );
    }

    await persistPendingJianyingDraftJob(
      null,
      targetConversationId,
      `jianying_draft_${boundResult.status}`,
      pendingJob.job_id,
      { [pendingJob.storyboard_version_id]: boundResult },
    ).catch(() => {});
  };

  const clearExpiredJianyingDraftJob = async (pendingJob: PendingJianyingDraftJob, message: string) => {
    const targetConversationId = pendingJob.conversation_id;
    pushAssistant(message, targetConversationId);
    await persistPendingJianyingDraftJob(
      null,
      targetConversationId,
      "jianying_draft_job_expired",
      pendingJob.job_id,
      {},
      message,
    ).catch(() => {});
  };

  const resumePendingJianyingDraftJob = async (pendingJianyingDraftJob: PendingJianyingDraftJob) => {
    const pendingJob = pendingJianyingDraftJob;
    const targetConversationId = pendingJob.conversation_id;
    const pollKey = `${targetConversationId}:${pendingJob.job_id}`;
    if (activeJianyingDraftJobPollsRef.current.has(pollKey)) return;
    activeJianyingDraftJobPollsRef.current.add(pollKey);
    const shouldContinuePolling = () =>
      (typeof document === "undefined" || !document.hidden) && isVisibleConversation(targetConversationId);
    const timeoutResult = (): JianyingDraftJobResponse => ({
      status: "timeout",
      job_id: pendingJob.job_id,
      provider_task_id: null,
      conversation_id: targetConversationId,
      storyboard_version_id: pendingJob.storyboard_version_id,
      download_url: null,
      file_name: null,
      expire_at: null,
      message: "剪映草稿客户端轮询超时，请从结果卡片重新生成。",
    });
    const failedResult = (message: string): JianyingDraftJobResponse => ({
      ...timeoutResult(),
      status: "failed",
      message,
    });
    try {
      if (!shouldContinuePolling()) return;
      let capability: JianyingDraftCapability;
      try {
        capability = await api.getJianyingDraftCapability();
      } catch {
        await completeJianyingDraftJob(pendingJob, failedResult(jianyingDraftPublicErrorMessage("capability")));
        return;
      }
      setJianyingDraftCapability(capability);
      if (!shouldContinuePolling()) return;
      if (!capability.available) {
        await completeJianyingDraftJob(pendingJob, {
          ...failedResult("剪映草稿服务待接入"),
          status: "not_configured",
        });
        return;
      }
      const pollIntervalSeconds = Number(capability.poll_interval_seconds);
      if (!Number.isFinite(pollIntervalSeconds) || pollIntervalSeconds <= 0) {
        await completeJianyingDraftJob(pendingJob, failedResult("剪映草稿服务未返回有效轮询间隔。"));
        return;
      }
      const pollIntervalMs = pollIntervalSeconds * 1000;
      let retryCount = 0;
      while (shouldContinuePolling()) {
        const startedAt = Date.parse(pendingJob.started_at);
        if (!Number.isFinite(startedAt) || Date.now() - startedAt >= JIANYING_DRAFT_CLIENT_TIMEOUT_MS) {
          await completeJianyingDraftJob(pendingJob, timeoutResult());
          return;
        }
        try {
          const result = await api.getJianyingDraftJob(pendingJob.job_id);
          if (!shouldContinuePolling()) return;
          retryCount = 0;
          if (result.status === "succeeded" || result.status === "failed" || result.status === "timeout" || result.status === "not_configured") {
            await completeJianyingDraftJob(pendingJob, result);
            return;
          }
        } catch (err) {
          if (!shouldContinuePolling()) return;
          if (err instanceof ApiError && err.status === 404) {
            await clearExpiredJianyingDraftJob(
              pendingJob,
              "之前的剪映草稿任务不存在或已过期。为避免重复创建，我没有自动重启任务，请从视频结果卡片手动重试。",
            );
            return;
          }
          retryCount += 1;
          if (retryCount >= 3) {
            await completeJianyingDraftJob(pendingJob, failedResult(jianyingDraftPublicErrorMessage("poll")));
            return;
          }
        }
        await new Promise<void>((resolve) => window.setTimeout(resolve, pollIntervalMs));
      }
    } finally {
      activeJianyingDraftJobPollsRef.current.delete(pollKey);
    }
  };

  const resumePendingImageJob = async (pendingImageJob: PendingImageJob, processedKey = "") => {
    const pollKey = `${pendingImageJob.conversation_id}:${pendingImageJob.job_api}:${pendingImageJob.job_id}`;
    if (activeImageJobPollsRef.current.has(pollKey)) return;
    activeImageJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingImageJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingImageJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      if (pendingImageJob.job_api === "edit_asset") {
        const status = await api.getImageAssetEditJob(pendingImageJob.job_id);
        if (stopIfHidden()) return;
        const editResult =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollImageAssetEditJob(pendingImageJob.job_id, shouldContinuePolling);
        if (!editResult || stopIfHidden()) return;
        await handleCompletedImageAssetEditJob(pendingImageJob, editResult, processedKey);
      } else if (pendingImageJob.job_api === "fuse_asset") {
        const status = await api.getImageAssetFusionJob(pendingImageJob.job_id);
        if (stopIfHidden()) return;
        const fusionResult =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollImageAssetFusionJob(pendingImageJob.job_id, shouldContinuePolling);
        if (!fusionResult || stopIfHidden()) return;
        await handleCompletedImageAssetEditJob(pendingImageJob, fusionResult, processedKey);
      } else {
        const status = await api.getImageGenerationJob(pendingImageJob.job_id);
        if (stopIfHidden()) return;
        const imageResult =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollImageGenerationJob(pendingImageJob.job_id, shouldContinuePolling);
        if (!imageResult || stopIfHidden()) return;
        await handleCompletedImageGenerationJob(pendingImageJob, imageResult, processedKey);
      }
    } catch (err) {
      if (stopIfHidden()) return;
      releaseArtifactAction(processedKey);
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的图片生成任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从最新图片卡片手动重试。"
          : `继续查询图片生成任务失败:${message}`,
        pendingImageJob.conversation_id,
      );
      await clearPendingImageJob(pendingImageJob.conversation_id, "image_job_resume_failed", {
        image_job_resume_error: message,
      }).catch(() => {});
    } finally {
      if (pausedForHiddenConversation) releaseArtifactAction(processedKey);
      activeImageJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingImageJob.conversation_id, false);
    }
  };

  const resumePendingScenePackageJob = async (pendingScenePackageJob: PendingScenePackageJob, processedKey = "") => {
    const pollKey = `${pendingScenePackageJob.conversation_id}:${pendingScenePackageJob.kind}:${pendingScenePackageJob.job_id}`;
    if (activeScenePackageJobPollsRef.current.has(pollKey)) return;
    activeScenePackageJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingScenePackageJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingScenePackageJob.conversation_id, true);
    let lastAssetProgressCompleted = -1;
    let earlyScenePackageCardShown = false;
    const syncScenePackageStage = async (status: PrepareScenePackagesJobStatusResponse) => {
      const previousStage = workflowProgressConversationIdRef.current === pendingScenePackageJob.conversation_id
        ? workflowProgressRef.current?.scene_package_stage
        : null;
      if (pendingScenePackageJob.kind === "scene_package_generation") {
        setAssetPackageProgressSteps((current) => {
          const base = current.length > 0 ? current : createAssetPackageProgressSteps();
          if (status.status === "failed") {
            return base.map((step) => (
              step.id === "packages" || step.status === "running"
                ? { ...step, status: "failed" as const, detail: status.error || "场景包生成失败" }
                : step
            ));
          }
          const stage = (status.status === "completed" || status.status === "quota_paused")
            && status.stage !== "awaiting_image_model"
            ? "completed"
            : status.stage;
          let next = applyAssetPackageJobStage(base, stage);
          if (status.stage === "generate_scene_assets" && status.asset_progress) {
            next = applyAssetPackageAssetProgress(next, status.asset_progress);
          }
          return next;
        });
        const packages = status.result?.videoScenePackages;
        if (
          packages?.ok
          && status.stage !== "awaiting_image_model"
          && (
            status.stage === "generate_scene_assets"
            || status.status === "completed"
            || status.status === "quota_paused"
          )
        ) {
          const generating = status.status === "running" && status.stage === "generate_scene_assets";
          const progressTick = Boolean(
            status.asset_progress
            && status.asset_progress.total > 0
            && status.asset_progress.completed > lastAssetProgressCompleted,
          );
          const stageEntered = previousStage !== status.stage && status.stage === "generate_scene_assets";
          if (generating && (!earlyScenePackageCardShown || progressTick || stageEntered)) {
            upsertEarlyScenePackageCard(pendingScenePackageJob, packages, {
              generating: true,
              sceneAssetFailures: status.result?.sceneAssetFailures || [],
            });
            earlyScenePackageCardShown = true;
          }
        }
        if (
          status.asset_progress
          && status.asset_progress.total > 0
          && status.asset_progress.completed > lastAssetProgressCompleted
        ) {
          lastAssetProgressCompleted = status.asset_progress.completed;
          pushSceneAssetProgressTip(pendingScenePackageJob, status.asset_progress);
        }
      }
      advanceWorkflowProgress(
        pendingScenePackageJob.conversation_id,
        pendingScenePackageJob.kind === "scene_asset_generation"
          ? "scene_asset_generation_running"
          : pendingScenePackageJob.kind === "scene_asset_revision"
            ? "scene_asset_revision_running"
            : "scene_package_generation_running",
        {
          intent: "video",
          scene_package_stage: status.stage,
        },
      );
      if (status.status === "running" && status.stage === "generate_scene_assets" && previousStage !== status.stage) {
        await updateConversationWithProgress(
          pendingScenePackageJob.conversation_id,
          {
            last_phase: "scene_package_generation_running",
            context: {
              ...makeSnapshot(pendingScenePackageJob.conversation_id),
              pendingScenePackageJob,
              pending_scene_package_job: pendingScenePackageJob,
            } as unknown as Record<string, unknown>,
          },
          { intent: "video", scene_package_stage: status.stage },
        );
      }
    };
    try {
      if (stopIfHidden()) return;
      if (pendingScenePackageJob.kind === "scene_asset_revision") {
        const request = pendingScenePackageJob.request as ScenePackageAssetRevisionRequest;
        const status = await api.getScenePackageAssetRevisionJob(pendingScenePackageJob.job_id);
        if (stopIfHidden()) return;
        const revision =
          status.status === "completed" && status.result
            ? status.result
            : await api.pollScenePackageAssetRevisionJob(pendingScenePackageJob.job_id, shouldContinuePolling);
        if (!revision || stopIfHidden()) return;
        await handleCompletedSceneAssetRevisionJob(
          pendingScenePackageJob,
          {
            ...revision,
            operation: revision.asset_id ? revision.operation : request.operation,
            asset_id: revision.asset_id || request.asset_id,
            asset_group: revision.asset_id ? revision.asset_group : request.asset_group,
          },
          processedKey,
        );
      } else if (pendingScenePackageJob.kind === "scene_asset_generation") {
        const syncSceneAssetGeneration = async (
          status: {
            status: string;
            stage?: string;
            result?: GenerateSceneAssetsResponse | null;
            asset_progress?: PrepareScenePackagesJobStatusResponse["asset_progress"];
            error?: string | null;
          },
        ) => {
          setAssetPackageProgressSteps((current) => {
            const base = current.length > 0 ? current : createAssetPackageProgressSteps();
            if (status.status === "failed") {
              return base.map((step) => (
                step.id === "assets" || step.status === "running"
                  ? { ...step, status: "failed" as const, detail: status.error || "场景参考图生成失败" }
                  : step
              ));
            }
            const stage = status.status === "completed" || status.status === "quota_paused"
              ? "completed"
              : "generate_scene_assets";
            let next = applyAssetPackageJobStage(base, stage);
            if (status.status === "running" && status.asset_progress) {
              next = applyAssetPackageAssetProgress(next, status.asset_progress);
            }
            return next;
          });
          const packages = pendingScenePackageJob.artifact.videoScenePackages;
          if (packages?.ok && status.status === "running") {
            const mergedPackages: PrepareScenePackagesResponse = {
              ...packages,
              global_assets: status.result?.global_assets || packages.global_assets,
              scene_packages: status.result?.scene_packages?.length
                ? status.result.scene_packages
                : packages.scene_packages,
            };
            const progressTick = Boolean(
              status.asset_progress
              && status.asset_progress.total > 0
              && status.asset_progress.completed > lastAssetProgressCompleted,
            );
            if (!earlyScenePackageCardShown || progressTick) {
              upsertEarlyScenePackageCard(pendingScenePackageJob, mergedPackages, {
                generating: true,
                sceneAssetFailures: status.result?.failed_assets || [],
              });
              earlyScenePackageCardShown = true;
            }
          }
          if (
            status.asset_progress
            && status.asset_progress.total > 0
            && status.asset_progress.completed > lastAssetProgressCompleted
          ) {
            lastAssetProgressCompleted = status.asset_progress.completed;
            pushSceneAssetProgressTip(pendingScenePackageJob, status.asset_progress);
          }
          advanceWorkflowProgress(pendingScenePackageJob.conversation_id, "scene_asset_generation_running", {
            intent: "video",
            scene_package_stage: status.stage || "generate_scene_assets",
          });
        };
        const status = await api.getSceneAssetsJob(pendingScenePackageJob.job_id);
        await syncSceneAssetGeneration(status);
        if (stopIfHidden()) return;
        const sceneAssets =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollSceneAssetsJob(
              pendingScenePackageJob.job_id,
              shouldContinuePolling,
              syncSceneAssetGeneration,
            );
        if (!sceneAssets || stopIfHidden()) return;
        await handleCompletedSceneAssetJob(pendingScenePackageJob, sceneAssets, processedKey);
      } else {
        const status = await api.getPrepareScenePackagesJob(pendingScenePackageJob.job_id);
        await syncScenePackageStage(status);
        if (stopIfHidden()) return;
        let completedStage = status.stage || "completed";
        let result: PrepareScenePackagesJobResult | null = null;
        if ((status.status === "completed" || status.status === "quota_paused") && status.result) {
          result = status.result;
        } else {
          result = await api.pollPrepareScenePackagesJob(
            pendingScenePackageJob.job_id,
            shouldContinuePolling,
            async (nextStatus) => {
              if (nextStatus.status === "completed" || nextStatus.status === "quota_paused") {
                completedStage = nextStatus.stage || completedStage;
              }
              await syncScenePackageStage(nextStatus);
            },
          );
        }
        if (!result || stopIfHidden()) return;
        await handleCompletedScenePackageJob(pendingScenePackageJob, result, processedKey, completedStage);
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      const message = err instanceof Error ? err.message : String(err);
      const action = classifyScenePackageJobResume(err);
      if (action === "retain_pending") {
        const nextAttempt = (pendingScenePackageJob.restart_count || 0) + 1;
        const nextPending: PendingScenePackageJob = {
          ...pendingScenePackageJob,
          restart_count: nextAttempt,
        };
        pendingScenePackageJobRef.current = nextPending;
        pushAssistant(
          `网络或认证暂时不可用，已保留场景包任务，${Math.round(scenePackageJobResumeDelayMs(nextAttempt) / 1000)} 秒后自动继续查询…`,
          pendingScenePackageJob.conversation_id,
        );
        window.setTimeout(() => {
          const current = pendingScenePackageJobRef.current;
          if (!current || current.job_id !== nextPending.job_id) return;
          if (!isVisibleConversation(current.conversation_id)) return;
          void resumePendingScenePackageJob(current).catch(() => {});
        }, scenePackageJobResumeDelayMs(nextAttempt));
        return;
      }
      pushAssistant(
        action === "clear_not_found"
          ? "之前的场景包、参考图或素材修订任务不存在或已过期。为避免重复生成，我没有自动重启，请从最新 plan 或场景包卡片手动重试。"
          : `继续查询场景包生成任务失败:${message}`,
        pendingScenePackageJob.conversation_id,
      );
      await clearPendingScenePackageJob(pendingScenePackageJob.conversation_id, "scene_package_job_resume_failed", {
        scene_package_job_resume_error: message,
      }).catch(() => {});
    } finally {
      if (pausedForHiddenConversation) releaseArtifactAction(processedKey);
      activeScenePackageJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingScenePackageJob.conversation_id, false);
    }
  };

  const pollPptJobResult = async <T extends Record<string, unknown>>(
    pendingPptJob: PendingPptJob,
    onStatus?: (status: PptJobStatusResponse) => void,
    shouldContinue: () => boolean = () => true,
  ): Promise<T | null> => {
    if (!shouldContinue()) return null;
    const status = await api.getPptJob(pendingPptJob.job_id);
    if (!shouldContinue()) return null;
    onStatus?.(status);
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result as T;
    if (status.status === "failed") {
      return {
        ok: false,
        message: status.error || status.message || "PPT 生成失败",
        error: status.error || status.message || "PPT 生成失败",
      } as unknown as T;
    }
    return api.pollPptJob<T>(pendingPptJob.job_id, onStatus, shouldContinue);
  };

  const completedPptLastPhase = (pendingPptJob: PendingPptJob, ok: boolean): string => {
    const failed = !ok;
    if (pendingPptJob.kind === "summary_generation") return failed ? "ppt_outline_failed" : "ppt_outline_review";
    if (pendingPptJob.kind === "summary_update") return failed ? "ppt_outline_update_failed" : "ppt_outline_updated";
    if (pendingPptJob.kind === "content_json_generation") return failed ? "ppt_content_json_failed" : "ppt_content_json_ready";
    if (pendingPptJob.kind === "image_generation" || pendingPptJob.kind === "image_regeneration") return failed ? "ppt_images_failed" : "ppt_images_ready";
    if (pendingPptJob.kind === "file_generation" || pendingPptJob.kind === "file_regeneration") return failed ? "ppt_file_failed" : "ppt_file_ready";
    return failed ? "ppt_job_failed" : "ppt_job_completed";
  };

  const findLatestPptImagesMessageId = (targetConversationId: string): string => {
    const message = [...messages]
      .reverse()
      .find((item) => messageConversationId(item, targetConversationId) === targetConversationId && item.artifact?.type === "ppt_images");
    return message?.id || "";
  };

  const handleCompletedPptSummaryJob = async (pendingPptJob: PendingPptJob, result: PptSummaryResult) => {
    const context = pendingPptJob.context;
    if (!context) {
      throw new Error("PPT 大纲任务缺少恢复上下文");
    }
    const targetConversationId = pendingPptJob.conversation_id;
    pushPptOutlineArtifact(result, context, targetConversationId);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, result.ok), {
      intent: "ppt",
      form_values: context.formValues,
      intake_context: context.intakeContext,
      materials: context.materials || [],
      ppt_summary: result,
      ppt_outline_feedback: pendingPptJob.kind === "summary_update"
        ? (pendingPptJob.request as PptSummaryUpdateJobRequest).modification_opinion
        : undefined,
    }).catch(() => {});
  };

  const startPptImagesFromContentJson = async (
    contentJson: PptContentJsonResult,
    sourceArtifact: ChatArtifact,
    pendingPptJob: PendingPptJob,
    processedKey = "",
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const projectId =
      numericValue(contentJson.smart_ppt_project_id)
      || numericValue((pendingPptJob.request as PptContentJsonJobRequest).smart_ppt_project_id)
      || pptProjectId(sourceArtifact);
    if (!projectId) {
      releaseArtifactAction(processedKey);
      pushAssistant("当前 PPT 项目 ID 缺失，无法继续生成页面图片。", targetConversationId);
      await clearPendingPptJob(targetConversationId, "ppt_images_failed", {
        intent: "ppt",
        ppt_content_json: contentJson,
      }).catch(() => {});
      return;
    }
    const imageArtifactSource = { ...sourceArtifact, pptContentJson: contentJson };
    const pendingImages = pendingPptImagesFromContentJson(contentJson, projectId);
    const imageMessage = pushPptImagesArtifact(pendingImages, contentJson, imageArtifactSource, targetConversationId);
    const request: PptImagesJobRequest = {
      content_json: contentJson.content_json || contentJson.pages || [],
      smart_ppt_project_id: projectId,
    };
    const started = await api.createPptImagesJob(request);
    const nextPendingPptJob: PendingPptJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: pendingPptJob.source_message_id,
      kind: "image_generation",
      started_at: new Date().toISOString(),
      request,
      artifact: imageArtifactSource,
      image_message_id: imageMessage.id,
    };
    await persistPendingPptJob(nextPendingPptJob, targetConversationId, "ppt_images_running", {
      intent: "ppt",
      ppt_content_json: contentJson,
      ppt_images: pendingImages,
    });
    await resumePendingPptJob(nextPendingPptJob, processedKey);
  };

  const handleCompletedPptContentJsonJob = async (
    pendingPptJob: PendingPptJob,
    contentJson: PptContentJsonResult,
    processedKey = "",
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const sourceArtifact = pendingPptJob.artifact;
    if (!sourceArtifact) {
      throw new Error("PPT 页面结构任务缺少来源卡片");
    }
    if (!contentJson.ok) {
      releaseArtifactAction(processedKey);
      const projectId = pptProjectId(sourceArtifact) || numericValue((pendingPptJob.request as PptContentJsonJobRequest).smart_ppt_project_id) || 0;
      const failedImages: PptImagesResult = {
        ok: false,
        smart_ppt_project_id: projectId || null,
        pages: [],
        message: String(contentJson.message || contentJson.error || "PPT 页面 JSON 生成失败。"),
        quota_insufficient: Boolean(contentJson.quota_insufficient),
      };
      pushPptImagesArtifact(failedImages, contentJson, sourceArtifact, targetConversationId);
      await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, false), {
        intent: "ppt",
        ppt_content_json: contentJson,
        ppt_images: failedImages,
      }).catch(() => {});
      return;
    }
    pushAssistant("PPT 页面结构已生成，正在并行生成每页 PPT 图片…", targetConversationId);
    await startPptImagesFromContentJson(contentJson, sourceArtifact, pendingPptJob, processedKey);
  };

  const handleCompletedPptImagesJob = async (
    pendingPptJob: PendingPptJob,
    pptImages: PptImagesResult,
    processedKey = "",
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const imageMessageId = pendingPptJob.image_message_id || findLatestPptImagesMessageId(targetConversationId);
    if (imageMessageId) updatePptImagesArtifactInMessage(imageMessageId, targetConversationId, pptImages, pendingPptJob.artifact);
    if (!pptImages.ok) releaseArtifactAction(processedKey);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, pptImages.ok), {
      intent: "ppt",
      ppt_content_json: pendingPptJob.artifact?.pptContentJson,
      ppt_images: pptImages,
    }).catch(() => {});
  };

  const handleCompletedPptImageRegenerationJob = async (
    pendingPptJob: PendingPptJob,
    result: Record<string, unknown>,
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const sourceArtifact = pendingPptJob.artifact;
    const pageIndex = (pendingPptJob.request as PptImageRegenerationJobRequest).page_index;
    if (!sourceArtifact?.pptImages) {
      throw new Error("PPT 单页重生任务缺少来源页面图片");
    }
    const nextPage = result.page as PptPageImage | undefined;
    const nextPages = sourceArtifact.pptImages.pages.map((page) => {
      if (page.page_index !== pageIndex) return page;
      if (!nextPage) {
        return {
          ...page,
          status: "failed",
          image_url: null,
          error: String(result.message || result.error || "本页图片重新生成失败。"),
        };
      }
      return {
        ...nextPage,
        page_index: pageIndex,
        json_content: nextPage.json_content || page.json_content || (pendingPptJob.request as PptImageRegenerationJobRequest).page_json,
        status: nextPage.status || (nextPage.image_url ? "completed" : "failed"),
      };
    });
    const nextImages: PptImagesResult = {
      ...sourceArtifact.pptImages,
      ok: nextPages.length > 0 && nextPages.every((page) => page.status === "completed" && Boolean(page.image_url)),
      pages: nextPages,
      message: String(result.message || "PPT 页面图片已重新生成。"),
      quota_insufficient: Boolean(result.quota_insufficient),
    };
    const imageMessageId = pendingPptJob.image_message_id || findLatestPptImagesMessageId(targetConversationId);
    if (imageMessageId) updatePptImagesArtifactInMessage(imageMessageId, targetConversationId, nextImages, sourceArtifact);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, nextImages.ok), {
      intent: "ppt",
      ppt_content_json: sourceArtifact.pptContentJson,
      ppt_images: nextImages,
    }).catch(() => {});
  };

  const handleCompletedPptFileJob = async (pendingPptJob: PendingPptJob, pptFile: PptFileResult, processedKey = "") => {
    const sourceArtifact = pendingPptJob.artifact;
    if (!sourceArtifact) {
      throw new Error("PPT 附件任务缺少来源卡片");
    }
    const targetConversationId = pendingPptJob.conversation_id;
    if (!pptFile.ok) releaseArtifactAction(processedKey);
    pushPptFileArtifact(pptFile, sourceArtifact, targetConversationId);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, pptFile.ok), {
      intent: "ppt",
      ppt_content_json: sourceArtifact.pptContentJson,
      ppt_images: sourceArtifact.pptImages,
      ppt_file: pptFile,
    }).catch(() => {});
  };

  const resumePendingPptJob = async (pendingPptJob: PendingPptJob, processedKey = "") => {
    const pollKey = `${pendingPptJob.conversation_id}:${pendingPptJob.kind}:${pendingPptJob.job_id}`;
    if (activePptJobPollsRef.current.has(pollKey)) return;
    activePptJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingPptJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingPptJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      if (pendingPptJob.kind === "summary_generation" || pendingPptJob.kind === "summary_update") {
        const result = await pollPptJobResult<PptSummaryResult>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptSummaryJob(pendingPptJob, result);
      } else if (pendingPptJob.kind === "content_json_generation") {
        const result = await pollPptJobResult<PptContentJsonResult>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptContentJsonJob(pendingPptJob, result, processedKey);
      } else if (pendingPptJob.kind === "image_generation") {
        const imageMessageId = pendingPptJob.image_message_id || findLatestPptImagesMessageId(pendingPptJob.conversation_id);
        const result = await pollPptJobResult<PptImagesResult>(pendingPptJob, (status) => {
          const partialImages = status.result as PptImagesResult | null;
          if (partialImages?.pages && imageMessageId) {
            updatePptImagesArtifactInMessage(imageMessageId, pendingPptJob.conversation_id, partialImages, pendingPptJob.artifact);
          }
        }, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptImagesJob(pendingPptJob, result, processedKey);
      } else if (pendingPptJob.kind === "image_regeneration") {
        const result = await pollPptJobResult<Record<string, unknown>>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptImageRegenerationJob(pendingPptJob, result);
      } else {
        const result = await pollPptJobResult<PptFileResult>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptFileJob(pendingPptJob, result, processedKey);
      }
    } catch (err) {
      if (stopIfHidden()) return;
      releaseArtifactAction(processedKey);
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的 PPT 生成任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从最新 PPT 卡片手动重试。"
          : `继续查询 PPT 生成任务失败:${message}`,
        pendingPptJob.conversation_id,
      );
      await clearPendingPptJob(pendingPptJob.conversation_id, "ppt_job_resume_failed", {
        ppt_job_resume_error: message,
      }).catch(() => {});
    } finally {
      if (pausedForHiddenConversation) releaseArtifactAction(processedKey);
      activePptJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingPptJob.conversation_id, false);
    }
  };

  const pushReviewArtifact = (phase: TaskPhase) => {
    const artifact = REVIEW_ARTIFACT[phase];
    if (!artifact) return;
    const key = `${phase}:artifact`;
    if (announcedPhasesRef.current.has(key)) return;
    announcedPhasesRef.current.add(key);
    pushArtifact(PHASE_MSG[phase] || "请在画布确认。", artifact);
  };

  const appendTimelineEvent = (event: TaskEvent) => {
    const entry = toTimelineEntry(event);
    if (!entry) return;
    setCanvas((c) => {
      const timeline = c.timeline || [];
      if (timeline.some((item) => item.id === entry.id)) return c;
      return { ...c, timeline: [...timeline, entry].slice(-80) };
    });
  };

  async function reconcileTaskFromServer(taskId: string) {
    try {
      const task = await api.getTask(taskId);
      setActiveTaskId(task.task_id);
      const phase = task.phase as TaskPhase;
      const confirmed = task.phase !== "brief_review";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase: phase || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
      }));

      if (["segment_review", "edit_review", "qc_review"].includes(task.phase)) {
        setCanvasOpen(true);
        await loadResults(phase);
        pushReviewArtifact(phase);
        if (PHASE_MSG[task.phase] && !announcedPhasesRef.current.has(task.phase)) {
          announcedPhasesRef.current.add(task.phase);
        }
        return;
      }

      if (task.phase === "brief_review") {
        setCanvasOpen(true);
        if (!announcedPhasesRef.current.has("brief_review")) {
          announcedPhasesRef.current.add("brief_review");
          pushAssistant(PHASE_MSG.brief_review);
        }
        return;
      }

      if (task.status === "done") {
        await loadResults("done");
        return;
      }

      if (task.status === "error") {
        pushAssistant(`生成失败:${task.error || "未知错误"}`);
        setBusy(false);
      }
    } catch {
      /* keep restored snapshot if server reconciliation fails */
    }
  }

  const applySnapshot = (snapshot: Partial<WorkspaceSnapshot>, targetConversationId: string) => {
    if (Array.isArray(snapshot.messages)) {
      messagesRef.current = snapshot.messages;
      setMessages(snapshot.messages);
    }
    setPendingMaterials(Array.isArray(snapshot.pendingMaterials) ? snapshot.pendingMaterials : []);
    setDialogOpen(false);
    setPendingFormValues({});
    setPendingCore("");
    pendingDialogContextRef.current = null;
    flowDraftRef.current = snapshot.flowDraft || null;
    pendingMessageJobRef.current = snapshot.pendingMessageJob || snapshot.pending_message_job || null;
    const rawRuntimeTurns = snapshot.pendingAgentRuntimeTurns
      || snapshot.pending_agent_runtime_turns;
    const restoredRuntimeTurns = (
      Array.isArray(rawRuntimeTurns) ? rawRuntimeTurns : []
    )
      .map((item) => parsePendingSupervisorTurn(item, targetConversationId))
      .filter((item): item is PendingSupervisorTurn => item !== null);
    pendingSupervisorTurnsRef.current = restoredRuntimeTurns;
    if (targetConversationId) {
      pendingSupervisorTurnsByConversationRef.current.set(
        targetConversationId,
        restoredRuntimeTurns,
      );
      const unavailableNoticeVersion = snapshot.agentRuntimeUnavailableNoticeVersion
        ?? snapshot.agent_runtime_unavailable_notice_version;
      if (
        typeof unavailableNoticeVersion === "number"
        && Number.isFinite(unavailableNoticeVersion)
        && unavailableNoticeVersion > 0
      ) {
        unavailableSupervisorNoticeVersionsRef.current.set(
          targetConversationId,
          unavailableNoticeVersion,
        );
      } else {
        unavailableSupervisorNoticeVersionsRef.current.delete(
          targetConversationId,
        );
      }
    }
    setPendingSupervisorTurns(restoredRuntimeTurns);
    pendingPlanMessagePersistenceIdsRef.current = new Set(
      pendingMessageJobRef.current?.continue_after_save?.type === "plan_save"
        ? [pendingMessageJobRef.current.source_message_id]
        : [],
    );
    pendingIntakeJobRef.current = snapshot.pendingIntakeJob || snapshot.pending_intake_job || null;
    pendingDirectionJobRef.current = snapshot.pendingDirectionJob || snapshot.pending_direction_job || null;
    pendingPlanJobRef.current = snapshot.pendingPlanJob || snapshot.pending_plan_job || null;
    planRevisionArtifactRef.current = snapshot.pendingPlanRevisionRequest || snapshot.pending_plan_revision_request || null;
    const restoredPlanRevisionChoice = snapshot.pendingPlanRevisionChoice || snapshot.pending_plan_revision_choice || null;
    setPendingPlanRevisionChoice(restoredPlanRevisionChoice);
    setAgentRevisionSourceMessageId(
      planRevisionArtifactRef.current?.sourceMessageId
        || restoredPlanRevisionChoice?.sourceMessageId
        || (pendingPlanJobRef.current?.kind === "plan_revision" ? pendingPlanJobRef.current.source_message_id : ""),
    );
    pendingImageEditRequestRef.current = snapshot.pendingImageEditRequest || null;
    imageEditConfirmedSelectionsRef.current = snapshot.imageEditConfirmedSelections || {};
    pendingImageJobRef.current = snapshot.pendingImageJob || snapshot.pending_image_job || null;
    imageRevisionArtifactRef.current = snapshot.pendingImageRevision || snapshot.pending_image_revision || null;
    pptOutlineRevisionArtifactRef.current = snapshot.pendingPptOutlineRevision || snapshot.pending_ppt_outline_revision || null;
    pendingScenePackageJobRef.current = snapshot.pendingScenePackageJob || snapshot.pending_scene_package_job || null;
    setPendingScenePackageResumeVersion((version) => version + 1);
    pendingVideoJobRef.current = snapshot.pendingVideoJob || snapshot.pending_video_job || null;
    videoRevisionArtifactRef.current = snapshot.pendingVideoRevision || snapshot.pending_video_revision || null;
    pendingPptJobRef.current = snapshot.pendingPptJob || snapshot.pending_ppt_job || null;
    const pendingJianyingDraftJob = snapshot.pendingJianyingDraftJob || snapshot.pending_jianying_draft_job || null;
    pendingJianyingDraftJobRef.current =
      pendingJianyingDraftJob?.conversation_id === targetConversationId ? pendingJianyingDraftJob : null;
    setJianyingDraftRecordsForConversation(
      targetConversationId,
      snapshot.jianyingDraftRecords || snapshot.jianying_draft_records || {},
    );
    const restoredWorkflowProgress = snapshot.workflowProgress || snapshot.workflow_progress || null;
    workflowProgressConversationIdRef.current = targetConversationId;
    workflowProgressRef.current = restoredWorkflowProgress;
    setWorkflowProgress(restoredWorkflowProgress);
    const restoredPlanAnchors = snapshot.videoAgentPlanAnchors || snapshot.video_agent_plan_anchors;
    if (restoredPlanAnchors && typeof restoredPlanAnchors === "object") {
      const next = Object.fromEntries(
        Object.entries(restoredPlanAnchors).filter(
          (entry): entry is [string, string] => typeof entry[0] === "string" && typeof entry[1] === "string",
        ),
      );
      videoAgentPlanAnchorsRef.current = next;
      setVideoAgentPlanAnchors(next);
    }
    const restoredAssetPackageAnchor = String(
      snapshot.assetPackageAnchorMessageId
      || snapshot.asset_package_anchor_message_id
      || "",
    ).trim();
    if (restoredAssetPackageAnchor) {
      assetPackageAnchorMessageIdRef.current = restoredAssetPackageAnchor;
      setAssetPackageAnchorMessageId(restoredAssetPackageAnchor);
    }
    setPptDoneForConversation(targetConversationId, snapshot.ppt_done === true);
    setReferencedMaterials([]);
    if (snapshot.canvas) setCanvas(snapshot.canvas);
    if (typeof snapshot.canvasOpen === "boolean") setCanvasOpen(snapshot.canvasOpen);
    if (typeof snapshot.briefConfirmed === "boolean") {
      setBriefConfirmed(snapshot.briefConfirmed);
      briefConfirmedRef.current = snapshot.briefConfirmed;
    }
    if (snapshot.taskId) setActiveTaskId(snapshot.taskId);
    if (typeof snapshot.lastEventId === "number") {
      lastEventIdRef.current = snapshot.lastEventId;
      seenEventIdsRef.current = new Set(Array.from({ length: snapshot.lastEventId }, (_, i) => i + 1));
    }
    if (Array.isArray(snapshot.announcedPhases)) announcedPhasesRef.current = new Set(snapshot.announcedPhases);
    if (typeof snapshot.briefReadyShown === "boolean") briefReadyShownRef.current = snapshot.briefReadyShown;
  };

  const makeSnapshot = (snapshotConversationId = currentConversationId): WorkspaceSnapshot => {
    const scenePackageSnapshot = latestScenePackageSnapshotForConversation(messagesRef.current, snapshotConversationId);
    const activePlanSnapshot = activePlanSnapshotForConversation(
      messagesRef.current,
      snapshotConversationId,
      pendingPlanMessagePersistenceIdsRef.current,
    );
    return {
      taskId: currentTaskId,
      pendingMaterials,
      flowDraft: flowDraftRef.current,
      pendingMessageJob:
        pendingMessageJobRef.current?.conversation_id === snapshotConversationId ? pendingMessageJobRef.current : null,
      pending_message_job:
        pendingMessageJobRef.current?.conversation_id === snapshotConversationId ? pendingMessageJobRef.current : null,
      pendingAgentRuntimeTurns: pendingSupervisorTurnsRef.current.filter(
        (item) => item.conversationId === snapshotConversationId,
      ),
      pending_agent_runtime_turns: pendingSupervisorTurnsRef.current.filter(
        (item) => item.conversationId === snapshotConversationId,
      ),
      agentRuntimeUnavailableNoticeVersion:
        unavailableSupervisorNoticeVersionsRef.current.get(snapshotConversationId),
      agent_runtime_unavailable_notice_version:
        unavailableSupervisorNoticeVersionsRef.current.get(snapshotConversationId),
      pendingIntakeJob:
        pendingIntakeJobRef.current?.conversation_id === snapshotConversationId ? pendingIntakeJobRef.current : null,
      pending_intake_job:
        pendingIntakeJobRef.current?.conversation_id === snapshotConversationId ? pendingIntakeJobRef.current : null,
      pendingDirectionJob:
        pendingDirectionJobRef.current?.conversation_id === snapshotConversationId ? pendingDirectionJobRef.current : null,
      pending_direction_job:
        pendingDirectionJobRef.current?.conversation_id === snapshotConversationId ? pendingDirectionJobRef.current : null,
      pendingPlanJob:
        pendingPlanJobRef.current?.conversation_id === snapshotConversationId ? pendingPlanJobRef.current : null,
      pending_plan_job:
        pendingPlanJobRef.current?.conversation_id === snapshotConversationId ? pendingPlanJobRef.current : null,
      pendingPlanRevisionRequest:
        planRevisionArtifactRef.current?.conversationId === snapshotConversationId ? planRevisionArtifactRef.current : null,
      pending_plan_revision_request:
        planRevisionArtifactRef.current?.conversationId === snapshotConversationId ? planRevisionArtifactRef.current : null,
      pendingPlanRevisionChoice:
        pendingPlanRevisionChoice?.conversationId === snapshotConversationId ? pendingPlanRevisionChoice : null,
      pending_plan_revision_choice:
        pendingPlanRevisionChoice?.conversationId === snapshotConversationId ? pendingPlanRevisionChoice : null,
      pendingImageEditRequest:
        pendingImageEditRequestRef.current?.conversationId === snapshotConversationId ? pendingImageEditRequestRef.current : null,
      imageEditConfirmedSelections: imageEditConfirmedSelectionsRef.current,
      pendingImageJob:
        pendingImageJobRef.current?.conversation_id === snapshotConversationId ? pendingImageJobRef.current : null,
      pending_image_job:
        pendingImageJobRef.current?.conversation_id === snapshotConversationId ? pendingImageJobRef.current : null,
      pendingImageRevision:
        imageRevisionArtifactRef.current?.conversationId === snapshotConversationId ? imageRevisionArtifactRef.current : null,
      pending_image_revision:
        imageRevisionArtifactRef.current?.conversationId === snapshotConversationId ? imageRevisionArtifactRef.current : null,
      pendingPptOutlineRevision:
        pptOutlineRevisionArtifactRef.current?.conversationId === snapshotConversationId ? pptOutlineRevisionArtifactRef.current : null,
      pending_ppt_outline_revision:
        pptOutlineRevisionArtifactRef.current?.conversationId === snapshotConversationId ? pptOutlineRevisionArtifactRef.current : null,
      pendingScenePackageJob:
        pendingScenePackageJobRef.current?.conversation_id === snapshotConversationId ? pendingScenePackageJobRef.current : null,
      pending_scene_package_job:
        pendingScenePackageJobRef.current?.conversation_id === snapshotConversationId ? pendingScenePackageJobRef.current : null,
      pendingVideoJob:
        pendingVideoJobRef.current?.conversation_id === snapshotConversationId ? pendingVideoJobRef.current : null,
      pending_video_job:
        pendingVideoJobRef.current?.conversation_id === snapshotConversationId ? pendingVideoJobRef.current : null,
      pendingVideoRevision:
        videoRevisionArtifactRef.current?.conversationId === snapshotConversationId ? videoRevisionArtifactRef.current : null,
      pending_video_revision:
        videoRevisionArtifactRef.current?.conversationId === snapshotConversationId ? videoRevisionArtifactRef.current : null,
      pendingPptJob:
        pendingPptJobRef.current?.conversation_id === snapshotConversationId ? pendingPptJobRef.current : null,
      pending_ppt_job:
        pendingPptJobRef.current?.conversation_id === snapshotConversationId ? pendingPptJobRef.current : null,
      pendingJianyingDraftJob:
        pendingJianyingDraftJobRef.current?.conversation_id === snapshotConversationId ? pendingJianyingDraftJobRef.current : null,
      pending_jianying_draft_job:
        pendingJianyingDraftJobRef.current?.conversation_id === snapshotConversationId ? pendingJianyingDraftJobRef.current : null,
      jianyingDraftRecords: jianyingDraftRecordsForConversation(snapshotConversationId),
      jianying_draft_records: jianyingDraftRecordsForConversation(snapshotConversationId),
      ppt_done: isPptDoneForConversation(snapshotConversationId),
      image_accepted: latestImageResultArtifactForConversation(messagesRef.current, snapshotConversationId)?.imageAccepted === true,
      video_accepted: latestVideoResultArtifactForConversation(messagesRef.current, snapshotConversationId)?.videoAccepted === true,
      canvas,
      canvasOpen,
      briefConfirmed,
      lastEventId: lastEventIdRef.current,
      announcedPhases: Array.from(announcedPhasesRef.current),
      briefReadyShown: briefReadyShownRef.current,
      workflowProgress:
        workflowProgressConversationIdRef.current === snapshotConversationId ? workflowProgressRef.current : null,
      workflow_progress:
        workflowProgressConversationIdRef.current === snapshotConversationId ? workflowProgressRef.current : null,
      videoAgentPlanAnchors: videoAgentPlanAnchorsRef.current,
      video_agent_plan_anchors: videoAgentPlanAnchorsRef.current,
      assetPackageAnchorMessageId: assetPackageAnchorMessageIdRef.current || undefined,
      asset_package_anchor_message_id: assetPackageAnchorMessageIdRef.current || undefined,
      ...activePlanSnapshot,
      ...scenePackageSnapshot,
    };
  };

  const resetWorkspace = () => {
    unsubRef.current();
    setActiveConversationId("");
    setActiveTaskId("");
    setMessages([]);
    setCanvas(EMPTY_CANVAS);
    setCanvasOpen(false);
    setSelectedStoryboardMessageId("");
    setDialogOpen(false);
    setPendingCore("");
    setPendingIntent("video");
    setPendingFormValues({});
    setPendingMaterials([]);
    setReferencedMaterials([]);
    setComposerPrefillRequest(null);
    setBusy(false);
    setResolvedOrchestrationMode("frontend_v2");
    setResolvedAgentRuntimeMode("off");
    primaryExecutionReadyRef.current = false;
    pendingSupervisorTurnsRef.current = [];
    unavailableSupervisorNoticeVersionsRef.current = new Map();
    unavailableSupervisorRecoveryInFlightRef.current = new Set();
    setPendingSupervisorTurns([]);
    setBriefConfirmed(false);
    briefConfirmedRef.current = false;
    seenEventIdsRef.current = new Set();
    announcedPhasesRef.current = new Set();
    processedArtifactIdsRef.current = new Set();
    pendingDialogContextRef.current = null;
    flowDraftRef.current = null;
    pendingMessageJobRef.current = null;
    pendingPlanMessagePersistenceIdsRef.current = new Set();
    pendingIntakeJobRef.current = null;
    pendingDirectionJobRef.current = null;
    pendingPlanJobRef.current = null;
    pendingImageEditRequestRef.current = null;
    imageEditConfirmedSelectionsRef.current = {};
    pendingImageJobRef.current = null;
    pendingScenePackageJobRef.current = null;
    pendingVideoJobRef.current = null;
    pendingPptJobRef.current = null;
    pendingJianyingDraftJobRef.current = null;
    jianyingDraftRecordsByConversationRef.current = new Map();
    workflowProgressConversationIdRef.current = "";
    workflowProgressRef.current = null;
    setWorkflowProgress(null);
    pptDoneConversationIdsRef.current = new Set();
    planRevisionArtifactRef.current = null;
    pptOutlineRevisionArtifactRef.current = null;
    setPendingPlanRevisionChoice(null);
    setAgentRevisionSourceMessageId("");
    imageRevisionArtifactRef.current = null;
    videoRevisionArtifactRef.current = null;
    briefReadyShownRef.current = false;
    lastEventIdRef.current = 0;
  };

  const applyConversation = async (detail: ConversationDetailResponse) => {
    primaryExecutionReadyRef.current = resolveWorkspacePrimaryExecutionReady({
      conversation: detail.conversation,
      messages: detail.messages,
    });
    const resolvedMode = resolveWorkspaceOrchestrationMode({
      conversation: detail.conversation,
      messages: detail.messages,
    });
    const resolvedAgentRuntimeMode = resolveWorkspaceAgentRuntimeMode({
      conversation: detail.conversation,
      messages: detail.messages,
    });
    setResolvedAgentRuntimeMode(resolvedAgentRuntimeMode);
    setResolvedOrchestrationMode(resolvedMode);
    const snapshot = (detail.conversation.context || {}) as Partial<WorkspaceSnapshot>;
    const pendingImageEditRequest =
      snapshot.pendingImageEditRequest || ((detail.conversation.context || {}) as Record<string, unknown>).pending_image_edit_request || null;
    const imageEditConfirmedSelections = snapshot.imageEditConfirmedSelections || {};
    const flowDraft = snapshot.flowDraft || null;
    const pendingMessageJob = snapshot.pendingMessageJob || snapshot.pending_message_job || null;
    const pendingIntakeJob = snapshot.pendingIntakeJob || snapshot.pending_intake_job || null;
    const pendingDirectionJob = snapshot.pendingDirectionJob || snapshot.pending_direction_job || null;
    const storedPendingPlanJob = snapshot.pendingPlanJob || snapshot.pending_plan_job || null;
    const recoveredPendingPlanJobCandidate = storedPendingPlanJob
      ? null
      : loadPendingPlanJobRecovery<PendingPlanJob>(
          browserSessionStorage(),
          detail.conversation.conversation_id,
        );
    const recoveredPendingPlanJob = isRecoverablePendingPlanJob(recoveredPendingPlanJobCandidate)
      ? recoveredPendingPlanJobCandidate
      : null;
    if (storedPendingPlanJob) {
      clearPendingPlanJobRecovery(browserSessionStorage(), detail.conversation.conversation_id);
    } else if (recoveredPendingPlanJobCandidate && !recoveredPendingPlanJob) {
      clearPendingPlanJobRecovery(browserSessionStorage(), detail.conversation.conversation_id);
    }
    const pendingPlanJob = storedPendingPlanJob || recoveredPendingPlanJob;
    const pendingImageJob = snapshot.pendingImageJob || snapshot.pending_image_job || null;
    const pendingImageRevision = snapshot.pendingImageRevision || snapshot.pending_image_revision || null;
    const pendingPptOutlineRevision = snapshot.pendingPptOutlineRevision || snapshot.pending_ppt_outline_revision || null;
    const pendingVideoRevision = snapshot.pendingVideoRevision || snapshot.pending_video_revision || null;
    const pendingScenePackageJob = snapshot.pendingScenePackageJob || snapshot.pending_scene_package_job || null;
    const pendingPptJob = snapshot.pendingPptJob || snapshot.pending_ppt_job || null;
    const pendingJianyingDraftJob = snapshot.pendingJianyingDraftJob || snapshot.pending_jianying_draft_job || null;
    const jianyingDraftRecords = snapshot.jianyingDraftRecords || snapshot.jianying_draft_records || {};
    const restoredMessages = detail.messages
      .map((message) => messageFromResponse(message, detail.conversation.conversation_id))
      .filter((m): m is ChatMessage => Boolean(m));
    const restoredMessagesWithImageEditSelections = applyImageEditConfirmedSelectionsToMessages(restoredMessages, imageEditConfirmedSelections);
    const contextMessages = restoreLatestVideoScenePackagesFromContext(
      restoredConversationMessages(undefined, restoredMessagesWithImageEditSelections),
      snapshot as Partial<Record<string, unknown>>,
    );
    const pptAwareMessages = markLatestPptFileDoneFromContext(contextMessages, snapshot as Partial<Record<string, unknown>>);
    const imageAwareMessages = markLatestImageResultAcceptedFromContext(pptAwareMessages, snapshot as Partial<Record<string, unknown>>);
    const videoAwareMessages = markLatestVideoResultAcceptedFromContext(imageAwareMessages, snapshot as Partial<Record<string, unknown>>);
    const normalizedMessages = normalizeRestoredMessageReferences(
      dedupeRestoredScenePackageMessages(restorePendingMessageJobMessage(videoAwareMessages, pendingMessageJob)),
    );
    const resumableScenePackageJob = pendingScenePackageJob
      && pendingScenePackageJob.conversation_id === detail.conversation.conversation_id
      && !hasMaterializedScenePackageJob(normalizedMessages, pendingScenePackageJob)
      ? pendingScenePackageJob
      : null;
    const reconciledMessages = reconcileStaleSceneAssetUiFlags(normalizedMessages, {
      hasActiveAssetJob: Boolean(
        resumableScenePackageJob
        && (
          resumableScenePackageJob.kind === "scene_asset_generation"
          || resumableScenePackageJob.kind === "scene_package_generation"
        ),
      ),
    });
    const pendingPlanJobMaterialized = Boolean(
      pendingPlanJob
      && hasMaterializedPlanJob(reconciledMessages, detail.conversation.conversation_id, pendingPlanJob),
    );
    if (pendingPlanJobMaterialized && pendingPlanJob) {
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        detail.conversation.conversation_id,
        pendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(pendingPlanJob);
    }
    const contextIntent = isCreationIntent((detail.conversation.context || {}).intent)
      ? (detail.conversation.context || {}).intent as CreationIntent
      : null;
    const storedWorkflowProgress = snapshot.workflowProgress || snapshot.workflow_progress || null;
    const restoredLastPhase = (() => {
      const phase = String(detail.conversation.last_phase || "");
      if (!/scene_package_job_resume_failed|scene_asset_job_resume_failed/.test(phase)) return phase;
      const latestPackages = [...reconciledMessages].reverse().find((message) => (
        message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages
      ))?.artifact;
      if (!latestPackages?.videoScenePackages) return phase;
      if (latestPackages.sceneAssetsAwaitingModel) return "scene_package_awaiting_image_model";
      if (scenePackageHasGeneratedImages(latestPackages.videoScenePackages)) return "scene_package_ready";
      return "scene_package_awaiting_image_model";
    })();
    const fallbackBoard = storedWorkflowProgress
      ? null
      : deriveWorkflowTaskBoard({
          lastPhase: restoredLastPhase || detail.conversation.last_phase,
          fallbackIntent: contextIntent,
          messages: reconciledMessages,
        });
    const restoredWorkflowProgress: WorkflowProgressSnapshot | null = (() => {
      const base = storedWorkflowProgress || (fallbackBoard
        ? {
            version: 1 as const,
            intent: fallbackBoard.intent,
            flow_kind: fallbackBoard.flowKind,
            source_message_id: "",
            last_phase: restoredLastPhase || detail.conversation.last_phase,
            scene_package_stage: null as string | null,
            updated_at: detail.conversation.updated_at || new Date().toISOString(),
          }
        : null);
      if (!base) return null;
      if (!restoredLastPhase || base.last_phase === restoredLastPhase) return base;
      return { ...base, last_phase: restoredLastPhase };
    })();
    applySnapshot({
      ...snapshot,
      pendingScenePackageJob: resumableScenePackageJob,
      pending_scene_package_job: resumableScenePackageJob,
      flowDraft,
      pendingMessageJob,
      pending_message_job: pendingMessageJob,
      pendingIntakeJob,
      pending_intake_job: pendingIntakeJob,
      pendingDirectionJob,
      pending_direction_job: pendingDirectionJob,
      pendingPlanJob: pendingPlanJobMaterialized ? null : pendingPlanJob,
      pending_plan_job: pendingPlanJobMaterialized ? null : pendingPlanJob,
      pendingPlanRevisionChoice:
        pendingPlanJob?.kind === "plan_revision" ? null : snapshot.pendingPlanRevisionChoice || snapshot.pending_plan_revision_choice || null,
      pending_plan_revision_choice:
        pendingPlanJob?.kind === "plan_revision" ? null : snapshot.pendingPlanRevisionChoice || snapshot.pending_plan_revision_choice || null,
      pendingImageEditRequest: pendingImageEditRequest as PendingImageEditRequest | null,
      pendingImageJob,
      pending_image_job: pendingImageJob,
      pendingImageRevision,
      pending_image_revision: pendingImageRevision,
      pendingPptOutlineRevision,
      pending_ppt_outline_revision: pendingPptOutlineRevision,
      pendingVideoRevision,
      pending_video_revision: pendingVideoRevision,
      pendingPptJob,
      pending_ppt_job: pendingPptJob,
      pendingJianyingDraftJob:
        pendingJianyingDraftJob?.conversation_id === detail.conversation.conversation_id ? pendingJianyingDraftJob : null,
      pending_jianying_draft_job:
        pendingJianyingDraftJob?.conversation_id === detail.conversation.conversation_id ? pendingJianyingDraftJob : null,
      jianyingDraftRecords,
      jianying_draft_records: jianyingDraftRecords,
      imageEditConfirmedSelections,
      workflowProgress: restoredWorkflowProgress,
      workflow_progress: restoredWorkflowProgress,
      messages: reconciledMessages,
    }, detail.conversation.conversation_id);
    if (!pendingPlanJobMaterialized && pendingPlanJob?.context.processedKey) {
      processedArtifactIdsRef.current.add(pendingPlanJob.context.processedKey);
    }
    if (resolvedMode !== "frontend_v2") return;
    if (recoveredPendingPlanJob && !pendingPlanJobMaterialized) {
      notifyPlanJobPersistenceRecovery(recoveredPendingPlanJob);
      schedulePendingPlanJobPersistence(
        recoveredPendingPlanJob,
        pendingPlanJobRunningPhase(recoveredPendingPlanJob.kind),
        pendingPlanJobPersistenceContext(recoveredPendingPlanJob.kind),
      );
    }
    if (pendingMessageJob?.job_id && pendingMessageJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingMessageJob(pendingMessageJob);
      }, 0);
    } else if (pendingIntakeJob?.job_id && pendingIntakeJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingIntakeJob(pendingIntakeJob);
      }, 0);
    }
    if (pendingDirectionJob?.job_id && pendingDirectionJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingDirectionJob(pendingDirectionJob);
      }, 0);
    } else if (
      flowDraft?.stage === "directions_ready" &&
      flowDraft.creativeDirections?.length &&
      isCreationIntent(flowDraft.intent) &&
      !hasPostDirectionArtifactForDirections(reconciledMessages, detail.conversation.conversation_id, flowDraft.creativeDirections) &&
      !hasDirectionsArtifactForDraft(reconciledMessages, detail.conversation.conversation_id, flowDraft)
    ) {
      pushDirectionsArtifact(flowDraft.creativeDirections, {
        intent: flowDraft.intent,
        formValues: flowDraft.formValues || (flowDraft.form ? valuesFromForm(flowDraft.form) : {}),
        materials: flowDraft.materials || [],
        coreMessage: flowDraft.coreMessage || "",
        intakeContext: flowDraft.intakeContext,
      }, detail.conversation.conversation_id);
    } else if (
      flowDraft?.stage === "form_pending" &&
      !hasPassedRequirementCollection(
        reconciledMessages,
        detail.conversation.conversation_id,
        snapshot as Partial<WorkspaceSnapshot> & Record<string, unknown>,
        detail.conversation.last_phase,
      )
    ) {
      window.setTimeout(() => {
        restoreFormDraft(flowDraft, detail.conversation.conversation_id);
      }, 0);
    }
    if (
      !pendingMessageJob &&
      pendingPlanJob?.job_id &&
      pendingPlanJob.conversation_id === detail.conversation.conversation_id &&
      !hasMaterializedPlanJob(reconciledMessages, detail.conversation.conversation_id, pendingPlanJob)
    ) {
      window.setTimeout(() => {
        void resumePendingPlanJob(pendingPlanJob);
      }, 0);
    } else if (pendingPlanJob?.job_id && pendingPlanJob.conversation_id === detail.conversation.conversation_id) {
      await clearPendingPlanJob(detail.conversation.conversation_id, "plan_review").catch(() => {});
    }
    if (pendingImageJob?.job_id && pendingImageJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingImageJob(pendingImageJob);
      }, 0);
    }
    if (
      pendingScenePackageJob?.job_id &&
      pendingScenePackageJob.conversation_id === detail.conversation.conversation_id &&
      !hasMaterializedScenePackageJob(reconciledMessages, pendingScenePackageJob)
    ) {
      window.setTimeout(() => {
        void resumePendingScenePackageJob(pendingScenePackageJob);
      }, 0);
    } else if (pendingScenePackageJob?.job_id && pendingScenePackageJob.conversation_id === detail.conversation.conversation_id) {
      const scenePackageMessage = reconciledMessages
        .slice()
        .reverse()
        .find((message) => message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages);
      await clearPendingScenePackageJob(
        detail.conversation.conversation_id,
        "scene_package_ready",
        scenePackageMessage?.artifact?.videoScenePackages
          ? scenePackageContext(
              scenePackageMessage.artifact,
              scenePackageMessage.artifact.videoScenePackages,
              scenePackageMessage.artifact.sceneAssetFailures || [],
            )
          : {},
      ).catch(() => {});
    }
    const pendingVideoJob = snapshot.pendingVideoJob || snapshot.pending_video_job || null;
    if (pendingVideoJob?.job_id && pendingVideoJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingVideoJob(pendingVideoJob);
      }, 0);
    }
    if (pendingPptJob?.job_id && pendingPptJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingPptJob(pendingPptJob);
      }, 0);
    }
    if (pendingJianyingDraftJob?.job_id && pendingJianyingDraftJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingJianyingDraftJob(pendingJianyingDraftJob);
      }, 0);
    }
    const taskId = snapshot.taskId || detail.conversation.current_task_id || "";
    if (taskId) {
      setActiveTaskId(taskId);
      unsubRef.current = subscribeTaskEvents(taskId, onEvent, snapshot.lastEventId || undefined);
      await reconcileTaskFromServer(taskId);
    }
  };

  const resumeVisiblePendingJobs = () => {
    const activeConversationId = conversationIdRef.current;
    if (!activeConversationId || !pageVisibleRef.current || orchestrationModeRef.current !== "frontend_v2") return;
    const pendingMessageJob = pendingMessageJobRef.current;
    if (pendingMessageJob?.job_id && pendingMessageJob.conversation_id === activeConversationId) {
      void resumePendingMessageJob(pendingMessageJob);
      return;
    }
    const pendingIntakeJob = pendingIntakeJobRef.current;
    if (pendingIntakeJob?.job_id && pendingIntakeJob.conversation_id === activeConversationId) {
      void resumePendingIntakeJob(pendingIntakeJob);
    }
    const pendingDirectionJob = pendingDirectionJobRef.current;
    if (pendingDirectionJob?.job_id && pendingDirectionJob.conversation_id === activeConversationId) {
      void resumePendingDirectionJob(pendingDirectionJob);
    }
    const pendingPlanJob = pendingPlanJobRef.current;
    if (pendingPlanJob?.job_id && pendingPlanJob.conversation_id === activeConversationId) {
      const recoveryHandle = loadPendingPlanJobRecovery<PendingPlanJob>(
        browserSessionStorage(),
        activeConversationId,
      );
      if (recoveryHandle?.job_id === pendingPlanJob.job_id) {
        schedulePendingPlanJobPersistence(
          pendingPlanJob,
          pendingPlanJobRunningPhase(pendingPlanJob.kind),
          pendingPlanJobPersistenceContext(pendingPlanJob.kind),
        );
      }
      void resumePendingPlanJob(pendingPlanJob);
    }
    const pendingImageJob = pendingImageJobRef.current;
    if (pendingImageJob?.job_id && pendingImageJob.conversation_id === activeConversationId) {
      void resumePendingImageJob(pendingImageJob);
    }
    const pendingScenePackageJob = pendingScenePackageJobRef.current;
    if (pendingScenePackageJob?.job_id && pendingScenePackageJob.conversation_id === activeConversationId) {
      void resumePendingScenePackageJob(pendingScenePackageJob);
    }
    const pendingVideoJob = pendingVideoJobRef.current;
    if (pendingVideoJob?.job_id && pendingVideoJob.conversation_id === activeConversationId) {
      void resumePendingVideoJob(pendingVideoJob);
    }
    const pendingPptJob = pendingPptJobRef.current;
    if (pendingPptJob?.job_id && pendingPptJob.conversation_id === activeConversationId) {
      void resumePendingPptJob(pendingPptJob);
    }
    const pendingJianyingDraftJob = pendingJianyingDraftJobRef.current;
    if (pendingJianyingDraftJob?.job_id && pendingJianyingDraftJob.conversation_id === activeConversationId) {
      void resumePendingJianyingDraftJob(pendingJianyingDraftJob);
    }
  };

  // 对话恢复是“先写 Ref、再提交 React 状态”。素材修订 job 额外在提交完成后接力一次，
  // 避免切换对话或刷新时，首个 setTimeout 早于可见对话状态而永久停止轮询。
  useEffect(() => {
    if (!orchestrationResolved || !currentConversationId || !pageVisibleRef.current) return;
    const pendingScenePackageJob = pendingScenePackageJobRef.current;
    if (
      !pendingScenePackageJob?.job_id
      || pendingScenePackageJob.conversation_id !== currentConversationId
      || hasMaterializedScenePackageJob(messagesRef.current, pendingScenePackageJob)
    ) {
      return;
    }
    void resumePendingScenePackageJob(pendingScenePackageJob);
  }, [currentConversationId, orchestrationResolved, pendingScenePackageResumeVersion]);

  // 刷新或压缩完成后，服务端 Turn 可能已经进入 processing，但旧版页面内存里的
  // pendingSupervisorTurns 已丢失。这里按服务端队列和已保存用户消息重建接力 DTO，
  // 类似从数据库恢复一条待执行的 Service Command，绝不重新注册或重复计费。
  useEffect(() => {
    if (
      orchestrationModeRef.current === "video_agent_v2"
      && !primaryExecutionReadyRef.current
    ) return;
    const runtimeAttached = orchestrationModeRef.current === "video_agent_v2"
      || agentRuntimeModeRef.current === "assist"
      || agentRuntimeModeRef.current === "shadow"
      || agentRuntimeModeRef.current === "primary";
    if (
      !currentConversationId
      || !runtimeAttached
      || supervisorRuntime.state.connection.status !== "connected"
    ) return;
    const localIds = new Set(
      pendingSupervisorTurns
        .filter((item) => item.conversationId === currentConversationId)
        .map((item) => item.clientInputId),
    );
    const recoverableInput = supervisorRuntime.state.inputQueue.find(
      (item) => ["accepted", "processing", "queued"].includes(item.status)
        && !localIds.has(item.clientInputId),
    );
    if (!recoverableInput) return;
    const sourceMessage = messagesRef.current.find(
      (message) => message.id === recoverableInput.clientInputId
        && messageConversationId(message, currentConversationId) === currentConversationId
        && message.role === "user",
    );
    if (!sourceMessage) return;

    const lastPhase = workflowProgressConversationIdRef.current === currentConversationId
      ? workflowProgressRef.current?.last_phase || ""
      : "";
    if (!imageRevisionArtifactRef.current) {
      const imageArtifact = latestImageResultArtifactForConversation(messagesRef.current, currentConversationId);
      const looksLikeRevision = /修改|重新生成|背景|轮廓光|亮明|保持不变/u.test(sourceMessage.content);
      if (imageArtifact && (lastPhase === "image_regeneration_running" || looksLikeRevision)) {
        imageRevisionArtifactRef.current = {
          conversationId: currentConversationId,
          artifact: imageArtifact,
        };
      }
    }
    if (!pptOutlineRevisionArtifactRef.current && latestPptRevisionRequestedForConversation(messagesRef.current, currentConversationId, sourceMessage.content)) {
      const outlineMessage = [...messagesRef.current]
        .reverse()
        .find((message) => messageConversationId(message, currentConversationId) === currentConversationId && message.artifact?.pptSummary);
      if (outlineMessage?.artifact) {
        pptOutlineRevisionArtifactRef.current = {
          conversationId: currentConversationId,
          artifact: outlineMessage.artifact,
        };
      }
    }

    const recoveredTurn: PendingSupervisorTurn = {
      conversationId: currentConversationId,
      clientInputId: recoverableInput.clientInputId,
      content: sourceMessage.content,
      materials: sourceMessage.materials || [],
      replyToMessageId: null,
      artifactRefs: [],
      interruptId: null,
      explicitAction: null,
      continueLegacy: false,
      registrationStatus: "registered",
      runId: recoverableInput.turnId || undefined,
    };
    void persistPendingSupervisorTurns(
      (current) => current.some((item) => item.clientInputId === recoveredTurn.clientInputId)
        ? current
        : [recoveredTurn, ...current],
      currentConversationId,
    ).catch(() => {});
  }, [
    currentConversationId,
    agentRuntimeMode,
    pendingSupervisorTurns,
    orchestrationMode,
    orchestrationResolved,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.inputQueue,
  ]);

  // M13.2 早期候选可能已经把 Turn 保存为 accepted，却没有真实 Graph Handler。
  // 这里按会话写一次稳定提示和版本 marker，再一次性清空全部本地 pending；
  // 即使进程在两次写入之间退出，刷新也只补齐 marker，不会重复新增消息。
  useEffect(() => {
    if (
      !orchestrationResolved
      || !currentConversationId
      || !primaryExecutionUnavailable
    ) {
      return;
    }
    const targetConversationId = currentConversationId;
    const noticeId = unavailableSupervisorNoticeId(targetConversationId);
    const noticePersisted = messagesRef.current.some(
      (message) => (
        message.id === noticeId
        || message.content === UNAVAILABLE_SUPERVISOR_NOTICE
      )
        && messageConversationId(message, targetConversationId) === targetConversationId,
    );
    const pendingCount = pendingSupervisorTurns.filter(
      (item) => item.conversationId === targetConversationId,
    ).length;
    const hasActiveInput = supervisorRuntime.state.inputQueue.some(
      (item) => ["sending", "queued", "processing", "accepted"].includes(item.status),
    );
    const recoveryAction = resolveUnavailableSupervisorRecovery({
      orchestrationMode,
      primaryExecutionReady: primaryExecutionReadyRef.current,
      connectionStatus: supervisorRuntime.state.connection.status,
      pendingCount,
      hasActiveInput,
      markerVersion:
        unavailableSupervisorNoticeVersionsRef.current.get(targetConversationId) ?? 0,
      noticePersisted,
    });
    if (
      recoveryAction === "none"
      || unavailableSupervisorRecoveryInFlightRef.current.has(targetConversationId)
    ) {
      return;
    }
    unavailableSupervisorRecoveryInFlightRef.current.add(targetConversationId);
    const convergeUnavailableSupervisor = async () => {
      try {
        if (recoveryAction === "persist_notice") {
          await appendPersistedSupervisorNotice(
            UNAVAILABLE_SUPERVISOR_NOTICE,
            targetConversationId,
            noticeId,
          );
        }
        unavailableSupervisorNoticeVersionsRef.current.set(
          targetConversationId,
          UNAVAILABLE_SUPERVISOR_NOTICE_VERSION,
        );
        await persistPendingSupervisorTurns(
          () => [],
          targetConversationId,
        );
      } catch {
        unavailableSupervisorNoticeVersionsRef.current.delete(
          targetConversationId,
        );
      } finally {
        unavailableSupervisorRecoveryInFlightRef.current.delete(
          targetConversationId,
        );
      }
    };
    void convergeUnavailableSupervisor();
  }, [
    currentConversationId,
    orchestrationMode,
    orchestrationResolved,
    pendingSupervisorTurns,
    primaryExecutionUnavailable,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.inputQueue,
  ]);

  useEffect(() => {
    const handleVisibilityResume = () => {
      pageVisibleRef.current = typeof document === "undefined" || document.visibilityState !== "hidden";
      if (pageVisibleRef.current) resumeVisiblePendingJobs();
    };
    const handleFocusResume = () => {
      pageVisibleRef.current = true;
      resumeVisiblePendingJobs();
    };
    document.addEventListener("visibilitychange", handleVisibilityResume);
    window.addEventListener("focus", handleFocusResume);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityResume);
      window.removeEventListener("focus", handleFocusResume);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const restoreConversation = async () => {
      restoringRef.current = true;
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      if (!conversationId) {
        resetWorkspace();
        restoringRef.current = false;
        setBusy(false);
        return;
      }
      if (skipRouteRestoreConversationRef.current === conversationId) {
        skipRouteRestoreConversationRef.current = "";
        restoringRef.current = false;
        setBusy(false);
        return;
      }
      setDialogOpen(false);
      setPendingCore("");
      setPendingFormValues({});
      setPendingMaterials([]);
      setSelectedStoryboardMessageId("");
      setSelectedPlanEditorMessageId("");
      pendingDialogContextRef.current = null;
      unsubRef.current();
      seenEventIdsRef.current = new Set();
      announcedPhasesRef.current = new Set();
      briefReadyShownRef.current = false;
      lastEventIdRef.current = 0;
      setActiveConversationId(conversationId);
      orchestrationModeRef.current = null;
      agentRuntimeModeRef.current = null;
      primaryExecutionReadyRef.current = false;
      setOrchestrationResolved(false);
      setBusy(true);
      try {
        const detail = await api.resumeConversation(conversationId);
        if (cancelled) return;
        await applyConversation(detail);
      } catch (err) {
        if (!cancelled) {
          // 失效会话（常见于本地重启后）必须离开死链，否则输入区和新建都会像被锁住。
          resetWorkspace();
          navigate("/", { replace: true });
          const message = err instanceof Error ? err.message : String(err);
          if (!/not found|404|Conversation not found/i.test(message)) {
            pushAssistant(`历史对话恢复失败:${message}`);
          }
        }
      } finally {
        // 即使被路由切换取消，也要释放本轮恢复锁，避免新建对话后仍 busy。
        restoringRef.current = false;
        if (!cancelled) {
          setBusy(false);
          const deferredInputs = deferredOwnershipInputsRef.current.filter(
            (item) => item.routeConversationId === conversationId,
          );
          deferredOwnershipInputsRef.current = deferredOwnershipInputsRef.current.filter(
            (item) => item.routeConversationId !== conversationId,
          );
          for (const item of deferredInputs) {
            window.setTimeout(() => void handleSend(item.input), 0);
          }
        } else {
          setBusy(false);
        }
      }
    };
    void restoreConversation();
    return () => {
      cancelled = true;
      restoringRef.current = false;
      if (conversationIdRef.current === conversationId) {
        setActiveConversationId("");
      }
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    };
  }, [conversationId, navigate]);

  useEffect(() => {
    const startFreshConversation = () => {
      skipRouteRestoreConversationRef.current = "";
      deferredOwnershipInputsRef.current = [];
      restoringRef.current = false;
      setBusy(false);
      resetWorkspace();
      if (routeConversationIdRef.current) {
        navigate("/", { replace: true });
      }
    };
    window.addEventListener("pixelflow-new-conversation", startFreshConversation);
    return () => {
      window.removeEventListener("pixelflow-new-conversation", startFreshConversation);
    };
  }, [navigate]);

  useEffect(() => {
    if (restoringRef.current || !currentConversationId || orchestrationModeRef.current !== "frontend_v2") return;
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      const snapshot = makeSnapshot(currentConversationId);
      void updateConversationWithProgress(currentConversationId, {
          current_task_id: currentTaskId || null,
          last_phase: workflowProgressRef.current?.last_phase || String(canvas.phase || "idle"),
          context: snapshot as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }, 400);
  }, [messages, pendingMaterials, pendingPlanRevisionChoice, canvas, canvasOpen, briefConfirmed, currentTaskId, currentConversationId]);

  const titleFromPrompt = (text: string) => {
    const normalized = text.trim() || "带附件对话";
    return normalized.length > 18 ? `${normalized.slice(0, 18)}...` : normalized;
  };

  const ensureConversation = async (title: string): Promise<ConversationOwnership> => {
    if (conversationIdRef.current) {
      return {
        conversationId: conversationIdRef.current,
        orchestrationMode: orchestrationModeRef.current ?? "frontend_v2",
        agentRuntimeMode: agentRuntimeModeRef.current ?? "off",
      };
    }
    const created = await api.createConversation({
      title: titleFromPrompt(title),
      last_phase: String(canvas.phase || "idle"),
      current_task_id: currentTaskId || null,
      context: makeSnapshot() as unknown as Record<string, unknown>,
    });
    const createdMode = resolveWorkspaceOrchestrationMode(created);
    const createdAgentRuntimeMode = resolveWorkspaceAgentRuntimeMode(created);
    primaryExecutionReadyRef.current = resolveWorkspacePrimaryExecutionReady(created);
    skipRouteRestoreConversationRef.current = created.conversation_id;
    setResolvedAgentRuntimeMode(createdAgentRuntimeMode);
    setResolvedOrchestrationMode(createdMode);
    setActiveConversationId(created.conversation_id);
    window.dispatchEvent(new Event("pixelflow-conversations-updated"));
    notifyContentAppConversationsUpdated(created.conversation_id);
    return {
      conversationId: created.conversation_id,
      orchestrationMode: createdMode,
      agentRuntimeMode: createdAgentRuntimeMode,
    };
  };

  const normalizeSendInput = (input: string | AgentUserMessagePayload): AgentUserMessagePayload => {
    if (typeof input === "string") return { content: input, materials: [] };
    return {
      content: input.content,
      materials: Array.isArray(input.materials) ? input.materials : [],
      reply_to_message_id: input.reply_to_message_id ?? null,
      artifact_refs: Array.isArray(input.artifact_refs) ? input.artifact_refs : [],
      interrupt_id: input.interrupt_id ?? null,
    };
  };

  const appendSupervisorNotice = (content: string, targetConversationId: string) => {
    const notice: ChatMessage = {
      id: uid(),
      conversationId: targetConversationId,
      role: "assistant",
      content,
      time: now(),
    };
    setMessages((items) => {
      const nextItems = appendVisibleConversationMessage(items, {
        activeConversationId: conversationIdRef.current,
        targetConversationId,
        message: notice,
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const appendPersistedSupervisorNotice = async (
    content: string,
    targetConversationId: string,
    messageId: string,
  ) => {
    if (messagesRef.current.some(
      (message) => message.id === messageId
        && messageConversationId(message, targetConversationId) === targetConversationId,
    )) {
      return;
    }
    const optimisticMessage = appendOptimisticMessageForConversation({
      id: messageId,
      conversationId: targetConversationId,
      role: "assistant",
      content,
      time: "",
    }, targetConversationId);
    try {
      const savedMessage = await persistChatMessage(
        targetConversationId,
        optimisticMessage,
      );
      replaceOptimisticMessage(
        optimisticMessage.id,
        savedMessage,
        targetConversationId,
      );
    } catch (error) {
      removeOptimisticMessage(optimisticMessage.id, targetConversationId);
      throw error;
    }
    await supervisorRuntime.refreshSnapshot().catch(() => {});
  };

  const handleSupervisorTurn = async (
    pendingTurn: PendingSupervisorTurn,
    contextVersion: number,
  ): Promise<RegisteredSupervisorTurn | null> => {
    const targetConversationId = pendingTurn.conversationId;
    const runtimeAttached = orchestrationModeRef.current === "video_agent_v2"
      || agentRuntimeModeRef.current === "assist"
      || agentRuntimeModeRef.current === "shadow"
      || agentRuntimeModeRef.current === "primary";
    if (!targetConversationId || !runtimeAttached) return null;
    try {
      // 每次写请求前重新读取 CAS 版本，避免上一轮 Turn 或事件消费后继续使用旧版本。
      await supervisorRuntime.refreshSnapshot();
      const expectedContextVersion = supervisorRuntime.getContextVersion() ?? contextVersion;
      const submission = buildSupervisorSubmission({
        conversationId: targetConversationId,
        clientInputId: pendingTurn.clientInputId,
        content: pendingTurn.content,
        materials: pendingTurn.materials as JsonObject[],
        replyToMessageId: pendingTurn.replyToMessageId,
        artifactRefs: pendingTurn.artifactRefs,
        interruptId: orchestrationModeRef.current === "video_agent_v2"
          ? pendingTurn.interruptId
          : null,
        explicitAction: pendingTurn.explicitAction,
      }, expectedContextVersion);
      if (submission.kind === "interrupt") {
        if (orchestrationModeRef.current !== "video_agent_v2") return null;
        await supervisorRuntime.respondToInterrupt(submission.interruptId, submission.request);
        // interrupt 成功后先原子移除恢复上下文；若写回失败，保留相同
        // client_response_id 供幂等重试，不创建额外 Turn。
        await persistPendingSupervisorTurns(
          (current) => current.filter(
            (item) => item.clientInputId !== pendingTurn.clientInputId,
          ),
          targetConversationId,
        );
        await supervisorRuntime.refreshSnapshot().catch(() => {});
        return null;
      }
      const request: TurnStartRequest = submission.request;
      const started = parseRegisteredSupervisorTurn(
        await supervisorRuntime.startTurn(request),
      );
      setResolvedOrchestrationMode(started.orchestrationMode);
      primaryExecutionReadyRef.current = started.orchestrationMode === "video_agent_v2";
      if (started.routeIntent === "unknown") {
        await appendPersistedSupervisorNotice(
          "我还不能确定你要创建视频、图片、PPT，还是分析参考视频。请补充说明目标。",
          targetConversationId,
          `agent-route-clarification:${targetConversationId}:${pendingTurn.clientInputId}:v1`,
        );
      }
      // Turn 已接受后刷新一次，让紧随其后的排队输入看到服务端最新版本。
      try {
        await supervisorRuntime.refreshSnapshot();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return null;
      }
      return started;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return null;
      appendSupervisorNotice("会话 Agent 请求失败，请稍后重试。", targetConversationId);
      return null;
    }
  };

  async function submitSupervisorAction(
    content: string,
    explicitAction: ExplicitActionSignal,
    options: SubmitSupervisorActionOptions,
  ): Promise<void> {
    const clientInputId = crypto.randomUUID();
    const targetConversationId = currentConversationId;
    const interruptId = supervisorRuntime.state.interrupt?.interruptId ?? null;
    if (
      orchestrationModeRef.current !== "video_agent_v2"
      || !targetConversationId
      || !interruptId
      || !content.trim()
    ) return;
    const pendingTurn: PendingSupervisorTurn = {
      conversationId: targetConversationId,
      clientInputId,
      content,
      materials: options.materials || [],
      replyToMessageId: options.replyToMessageId ?? null,
      artifactRefs: options.artifactRefs
        ?? (explicitAction.artifact_ref ? [explicitAction.artifact_ref] : []),
      interruptId,
      explicitAction,
      continueLegacy: false,
      registrationStatus: "pending",
    };
    await persistPendingSupervisorTurns(
      (current) => current.some((item) => item.clientInputId === clientInputId)
        ? current
        : [...current, pendingTurn],
      targetConversationId,
    );
    ensurePendingSupervisorTurnVisible(pendingTurn);
  }

  useEffect(() => {
    // 恢复时优先处理尚未提交到 Runtime 的输入；已注册 Turn 则优先选择
    // Snapshot 中仍存在的 input，避免已失效的旧 registered Turn 一直 wait，
    // 阻塞后续输入。
    const candidateTurns = pendingSupervisorTurns
      .filter(
        (item) => item.conversationId === currentConversationId
          && !supervisorTurnInFlightRef.current.has(item.clientInputId)
          && !supervisorLegacyHandoffClaimedRef.current.has(item.clientInputId),
      );
    const pendingTurn = candidateTurns.find((item) => item.registrationStatus === "pending")
      || candidateTurns.find((item) => supervisorRuntime.state.inputQueue.some(
        (serverItem) => serverItem.clientInputId === item.clientInputId,
      ))
      || candidateTurns[0];
    const runtimeAttached = orchestrationModeRef.current === "video_agent_v2"
      || agentRuntimeModeRef.current === "assist"
      || agentRuntimeModeRef.current === "shadow"
      || agentRuntimeModeRef.current === "primary";
    if (!pendingTurn || !runtimeAttached) return;
    if (supervisorRuntime.state.connection.status === "fatal") {
      appendSupervisorNotice("会话 Agent 状态恢复失败，请刷新后重试。", pendingTurn.conversationId);
      return;
    }
    const serverInput = supervisorRuntime.state.inputQueue.find(
      (item) => item.clientInputId === pendingTurn.clientInputId,
    );
    // Snapshot 的 contextVersion 只作为 CAS 优化值；恢复阶段它可能尚未从
    // 外部 Store 反映到本次 render。真正注册前 handleSupervisorTurn 会再读
    // 一次最新 Snapshot，因此不能因为这里暂时为 null 而把已落库 Turn 卡死。
    if (supervisorRuntime.state.connection.status !== "connected") return;
    if (serverInput) ensurePendingSupervisorTurnVisible(pendingTurn);
    if (
      pendingTurn.explicitAction
      && pendingTurn.registrationStatus === "registered"
      && pendingTurn.runId
    ) {
      supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
      void supervisorRuntime.getRunStatus(pendingTurn.runId)
        .then(async (value) => {
          if (!value || typeof value !== "object" || Array.isArray(value)) return;
          const status = value.status;
          if (status !== "waiting_user" && status !== "completed" && status !== "failed") return;
          await persistPendingSupervisorTurns(
            (current) => current.filter(
              (item) => item.clientInputId !== pendingTurn.clientInputId,
            ),
            pendingTurn.conversationId,
          );
          await supervisorRuntime.refreshSnapshot().catch(() => {});
        })
        .catch(() => {})
        .finally(() => {
          supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
        });
      return;
    }
    const handoffAction = resolveAssistHandoffAction({
      orchestrationMode: orchestrationModeRef.current ?? "frontend_v2",
      primaryExecutionReady: primaryExecutionReadyRef.current,
      registrationStatus: pendingTurn.registrationStatus,
      serverInputStatus: serverInput?.status,
      serverRunStatus: supervisorRuntime.state.run.status,
      continueLegacy: pendingTurn.continueLegacy,
      legacyBusy,
      dialogOpen,
      pendingPlanRevision: Boolean(pendingPlanRevisionChoice),
    });
    if (handoffAction !== "register") {
      if (handoffAction === "wait") return;
      // 历史未接线会话由上面的会话级恢复 effect 统一收敛，避免每条 pending
      // 各写一条随机提示或在清空后因孤儿 inputQueue 再次重建。
      if (handoffAction === "unavailable") return;
      if (handoffAction === "failed") {
        supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
        void appendPersistedSupervisorNotice(
          "会话 Agent 未能处理已保存输入，请稍后重试。",
          pendingTurn.conversationId,
          failedSupervisorNoticeId(
            pendingTurn.conversationId,
            pendingTurn.clientInputId,
          ),
        )
          .then(() => persistPendingSupervisorTurns(
            (current) => current.filter(
              (item) => item.clientInputId !== pendingTurn.clientInputId,
            ),
            pendingTurn.conversationId,
          ))
          .catch(() => {})
          .finally(() => {
            supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
          });
        return;
      }
      // acknowledge / 历史 continue_legacy：V2 不再踢回旧采集，只清掉 pending。
      supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
      void persistPendingSupervisorTurns(
        (current) => current.filter(
          (item) => item.clientInputId !== pendingTurn.clientInputId,
        ),
        pendingTurn.conversationId,
      )
        .then(() => supervisorRuntime.refreshSnapshot().catch(() => {}))
        .finally(() => {
          supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
          supervisorLegacyHandoffClaimedRef.current.delete(pendingTurn.clientInputId);
        });
      return;
    }
    supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
    void handleSupervisorTurn(pendingTurn, supervisorRuntime.getContextVersion() ?? 0)
      .then(async (registered) => {
        if (!registered) return;
        const registeredTurn: PendingSupervisorTurn = {
          ...pendingTurn,
          continueLegacy: false,
          registrationStatus: "registered",
          runId: registered.runId,
        };
        ensurePendingSupervisorTurnVisible(registeredTurn);
        await persistPendingSupervisorTurns(
          (current) => current.map(
            (item) => item.clientInputId === pendingTurn.clientInputId
              ? registeredTurn
              : item,
          ),
          pendingTurn.conversationId,
        );
      })
      .finally(() => {
        supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
      });
  }, [
    currentConversationId,
    agentRuntimeMode,
    dialogOpen,
    legacyBusy,
    orchestrationMode,
    orchestrationResolved,
    pendingPlanRevisionChoice,
    pendingSupervisorTurns,
    supervisorRuntime.contextVersion,
    supervisorRuntime.getContextVersion,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.inputQueue,
    supervisorRuntime.state.run.status,
  ]);

  const shouldUseRecoverableIntakeEntry = (
    text: string,
    materials: Array<Record<string, unknown>>,
    activeConversation: string,
  ): boolean => {
    if (sceneGlobalAssetReferenceFromMaterials(materials)) return false;
    if (pendingImageEditRequestRef.current?.conversationId === activeConversation && !looksLikeImageEditPrompt(text)) return false;
    if (pptOutlineRevisionArtifactRef.current?.conversationId === activeConversation && pptOutlineRevisionArtifactRef.current.artifact?.pptSummary) return false;
    if (planRevisionArtifactRef.current?.conversationId === activeConversation && planRevisionArtifactRef.current.artifact.intent && planRevisionArtifactRef.current.artifact.formValues) return false;
    if (imageRevisionArtifactRef.current?.conversationId === activeConversation && imageRevisionArtifactRef.current.artifact?.imageResult) return false;
    const pendingVideoRevisionArtifact = videoRevisionArtifactRef.current?.artifact;
    if (
      videoRevisionArtifactRef.current?.conversationId === activeConversation &&
      pendingVideoRevisionArtifact?.mergedVideo &&
      pendingVideoRevisionArtifact?.generatedSceneVideos &&
      pendingVideoRevisionArtifact?.videoScenePackages
    ) {
      return false;
    }
    return true;
  };

  const handleSend = async (
    input: string | AgentUserMessagePayload,
    runtimeOptions: SendRuntimeOptions = {},
  ) => {
    if (restoringRef.current) {
      deferredOwnershipInputsRef.current.push({
        routeConversationId: routeConversationIdRef.current,
        input,
      });
      return;
    }
    const {
      content: text,
      materials = [],
      reply_to_message_id: replyToMessageId = null,
      artifact_refs: artifactRefs = [],
      interrupt_id: interruptId = null,
    } = normalizeSendInput(input);
    let activeConversation = conversationIdRef.current;
    const message: ChatMessage = {
      id: runtimeOptions.clientInputId ?? uid(),
      conversationId: activeConversation || undefined,
      role: "user",
      content: text,
      materials,
      time: "",
    };
    lastPlanAnchorUserMessageIdRef.current = message.id;
    try {
      const ownership = await ensureConversation(text);
      activeConversation = ownership.conversationId;
      // 资产包待确认：允许自然语言重做/修改后重新渲染；也可确认生成成片。
      if (
        ownership.orchestrationMode === "video_agent_v2"
        && !runtimeOptions.skipRuntimeRegistration
      ) {
        const latestScenePackageMessage = [...messagesRef.current]
          .reverse()
          .find((item) => (
            messageConversationId(item, activeConversation) === activeConversation
            && item.artifact?.type === "video_scene_packages"
            && item.artifact.videoScenePackages
          ));
        const hasReadyScenePackage = Boolean(latestScenePackageMessage?.artifact?.videoScenePackages);
        const workspace = videoAgentView.workspace;
        const scriptMarkdown = resolveGeneratableScriptMarkdown({
          scriptContent: workspace?.script?.content,
          stages: workspace?.scriptStages,
        });
        const canRebuildAssetPackage = workspaceHasGeneratableScript({
          scriptContent: workspace?.script?.content,
          stages: workspace?.scriptStages,
        }) && Boolean(scriptMarkdown);

        if (
          hasReadyScenePackage
          && isConfirmGenerateVideoFromPackagesRequest(text)
          && latestScenePackageMessage
        ) {
          await appendMessageForConversation(
            { ...message, conversationId: activeConversation },
            activeConversation,
          );
          setReferencedMaterials([]);
          if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
          await handleGenerateVideoFromScenePackages(latestScenePackageMessage);
          return;
        }

        if (
          canRebuildAssetPackage
          && (
            isRegenerateVideoAssetPackageRequest(text)
            || (hasReadyScenePackage && isReviseVideoAssetPackageRequest(text))
            || (scriptPlanConfirmedRef.current && isRegenerateVideoAssetPackageRequest(text))
          )
        ) {
          await appendMessageForConversation(
            { ...message, conversationId: activeConversation },
            activeConversation,
          );
          scriptPlanConfirmedRef.current = true;
          const materialsForJob = (workspace?.assets || [])
            .filter((asset): asset is typeof asset & { url: string } => Boolean(asset.url))
            .map((asset) => ({
              asset_id: asset.artifactRef,
              url: asset.url,
              type: asset.mediaType,
            }));
          const isPureRegenerate = isRegenerateVideoAssetPackageRequest(text)
            && !/改成|换成|调整|补齐|增加|删掉|删除|去掉|修改/.test(text);
          await startVideoAgentAssetPackageFromScript(
            activeConversation,
            scriptMarkdown,
            materialsForJob,
            isPureRegenerate
              ? "已收到，正在重新生成视频资产包…"
              : "已收到修改意见，正在重新生成视频资产包…",
            {
              revisionFeedback: isPureRegenerate ? undefined : text,
            },
          );
          setReferencedMaterials([]);
          if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
          return;
        }
      }

      // 脚本已就绪时的成片意图：必须先确认脚本方案；角色不清则走全流程 Plan。
      if (
        ownership.orchestrationMode === "video_agent_v2"
        && !runtimeOptions.skipRuntimeRegistration
        && (
          isContinueVideoGenerationRequest(text)
          || isConfirmScriptPlanRequest(text)
        )
        && !isRegenerateVideoAssetPackageRequest(text)
        && !isReviseVideoAssetPackageRequest(text)
      ) {
        const workspace = videoAgentView.workspace;
        const scriptMarkdown = resolveGeneratableScriptMarkdown({
          scriptContent: workspace?.script?.content,
          stages: workspace?.scriptStages,
        });
        if (workspaceHasGeneratableScript({
          scriptContent: workspace?.script?.content,
          stages: workspace?.scriptStages,
        }) && scriptMarkdown) {
          if (scriptNeedsFullCharacterPlan({
            scriptContent: scriptMarkdown,
            stages: workspace?.scriptStages,
          })) {
            const readiness = analyzeScriptCharacterReadiness({
              scriptContent: scriptMarkdown,
              stages: workspace?.scriptStages,
            });
            const hints = readiness.missingHints.slice(0, 2).join("；") || "角色设定不完整";
            characterSupplementNoticeRef.current =
              `当前脚本的角色设定不够清晰（${hints}）。将按全流程生成执行方案，请先补充全部出镜角色（含视觉形象），确认脚本方案后再生成视频资产包。`;
            // 不拦截：交给下方 turns/start 种子全流程 Plan，保留时间线可回溯。
          } else if (
            !isConfirmScriptPlanRequest(text)
            && !scriptPlanConfirmedRef.current
          ) {
            if (!workspaceHasExportReady({
              scriptContent: scriptMarkdown,
              stages: workspace?.scriptStages,
            })) {
              await appendMessageForConversation(
                { ...message, conversationId: activeConversation },
                activeConversation,
              );
              pushAssistant(
                "脚本尚未完成「导出脚本产物」。请先等导出完成，再确认脚本方案并生成视频资产包。",
                activeConversation,
              );
              setReferencedMaterials([]);
              if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
              return;
            }
            await appendMessageForConversation(
              { ...message, conversationId: activeConversation },
              activeConversation,
            );
            ensureDurableScriptPlanMessage(
              activeConversation,
              scriptMarkdown,
              message.id,
            );
            pushAssistant(
              "请先确认脚本执行方案后再生成视频资产包。可点击对话里的「同意方案」、右侧「确认脚本并生成资产包」，或回复「确认脚本」。",
              activeConversation,
            );
            setReferencedMaterials([]);
            if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
            return;
          } else {
            await appendMessageForConversation(
              { ...message, conversationId: activeConversation },
              activeConversation,
            );
            try {
              await confirmScriptPlanAndGenerateAssetPackage(
                activeConversation,
                scriptMarkdown,
              );
            } catch (err) {
              pushAssistant(
                err instanceof Error ? err.message : `确认脚本方案失败：${String(err)}`,
                activeConversation,
              );
            }
            setReferencedMaterials([]);
            if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
            return;
          }
        } else {
          await appendMessageForConversation(
            { ...message, conversationId: activeConversation },
            activeConversation,
          );
          pushAssistant(
            "当前还没有可生成视频的脚本。请先完成脚本生成，或在右侧保存脚本后再试。",
            activeConversation,
          );
          setReferencedMaterials([]);
          if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
          return;
        }
      }
      // 中途需求大变：先告知需重新设计任务规划，避免在旧 Plan 上硬跑。
      if (
        ownership.orchestrationMode === "video_agent_v2"
        && !runtimeOptions.skipRuntimeRegistration
      ) {
        const workspace = videoAgentView.workspace;
        const previousBrief = [
          supervisorRuntime.state.videoAgentPlan?.publicGoal,
          workspace?.script?.content,
          workspace?.scriptStages?.find((stage) => stage.stageId === "start")?.content,
        ].find((item) => typeof item === "string" && item.trim())?.trim() || "";
        const hasActivePlan = Boolean(supervisorRuntime.state.videoAgentPlan?.planId)
          || (workspace?.scriptStages?.length ?? 0) > 0
          || Boolean(workspace?.script?.content);
        if (
          hasActivePlan
          && isMajorRequirementChangeRequest(text, previousBrief)
          && !isRedesignTaskPlanRequest(text)
        ) {
          await appendMessageForConversation(
            { ...message, conversationId: activeConversation },
            activeConversation,
          );
          pushAssistant(
            "检测到当前输入与既有任务规划差异较大。若要按新需求继续，请回复「重新设计任务规划」；系统将废弃当前执行计划并重新规划后再执行。若只是微调脚本/资产包，请直接说明修改点。",
            activeConversation,
          );
          setReferencedMaterials([]);
          if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
          return;
        }
        if (hasActivePlan && isRedesignTaskPlanRequest(text)) {
          characterSupplementNoticeRef.current =
            "已确认按新需求重新设计任务规划，正在重新生成执行方案…";
          scriptPlanConfirmedRef.current = false;
        }
      }
      const shouldRegisterRuntime = ownership.orchestrationMode === "video_agent_v2"
        || ownership.agentRuntimeMode === "assist"
        || ownership.agentRuntimeMode === "shadow"
        || ownership.agentRuntimeMode === "primary";
      if (shouldRegisterRuntime && !runtimeOptions.skipRuntimeRegistration) {
        const restoredInterruptId = ownership.orchestrationMode === "video_agent_v2"
          && supervisorRuntime.state.conversationId === activeConversation
          ? supervisorRuntime.state.interrupt?.interruptId ?? null
          : null;
        const pendingTurn: PendingSupervisorTurn = {
          conversationId: activeConversation,
          clientInputId: message.id,
          content: text,
          materials,
          replyToMessageId,
          artifactRefs,
          interruptId: ownership.orchestrationMode === "video_agent_v2"
            ? interruptId ?? restoredInterruptId
            : null,
          explicitAction: null,
          continueLegacy: false,
          registrationStatus: "pending",
        };
        await persistPendingSupervisorTurns(
          (current) => (
            current.some((item) => item.clientInputId === message.id)
              ? current
              : [...current, pendingTurn]
          ),
          activeConversation,
        );
        ensurePendingSupervisorTurnVisible(pendingTurn);
        // 在 turns/start 返回前先给即时回执，避免用户感觉“发出去没反应”。
        if (ownership.orchestrationMode === "video_agent_v2") {
          const notice = characterSupplementNoticeRef.current
            || "已收到创作请求，正在生成执行方案…";
          characterSupplementNoticeRef.current = "";
          void appendPersistedSupervisorNotice(
            notice,
            activeConversation,
            `agent-ack:${activeConversation}:${message.id}:v1`,
          );
        }
        setReferencedMaterials([]);
        if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
        return;
      }
      const pendingPlanMessageJob = pendingMessageJobRef.current;
      if (pendingPlanMessageJob && isPendingPlanSaveForConversation(pendingPlanMessageJob, activeConversation)) {
        schedulePendingMessageJobResume(pendingPlanMessageJob, 0);
        pushAssistant("当前 plan.md 仍在保存，我已继续查询原任务；保存完成后请重新发送这条消息。", activeConversation);
        return;
      }
      if (shouldUseRecoverableIntakeEntry(text, materials, activeConversation)) {
        const pendingMessageJob = await startConversationMessageJobForConversation(message, activeConversation, {
          type: "handle_send",
          content: text,
          materials,
        });
        if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
        if (pendingMessageJob) await resumePendingMessageJob(pendingMessageJob);
        return;
      }
      await appendMessageForConversation(message, activeConversation);
      if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
    } catch (err) {
      pushAssistant(`对话保存失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      return;
    }
    const sceneGlobalAssetReference = sceneGlobalAssetReferenceFromMaterials(materials);
    if (sceneGlobalAssetReference) {
      if (sceneGlobalAssetReference.scene_global_asset_action === "delete") {
        await handleDeleteReferencedGlobalAsset(sceneGlobalAssetReference, activeConversation);
      } else {
        await handleEditReferencedGlobalAsset(sceneGlobalAssetReference, text, activeConversation, materials);
      }
      return;
    }
    if (pendingImageEditRequestRef.current?.conversationId === activeConversation) {
      const pendingRequest = pendingImageEditRequestRef.current;
      if (looksLikeImageEditPrompt(text)) {
        pendingImageEditRequestRef.current = null;
      } else {
        const flowMaterials = mergeMaterials(pendingRequest.materials, materials);
        const nextPrompt = text.trim() || pendingRequest.prompt;
        if (!hasImageMaterial(flowMaterials)) {
          pushAssistant("我还没有找到需要编辑的原图，请上传需要编辑的图片后再提交。", activeConversation);
          return;
        }
        await showImageEditOptions({
          ...pendingRequest,
          prompt: nextPrompt,
          materials: flowMaterials,
        });
        return;
      }
    }
    const pendingPptOutlineRevision = pptOutlineRevisionArtifactRef.current;
    const pendingPptOutlineArtifact = pendingPptOutlineRevision?.artifact;
    if (pendingPptOutlineRevision?.conversationId === activeConversation && pendingPptOutlineArtifact?.pptSummary) {
      const projectId = pptProjectId(pendingPptOutlineArtifact);
      pptOutlineRevisionArtifactRef.current = null;
      if (!projectId) {
        pushAssistant("当前 PPT 项目 ID 缺失，无法更新大纲。请重新生成 PPT 大纲。", activeConversation);
        return;
      }
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到 PPT 大纲修改意见，正在调用 SmartPPT 更新大纲…", activeConversation);
      try {
        const request: PptSummaryUpdateJobRequest = {
          original_outline: String(pendingPptOutlineArtifact.pptSummary.summary || ""),
          modification_opinion: text,
          smart_ppt_project_id: projectId,
        };
        const started = await api.createPptSummaryUpdateJob(request);
        const pendingPptJob: PendingPptJob = {
          job_id: started.job_id,
          conversation_id: activeConversation,
          source_message_id: "",
          kind: "summary_update",
          started_at: new Date().toISOString(),
          request,
          artifact: pendingPptOutlineArtifact,
          context: {
            formValues: pendingPptOutlineArtifact.formValues || {},
            materials: mergeMaterials(pendingPptOutlineArtifact.materials, materials),
            coreMessage: `${pendingPptOutlineArtifact.coreMessage || pendingCore}\n大纲修改意见：${text}`,
            intakeContext: pendingPptOutlineArtifact.intakeContext,
            pptStyle: String(pendingPptOutlineArtifact.pptStyle || pendingPptOutlineArtifact.formValues?.ppt_style || "极简商务"),
          },
        };
        await persistPendingPptJob(pendingPptJob, activeConversation, "ppt_outline_update_running", {
          intent: "ppt",
          ppt_outline_feedback: text,
          formValues: pendingPptOutlineArtifact.formValues || {},
          materials: mergeMaterials(pendingPptOutlineArtifact.materials, materials),
          intakeContext: pendingPptOutlineArtifact.intakeContext,
        });
        await resumePendingPptJob(pendingPptJob);
      } catch (err) {
        pushAssistant(`PPT 大纲更新失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    const pendingPlanRevision = planRevisionArtifactRef.current;
    if (pendingPlanRevision?.conversationId === activeConversation && pendingPlanRevision.artifact.intent && pendingPlanRevision.artifact.formValues) {
      const revisionArtifact = pendingPlanRevision.artifact;
      const flowMaterials = mergeMaterials(revisionArtifact.materials, materials);
      if (!isCreationIntent(revisionArtifact.intent) || !revisionArtifact.formValues || !revisionArtifact.plan || !revisionArtifact.selectedDirection) return;
      planRevisionArtifactRef.current = null;
      const choice: PendingPlanRevisionChoice = {
        conversationId: activeConversation,
        artifact: revisionArtifact,
        feedback: text.trim(),
        materials: flowMaterials,
        sourceMessageId: pendingPlanRevision.sourceMessageId || "",
        processedKey: pendingPlanRevision.processedKey,
      };
      setPendingPlanRevisionChoice(choice);
      pushAssistant("已收到 plan.md 修改意见，请选择是在当前创意上修改，还是放弃当前创意并重新生成 3 个方向。", activeConversation);
      void updateConversationWithProgress(activeConversation, {
          last_phase: "plan_revision_mode_pending",
          context: {
            ...makeSnapshot(activeConversation),
            pendingPlanRevisionRequest: null,
            pending_plan_revision_request: null,
            pendingPlanRevisionChoice: choice,
            pending_plan_revision_choice: choice,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
      return;
    }
    const pendingImageRevision = imageRevisionArtifactRef.current;
    const pendingImageRevisionArtifact = pendingImageRevision?.artifact;
    const pendingSceneGlobalAssetReference =
      pendingImageRevision?.conversationId === activeConversation && pendingImageRevisionArtifact?.imageResult
        ? sceneGlobalAssetReferenceFromMaterials(pendingImageRevisionArtifact.materials || [])
        : null;
    if (pendingSceneGlobalAssetReference) {
      if (!text.trim()) {
        pushAssistant(`请在输入框填写「${pendingSceneGlobalAssetReference.name}」的图片修改意见，我会继续编辑这张全局素材。`, activeConversation);
        return;
      }
      imageRevisionArtifactRef.current = null;
      const flowMaterials = mergeMaterials(pendingImageRevisionArtifact?.materials || [], materials);
      await handleEditReferencedGlobalAsset(pendingSceneGlobalAssetReference, text, activeConversation, flowMaterials);
      return;
    }
    if (pendingImageRevision?.conversationId === activeConversation && pendingImageRevisionArtifact?.imagePrepare && pendingImageRevisionArtifact.imageResult) {
      const flowMaterials = mergeMaterials(pendingImageRevisionArtifact.materials, materials);
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到图片修改意见，正在重新准备参数并生成图片…", activeConversation);
      try {
        const imagePrepare = await api.prepareImageGeneration(
          {
            ...buildImageRevisionPreparePayload({
              formValues: pendingImageRevisionArtifact.formValues,
              selectedDirection: pendingImageRevisionArtifact.selectedDirection as unknown as Record<string, unknown>,
              planMarkdown: pendingImageRevisionArtifact.plan?.plan_markdown,
              feedback: text,
            }),
            creation_contract: pendingImageRevisionArtifact.plan?.creation_contract,
            materials: flowMaterials,
            intake_context: pendingImageRevisionArtifact.intakeContext,
          },
        );
        if (!imagePrepare.ok) {
          pushArtifact("图片重新生成准备失败，请查看提示。", {
            type: "image_prepare",
            title: "图片重新生成准备",
            description: imagePrepare.message,
            actionLabel: "查看",
            imagePrepare,
            imageRevisionFeedback: text,
            intent: "image",
            formValues: pendingImageRevisionArtifact.formValues,
            intakeContext: pendingImageRevisionArtifact.intakeContext,
            materials: flowMaterials,
            selectedDirection: pendingImageRevisionArtifact.selectedDirection,
            plan: pendingImageRevisionArtifact.plan,
          }, activeConversation);
          setBusyForConversation(activeConversation, false);
          return;
        }
        const request: ImageGenerationJobRequest = {
          method: imagePrepare.method,
          prompt: imagePrepare.prompt,
          negative_prompt: imagePrepare.negative_prompt,
          params: imagePrepare.params,
        };
        const started = await api.startImageGenerationJob(request);
        const pendingImageJob: PendingImageJob = {
          job_id: started.job_id,
          conversation_id: activeConversation,
          source_message_id: "",
          kind: "image_regeneration",
          job_api: "generate",
          started_at: new Date().toISOString(),
          request,
          imagePrepare,
          revision_feedback: text,
          artifact: {
            ...pendingImageRevisionArtifact,
            imagePrepare,
            imageRevisionFeedback: text,
            materials: flowMaterials,
          },
        };
        await persistPendingImageJob(pendingImageJob, activeConversation, "image_regeneration_running", {
          image_revision_feedback: text,
          intake_context: pendingImageRevisionArtifact.intakeContext,
          materials: flowMaterials,
          image_prepare: imagePrepare,
          pendingImageRevision: null,
          pending_image_revision: null,
        });
        imageRevisionArtifactRef.current = null;
        await resumePendingImageJob(pendingImageJob);
      } catch (err) {
        pushAssistant(`图片重新生成失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    const pendingVideoRevision = videoRevisionArtifactRef.current;
    const pendingVideoRevisionArtifact = pendingVideoRevision?.artifact;
    const pendingMergedVideo = pendingVideoRevisionArtifact?.mergedVideo;
    const pendingGeneratedSceneVideos = pendingVideoRevisionArtifact?.generatedSceneVideos;
    const pendingVideoScenePackages = pendingVideoRevisionArtifact?.videoScenePackages;
    if (pendingVideoRevision?.conversationId === activeConversation && pendingVideoRevisionArtifact && pendingMergedVideo && pendingGeneratedSceneVideos && pendingVideoScenePackages) {
      const revisionArtifact = pendingVideoRevisionArtifact;
      const flowMaterials = mergeMaterials(revisionArtifact.materials, materials);
      const mergedVideo = pendingMergedVideo;
      const generatedSceneVideos = pendingGeneratedSceneVideos;
      const videoScenePackages = pendingVideoScenePackages;
      const originalVideoScenePackages =
        revisionArtifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, activeConversation) || videoScenePackages;
      const mergedVideoUrl = mergedVideo.merged_video_url;
      videoRevisionArtifactRef.current = null;
      if (!mergedVideoUrl) {
        pushAssistant("当前没有可分析的合并视频链接，无法进入视频修改流程。", activeConversation);
        return;
      }
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到视频修改意见，正在调用 QAAgent QC 质检 Skill…", activeConversation);
      try {
        const qualityReview = await api.reviewVideoQuality({
          merged_video_url: mergedVideoUrl,
          scene_videos: generatedSceneVideos.scene_videos.map((scene) => ({
            scene_id: scene.scene_id,
            scene_index: scene.scene_index,
            video_url: scene.video_url,
          })),
          scene_packages: videoScenePackages.scene_packages as unknown as Array<Record<string, unknown>>,
          original_scene_packages: originalVideoScenePackages.scene_packages as unknown as Array<Record<string, unknown>>,
          plan: revisionArtifact.plan as unknown as Record<string, unknown>,
          form_values: revisionArtifact.formValues || {},
          intake_context: revisionArtifact.intakeContext || {},
          selected_direction: revisionArtifact.selectedDirection as unknown as Record<string, unknown>,
          materials: flowMaterials,
          user_feedback: text,
        });
        const affectedSceneIds = new Set(qualityReview.affected_scene_ids || []);
        const affectedSceneLabel = formatSceneIndexesForMessage(videoScenePackages.scene_packages, affectedSceneIds);
        pushArtifact(qualityReview.ok ? "QAAgent QC 质检已完成，请选择本轮修改策略。" : "QAAgent QC 质检失败，可选择只按用户意见继续修改。", {
          type: "video_quality_review",
          title: "QAAgent QC 质检",
          description: qualityReview.ok
            ? `质检定位：${affectedSceneLabel}。`
            : qualityReview.message,
          actionLabel: "选择",
          videoQualityReview: qualityReview,
          videoRevisionFeedback: text,
          videoScenePackages,
          originalVideoScenePackages,
          generatedSceneVideos,
          mergedVideo,
          intent: "video",
          formValues: revisionArtifact.formValues,
          intakeContext: revisionArtifact.intakeContext,
          materials: flowMaterials,
          selectedDirection: revisionArtifact.selectedDirection,
          plan: revisionArtifact.plan,
        }, activeConversation);
        if (activeConversation) {
          void updateConversationWithProgress(activeConversation, {
              last_phase: qualityReview.ok ? "video_quality_review_ready" : "video_quality_review_failed",
              context: {
                ...makeSnapshot(),
                video_revision_feedback: text,
                intake_context: revisionArtifact.intakeContext,
                materials: flowMaterials,
                video_quality_review: qualityReview,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`QAAgent QC 质检失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    setBusyForConversation(activeConversation, true);
    pushAssistant("正在调用采集 Agent 识别意图，并抽取可自动填充的表单字段…", activeConversation);
    try {
      const pendingIntakeJob = await startIntakeAnalyzeJob(
        activeConversation,
        { prompt: text, materials },
        message.id,
      );
      void pendingIntakeJob;
    } catch (err) {
      pushAssistant(`采集 Agent 意图识别失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      setBusyForConversation(activeConversation, false);
    }
  };

  const handleRetryVideoAnalysis = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.videoAnalysis || artifact.videoAnalysis.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const prompt = artifact.coreMessage || msg.content;
    const materials = artifact.materials || msg.materials || [];
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在重新调用视频分析 Skill…", targetConversationId);
    try {
      const videoAnalysis = await api.analyzeStoryboards({ prompt, materials });
      if (!videoAnalysis.ok) releaseArtifactAction(processedKey);
      pushArtifact(videoAnalysis.ok ? "视频分析已重新完成，结果如下。" : "视频分析仍未完成，请查看原因后补充视频链接。", {
        type: "video_analysis_result",
        title: videoAnalysis.mode === "batch" ? "批量视频分析" : "视频分析",
        description: videoAnalysis.ok
          ? `${videoAnalysis.video_urls.length} 个视频，调用 ${videoAnalysis.endpoint}`
          : videoAnalysis.message,
        actionLabel: "查看",
        intent: "video_analysis",
        coreMessage: prompt,
        materials,
        videoAnalysis,
      }, targetConversationId);
      if (targetConversationId) {
        void updateConversationWithProgress(targetConversationId, {
            last_phase: videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed",
            context: {
              ...makeSnapshot(),
              intent: "video_analysis",
              materials,
              video_analysis: videoAnalysis,
            } as unknown as Record<string, unknown>,
          }, { intent: null })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频分析重试失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  async function onEvent(e: TaskEvent) {
    // SSE 事件分发器：后端事件表可能因为断线重连/afterId 被重复消费，这里先按 id 去重。
    if (e.id && seenEventIdsRef.current.has(e.id)) return;
    if (e.id) {
      seenEventIdsRef.current.add(e.id);
      lastEventIdRef.current = Math.max(lastEventIdRef.current, e.id);
    }
    const phase = (e.data.phase as string) || "";
    appendTimelineEvent(e);
    switch (e.event) {
      case "phase_change":
        if (phase) {
          // Brief 未人工确认前，忽略 generate/edit/qc/done 阶段回放，避免旧 run 的 pending
          // 事件把画布提前推进到生成结果态。
          if (["generate", "edit", "qc", "done"].includes(phase) && !briefConfirmedRef.current) return;
          setCanvas((c) => ({ ...c, phase: phase as TaskPhase }));
          if (["segment_review", "edit_review", "qc_review", "done"].includes(phase)) {
            void loadResults(phase as TaskPhase);
            pushReviewArtifact(phase as TaskPhase);
          }
          if (PHASE_MSG[phase] && !announcedPhasesRef.current.has(phase)) {
            announcedPhasesRef.current.add(phase);
            if (!(phase in REVIEW_ARTIFACT)) pushAssistant(PHASE_MSG[phase]);
          }
        }
        break;
      case "brief_ready":
        // brief_ready 表示后端在 LangGraph interrupt 前已经准备好 Brief，前端需要展示确认卡。
        if (briefConfirmedRef.current || briefReadyShownRef.current) return;
        briefReadyShownRef.current = true;
        setCanvas((c) => ({ ...c, phase: "brief_review", brief: toBrief((e.data.brief as Record<string, unknown>) || {}) }));
        setBusy(false);
        pushArtifact("Brief 已生成。点击下方素材卡打开画布查看和确认。", {
          type: "brief",
          title: "视频 Brief",
          description: "分镜、旁白与投放参数",
          actionLabel: "查看",
        });
        break;
      case "task_done":
        // task_done 代表业务任务已完成，可以从 /assets 拉取最终视频或生成片段。
        await loadResults();
        break;
      case "brief_confirmed":
        // brief_confirmed 是业务事件，表示用户确认动作已被后端接收。
        briefConfirmedRef.current = true;
        setBriefConfirmed(true);
        break;
      case "run_finished":
        // run_finished 只表示某个 LangGraph run 结束，不一定等于业务任务 done；
        // 需要再查任务详情，同步 checkpoint 后的 phase/status/brief。
        await refreshTaskAfterRun();
        break;
      case "task_failed":
        pushAssistant(`生成失败:${String(e.data.error ?? "未知错误")}`);
        setBusy(false);
        break;
      case "auth_revoked":
        // 后端 SSE 会在长连接期间持续复查 content-app 登录态；被禁用或 token 失效时会主动断开。
        pushAssistant(`登录态已失效:${String(e.data.message ?? "请重新从 content-app 进入")}`);
        setBusy(false);
        unsubRef.current();
        break;
    }
  }

  async function loadResults(nextPhase: TaskPhase = "done") {
    // 从 /assets 拉取画布可展示的视频资产。当前只展示 final_video 和 generated_video；
    // jianying_draft 是本地草稿路径，浏览器通常不能直接播放。
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const [assets, taskResult] = await Promise.all([api.listAssets(id), api.getResult(id).catch(() => null)]);
      const videos = assets.filter((a) => a.asset_type === "final_video" || a.asset_type === "generated_video");
      const results: VideoResult[] = await Promise.all(
        videos.map(async (a, i) => {
          let url = a.url;
          if (a.asset_type === "final_video") {
            try {
              url = await api.assetContentBlobUrl(id, a.asset_id);
            } catch {
              url = "";
            }
          }
          return {
            id: a.asset_id || `r${i}`,
            url,
            assetType: a.asset_type,
            status: a.status === "ready" && url ? "success" : a.status === "error" ? "failed" : "pending",
          };
        }),
      );
      const qcReport = taskResult?.result?.qc_report;
      setCanvas((c) => ({
        ...c,
        phase: nextPhase,
        results,
        qcReport: qcReport && typeof qcReport === "object" ? c.qcReport || (qcReport as CanvasState["qcReport"]) : c.qcReport,
      }));
      if (nextPhase === "done") {
        pushArtifact("生成完成,素材已就绪。点击下方素材卡打开画布查看。", {
          type: "results",
          title: "生成素材",
          description: `${results.length} 条视频结果`,
          actionLabel: "打开",
        });
      }
    } catch {
      pushAssistant("结果拉取失败,请稍后在历史中查看。");
    } finally {
      setBusy(false);
    }
  }

  async function refreshTaskAfterRun() {
    // LangGraph run 结束后重新查询业务任务。后端 getTask 会先同步 checkpoint，
    // 因此前端能拿到最新 phase、brief、error 和 result。
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const task = await api.getTask(id);
      const confirmed = task.phase !== "brief_review";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase: (task.phase as TaskPhase) || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
      }));
      if (task.status === "done") {
        await loadResults("done");
        return;
      }
      if (["segment_review", "edit_review", "qc_review"].includes(task.phase)) {
        await loadResults(task.phase as TaskPhase);
        if (PHASE_MSG[task.phase] && !announcedPhasesRef.current.has(task.phase)) {
          announcedPhasesRef.current.add(task.phase);
          pushAssistant(PHASE_MSG[task.phase]);
        }
        return;
      }
      if (task.phase === "brief_review") {
        setBusy(false);
        pushAssistant("Brief 已就绪,请打开素材卡确认后再生成视频。");
      }
      if (task.status === "error") {
        setBusy(false);
        pushAssistant(`生成失败:${task.error || "未知错误"}`);
      }
    } catch {
      pushAssistant("任务状态同步失败,请稍后重试。");
      setBusy(false);
    }
  }

  // 参数弹窗确认后的主链路：创建业务任务 -> 记录 taskId -> 重置事件缓存 -> 订阅 SSE。
  const handleConfirmParams = async (form: GenParamsForm) => {
    const dialogContext = pendingDialogContextRef.current;
    const targetConversationId = dialogContext?.conversationId || conversationIdRef.current;
    const flowMaterials = dialogContext?.materials || pendingMaterials;
    const flowCoreMessage = dialogContext?.coreMessage || pendingCore;
    const flowIntakeContext = dialogContext?.intakeContext || {};
    setDialogOpen(false);
    setPendingFormValues({});
    setBusyForConversation(targetConversationId, true);
    const values = valuesFromForm(form);
    try {
      if (form.intent === "ppt") {
        advanceWorkflowProgress(targetConversationId, "ppt_outline_running", { intent: "ppt", flow_kind: "standard" });
        pushAssistant("正在调用 SmartPPT 生成 PPT 大纲，这一步可能需要等待一会儿…", targetConversationId);
        const request: PptSummaryJobRequest = {
          ppt_topic: form.ppt_topic,
          ppt_style: form.ppt_style,
          attachments: form.attachments,
        };
        const started = await api.createPptSummaryJob(request);
        pendingDialogContextRef.current = null;
        const pendingPptJob: PendingPptJob = {
          job_id: started.job_id,
          conversation_id: targetConversationId,
          source_message_id: "",
          kind: "summary_generation",
          started_at: new Date().toISOString(),
          request,
          context: {
            formValues: values,
            materials: mergeMaterials(flowMaterials, form.attachments),
            coreMessage: flowCoreMessage,
            intakeContext: flowIntakeContext,
            pptStyle: form.ppt_style,
          },
        };
        await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_outline_running", {
          intent: "ppt",
          ppt_form: form,
          form_values: values,
          intake_context: flowIntakeContext,
          materials: mergeMaterials(flowMaterials, form.attachments),
        });
        await resumePendingPptJob(pendingPptJob);
        setBusyForConversation(targetConversationId, false);
        return;
      }
      pendingDialogContextRef.current = null;
      await startDirectionJob(
        targetConversationId,
        {
          intent: form.intent,
          values,
          materials: flowMaterials,
          intake_context: flowIntakeContext,
        },
        {
          intent: form.intent,
          formValues: values,
          materials: flowMaterials,
          coreMessage: flowCoreMessage,
          intakeContext: flowIntakeContext,
          form,
        },
        "directions_running",
      );
      setBusyForConversation(targetConversationId, false);
    } catch (err) {
      pushAssistant(`采集处理失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleCancelParamsDialog = () => {
    const dialogContext = pendingDialogContextRef.current;
    const targetConversationId = dialogContext?.conversationId || conversationIdRef.current;
    const cancelledIntent = pendingIntent;
    setDialogOpen(false);
    setPendingCore("");
    setPendingFormValues({});
    setPendingMaterials([]);
    pendingDialogContextRef.current = null;
    const flowDraft = makeFlowDraft("form_cancelled", {
      intent: cancelledIntent,
      coreMessage: dialogContext?.coreMessage || pendingCore,
      materials: dialogContext?.materials || pendingMaterials,
      intakeContext: dialogContext?.intakeContext,
      formValues: pendingFormValues,
    });
    flowDraftRef.current = flowDraft;
    setBusyForConversation(targetConversationId, false);
    pushAssistant("已取消当前需求表单，流程已终止。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "form_cancelled",
          context: {
            ...makeSnapshot(),
            flowDraft,
            intent: cancelledIntent,
            form_cancelled: true,
          } as unknown as Record<string, unknown>,
        }, { intent: cancelledIntent })
        .catch(() => {});
    }
  };

  const handleApprovePptOutline = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.pptSummary) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const projectId = pptProjectId(artifact);
    if (!projectId) {
      pushAssistant("当前 PPT 项目 ID 缺失，无法继续生成页面结构。", targetConversationId);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "ppt_content_json_running", { intent: "ppt" });
    pushAssistant("PPT 大纲已确认，正在生成页面 JSON 并准备生成 PPT 图片…", targetConversationId);
    try {
      const pptStyle = String(artifact.pptStyle || artifact.formValues?.ppt_style || "极简商务");
      const request: PptContentJsonJobRequest = {
        original_outline: String(artifact.pptSummary.summary || ""),
        ppt_style: pptStyle,
        smart_ppt_project_id: projectId,
      };
      const started = await api.createPptContentJsonJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "content_json_generation",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_content_json_running", {
        intent: "ppt",
        ppt_summary: artifact.pptSummary,
      });
      await resumePendingPptJob(pendingPptJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`PPT 页面图片生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRevisePptOutline = (msg: ChatMessage) => {
    if (!msg.artifact?.pptSummary) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    pptOutlineRevisionArtifactRef.current = { conversationId: targetConversationId, artifact: msg.artifact };
    pushAssistant("请在输入框填写 PPT 大纲修改意见，我会基于当前大纲继续更新。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "ppt_outline_revision_requested",
          context: {
            ...makeSnapshot(targetConversationId),
            pendingPptOutlineRevision: pptOutlineRevisionArtifactRef.current,
            pending_ppt_outline_revision: pptOutlineRevisionArtifactRef.current,
          } as unknown as Record<string, unknown>,
        }, { intent: "ppt" })
        .catch(() => {});
    }
  };

  const handleRegeneratePptImage = async (msg: ChatMessage, pageIndex: number) => {
    const artifact = msg.artifact;
    if (!artifact?.pptImages) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const projectId = pptProjectId(artifact);
    const pageJson = pptPageJson(artifact, pageIndex);
    if (!projectId || !pageJson) {
      pushAssistant("当前页缺少 PPT 项目 ID 或页面 JSON，无法重新生成。", targetConversationId);
      return;
    }
    const runningImages: PptImagesResult = {
      ...artifact.pptImages,
      ok: false,
      pages: artifact.pptImages.pages.map((page) =>
        page.page_index === pageIndex
          ? {
              ...page,
              status: "running",
              image_url: null,
              error: null,
              json_content: page.json_content || pageJson,
            }
          : page,
      ),
      message: `第 ${pageIndex} 页 PPT 图片重新生成中。`,
      quota_insufficient: false,
    };
    updatePptImagesArtifactInMessage(msg.id, targetConversationId, runningImages, artifact);
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`正在重新生成第 ${pageIndex} 页 PPT 图片…`, targetConversationId);
    try {
      const request: PptImageRegenerationJobRequest = {
        page_index: pageIndex,
        page_json: pageJson,
        smart_ppt_project_id: projectId,
      };
      const started = await api.createPptImageRegenerationJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "image_regeneration",
        started_at: new Date().toISOString(),
        request,
        artifact: { ...artifact, pptImages: runningImages },
        image_message_id: msg.id,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_image_regeneration_running", {
        intent: "ppt",
        ppt_images: runningImages,
      });
      await resumePendingPptJob(pendingPptJob);
    } catch (err) {
      const failedImages: PptImagesResult = {
        ...runningImages,
        ok: false,
        pages: runningImages.pages.map((page) =>
          page.page_index === pageIndex
            ? {
                ...page,
                status: "failed",
                image_url: null,
                error: err instanceof Error ? err.message : String(err),
              }
            : page,
        ),
        message: `第 ${pageIndex} 页 PPT 图片重新生成失败。`,
      };
      updatePptImagesArtifactInMessage(msg.id, targetConversationId, failedImages, artifact);
      pushAssistant(`PPT 页面图片重新生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleGeneratePptFile = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.pptImages) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const projectId = pptProjectId(artifact);
    const fileUrls = pptImageFileUrls(artifact);
    if (!projectId || !fileUrls.length) {
      pushAssistant("请先确保所有 PPT 页面图片都已生成成功。", targetConversationId);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "ppt_file_generation_running", { intent: "ppt" });
    pushAssistant("正在调用 SmartPPT 生成 PPT 附件…", targetConversationId);
    try {
      const request: PptFileJobRequest = { smart_ppt_project_id: projectId, file_urls: fileUrls };
      const started = await api.createPptFileJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "file_generation",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_file_generation_running", {
        intent: "ppt",
        ppt_images: artifact.pptImages,
      });
      await resumePendingPptJob(pendingPptJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`PPT 附件生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRegeneratePptFile = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!artifact?.pptFile || artifact.pptDone || isPptDoneForConversation(targetConversationId)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const projectId = pptProjectId(artifact);
    const fileUrls = pptImageFileUrls(artifact);
    if (!projectId || !fileUrls.length) {
      releaseArtifactAction(processedKey);
      pushAssistant("没有找到可用于重新生成 PPT 附件的页面图片。", targetConversationId);
      return;
    }
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在重新生成 PPT 附件…", targetConversationId);
    try {
      const request: PptFileJobRequest = { smart_ppt_project_id: projectId, file_urls: fileUrls };
      const started = await api.createPptFileJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "file_regeneration",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_file_regeneration_running", {
        intent: "ppt",
        ppt_images: artifact.pptImages,
      });
      await resumePendingPptJob(pendingPptJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`PPT 附件重新生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleAcceptPptFile = (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!msg.artifact?.pptFile?.ok || msg.artifact.pptDone || isPptDoneForConversation(targetConversationId)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setPptDoneForConversation(targetConversationId, true);
    markPptFileDoneInMessage(msg.id, targetConversationId);
    pushAssistant("已确认 PPT 附件满意，制作 PPT 流程结束。", targetConversationId);
    void updateConversationWithProgress(targetConversationId, {
        last_phase: "ppt_done",
        context: {
          ...makeSnapshot(),
          intent: "ppt",
          ppt_done: true,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {})
      .finally(() => releaseArtifactAction(processedKey));
  };

  const handleRegenerateDirections = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (artifact?.type !== "directions" || !isCreationIntent(artifact.intent) || !artifact.formValues) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (hasLaterDirectionSuccessor(messagesRef.current, targetConversationId, msg)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const previousDirections = (artifact.directions || []).map((direction) => ({
      title: direction.title,
      description: direction.description,
      tags: direction.tags,
      data: direction.data,
    }));
    const regenerationFeedback = "用户对上一轮 3 个创意方向都不满意，请避开上一轮方向，生成新的差异化方向。";
    setBusyForConversation(targetConversationId, true);
    pushAssistant("已收到不满意反馈，正在重新生成 3 个创意方向…", targetConversationId);
    try {
      await startDirectionJob(
        targetConversationId,
        {
          intent: artifact.intent,
          values: artifact.formValues,
          materials: artifact.materials || [],
          product_creative_profile: {
            core_message: artifact.coreMessage || pendingCore,
            regenerate: true,
            revision_feedback: regenerationFeedback,
            regeneration_feedback: regenerationFeedback,
            previous_creative_directions: previousDirections,
          },
          intake_context: artifact.intakeContext,
        },
        {
          intent: artifact.intent,
          formValues: artifact.formValues,
          materials: artifact.materials || [],
          coreMessage: artifact.coreMessage || pendingCore,
          intakeContext: artifact.intakeContext,
          revisionFeedback: regenerationFeedback,
        },
        "directions_running",
        msg.id,
      );
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`重新生成创意方向失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleSelectDirection = async (msg: ChatMessage, direction: CreativeDirectionResponse) => {
    if (!isCreationIntent(msg.artifact?.intent) || !msg.artifact?.formValues) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (hasLaterDirectionSuccessor(messagesRef.current, targetConversationId, msg)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "plan_generation_running", { intent: msg.artifact.intent });
    pushAssistant(`已选择创意方向「${direction.title}」，正在生成 plan.md…`, targetConversationId);
    try {
      const request: PlanGenerationJobRequest = {
        intent: msg.artifact.intent,
        form_values: msg.artifact.formValues,
        selected_direction: direction as unknown as Record<string, unknown>,
        product_creative_profile: { core_message: msg.artifact.coreMessage || pendingCore },
        intake_context: msg.artifact.intakeContext,
        materials: msg.artifact.materials || [],
      };
      const started = await api.startPlanMarkdownJob(request);
      const pendingPlanJob: PendingPlanJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "plan_generation",
        started_at: new Date().toISOString(),
        request,
        context: {
          intent: msg.artifact.intent,
          formValues: msg.artifact.formValues,
          selectedDirection: direction,
          materials: msg.artifact.materials || [],
          coreMessage: msg.artifact.coreMessage || pendingCore,
          intakeContext: msg.artifact.intakeContext,
          processedKey,
        },
      };
      const persistenceContext = {
        selected_direction: direction,
        form_values: msg.artifact.formValues,
        intake_context: msg.artifact.intakeContext,
        materials: msg.artifact.materials || [],
      };
      await continueStartedPlanJob({
        pendingPlanJob,
        saveRecovery: (job) => {
          pendingPlanJobRef.current = job;
          savePendingPlanJobRecovery(browserSessionStorage(), job);
        },
        persistPending: (job) => persistPendingPlanJob(
          job,
          targetConversationId,
          "plan_generation_running",
          persistenceContext,
        ),
        notifyRecovery: notifyPlanJobPersistenceRecovery,
        schedulePersistenceRetry: (job) => schedulePendingPlanJobPersistence(
          job,
          "plan_generation_running",
          persistenceContext,
        ),
        resumePending: resumePendingPlanJob,
      });
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleApprovePlan = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.plan) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    // Video Agent 脚本方案卡：「同意方案」= 右侧确认 = 自然语言确认
    if (
      artifact.scriptPlanConfirmForAssets
      || artifact.title === "脚本方案待确认"
      || artifact.title === "已确认脚本方案"
    ) {
      if (!targetConversationId) return;
      const processedKey = beginArtifactAction(msg, targetConversationId);
      if (!processedKey) return;
      try {
        await confirmScriptPlanAndGenerateAssetPackage(
          targetConversationId,
          artifact.plan.plan_markdown || "",
          msg.id,
        );
      } catch (err) {
        releaseArtifactAction(processedKey);
        pushAssistant(
          err instanceof Error ? err.message : `确认脚本方案失败：${String(err)}`,
          targetConversationId,
        );
      }
      return;
    }
    if (!artifact.intent || !artifact.formValues || !artifact.selectedDirection) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    if (artifact.intent === "image") {
      setBusyForConversation(targetConversationId, true);
      advanceWorkflowProgress(targetConversationId, "image_prepare_running", { intent: "image" });
      pushAssistant("图片 plan.md 已同意，正在准备图片生成参数…", targetConversationId);
      try {
        const imagePrepare = await api.prepareImageGeneration({
          form_values: artifact.formValues,
          plan_markdown: artifact.plan.plan_markdown,
          selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
          creation_contract: artifact.plan.creation_contract,
          materials: artifact.materials || [],
          intake_context: artifact.intakeContext,
        });
        if (!imagePrepare.ok) {
          releaseArtifactAction(processedKey);
          pushArtifact("图片生成准备发现当前能力暂不可用，请按提示调整。", {
            type: "image_prepare",
            title: "图片生成准备",
            description: imagePrepare.message,
            actionLabel: "查看",
            imagePrepare,
            intent: "image",
            formValues: artifact.formValues,
            intakeContext: artifact.intakeContext,
            materials: artifact.materials || [],
            selectedDirection: artifact.selectedDirection,
            plan: artifact.plan,
          }, targetConversationId);
          if (targetConversationId) {
            void updateConversationWithProgress(targetConversationId, {
                last_phase: "image_generation_blocked",
                context: {
                  ...makeSnapshot(),
                  plan_approved: true,
                  plan_markdown: artifact.plan.plan_markdown,
                  intake_context: artifact.intakeContext,
                  materials: artifact.materials || [],
                  image_prepare: imagePrepare,
                } as unknown as Record<string, unknown>,
              })
              .catch(() => {});
          }
          return;
        }
        pushAssistant(`正在调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
        const request: ImageGenerationJobRequest = {
          method: imagePrepare.method,
          prompt: imagePrepare.prompt,
          negative_prompt: imagePrepare.negative_prompt,
          params: imagePrepare.params,
        };
        const started = await api.startImageGenerationJob(request);
        const pendingImageJob: PendingImageJob = {
          job_id: started.job_id,
          conversation_id: targetConversationId,
          source_message_id: msg.id,
          kind: "image_generation",
          job_api: "generate",
          started_at: new Date().toISOString(),
          request,
          artifact,
          imagePrepare,
        };
        await persistPendingImageJob(pendingImageJob, targetConversationId, "image_generation_running", {
          plan_approved: true,
          plan_markdown: artifact.plan.plan_markdown,
          intake_context: artifact.intakeContext,
          materials: artifact.materials || [],
          image_prepare: imagePrepare,
        });
        await resumePendingImageJob(pendingImageJob, processedKey);
      } catch (err) {
        releaseArtifactAction(processedKey);
        pushAssistant(`图片生成参数准备失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      } finally {
        setBusyForConversation(targetConversationId, false);
      }
      return;
    }
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "scene_package_generation_running", {
      intent: "video",
      scene_package_stage: "prepare_scene_packages",
    });
    const formValues = artifact.formValues;
    const selectedDirection = artifact.selectedDirection;
    const creationContract = artifact.plan.creation_contract as unknown as VideoCreationContract;
    if (!creationContract || typeof creationContract.video_duration_sec !== "number" || !creationContract.video_model || !creationContract.image_model) {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
      pushAssistant("视频 plan.md 缺少完整制作合同，请重新生成 plan.md 后再继续。", targetConversationId);
      return;
    }
    const assetManifest = artifact.plan.asset_manifest;
    if (!assetManifest || !Array.isArray(assetManifest.characters) || !Array.isArray(assetManifest.scenes) || !Array.isArray(assetManifest.props)) {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
      pushAssistant("视频 plan.md 缺少最终角色、道具、场景清单，请先重新生成或修订 plan.md。", targetConversationId);
      return;
    }
    pushAssistant("视频plan.md已同意,正在准备可编辑视频资产", targetConversationId);
    try {
      const request: PrepareScenePackagesJobRequest = {
        form_values: formValues,
        plan_markdown: artifact.plan.plan_markdown,
        selected_direction: selectedDirection as unknown as Record<string, unknown>,
        materials: artifact.materials || [],
        target_duration_ms: creationContract.video_duration_sec * 1000,
        creation_contract: creationContract,
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: assetManifest,
      };
      const started = await api.startPrepareScenePackagesJob(request);
      const pendingScenePackageJob: PendingScenePackageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "scene_package_generation",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      advanceWorkflowProgress(targetConversationId, "scene_package_generation_running", {
        intent: "video",
        scene_package_stage: started.stage || "prepare_scene_packages",
      });
      await persistPendingScenePackageJob(pendingScenePackageJob, targetConversationId, "scene_package_generation_running", {
        form_values: formValues,
        intake_context: artifact.intakeContext,
        materials: artifact.materials || [],
        selected_direction: selectedDirection,
        plan_markdown: artifact.plan.plan_markdown,
        plan_approved: true,
        creation_contract: creationContract,
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: assetManifest,
      });
      await resumePendingScenePackageJob(pendingScenePackageJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频场景包准备失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  /** 把终稿脚本落到聊天消息，供「同意方案」确认后生成资产包。 */
  const ensureDurableScriptPlanMessage = (
    targetConversationId: string,
    planMarkdown: string,
    _afterUserMessageId?: string,
  ) => {
    if (!targetConversationId || !planMarkdown.trim()) return;
    const fingerprint = `${targetConversationId}:${planMarkdown.length}:${planMarkdown.slice(0, 80)}`;
    if (durableScriptPlanMessageIdsRef.current.has(fingerprint)) return;
    const already = messagesRef.current.some((message) => (
      message.conversationId === targetConversationId
      && message.role === "assistant"
      && message.artifact?.type === "plan"
      && (
        message.artifact.scriptPlanConfirmForAssets
        || message.artifact.title === "脚本方案待确认"
        || message.artifact.title === "已确认脚本方案"
      )
      && message.artifact.plan?.plan_markdown === planMarkdown
    ));
    if (already) {
      durableScriptPlanMessageIdsRef.current.add(fingerprint);
      return;
    }
    const plan = createVideoAgentPlanResponse({ planMarkdown });
    void appendMessageForConversation(
      {
        id: uid(),
        conversationId: targetConversationId,
        role: "assistant",
        content: "脚本方案已就绪。点击「同意方案」、右侧确认，或回复「确认脚本」，即可生成视频资产包。",
        time: "",
        artifact: {
          type: "plan",
          title: "脚本方案待确认",
          description: "确认后将生成视频资产包（含角色/场景/道具）",
          actionLabel: "查看",
          intent: "video",
          formValues: {},
          materials: [],
          plan,
          scriptPlanConfirmForAssets: true,
          scriptPlanConfirmed: false,
        },
      },
      targetConversationId,
    );
    durableScriptPlanMessageIdsRef.current.add(fingerprint);
  };

  const markDurableScriptPlanConfirmed = (targetConversationId: string, sourceMessageId?: string) => {
    setMessages((items) => items.map((message) => {
      if (messageConversationId(message, targetConversationId) !== targetConversationId) return message;
      if (sourceMessageId && message.id === sourceMessageId && message.artifact?.type === "plan") {
        return {
          ...message,
          artifact: {
            ...message.artifact,
            title: "已确认脚本方案",
            description: "已确认，正在生成视频资产包",
            scriptPlanConfirmForAssets: true,
            scriptPlanConfirmed: true,
          },
        };
      }
      if (
        message.artifact?.type === "plan"
        && message.artifact.scriptPlanConfirmForAssets
        && !message.artifact.scriptPlanConfirmed
      ) {
        return {
          ...message,
          artifact: {
            ...message.artifact,
            title: "已确认脚本方案",
            description: "已确认，正在生成视频资产包",
            scriptPlanConfirmed: true,
          },
        };
      }
      return message;
    }));
  };

  /** 三种确认信号的统一入口：右下角确认 / 同意方案 / 自然语言确认。 */
  const confirmScriptPlanAndGenerateAssetPackage = async (
    targetConversationId: string,
    markdownHint = "",
    sourceMessageId?: string,
  ) => {
    const workspace = videoAgentView.workspace;
    if (!targetConversationId || !workspace?.script) {
      throw new Error("当前会话没有可确认的脚本工作区");
    }
    const markdown = resolveGeneratableScriptMarkdown({
      scriptContent: markdownHint || workspace.script.content,
      stages: workspace.scriptStages,
    });
    if (!markdown.trim()) {
      throw new Error("当前没有可确认的脚本正文");
    }
    if (!workspaceHasExportReady({
      scriptContent: markdown,
      stages: workspace.scriptStages,
    })) {
      throw new Error("请先完成「导出脚本产物」，再确认并生成资产包");
    }
    const readinessInput = {
      scriptContent: markdown,
      stages: workspace.scriptStages,
    };
    if (scriptNeedsFullCharacterPlan(readinessInput)) {
      const readiness = analyzeScriptCharacterReadiness(readinessInput);
      const hints = readiness.missingHints.slice(0, 2).join("；") || "角色设定不完整";
      throw new Error(`请先补齐角色设定后再确认：${hints}`);
    }
    setConfirmingVideoAgentScript(true);
    try {
      try {
        await supervisorApi.saveVideoAgentScript(targetConversationId, {
          markdown,
          expected_revision: workspace.revision,
          confirm_for_generation: true,
        });
        await supervisorRuntime.refreshSnapshot();
      } catch {
        // 确认标记失败不阻断本轮资产包。
      }
      scriptPlanConfirmedRef.current = true;
      markDurableScriptPlanConfirmed(targetConversationId, sourceMessageId);
      ensureDurableScriptPlanMessage(targetConversationId, markdown);
      markDurableScriptPlanConfirmed(targetConversationId, sourceMessageId);
      const materials = (workspace.assets || [])
        .filter((asset): asset is typeof asset & { url: string } => Boolean(asset.url))
        .map((asset) => ({
          asset_id: asset.artifactRef,
          url: asset.url,
          type: asset.mediaType,
        }));
      await startVideoAgentAssetPackageFromScript(
        targetConversationId,
        markdown,
        materials,
        "已确认脚本方案，正在生成视频资产包…",
      );
    } finally {
      setConfirmingVideoAgentScript(false);
    }
  };

  /** 脚本就绪且用户确认方案后进入视频资产包生成。 */
  const startVideoAgentAssetPackageFromScript = async (
    targetConversationId: string,
    planMarkdown: string,
    materials: Array<Record<string, unknown>> = [],
    notice = "正在生成视频资产包…",
    options: { revisionFeedback?: string } = {},
  ) => {
    if (!targetConversationId || !planMarkdown.trim()) return;
    const existing = pendingScenePackageJobRef.current;
    if (existing?.conversation_id === targetConversationId) {
      pushAssistant("视频资产包仍在生成中，请稍候…", targetConversationId);
      return;
    }
    const workspace = videoAgentView.workspace;
    const revisionFeedback = options.revisionFeedback?.trim() || "";
    const basePlanMarkdown = buildAssetPackagePlanMarkdown({
      scriptContent: planMarkdown,
      stages: workspace?.scriptStages,
    });
    const fullPlanMarkdown = revisionFeedback
      ? `${basePlanMarkdown}\n\n【用户修改意见】\n${revisionFeedback}\n请严格按以上意见调整角色/场景/道具设定、参考图描述与分镜内容后重新生成视频资产包。`
      : basePlanMarkdown;
    const creationContract = await resolveVideoAgentCreationContract(fullPlanMarkdown);
    const productHint = extractConcreteProductHint(fullPlanMarkdown) || "脚本成片产品";
    const formValues: Record<string, unknown> = {
      product_info: productHint,
      video_duration_sec: creationContract.video_duration_sec,
      video_ratio: creationContract.video_ratio,
      video_model: creationContract.video_model,
      video_size: creationContract.video_size,
      video_sound: creationContract.video_sound,
      image_model: creationContract.image_model,
      video_usage: creationContract.video_usage || "宣传片",
    };
    const selectedDirection = {
      direction_id: "video-agent-script-confirmed",
      title: "脚本成片",
      description: "基于已确认脚本生成视频资产包",
      recommended: true,
      tags: ["script"],
      data: {},
    };
    const plan = createVideoAgentPlanResponse({
      planMarkdown: fullPlanMarkdown,
      creationContract: creationContract as unknown as Record<string, unknown>,
    });
    const artifact: ChatArtifact = {
      type: "plan",
      title: "已确认脚本",
      description: "正在生成视频资产包",
      actionLabel: "查看",
      intent: "video",
      formValues,
      materials,
      selectedDirection,
      plan,
    };
    const sourceMessageId = `video-agent-script-save:${targetConversationId}:${Date.now()}`;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "scene_package_generation_running", {
      intent: "video",
      scene_package_stage: "prepare_scene_packages",
    });
    // 先回执，再把进度卡锚在回执下方，保证时间线：脚本确认 → 已收到 → 分步执行。
    // 必须 await 落库后的真实 message id；乐观 id 被替换后锚点会失效并错误回落到首条用户消息。
    const noticeMessage = await appendMessageForConversation(
      {
        id: uid(),
        conversationId: targetConversationId,
        role: "assistant",
        content: notice,
        time: "",
      },
      targetConversationId,
    );
    assetPackageAnchorMessageIdRef.current = noticeMessage.id;
    setAssetPackageAnchorMessageId(noticeMessage.id);
    setAssetPackageProgressSteps(createAssetPackageProgressSteps());
    try {
      const request: PrepareScenePackagesJobRequest = {
        form_values: formValues,
        plan_markdown: fullPlanMarkdown,
        selected_direction: selectedDirection,
        materials,
        target_duration_ms: creationContract.video_duration_sec * 1000,
        creation_contract: creationContract,
        scene_blueprints: [],
        asset_manifest: undefined,
        // Video Agent：先结构 → 对话中选生图模型 → 再 generate-scene-assets。
        generate_images: false,
      };
      const started = await api.startPrepareScenePackagesJob(request);
      const pendingScenePackageJob: PendingScenePackageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: sourceMessageId,
        kind: "scene_package_generation",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        started.stage || "prepare_scene_packages",
      ));
      advanceWorkflowProgress(targetConversationId, "scene_package_generation_running", {
        intent: "video",
        scene_package_stage: started.stage || "prepare_scene_packages",
      });
      await persistPendingScenePackageJob(pendingScenePackageJob, targetConversationId, "scene_package_generation_running", {
        form_values: formValues,
        materials,
        selected_direction: selectedDirection,
        plan_markdown: fullPlanMarkdown,
        plan_approved: true,
        creation_contract: creationContract,
        scene_blueprints: [],
      });
      await resumePendingScenePackageJob(pendingScenePackageJob, "");
    } catch (err) {
      setAssetPackageProgressSteps((current) => {
        const base = current.length > 0 ? current : createAssetPackageProgressSteps();
        return base.map((step) => (
          step.status === "running" || step.id === "packages"
            ? {
                ...step,
                status: "failed" as const,
                detail: err instanceof Error ? err.message : String(err),
              }
            : step
        ));
      });
      pushAssistant(
        `视频资产包生成失败:${err instanceof Error ? err.message : String(err)}`,
        targetConversationId,
      );
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleEditPlan = (msg: ChatMessage) => {
    if (!msg.artifact?.plan || !isCreationIntent(msg.artifact.intent)) return;
    setSelectedStoryboardMessageId("");
    setSelectedPlanEditorMessageId(msg.id);
    setCanvasOpen(true);
  };

  const handlePublishPlanEdit = async (msg: ChatMessage, editedMarkdown: string) => {
    const artifact = msg.artifact;
    if (!artifact?.plan || !isCreationIntent(artifact.intent) || !artifact.formValues || !artifact.selectedDirection) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const existingPlanJob = pendingPlanJobRef.current;
    if (existingPlanJob?.job_id && existingPlanJob.conversation_id === targetConversationId) {
      setSavingPlanEdit(existingPlanJob.kind === "plan_manual_edit");
      setBusyForConversation(targetConversationId, true);
      schedulePendingPlanJobPersistence(
        existingPlanJob,
        pendingPlanJobRunningPhase(existingPlanJob.kind),
        pendingPlanJobPersistenceContext(existingPlanJob.kind),
      );
      await resumePendingPlanJob(existingPlanJob);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setSavingPlanEdit(true);
    setBusyForConversation(targetConversationId, true);
    try {
      const request: PlanManualEditJobRequest = {
        intent: artifact.intent,
        form_values: artifact.formValues,
        selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
        current_plan_markdown: artifact.plan.plan_markdown,
        edited_plan_markdown: editedMarkdown,
        current_plan_version: artifact.plan.plan_version || 1,
        plan_history: artifact.plan.plan_history || [],
        creation_contract: artifact.plan.creation_contract || artifact.creationContract || {},
        scene_durations_sec: artifact.plan.scene_durations_sec || [],
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: artifact.plan.asset_manifest,
        product_creative_profile: { core_message: artifact.coreMessage || pendingCore },
        intake_context: artifact.intakeContext,
        materials: artifact.materials || [],
      };
      const started = await api.startPlanManualEditJob(request);
      const pendingPlanJob: PendingPlanJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "plan_manual_edit",
        started_at: new Date().toISOString(),
        request,
        context: {
          intent: artifact.intent,
          formValues: artifact.formValues,
          selectedDirection: artifact.selectedDirection,
          materials: artifact.materials || [],
          coreMessage: artifact.coreMessage || pendingCore,
          intakeContext: artifact.intakeContext,
          processedKey,
        },
      };
      await continueStartedPlanJob({
        pendingPlanJob,
        saveRecovery: (job) => {
          pendingPlanJobRef.current = job;
          savePendingPlanJobRecovery(browserSessionStorage(), job);
        },
        persistPending: (job) => persistPendingPlanJob(
          job,
          targetConversationId,
          "plan_manual_edit_running",
          { plan_approved: false },
        ),
        notifyRecovery: notifyPlanJobPersistenceRecovery,
        schedulePersistenceRetry: (job) => schedulePendingPlanJobPersistence(
          job,
          "plan_manual_edit_running",
          { plan_approved: false },
        ),
        resumePending: resumePendingPlanJob,
      });
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 编辑发布失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setSavingPlanEdit(false);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetrySceneAssets = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    const hasSceneAssetFailures = Boolean(artifact?.sceneAssetFailures?.length);
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!artifact || !videoScenePackages?.scene_packages.length || !hasSceneAssetFailures) {
      if (targetConversationId) {
        pushAssistant("当前卡片没有可重试的失败参考图，请从最新场景包卡片操作。", targetConversationId);
      }
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) {
      pushAssistant("参考图重试仍在处理中，请稍候…", targetConversationId);
      return;
    }
    setBusyForConversation(targetConversationId, true);
    const targetAssets = sceneAssetRetryTargets(artifact.sceneAssetFailures);
    if (!targetAssets.length) {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
      pushAssistant("失败记录缺少可重试的素材标识，请从最新 plan 或场景包重新生成。", targetConversationId);
      return;
    }
    pushAssistant(`正在重新生成 ${targetAssets.length} 个失败的场景参考图…`, targetConversationId);
    try {
      // 旧合同可能仍是 gpt-image-2+1080p；重试前按实时能力升到 4K/2K。
      let creationContract = videoScenePackages.creation_contract || undefined;
      let imageSize = creationContract?.scene_image_size || "4K";
      let imageRatio = creationContract?.scene_image_ratio || "9:16";
      let imageModel = creationContract?.image_model || "gpt-image-2";
      try {
        const configs = await api.listImageGenerateModelConfigs();
        const selected = resolveImageModel(configs as ImageModelParamConfig[], imageModel);
        const caps = imageModelCapabilities(selected);
        imageModel = String(selected.modelType || imageModel);
        imageSize = preferredImageSize(caps.sizes, imageSize);
        imageRatio = preferredImageRatio(caps.aspect_ratios, imageRatio);
        if (creationContract) {
          creationContract = {
            ...creationContract,
            image_model: imageModel,
            image_model_capabilities: caps,
            scene_image_ratio: imageRatio,
            scene_image_size: imageSize,
          };
        }
      } catch {
        if (imageModel === "gpt-image-2" && String(imageSize).toLowerCase() === "1080p") {
          imageSize = "4K";
        }
      }
      const request: SceneAssetsJobRequest = {
        global_assets: videoScenePackages.global_assets,
        scene_packages: videoScenePackages.scene_packages,
        materials: artifact.materials || [],
        image_ratio: imageRatio,
        image_size: imageSize,
        model: imageModel,
        creation_contract: creationContract,
        target_assets: targetAssets,
      };
      const started = await api.startSceneAssetsJob(request);
      const pendingScenePackageJob: PendingScenePackageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "scene_asset_generation",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      await persistPendingScenePackageJob(pendingScenePackageJob, targetConversationId, "scene_asset_generation_running", {
        global_assets: videoScenePackages.global_assets,
        intake_context: artifact.intakeContext,
        scene_packages: videoScenePackages.scene_packages,
        scene_asset_failures: artifact.sceneAssetFailures || [],
        creation_contract: creationContract || videoScenePackages.creation_contract,
      });
      await resumePendingScenePackageJob(pendingScenePackageJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`场景参考图继续生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRevisePlan = (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setAgentRevisionSourceMessageId(msg.id);
    planRevisionArtifactRef.current = msg.artifact
      ? { conversationId: targetConversationId, artifact: msg.artifact, sourceMessageId: msg.id, processedKey }
      : null;
    pushAssistant("已暂停当前 plan.md。请在输入框填写修改意见，提交后我会让你选择修改当前 Plan 或重新生成创意。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "plan_revision_requested",
          context: {
            ...makeSnapshot(targetConversationId),
            plan_approved: false,
            plan_revision_requested: true,
            pendingPlanRevisionRequest: planRevisionArtifactRef.current,
            pending_plan_revision_request: planRevisionArtifactRef.current,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const handleConfirmPlanRevisionMode = async (mode: PlanRevisionMode) => {
    const pending = pendingPlanRevisionChoice;
    if (!pending) return;
    const { artifact, conversationId: targetConversationId, feedback, materials } = pending;
    if (!isCreationIntent(artifact.intent) || !artifact.formValues || !artifact.selectedDirection || !artifact.plan) return;
    setPendingPlanRevisionChoice(null);
    setBusyForConversation(targetConversationId, true);
    try {
      const existingPlanJob = pendingPlanJobRef.current;
      if (
        existingPlanJob?.job_id
        && existingPlanJob.conversation_id === targetConversationId
      ) {
        schedulePendingPlanJobPersistence(
          existingPlanJob,
          pendingPlanJobRunningPhase(existingPlanJob.kind),
          pendingPlanJobPersistenceContext(existingPlanJob.kind),
        );
        await resumePendingPlanJob(existingPlanJob);
        return;
      }
      if (mode === "regenerate_directions") {
        pushAssistant("已选择放弃当前创意，正在重新生成 3 个创意方向…", targetConversationId);
        await startDirectionJob(
          targetConversationId,
          {
            intent: artifact.intent,
            values: artifact.formValues,
            materials,
            product_creative_profile: { revision_feedback: feedback },
            intake_context: artifact.intakeContext,
          },
          {
            intent: artifact.intent,
            formValues: artifact.formValues,
            materials,
            coreMessage: `${artifact.coreMessage || pendingCore}\n修改意见：${feedback}`,
            intakeContext: artifact.intakeContext,
            revisionFeedback: feedback,
          },
          "directions_running",
          pending.sourceMessageId,
        );
        return;
      }

      pushAssistant(`正在当前创意基础上修订 plan.md v${artifact.plan.plan_version || 1}…`, targetConversationId);
      const request: PlanRevisionJobRequest = {
        intent: artifact.intent,
        form_values: artifact.formValues,
        selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
        current_plan_markdown: artifact.plan.plan_markdown,
        current_plan_version: artifact.plan.plan_version || 1,
        plan_history: artifact.plan.plan_history || [],
        revision_feedback: feedback,
        creation_contract: artifact.plan.creation_contract || artifact.creationContract || {},
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: artifact.plan.asset_manifest,
        product_creative_profile: { revision_feedback: feedback },
        intake_context: artifact.intakeContext,
        materials,
      };
      const started = await api.startPlanRevisionJob(request);
      const pendingPlanJob: PendingPlanJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: pending.sourceMessageId,
        kind: "plan_revision",
        started_at: new Date().toISOString(),
        request,
        context: {
          intent: artifact.intent,
          formValues: artifact.formValues,
          selectedDirection: artifact.selectedDirection,
          materials,
          coreMessage: artifact.coreMessage || pendingCore,
          intakeContext: artifact.intakeContext,
          processedKey: pending.processedKey,
        },
      };
      const persistenceContext = {
        pendingPlanRevisionChoice: null,
        pending_plan_revision_choice: null,
      };
      await continueStartedPlanJob({
        pendingPlanJob,
        saveRecovery: (job) => {
          pendingPlanJobRef.current = job;
          savePendingPlanJobRecovery(browserSessionStorage(), job);
        },
        persistPending: (job) => persistPendingPlanJob(
          job,
          targetConversationId,
          "plan_revision_running",
          persistenceContext,
        ),
        notifyRecovery: notifyPlanJobPersistenceRecovery,
        schedulePersistenceRetry: (job) => schedulePendingPlanJobPersistence(
          job,
          "plan_revision_running",
          persistenceContext,
        ),
        resumePending: resumePendingPlanJob,
      });
    } catch (err) {
      if (pending.processedKey) releaseArtifactAction(pending.processedKey);
      pushAssistant(`plan.md 修改失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleCancelPlanRevisionMode = () => {
    const pending = pendingPlanRevisionChoice;
    if (!pending) return;
    setPendingPlanRevisionChoice(null);
    setAgentRevisionSourceMessageId("");
    if (pending.processedKey) releaseArtifactAction(pending.processedKey);
    pushAssistant("已取消本次 plan.md 修改方式选择，当前 Plan 保持不变。", pending.conversationId);
    void updateConversationWithProgress(pending.conversationId, {
        last_phase: "plan_review",
        context: {
          ...makeSnapshot(pending.conversationId),
          pendingPlanRevisionChoice: null,
          pending_plan_revision_choice: null,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {});
  };

  const handleRollbackPlan = async (msg: ChatMessage, version: number) => {
    const artifact = msg.artifact;
    if (!artifact?.plan || !isCreationIntent(artifact.intent) || !artifact.formValues || !artifact.selectedDirection) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const rollbackSnapshot = makeSnapshot(targetConversationId);
    setBusyForConversation(targetConversationId, true);
    pushAssistant(
      `正在把 plan.md v${artifact.plan.plan_version || 1} 直接回退到 v${version}，不会创建新版本…`,
      targetConversationId,
    );
    try {
      const plan = await api.restorePlanMarkdown({
        intent: artifact.intent,
        current_plan_markdown: artifact.plan.plan_markdown,
        current_plan_version: artifact.plan.plan_version || 1,
        plan_history: artifact.plan.plan_history || [],
        restore_version: version,
        creation_contract: artifact.plan.creation_contract || artifact.creationContract || {},
        scene_durations_sec: artifact.plan.scene_durations_sec || [],
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: artifact.plan.asset_manifest,
      });
      await persistPlanArtifactForConversation(
        createPlanArtifactMessage(
          plan,
          artifact.selectedDirection,
          {
            intent: artifact.intent,
            formValues: artifact.formValues,
            materials: artifact.materials || [],
            coreMessage: artifact.coreMessage || pendingCore,
            intakeContext: artifact.intakeContext,
          },
          targetConversationId,
        ),
        targetConversationId,
        {
          type: "plan_save",
          last_phase: "plan_review",
          processed_key: processedKey,
          success_message: `已回退到 plan.md v${plan.plan_version}，未创建新版本。`,
          context: rollbackSnapshot as unknown as Record<string, unknown>,
        },
      );
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 回退失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleGenerateImage = async (msg: ChatMessage) => {
    const imagePrepare = msg.artifact?.imagePrepare;
    if (!imagePrepare || !imagePrepare.ok) return;
    const artifact = msg.artifact;
    if (!artifact) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`正在调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
    try {
      const request: ImageGenerationJobRequest = {
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
      };
      const started = await api.startImageGenerationJob(request);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "image_generation",
        job_api: "generate",
        started_at: new Date().toISOString(),
        request,
        artifact,
        imagePrepare,
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, "image_generation_running", {
        image_prepare: imagePrepare,
      });
      await resumePendingImageJob(pendingImageJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`图片生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetryImageResult = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.imageResult || canAcceptImageResult(artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const storedImageEditRequest = (artifact.imageEditRequest || {}) as Partial<PendingImageEditRequest>;
    const sceneGlobalAssetReference = storedImageEditRequest.sceneGlobalAssetReference
      || sceneGlobalAssetReferenceFromMaterials(artifact.materials || []);
    if (sceneGlobalAssetReference) {
      const processedKey = beginArtifactAction(msg, targetConversationId);
      if (!processedKey) return;
      releaseArtifactAction(processedKey);
      await pushSceneGlobalAssetEditOptions(
        sceneGlobalAssetReference,
        String(storedImageEditRequest.prompt || artifact.imageRevisionFeedback || "图片编辑"),
        targetConversationId,
        (storedImageEditRequest.materials || artifact.materials || []) as Array<Record<string, unknown>>,
      );
      return;
    }
    const imagePrepare = artifact.imagePrepare;
    if (!imagePrepare) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    if (imagePrepare.method === "image_edit") {
      const imageEditRequest = imageEditRequestFromArtifact(artifact, targetConversationId);
      pendingImageEditRequestRef.current = imageEditRequest;
      releaseArtifactAction(processedKey);
      await showImageEditOptions(imageEditRequest);
      return;
    }
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`已继续调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
    try {
      const request: ImageGenerationJobRequest = {
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
      };
      const started = await api.startImageGenerationJob(request);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "image_regeneration",
        job_api: "generate",
        started_at: new Date().toISOString(),
        request,
        artifact,
        imagePrepare,
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, "image_regeneration_running", {
        image_prepare: imagePrepare,
      });
      await resumePendingImageJob(pendingImageJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`图片继续生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  async function handleAcceptImageResult(msg: ChatMessage, auto = false) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (auto && !shouldAutoAcceptImageResult(msg, targetConversationId)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    imageRevisionArtifactRef.current = null;
    const sceneAssetReview = msg.artifact.sceneGlobalAssetEditReview;
    if (sceneAssetReview) {
      await startSceneGlobalAssetRevision(
        {
          source: "scene_global_asset",
          asset_id: sceneAssetReview.asset_id,
          asset_group: sceneAssetReview.asset_group,
          name: sceneAssetReview.asset_name || sceneAssetReview.asset_id,
          source_image_url: sceneAssetReview.source_image_url,
          url: sceneAssetReview.source_image_url,
          type: "image",
          filename: sceneAssetReview.asset_name || sceneAssetReview.asset_id,
          storyboard_message_id: sceneAssetReview.storyboard_message_id || sceneAssetReview.source_message_id,
        },
        "replace",
        {
          source: "image_asset",
          displayImageUrl: sceneAssetReview.edited_image_url,
          generationReferenceUrl: sceneAssetReview.edited_image_url,
          assetName: sceneAssetReview.asset_name,
          raw: sceneAssetReview.editResult as unknown as Record<string, unknown>,
        },
        {
          processedKey,
          reviewMessageId: msg.id,
          targetConversationId,
        },
      );
      return;
    }
    markImageResultAccepted(msg.id, targetConversationId);
    pushAssistant(auto ? timeoutReviewMessage(AUTO_CONFIRM_TIMEOUT_SECONDS) : "已确认图片满意，流程结束。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "image_accepted",
          context: { ...makeSnapshot(), image_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseImageResult(msg: ChatMessage) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const sceneAssetReview = msg.artifact.sceneGlobalAssetEditReview;
    if (!sceneAssetReview && isReviewExpired(msg.artifact.reviewExpiresAt)) {
      void handleAcceptImageResult(msg, true);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const sceneGlobalAssetReference = sceneAssetReview
      ? {
          source: "scene_global_asset" as const,
          asset_id: sceneAssetReview.asset_id,
          asset_group: sceneAssetReview.asset_group,
          scene_global_asset_action: "edit" as const,
          name: sceneAssetReview.asset_name || sceneAssetReview.asset_id,
          source_image_url: sceneAssetReview.edited_image_url,
          original_image_url: sceneAssetReview.original_image_url,
          url: sceneAssetReview.edited_image_url,
          type: "image" as const,
          filename: `${sceneAssetReview.asset_id}.png`,
          storyboard_message_id: sceneAssetReview.storyboard_message_id || sceneAssetReview.source_message_id,
        }
      : sceneGlobalAssetReferenceFromMaterials(msg.artifact.materials || []);
    const revisionArtifact = sceneGlobalAssetReference
      ? {
          ...msg.artifact,
          materials: [
            sceneGlobalAssetReference,
            ...(msg.artifact.materials || []).filter((material) => material.source !== "scene_global_asset"),
          ],
        }
      : msg.artifact;
    imageRevisionArtifactRef.current = { conversationId: targetConversationId, artifact: revisionArtifact };
    pushAssistant(
      sceneGlobalAssetReference
        ? `请在输入框填写「${sceneGlobalAssetReference.name}」的图片修改意见，我会继续编辑这张全局素材并替换回场景包。`
        : "请在输入框填写图片修改意见，我会基于当前 plan.md 和图片参数重新生成。",
      targetConversationId,
    );
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: sceneGlobalAssetReference ? "scene_global_asset_revision_requested" : "image_revision_requested",
          context: {
            ...makeSnapshot(),
            image_revision_requested: true,
            scene_global_asset_revision_requested: Boolean(sceneGlobalAssetReference),
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  const handleGenerateJianyingDraft = async (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const sourceMessageId = msg.artifact?.type === "jianying_draft" ? msg.artifact.pendingJianyingDraftJob?.source_message_id : msg.id;
    const latestMessage = messagesRef.current.find(
      (message) => message.id === sourceMessageId && messageConversationId(message, targetConversationId) === targetConversationId,
    ) || msg;
    const artifact = latestMessage.artifact;
    if (!artifact?.mergedVideo?.ok || !artifact.generatedSceneVideos?.ok || !artifact.videoScenePackages?.ok) return;
    if (!targetConversationId) return;

    const generatedSceneVideos = artifact.generatedSceneVideos;
    const expectedScenePackages = artifact.videoScenePackages.scene_packages;
    const successfulScenes: JianyingDraftScene[] = generatedSceneVideos.scene_videos.map((scene) => ({
      scene_id: scene.scene_id,
      scene_index: scene.scene_index,
      task_id: scene.task_id || null,
      video_url: scene.video_url,
    }));
    const expectedSceneIds = new Set(expectedScenePackages.map((scene) => scene.scene_id));
    const successfulSceneIds = new Set(successfulScenes.map((scene) => scene.scene_id));
    if (
      generatedSceneVideos.failed_scenes.length > 0 ||
      successfulScenes.length !== expectedScenePackages.length ||
      successfulSceneIds.size !== expectedSceneIds.size ||
      [...expectedSceneIds].some((sceneId) => !successfulSceneIds.has(sceneId))
    ) {
      pushAssistant("当前版本存在未成功生成的分镜，无法生成剪映草稿。", targetConversationId);
      return;
    }

    let storyboard_version_id: string;
    try {
      storyboard_version_id = storyboardVersionId(successfulScenes);
    } catch {
      pushAssistant("当前分镜视频不符合剪映草稿输入要求，请检查分镜视频后重试。", targetConversationId);
      return;
    }
    const existingPending = pendingJianyingDraftJobRef.current;
    if (existingPending?.conversation_id === targetConversationId && existingPending.storyboard_version_id === storyboard_version_id) {
      void resumePendingJianyingDraftJob(existingPending);
      return;
    }
    const existingRecord = jianyingDraftRecordsForConversation(targetConversationId)[storyboard_version_id];
    if (isJianyingDraftSucceededResultValid(existingRecord)) return;
    const retry_failed = existingRecord?.status === "failed" || existingRecord?.status === "timeout";
    if (!jianyingDraftStartGuardRef.current.tryAcquire(targetConversationId, storyboard_version_id)) return;
    try {
      let capability: JianyingDraftCapability;
      try {
        capability = await api.getJianyingDraftCapability();
        setJianyingDraftCapability(capability);
      } catch {
        pushAssistant(jianyingDraftPublicErrorMessage("capability"), targetConversationId);
        return;
      }
      if (!capability.available) return;
      const request: JianyingDraftStartRequest = {
        conversation_id: targetConversationId,
        storyboard_version_id,
        scenes: successfulScenes,
        video_task_id: artifact.mergedVideo.task_id || null,
        project_name: artifact.plan?.plan_markdown ? String(artifact.plan.plan_markdown.split("\n")[0] || "") : null,
        retry_failed,
      };
      const started = await api.startJianyingDraftJob(request);
      if (!started.job_id) {
        pushAssistant(jianyingDraftPublicErrorMessage("start"), targetConversationId);
        return;
      }
      const pendingJianyingDraftJob: PendingJianyingDraftJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: latestMessage.id,
        storyboard_version_id,
        started_at: new Date().toISOString(),
        request,
      };
      await persistPendingJianyingDraftJob(
        pendingJianyingDraftJob,
        targetConversationId,
        "jianying_draft_running",
        pendingJianyingDraftJob.job_id,
      );
      await resumePendingJianyingDraftJob(pendingJianyingDraftJob);
    } catch {
      pushAssistant(jianyingDraftPublicErrorMessage("start"), targetConversationId);
    } finally {
      jianyingDraftStartGuardRef.current.release(targetConversationId, storyboard_version_id);
    }
  };

  const handleSaveVideoScenePackage = async (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const latestMessage = messagesRef.current.find(
      (message) =>
        message.id === msg.id &&
        messageConversationId(message, targetConversationId) === targetConversationId &&
        Boolean(message.artifact?.videoScenePackages),
    );
    const artifact = latestMessage?.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    if (!latestMessage || !artifact || !videoScenePackages) {
      pushAssistant("当前没有找到可保存的分镜场景包，请重新打开后再试。", targetConversationId);
      return;
    }
    try {
      if (targetConversationId) {
        await api.updateConversationMessage(targetConversationId, latestMessage.id, {
          content: latestMessage.content,
          payload: {
            artifact,
            materials: artifact.materials || [],
            client_message_id: latestMessage.id,
          } as unknown as Record<string, unknown>,
        });
      }
      persistScenePackageSnapshot(targetConversationId, videoScenePackages, "scene_package_saved", {
        video_scene_package_edited_scene_ids: artifact.videoScenePackageEditedSceneIds || [],
      });
      setCanvasOpen(false);
      setSelectedStoryboardMessageId("");
      setSelectedPlanEditorMessageId("");
    } catch {
      pushAssistant("分镜保存失败，请检查网络后重试。", targetConversationId);
    }
  };

  const handleGenerateVideoFromScenePackages = async (msg: ChatMessage) => {
    const latestMessage =
      messagesRef.current.find(
        (message) => message.id === msg.id && message.artifact?.videoScenePackages,
      ) || msg;
    const artifact = latestMessage.artifact;
    if (!artifact) return;
    if (artifact.sceneAssetsGenerating) {
      pushAssistant(
        "参考图仍在生成中，请稍候。可先打开卡片查看场景包结构。",
        messageConversationId(latestMessage, conversationIdRef.current),
      );
      return;
    }
    if (artifact.sceneAssetsAwaitingModel) {
      pushAssistant(
        "场景包结构已就绪，请先在下方选择生图模型并生成参考图。",
        messageConversationId(latestMessage, conversationIdRef.current),
      );
      return;
    }
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages?.ok || videoScenePackages.scene_packages.length === 0) return;
    const targetConversationId = messageConversationId(latestMessage, conversationIdRef.current);
    const dirtySceneIds = new Set(artifact.videoScenePackageEditedSceneIds || []);
    const retrySceneIds = failedSceneIdsFromGeneratedSceneVideos(artifact.generatedSceneVideos, videoScenePackages.scene_packages);
    const isDirtySceneRegeneration = canReuseUneditedSceneVideos(videoScenePackages, artifact.generatedSceneVideos, dirtySceneIds);
    const hasGeneratedSceneVideos = Boolean(artifact.generatedSceneVideos?.scene_videos.length);
    const isFailedSceneRetry = Boolean(artifact.generatedSceneVideos && !artifact.generatedSceneVideos.ok && retrySceneIds.size > 0);
    if (hasGeneratedSceneVideos && dirtySceneIds.size === 0 && !isFailedSceneRetry) {
      pushAssistant("当前分镜没有检测到修改内容，无需重新生成视频。", targetConversationId);
      return;
    }
    if (artifact.generatedSceneVideos && !artifact.generatedSceneVideos.ok && retrySceneIds.size === 0) {
      pushAssistant("当前失败结果没有定位到具体分镜，无法只重试异常片段。请重新生成场景包后再试。", targetConversationId);
      return;
    }
    const processedKey = beginArtifactAction(latestMessage, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    const originalVideoScenePackages = artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId) || videoScenePackages;
    pushAssistant(
      isDirtySceneRegeneration
        ? `已保存分镜修改，正在重生成 ${dirtySceneIds.size} 个已修改分镜视频…`
        : isFailedSceneRetry
          ? `正在重新生成 ${retrySceneIds.size} 个失败或额度暂停的分镜视频…`
          : "场景包已确认，正在生成场景视频…",
      targetConversationId,
    );
    try {
      const request = isDirtySceneRegeneration
        ? sceneVideoRequestFromPackages(videoScenePackages, dirtySceneIds)
        : isFailedSceneRetry
          ? sceneVideoRequestFromPackages(videoScenePackages, retrySceneIds)
          : sceneVideoRequestFromPackages(videoScenePackages, undefined, dirtySceneIds);
      const started = await api.startSceneVideosJob(request);
      const pendingVideoJob: PendingVideoJob = isDirtySceneRegeneration
        ? {
            job_id: started.job_id,
            conversation_id: targetConversationId,
            source_message_id: latestMessage.id,
            kind: "scene_regeneration",
            started_at: new Date().toISOString(),
            request,
            artifact: { ...artifact, originalVideoScenePackages, videoScenePackageEditedSceneIds: Array.from(dirtySceneIds) },
            affected_scene_ids: Array.from(dirtySceneIds),
          }
        : isFailedSceneRetry
          ? {
              job_id: started.job_id,
              conversation_id: targetConversationId,
              source_message_id: latestMessage.id,
              kind: "scene_failed_retry",
              started_at: new Date().toISOString(),
              request,
              artifact: { ...artifact, originalVideoScenePackages },
              affected_scene_ids: Array.from(retrySceneIds),
            }
          : {
              job_id: started.job_id,
              conversation_id: targetConversationId,
              source_message_id: latestMessage.id,
              kind: "scene_generation",
              started_at: new Date().toISOString(),
              request,
              artifact: { ...artifact, originalVideoScenePackages },
            };
      await persistPendingVideoJob(pendingVideoJob, targetConversationId, isDirtySceneRegeneration || isFailedSceneRetry ? "video_regeneration_running" : "video_generation_running", {
        global_assets: videoScenePackages.global_assets,
        intake_context: artifact.intakeContext,
        scene_packages: videoScenePackages.scene_packages,
        generated_scene_videos: artifact.generatedSceneVideos?.scene_videos,
        failed_scenes: artifact.generatedSceneVideos?.failed_scenes,
        merged_video: artifact.mergedVideo,
        creation_contract: videoScenePackages.creation_contract,
        affected_scene_ids: isDirtySceneRegeneration ? Array.from(dirtySceneIds) : isFailedSceneRetry ? Array.from(retrySceneIds) : undefined,
      });
      await resumePendingVideoJob(pendingVideoJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetryVideoMerge = async (msg: ChatMessage) => {
    const generatedSceneVideos = msg.artifact?.generatedSceneVideos;
    const videoScenePackages = msg.artifact?.videoScenePackages;
    if (!generatedSceneVideos?.scene_videos.length || !videoScenePackages || msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在继续合并已生成的场景视频…", targetConversationId);
    try {
      await startAndResumeVideoMergeJob({
        targetConversationId,
        sourceMessageId: msg.id,
        artifact: { ...msg.artifact, videoScenePackages, generatedSceneVideos } as ChatArtifact,
        videoScenePackages,
        generatedSceneVideos,
        originalVideoScenePackages: msg.artifact?.originalVideoScenePackages,
        processedKey,
        mergePurpose: "generation",
      });
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频继续合并失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  async function handleAcceptVideoResult(msg: ChatMessage) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!targetConversationId) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const currentMessage = messagesRef.current.find(
      (message) =>
        message.id === msg.id &&
        messageConversationId(message, targetConversationId) === targetConversationId &&
        message.artifact?.type === "video_result",
    ) || msg;
    if (!currentMessage.artifact) {
      releaseArtifactAction(processedKey);
      return;
    }
    const acceptedArtifact: ChatArtifact = {
      ...currentMessage.artifact,
      videoAccepted: true,
    };
    try {
      await api.updateConversationMessage(targetConversationId, currentMessage.id, {
        content: currentMessage.content,
        payload: {
          artifact: acceptedArtifact,
          materials: currentMessage.materials || acceptedArtifact.materials || [],
          client_message_id: currentMessage.id,
        } as unknown as Record<string, unknown>,
      });
      videoRevisionArtifactRef.current = null;
      markVideoResultAccepted(currentMessage.id, targetConversationId);
      await updateConversationWithProgress(targetConversationId, {
          last_phase: "video_accepted",
          context: { ...makeSnapshot(), video_accepted: true, pendingVideoRevision: null, pending_video_revision: null } as unknown as Record<string, unknown>,
        });
      pushAssistant("已确认视频无修改意见，流程结束。", targetConversationId);
    } catch {
      releaseArtifactAction(processedKey);
      pushAssistant("视频结束状态保存失败，请检查网络后重试。", targetConversationId);
    }
  }

  function handleReviseVideoResult(msg: ChatMessage) {
    if (!msg.artifact?.mergedVideo?.ok || msg.artifact.videoAccepted) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    videoRevisionArtifactRef.current = {
      conversationId: targetConversationId,
      artifact: {
        ...msg.artifact,
        originalVideoScenePackages: msg.artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId),
      },
    };
    pushAssistant("请在输入框填写视频修改意见。我会先做 QAAgent QC 质检，再让你选择是否结合质检结果重生成受影响场景。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "video_revision_requested",
          context: {
            ...makeSnapshot(),
            video_revision_requested: true,
            video_revision_requested_at: new Date().toISOString(),
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  async function handleRegenerateVideoWithRevision(msg: ChatMessage, useQualityReview: boolean) {
    const artifact = msg.artifact;
    if (!artifact?.videoScenePackages || !artifact.generatedSceneVideos || !artifact.mergedVideo || !artifact.videoRevisionFeedback) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    const originalVideoScenePackages = artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId) || artifact.videoScenePackages;
    const affectedSceneIds = sceneIdsForRevision(
      artifact.videoScenePackages.scene_packages,
      artifact.videoRevisionFeedback,
      artifact.videoQualityReview,
      useQualityReview,
    );
    if (affectedSceneIds.size === 0) {
      releaseArtifactAction(processedKey);
      videoRevisionArtifactRef.current = {
        conversationId: targetConversationId,
        artifact: {
          ...artifact,
          originalVideoScenePackages,
        },
      };
      pushAssistant("QAAgent QC 质检没有定位到具体分镜。为了避免误把整条视频重做，请在修改意见里明确写出要修改的分镜，例如“只修改第2个分镜”。", targetConversationId);
      if (targetConversationId) {
        void updateConversationWithProgress(targetConversationId, {
            last_phase: "video_revision_scene_required",
            context: {
              ...makeSnapshot(),
              pendingVideoRevision: videoRevisionArtifactRef.current,
              pending_video_revision: videoRevisionArtifactRef.current,
              video_revision_feedback: artifact.videoRevisionFeedback,
              video_quality_review: artifact.videoQualityReview,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
      setBusyForConversation(targetConversationId, false);
      return;
    }
    const affectedSceneLabel = formatSceneIndexesForMessage(artifact.videoScenePackages.scene_packages, affectedSceneIds);
    pushAssistant(`正在重生成 ${affectedSceneLabel}，并复用未受影响分镜…`, targetConversationId);
    try {
      const nextVideoScenePackages = {
        ...artifact.videoScenePackages,
        scene_packages: scenePackagesWithRevisionContract(
          artifact.videoScenePackages.scene_packages as ScenePackageRecord[],
          affectedSceneIds,
          artifact.videoRevisionFeedback || "",
          useQualityReview ? artifact.videoQualityReview : undefined,
          artifact.videoScenePackages.global_assets,
          originalVideoScenePackages.scene_packages as ScenePackageRecord[],
        ) as typeof artifact.videoScenePackages.scene_packages,
      };
      const revisionArtifact: ChatArtifact = {
        ...artifact,
        videoScenePackages: nextVideoScenePackages,
        originalVideoScenePackages,
        videoScenePackageEditedSceneIds: Array.from(affectedSceneIds),
      };
      const request = sceneVideoRequestFromPackages(nextVideoScenePackages, affectedSceneIds);
      const started = await api.startSceneVideosJob(request);
      const pendingVideoJob: PendingVideoJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "scene_regeneration",
        started_at: new Date().toISOString(),
        request,
        artifact: revisionArtifact,
        affected_scene_ids: Array.from(affectedSceneIds),
        use_quality_review: useQualityReview,
      };
      await persistPendingVideoJob(pendingVideoJob, targetConversationId, "video_regeneration_running", {
        video_revision_feedback: artifact.videoRevisionFeedback,
        video_revision_use_quality_review: useQualityReview,
        affected_scene_ids: Array.from(affectedSceneIds),
        global_assets: nextVideoScenePackages.global_assets,
        scene_packages: nextVideoScenePackages.scene_packages,
      });
      await resumePendingVideoJob(pendingVideoJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频修改重生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  }

  const handleApprove = async () => {
    // 对应后端 /brief/confirm：恢复 LangGraph 的 Brief interrupt，批准后进入 GENERATE。
    pushAssistant("Brief 已确认,开始生成…");
    setBusy(true);
    briefConfirmedRef.current = true;
    setBriefConfirmed(true);
    try {
      await api.confirmBrief(taskIdRef.current, true);
      setCanvas((c) => ({ ...c, phase: "generate" }));
    } catch (err) {
      pushAssistant(`确认失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleRevise = async () => {
    // 对应后端 /brief/revise：当前只写业务 brief/反馈/偏好，不会直接恢复 LangGraph run。
    const fb = "请优化分镜节奏与卖点表达";
    pushAssistant("已请求修改 Brief。");
    try {
      await api.reviseBrief(taskIdRef.current, {}, fb);
      briefConfirmedRef.current = false;
      setBriefConfirmed(false);
      setCanvas((c) => ({ ...c, phase: "brief_review" }));
    } catch (err) {
      pushAssistant(`修改失败:${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleConfirmStage = async (stage: "segments" | "edit" | "qc", approved: boolean) => {
    setBusy(true);
    try {
      const task = await api.confirmStage(taskIdRef.current, stage, approved);
      setCanvas((c) => ({ ...c, phase: (task.phase as TaskPhase) || c.phase }));
      pushAssistant(approved ? "已确认,继续下一步。" : "已退回,重新处理。");
    } catch (err) {
      pushAssistant(`确认失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleOpenVideoResult = (msg: ChatMessage, video: VideoResult, results: VideoResult[]) => {
    setSelectedStoryboardMessageId("");
    setCanvasOpen(true);
    setCanvas((current) => ({
      ...current,
      phase: "done",
      results: results.length > 0 ? results : current.results,
      selectedVideo: video,
      selectedVideoSourceMessageId: video.assetType === "final_video" ? msg.id : undefined,
    }));
  };

  const handleDownloadPreviewVideo = (video: VideoResult, sourceMessageId?: string) => {
    if (video.assetType !== "final_video") return;
    const sourceMessage = sourceMessageId
      ? messagesRef.current.find((message) => message.id === sourceMessageId)
      : [...messagesRef.current].reverse().find((message) => message.artifact?.mergedVideo?.merged_video_url === video.url);
    if (
      sourceMessage
      && activeSupervisorVideoTarget?.ui.kind === "video_result_review"
      && activeSupervisorVideoMessage?.id === sourceMessage.id
      && video.url.startsWith("https://")
    ) {
      void submitSupervisorAction(
        "下载最终视频",
        buildSupervisorWorkflowAction({
          action: "continue_workflow",
          intent: "video",
          workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
          stage: activeSupervisorVideoTarget.stage,
          artifactRef: activeSupervisorVideoTarget.artifactRef,
          patch: { delivery_download_url: video.url },
        }),
        {
          artifactRefs: activeSupervisorVideoTarget.artifactRef
            ? [activeSupervisorVideoTarget.artifactRef]
            : [],
        },
      );
      return;
    }
    if (sourceMessage && legacyArtifactActionsEnabled) {
      void recordArtifactDownload(sourceMessage, video.url);
    }
  };

  useEffect(() => {
    if (
      restoringRef.current
      || !currentConversationId
      || !pageVisibleRef.current
      || orchestrationModeRef.current !== "frontend_v2"
    ) return;
    const visibleMessages = [...messages].reverse();
    const expiredImageResult = visibleMessages.find((message) => {
      if (messageConversationId(message, currentConversationId) !== currentConversationId) return false;
      if (message.artifact?.type !== "image_result" || !message.artifact.imageResult || message.artifact.imageAccepted) return false;
      if (hasPendingImageRevisionForResult(message, currentConversationId)) return false;
      return canAcceptImageResult(message.artifact.imageResult) && isReviewExpired(message.artifact.reviewExpiresAt);
    });
    if (expiredImageResult) {
      void handleAcceptImageResult(expiredImageResult, true);
    }
  }, [messages, currentConversationId]);

  const selectedStoryboardMessage = selectedStoryboardMessageId
    ? messages.find((message) => message.id === selectedStoryboardMessageId && message.artifact?.videoScenePackages)
    : undefined;
  const selectedPlanEditorMessage = selectedPlanEditorMessageId
    ? messages.find((message) => message.id === selectedPlanEditorMessageId && message.artifact?.type === "plan" && message.artifact.plan)
    : undefined;
  const derivedWorkflowTaskBoard = deriveWorkflowTaskBoard({
    progress: workflowProgress,
    messages,
  });
  const agentPlanTaskBoard = deriveWorkflowTaskBoardFromAgentPlan(
    supervisorRuntime.state.videoAgentPlan,
  );
  // Video Agent 有自主 Plan 时优先展示 Plan 步骤；否则回退固定阶段板。
  const workflowTaskBoard = (
    runtimePolicy.legacyRunnerEnabled || runtimePolicy.supervisorEnabled
  ) && (agentPlanTaskBoard || derivedWorkflowTaskBoard)
    ? {
        ...(agentPlanTaskBoard || derivedWorkflowTaskBoard)!,
        workflowId: `${currentConversationId}:${(agentPlanTaskBoard || derivedWorkflowTaskBoard)!.workflowId}`,
      }
    : null;
  const legacyArtifactActionsEnabled = runtimePolicy.legacyArtifactActionsEnabled;

  const jianyingDraftVersionForMessage = (msg: ChatMessage): string => {
    if (msg.artifact?.type === "jianying_draft") return msg.artifact.pendingJianyingDraftJob?.storyboard_version_id || "";
    const scenes = (msg.artifact?.generatedSceneVideos?.scene_videos || []).map((scene) => ({
      scene_id: scene.scene_id,
      scene_index: scene.scene_index,
      task_id: scene.task_id || null,
      video_url: scene.video_url,
    }));
    try {
      return scenes.length > 0 ? storyboardVersionId(scenes) : "";
    } catch {
      return "";
    }
  };

  const getJianyingDraftResult = (msg: ChatMessage): JianyingDraftJobResponse | null => {
    if (msg.artifact?.jianyingDraft) return msg.artifact.jianyingDraft;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const storyboardVersionId = jianyingDraftVersionForMessage(msg);
    return storyboardVersionId ? jianyingDraftRecordsForConversation(targetConversationId)[storyboardVersionId] || null : null;
  };

  const isJianyingDraftRunning = (msg: ChatMessage): boolean => {
    if (msg.artifact?.pendingJianyingDraftJob) return true;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const storyboardVersionId = jianyingDraftVersionForMessage(msg);
    const pending = pendingJianyingDraftJobRef.current;
    return Boolean(
      storyboardVersionId && pending?.conversation_id === targetConversationId && pending.storyboard_version_id === storyboardVersionId,
    );
  };

  const handleDownloadJianyingDraft = (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (targetConversationId) pushAssistant("剪映草稿已开始下载。", targetConversationId);
  };

  function renderSupervisorVideoArtifact(target: SupervisorVideoTarget) {
    const currentMessage = activeSupervisorVideoMessage;
    if (!currentMessage) return null;
    const acceptsMessage = (msg: ChatMessage): boolean => Boolean(
      msg.id === currentMessage.id
      && messageConversationId(msg, currentConversationId) === currentConversationId,
    );
    const submit = (
      content: string,
      request: {
        action: AgentAction;
        patch?: Readonly<Record<string, unknown>>;
      },
    ): Promise<void> => {
      return submitSupervisorAction(
        content,
        buildSupervisorWorkflowAction({
          action: request.action,
          intent: "video",
          workflowId: target.workflow.workflow_id,
          stage: target.stage,
          artifactRef: target.artifactRef,
          patch: request.patch || {},
        }),
        {
          artifactRefs: target.artifactRef ? [target.artifactRef] : [],
        },
      );
    };
    const replacementPatch = (replacement: SceneGlobalAssetReplacement): Record<string, unknown> => ({
      source: replacement.source,
      display_image_url: replacement.displayImageUrl,
      generation_reference_url: replacement.generationReferenceUrl,
      ...(replacement.thirdAssetId ? { third_asset_id: replacement.thirdAssetId } : {}),
      ...(replacement.assetType ? { asset_type: replacement.assetType } : {}),
      ...(replacement.contentAssetId ? { content_asset_id: replacement.contentAssetId } : {}),
      ...(replacement.assetName ? { asset_name: replacement.assetName } : {}),
    });
    const failedSceneIds = (msg: ChatMessage): string[] => Array.from(new Set(
      (msg.artifact?.generatedSceneVideos?.failed_scenes || [])
        .map((item) => typeof item.scene_id === "string" ? item.scene_id.trim() : "")
        .filter(Boolean),
    ));
    const submitSceneReviewDecision = (msg: ChatMessage): void => {
      if (!acceptsMessage(msg)) return;
      const failedIds = failedSceneIds(msg);
      if (failedIds.length > 0) {
        submit("重试失败的分镜视频", {
          action: "retry_failed",
          patch: { scene_ids: failedIds },
        });
        return;
      }
      if ((msg.artifact?.videoScenePackageEditedSceneIds || []).length > 0) {
        submit("重新生成已修改的分镜视频", { action: "regenerate_stage" });
        return;
      }
      submit("确认当前视频阶段", { action: "continue_workflow" });
    };
    const submitRevision = (msg: ChatMessage, useQualityReview: boolean): void => {
      if (!acceptsMessage(msg)) return;
      const packages = msg.artifact?.videoScenePackages;
      const scenes = packages?.scene_packages as ScenePackageRecord[] | undefined;
      if (!packages || !scenes?.length) return;
      const requested = window.prompt(
        useQualityReview ? "可补充修改意见；留空则只按质检结果修改。" : "请输入本次视频修改意见。",
        msg.artifact?.videoRevisionFeedback || "",
      );
      if (requested === null) return;
      const feedback = requested.trim();
      const qualityReview = useQualityReview ? msg.artifact?.videoQualityReview : undefined;
      const revisionFeedback = feedback || qualityReview?.revision_prompt?.trim() || "";
      if (!revisionFeedback) return;
      const affectedIds = sceneIdsForRevision(
        scenes,
        revisionFeedback,
        qualityReview,
        useQualityReview,
      );
      const revisedScenes = scenePackagesWithRevisionContract(
        scenes,
        affectedIds,
        revisionFeedback,
        qualityReview,
        packages.global_assets,
        msg.artifact?.originalVideoScenePackages?.scene_packages as ScenePackageRecord[] | undefined,
      );
      const scene_patches = Object.fromEntries(
        revisedScenes
          .filter((scene) => affectedIds.has(scene.scene_id))
          .map((scene) => [scene.scene_id, {
            storyline: scene.storyline || "",
            shot_description: scene.shot_description || {},
            narration: scene.narration || "",
            reference_asset_ids: scene.reference_asset_ids || [],
          }]),
      );
      if (Object.keys(scene_patches).length === 0) return;
      submit("按修改范围重新生成分镜", {
        action: "modify_workflow",
        patch: { scene_patches },
      });
    };

    return {
      message: currentMessage,
      onSelectDirection: (msg: ChatMessage, direction: CreativeDirectionResponse) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_direction_review") return;
        submit(`选择创意方向：${direction.title}`, {
          action: "continue_workflow",
          patch: { direction_id: direction.direction_id },
        });
      },
      onRegenerateDirections: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_direction_review") return;
        submit("重新生成视频创意方向", { action: "regenerate_stage" });
      },
      onApprovePlan: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        submit("同意当前视频创作方案", { action: "continue_workflow" });
      },
      onRevisePlan: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        const feedback = window.prompt("请输入方案修改意见。")?.trim();
        if (!feedback) return;
        submit("修改当前视频创作方案", {
          action: "modify_workflow",
          patch: { revision_feedback: feedback },
        });
      },
      onRollbackPlan: (msg: ChatMessage, version: number) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        submit(`恢复视频方案 v${version}`, {
          action: "modify_workflow",
          patch: { plan_version: version },
        });
      },
      onRegeneratePlanDirections: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        submit("返回创意方向并生成新创意", { action: "regenerate_stage" });
      },
      onGenerateVideoFromScenePackages: submitSceneReviewDecision,
      onRetrySceneAssets: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        submit("继续生成视频场景素材", { action: "continue_workflow" });
      },
      onRetryVideoMerge: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        submit("重试视频合并", { action: "retry_failed" });
      },
      onAcceptVideoResult: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        submit("确认最终视频", { action: "continue_workflow" });
      },
      onReviseVideoResult: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        const feedback = window.prompt("请输入视频修改意见。")?.trim();
        if (!feedback) return;
        submit("提交视频修改意见并启动质检", {
          action: "modify_workflow",
          patch: { user_feedback: feedback },
        });
      },
      onRegenerateVideoWithRevision: submitRevision,
      onGenerateJianyingDraft: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        const result = getJianyingDraftResult(msg);
        if (result?.status === "failed" || result?.status === "timeout") {
          submit("重新生成剪映草稿", {
            action: "retry_failed",
            patch: { jianying_action: "start" },
          });
          return;
        }
        submit("生成剪映草稿", {
          action: "modify_workflow",
          patch: { jianying_action: "start" },
        });
      },
      onDownloadJianyingDraft: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        const result = getJianyingDraftResult(msg);
        if (!result?.storyboard_version_id || !result.download_url?.startsWith("https://")) return;
        submit("下载剪映草稿", {
          action: "continue_workflow",
          patch: {
            jianying_action: "download",
            storyboard_version_id: result.storyboard_version_id,
            download_url: result.download_url,
          },
        });
      },
      onDownloadArtifact: (msg: ChatMessage, url: string) => {
        if (!acceptsMessage(msg) || !url.startsWith("https://")) return;
        submit("下载最终视频", {
          action: "continue_workflow",
          patch: { delivery_download_url: url },
        });
      },
      onUpdateVideoScenePackage: (sceneId: string, patch: ScenePackagePatch) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        const normalizedPatch: Record<string, unknown> = {};
        if (typeof patch.storyline === "string" && patch.storyline.trim()) {
          normalizedPatch.storyline = patch.storyline;
        }
        if (typeof patch.narration === "string") normalizedPatch.narration = patch.narration;
        if (patch.shot_description && typeof patch.shot_description === "object") {
          const text = typeof patch.shot_description.text === "string"
            ? patch.shot_description.text.trim()
            : "";
          if (text) normalizedPatch.shot_description = { text };
          const mentions = Array.isArray(patch.shot_description.mentions)
            ? patch.shot_description.mentions
            : [];
          normalizedPatch.reference_asset_ids = mentions
            .map((item) => item && typeof item === "object" && typeof item.asset_id === "string" ? item.asset_id : "")
            .filter(Boolean)
            .slice(0, 9);
        } else if (Array.isArray(patch.reference_asset_ids)) {
          normalizedPatch.reference_asset_ids = patch.reference_asset_ids.slice(0, 9);
        }
        if (Object.keys(normalizedPatch).length === 0) return;
        return submit("修改视频分镜", {
          action: "modify_workflow",
          patch: {
            scene_id: sceneId,
            scene_patch: normalizedPatch,
          },
        });
      },
      onDeleteGlobalAsset: (asset: SceneGlobalAssetReference) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        submit("删除视频全局素材", {
          action: "modify_workflow",
          patch: {
            asset_action: "delete",
            asset_group: asset.asset_group,
            asset_id: asset.asset_id,
          },
        });
      },
      onReplaceGlobalAsset: (
        asset: SceneGlobalAssetReference,
        replacement: SceneGlobalAssetReplacement,
      ) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        submit("替换视频全局素材", {
          action: "modify_workflow",
          patch: {
            asset_action: "replace",
            asset_group: asset.asset_group,
            asset_id: asset.asset_id,
            asset_patch: replacementPatch(replacement),
          },
        });
      },
      onAddGlobalAsset: (
        assetGroup: GlobalSceneAssetGroup,
        replacement: SceneGlobalAssetReplacement,
      ) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        const videoScenePackages = currentMessage.artifact?.videoScenePackages;
        if (!videoScenePackages) return;
        const added = addGlobalSceneAssetReference(videoScenePackages.global_assets, {
          assetGroup,
          manualId: replacement.contentAssetId || replacement.thirdAssetId || uid(),
          replacement,
        });
        submit("新增视频全局素材", {
          action: "modify_workflow",
          patch: {
            asset_action: "add",
            asset_group: assetGroup,
            asset_id: added.added_asset.asset_id,
            asset_patch: {
              ...replacementPatch(replacement),
              asset_name: added.added_asset.name,
            },
          },
        });
      },
      onSaveStoryboard: () => {
        setCanvasOpen(false);
        setSelectedStoryboardMessageId("");
      },
    };
  }

  const supervisorVideoArtifact = activeSupervisorVideoTarget
    ? renderSupervisorVideoArtifact(activeSupervisorVideoTarget)
    : null;
  const videoAgentWorkspace = videoAgentView.workspace;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1">
      <ChatPanel
        messages={messages}
        onSubmit={handleSend}
        onNewConversation={() => {
          window.dispatchEvent(new Event("pixelflow-new-conversation"));
          navigate("/", { replace: true });
        }}
        agentActivityBlocks={[
          ...(() => {
            const planOrder = videoAgentPlanHistory.order.length > 0
              ? videoAgentPlanHistory.order
              : supervisorRuntime.state.videoAgentPlanOrder;
            const planMap = Object.keys(videoAgentPlanHistory.plans).length > 0
              ? videoAgentPlanHistory.plans
              : supervisorRuntime.state.videoAgentPlans;
            return planOrder.map((planId) => {
              const plan = planMap[planId] || supervisorRuntime.state.videoAgentPlans[planId];
              if (!plan) return null;
              const anchoredId = videoAgentPlanAnchors[planId];
              const afterMessageId = (
                anchoredId
                && messages.some((message) => message.id === anchoredId)
              )
                ? anchoredId
                // 锚点失效时挂到首条用户消息，避免执行方案卡变成 orphan 沉到对话底部“消失”。
                : (messages.find((message) => message.role === "user")?.id || "");
              if (!afterMessageId) return null;
              const isActivePlan = supervisorRuntime.state.videoAgentPlan?.planId === planId;
              return {
                afterMessageId,
                content: (
                  <AgentPlanTimeline
                    plan={plan}
                    selectedStepId={selectedVideoAgentStepId}
                    scriptStages={videoAgentView.workspace?.scriptStages}
                    onSelectStep={setSelectedVideoAgentStepId}
                    confirmationSlot={isActivePlan && supervisorRuntime.state.videoAgentConfirmation ? (
                      <AgentConfirmationCard
                        confirmationId={supervisorRuntime.state.videoAgentConfirmation.confirmationId}
                        stepId={supervisorRuntime.state.videoAgentConfirmation.stepId}
                        title={supervisorRuntime.state.videoAgentConfirmation.title}
                        costSummary={supervisorRuntime.state.videoAgentConfirmation.costSummary}
                        affectedSceneIds={supervisorRuntime.state.videoAgentConfirmation.affectedSceneIds}
                        submitting={videoAgentConfirmationSubmitting}
                        actionAvailable={supervisorRuntime.state.videoAgentConfirmation.submittable}
                        unavailableReason={supervisorRuntime.state.videoAgentConfirmation.unavailableReason}
                        submissionError={videoAgentConfirmationError}
                        onSubmit={handleVideoAgentConfirmation}
                      />
                    ) : null}
                    quotaSlot={isActivePlan && supervisorRuntime.state.videoAgentQuota ? (
                      <AgentQuotaCard
                        quotaInterruptId={supervisorRuntime.state.videoAgentQuota.quotaInterruptId}
                        submitting={videoAgentQuotaSubmitting}
                        actionAvailable={supervisorRuntime.state.videoAgentQuota.submittable}
                        unavailableReason={supervisorRuntime.state.videoAgentQuota.unavailableReason}
                        submissionError={videoAgentQuotaError}
                        onSubmit={handleVideoAgentQuota}
                      />
                    ) : null}
                  />
                ),
              };
            });
          })()
            .filter((block): block is { afterMessageId: string; content: ReactElement } => block !== null),
          ...(assetPackageProgressSteps.length > 0
            ? [{
                afterMessageId: resolveAssetPackageProgressAnchorId({
                  preferredAnchorId: assetPackageAnchorMessageId,
                  messages,
                }),
                content: (
                  <AgentPipelineProgress
                    title="执行规划 · 视频资产包"
                    subtitle="分步生成"
                    steps={assetPackageProgressSteps}
                  />
                ),
              }]
            : []).filter((block) => Boolean(block.afterMessageId)),
        ]}
        referencedMaterials={referencedMaterials}
        onRemoveReferencedMaterial={handleRemoveReferencedMaterial}
        composerPrefillRequest={composerPrefillRequest}
        composerDisabled={interactionPolicy.composer.disabled}
        artifactActionsDisabled={interactionPolicy.artifact.actionsDisabled}
        runtimeBusy={interactionPolicy.runtime.busy}
        runtimeNotice={runtimeNotice}
        workflowTaskBoard={workflowTaskBoard}
        onSelectDirection={(legacyArtifactActionsEnabled ? handleSelectDirection : undefined)
          ?? supervisorVideoArtifact?.onSelectDirection}
        onRegenerateDirections={(legacyArtifactActionsEnabled ? handleRegenerateDirections : undefined)
          ?? supervisorVideoArtifact?.onRegenerateDirections}
        onApprovePlan={(msg) => {
          const isScriptAssetConfirm = Boolean(
            msg.artifact?.scriptPlanConfirmForAssets
            || msg.artifact?.title === "脚本方案待确认"
            || msg.artifact?.title === "已确认脚本方案",
          );
          if (isScriptAssetConfirm || legacyArtifactActionsEnabled) {
            void handleApprovePlan(msg);
            return;
          }
          supervisorVideoArtifact?.onApprovePlan?.(msg);
        }}
        onRegeneratePlanDirections={supervisorVideoArtifact?.onRegeneratePlanDirections}
        onEditPlan={legacyArtifactActionsEnabled ? handleEditPlan : undefined}
        onRevisePlan={(legacyArtifactActionsEnabled ? handleRevisePlan : undefined)
          ?? supervisorVideoArtifact?.onRevisePlan}
        agentRevisionSourceMessageId={agentRevisionSourceMessageId}
        onRollbackPlan={(legacyArtifactActionsEnabled ? handleRollbackPlan : undefined)
          ?? supervisorVideoArtifact?.onRollbackPlan}
        onGenerateImage={legacyArtifactActionsEnabled ? handleGenerateImage : undefined}
        onConfirmImageEditOptions={legacyArtifactActionsEnabled ? handleConfirmImageEditOptions : undefined}
        onConfirmSceneAssetModel={handleConfirmSceneAssetModel}
        onAcceptImageResult={legacyArtifactActionsEnabled ? handleAcceptImageResult : undefined}
        onReviseImageResult={legacyArtifactActionsEnabled ? handleReviseImageResult : undefined}
        onGenerateVideoFromScenePackages={(legacyArtifactActionsEnabled ? handleGenerateVideoFromScenePackages : undefined)
          ?? supervisorVideoArtifact?.onGenerateVideoFromScenePackages}
        onAcceptVideoResult={(legacyArtifactActionsEnabled ? handleAcceptVideoResult : undefined)
          ?? supervisorVideoArtifact?.onAcceptVideoResult}
        onReviseVideoResult={(legacyArtifactActionsEnabled ? handleReviseVideoResult : undefined)
          ?? supervisorVideoArtifact?.onReviseVideoResult}
        onOpenVideoResult={handleOpenVideoResult}
        onRegenerateVideoWithRevision={(legacyArtifactActionsEnabled ? handleRegenerateVideoWithRevision : undefined)
          ?? supervisorVideoArtifact?.onRegenerateVideoWithRevision}
        onRetryImageResult={legacyArtifactActionsEnabled ? handleRetryImageResult : undefined}
        onRetrySceneAssets={(msg: ChatMessage) => {
          // prepare-scene-packages / Video Agent 资产包走 Python job；v2 下不能落到空的 continue_workflow。
          if (msg.artifact?.videoScenePackages && msg.artifact.sceneAssetFailures?.length) {
            void handleRetrySceneAssets(msg);
            return;
          }
          if (legacyArtifactActionsEnabled) {
            void handleRetrySceneAssets(msg);
            return;
          }
          supervisorVideoArtifact?.onRetrySceneAssets?.(msg);
        }}
        onRetryVideoMerge={(legacyArtifactActionsEnabled ? handleRetryVideoMerge : undefined)
          ?? supervisorVideoArtifact?.onRetryVideoMerge}
        onRetryVideoAnalysis={legacyArtifactActionsEnabled ? handleRetryVideoAnalysis : undefined}
        onApprovePptOutline={legacyArtifactActionsEnabled ? handleApprovePptOutline : undefined}
        onRevisePptOutline={legacyArtifactActionsEnabled ? handleRevisePptOutline : undefined}
        onRegeneratePptImage={legacyArtifactActionsEnabled ? handleRegeneratePptImage : undefined}
        onGeneratePptFile={legacyArtifactActionsEnabled ? handleGeneratePptFile : undefined}
        onAcceptPptFile={legacyArtifactActionsEnabled ? handleAcceptPptFile : undefined}
        onRegeneratePptFile={legacyArtifactActionsEnabled ? handleRegeneratePptFile : undefined}
        onGenerateJianyingDraft={(legacyArtifactActionsEnabled ? handleGenerateJianyingDraft : undefined)
          ?? supervisorVideoArtifact?.onGenerateJianyingDraft}
        onDownloadJianyingDraft={(legacyArtifactActionsEnabled ? handleDownloadJianyingDraft : undefined)
          ?? supervisorVideoArtifact?.onDownloadJianyingDraft}
        jianyingDraftCapability={jianyingDraftCapability}
        getJianyingDraftResult={getJianyingDraftResult}
        isJianyingDraftRunning={isJianyingDraftRunning}
        onDownloadArtifact={(msg, url) => {
          if (supervisorVideoArtifact?.message?.id === msg.id) {
            supervisorVideoArtifact.onDownloadArtifact(msg, url);
            return;
          }
          if (legacyArtifactActionsEnabled) void recordArtifactDownload(msg, url);
        }}
        onOpenArtifact={(msg) => {
          if (!msg.artifact) return;
          if (
            supervisorVideoArtifact
            && supervisorVideoArtifact.message?.id !== msg.id
            && !legacyArtifactActionsEnabled
          ) return;
          setCanvasOpen(true);
          setSelectedPlanEditorMessageId("");
          if (msg.artifact.type === "video_scene_packages") {
            setSelectedStoryboardMessageId(msg.id);
            return;
          }
          setSelectedStoryboardMessageId("");
          if (msg.artifact.type === "brief") setCanvas((c) => ({ ...c, phase: "brief_review" }));
          if (msg.artifact.type === "results") setCanvas((c) => ({ ...c, phase: "done" }));
          if (msg.artifact.type === "segments") setCanvas((c) => ({ ...c, phase: "segment_review" }));
          if (msg.artifact.type === "edit") setCanvas((c) => ({ ...c, phase: "edit_review" }));
          if (msg.artifact.type === "qc") setCanvas((c) => ({ ...c, phase: "qc_review" }));
          if (msg.artifact.type === "video_result") setCanvas((c) => ({ ...c, phase: "done", selectedVideo: null }));
          if (["segments", "edit", "qc"].includes(msg.artifact.type)) {
            const phaseByType = { segments: "segment_review", edit: "edit_review", qc: "qc_review" } as const;
            void loadResults(phaseByType[msg.artifact.type as "segments" | "edit" | "qc"]);
          }
        }}
      />
      {legacyArtifactActionsEnabled && canvasOpen && selectedPlanEditorMessage?.artifact?.plan ? (
        <Suspense fallback={<aside className="fixed inset-0 z-50 flex h-full w-full min-w-0 items-center justify-center border-l border-line bg-[#f8fafc] text-[13px] text-ink-soft xl:static xl:z-auto xl:w-[52vw] xl:min-w-[680px]">正在加载 Markdown 编辑器…</aside>}>
          <PlanMarkdownEditor
            planVersion={selectedPlanEditorMessage.artifact.plan.plan_version || 1}
            initialMarkdown={selectedPlanEditorMessage.artifact.plan.plan_markdown}
            saving={savingPlanEdit}
            onConfirm={(markdown) => handlePublishPlanEdit(selectedPlanEditorMessage, markdown)}
            onClose={() => {
              setCanvasOpen(false);
              setSelectedPlanEditorMessageId("");
            }}
          />
        </Suspense>
      ) : canvasOpen && selectedStoryboardMessage?.artifact?.videoScenePackages ? (
        <VideoAgentStoryboardSurface
          msg={selectedStoryboardMessage}
          deferSceneUpdates={Boolean(supervisorVideoArtifact)}
          onUpdateVideoScenePackage={legacyArtifactActionsEnabled
            ? (sceneId, patch) => handleUpdateVideoScenePackage(selectedStoryboardMessage, sceneId, patch)
            : supervisorVideoArtifact?.onUpdateVideoScenePackage}
          onReferenceGlobalAsset={runtimePolicy.supervisorEnabled || legacyArtifactActionsEnabled ? handleReferenceGlobalAsset
            : undefined}
          onDeleteGlobalAsset={runtimePolicy.supervisorEnabled || legacyArtifactActionsEnabled ? handleDeleteGlobalAsset
            : undefined}
          onReplaceGlobalAsset={legacyArtifactActionsEnabled ? handleReplaceGlobalAsset : undefined}
          onSupervisorReplaceGlobalAsset={supervisorVideoArtifact?.onReplaceGlobalAsset}
          onAddGlobalAsset={legacyArtifactActionsEnabled
            ? (assetGroup, replacement) => handleAddGlobalAsset(selectedStoryboardMessage, assetGroup, replacement)
            : supervisorVideoArtifact?.onAddGlobalAsset}
          onGenerateVideo={legacyArtifactActionsEnabled
            ? () => handleGenerateVideoFromScenePackages(selectedStoryboardMessage)
            : () => supervisorVideoArtifact?.onGenerateVideoFromScenePackages(selectedStoryboardMessage)}
          onRetrySceneAssets={legacyArtifactActionsEnabled
            ? () => handleRetrySceneAssets(selectedStoryboardMessage)
            : () => supervisorVideoArtifact?.onRetrySceneAssets(selectedStoryboardMessage)}
          onSave={legacyArtifactActionsEnabled
            ? () => handleSaveVideoScenePackage(selectedStoryboardMessage)
            : supervisorVideoArtifact?.onSaveStoryboard}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
            setSelectedPlanEditorMessageId("");
          }}
        />
      ) : canvasOpen && (
        <CanvasPanel
          state={canvas}
          onApprove={legacyArtifactActionsEnabled ? handleApprove : () => undefined}
          onRevise={legacyArtifactActionsEnabled ? handleRevise : () => undefined}
          onConfirmStage={legacyArtifactActionsEnabled ? handleConfirmStage : undefined}
          onSelectVideo={(video) => setCanvas((current) => ({ ...current, selectedVideo: video }))}
          onDownloadVideo={handleDownloadPreviewVideo}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
            setSelectedPlanEditorMessageId("");
          }}
          briefConfirmed={briefConfirmed}
        />
      )}
      {!canvasOpen && videoAgentView.selectedEvidence ? (
        <SceneEvidencePanel
          revision={videoAgentView.selectedEvidence.revision}
          scene={videoAgentView.selectedEvidence.scene}
          scenes={videoAgentView.workspace?.scenes}
          selectedSceneId={videoAgentView.selectedSceneId}
          onSelectScene={videoAgentView.selectScene}
          onEditScene={(sceneId) => {
            const selected = videoAgentView.workspace?.scenes.find(
              (scene) => scene.sceneId === sceneId,
            );
            setComposerPrefillRequest({
              id: uid(),
              content: `请修改分镜 ${selected?.sceneIndex ?? sceneId}：`,
            });
          }}
        />
      ) : !canvasOpen && videoAgentWorkspace && (
        videoAgentWorkspace.script
        || videoAgentWorkspace.scriptStages.length > 0
      ) ? (
        <AgentScriptPreviewPanel
          revision={videoAgentWorkspace.revision}
          script={videoAgentWorkspace.script}
          stages={videoAgentWorkspace.scriptStages}
          focusStageId={(() => {
            const plan = supervisorRuntime.state.videoAgentPlan;
            const stepId = selectedVideoAgentStepId;
            if (!plan || !stepId) return null;
            const step = plan.steps[stepId];
            return step ? stageIdFromStep(step) : null;
          })()}
          saving={savingVideoAgentScript}
          confirming={confirmingVideoAgentScript}
          exportReady={workspaceHasExportReady({
            scriptContent: videoAgentWorkspace.script?.content,
            stages: videoAgentWorkspace.scriptStages,
          })}
          onSave={videoAgentWorkspace.script ? async (markdown) => {
            const conversationId = currentConversationId;
            const workspace = videoAgentView.workspace;
            if (!conversationId || !workspace?.script) {
              throw new Error("当前会话没有可保存的脚本工作区");
            }
            setSavingVideoAgentScript(true);
            try {
              await supervisorApi.saveVideoAgentScript(conversationId, {
                markdown,
                expected_revision: workspace.revision,
              });
              await supervisorRuntime.refreshSnapshot();
              pushAssistant(
                workspaceHasExportReady({
                  scriptContent: markdown,
                  stages: workspace.scriptStages,
                })
                  ? "脚本已保存。请确认脚本方案后再生成视频资产包。"
                  : "脚本已保存。请先完成「导出脚本产物」，再确认并生成资产包。",
                conversationId,
              );
            } finally {
              setSavingVideoAgentScript(false);
            }
          } : undefined}
          onConfirmScript={videoAgentWorkspace.script ? async (markdown) => {
            const conversationId = currentConversationId;
            if (!conversationId) {
              throw new Error("当前会话没有可确认的脚本工作区");
            }
            await confirmScriptPlanAndGenerateAssetPackage(conversationId, markdown);
          } : undefined}
        />
      ) : null}
      {legacyArtifactActionsEnabled && dialogOpen && (
        <GenParamsDialog
          key={`${pendingIntent}:${pendingCore}`}
          open
          intent={pendingIntent}
          initialCoreMessage={pendingCore}
          initialValues={pendingFormValues}
          initialMaterials={pendingMaterials}
          onConfirm={handleConfirmParams}
          onCancel={handleCancelParamsDialog}
        />
      )}
      {restoredSupervisorUi?.kind === "video_intake_form" && activeSupervisorVideoTarget ? (
        <GenParamsDialog
          key={`supervisor-video:${activeSupervisorVideoTarget.workflow.workflow_id}:${activeSupervisorVideoTarget.workflow.stage_version}`}
          open
          intent="video"
          initialCoreMessage={activeSupervisorVideoTarget.ui.coreMessage}
          initialValues={activeSupervisorVideoTarget.ui.formValues}
          initialMaterials={activeSupervisorVideoTarget.ui.materials}
          onConfirm={(form) => {
            void submitSupervisorAction(
              "确认视频创作需求",
              buildSupervisorWorkflowAction({
                action: "continue_workflow",
                intent: "video",
                workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
                stage: activeSupervisorVideoTarget.stage,
                artifactRef: activeSupervisorVideoTarget.artifactRef,
                patch: {
                  form_values: form,
                  intake_rounds: activeSupervisorVideoTarget.ui.intakeRounds,
                },
              }),
              {
                materials: activeSupervisorVideoTarget.ui.materials,
                artifactRefs: activeSupervisorVideoTarget.artifactRef
                  ? [activeSupervisorVideoTarget.artifactRef]
                  : [],
              },
            );
          }}
          onCancel={() => {
            void submitSupervisorAction(
              "取消视频创作流程",
              buildSupervisorWorkflowAction({
                action: "cancel_workflow",
                intent: "video",
                workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
                stage: activeSupervisorVideoTarget.stage,
                artifactRef: activeSupervisorVideoTarget.artifactRef,
                patch: { form_cancelled: true },
              }),
              {
                artifactRefs: activeSupervisorVideoTarget.artifactRef
                  ? [activeSupervisorVideoTarget.artifactRef]
                  : [],
              },
            );
          }}
        />
      ) : null}
      {restoredSupervisorUi?.kind === "authorization_required"
        && activeSupervisorVideoTarget
        && restoredSupervisorUi.authorizationAction ? (
          <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 px-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
              <h2 className="text-base font-semibold text-slate-900">需要重新授权</h2>
              <p className="mt-2 text-sm text-slate-600">
                当前操作尚未调用供应商。请确认登录状态后继续原操作。
              </p>
              <button
                type="button"
                className="mt-4 rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white"
                onClick={() => {
                  void submitSupervisorAction(
                    "授权后继续原视频操作",
                    restoredSupervisorUi.authorizationAction as ExplicitActionSignal,
                    {
                      artifactRefs: activeSupervisorVideoTarget.artifactRef
                        ? [activeSupervisorVideoTarget.artifactRef]
                        : [],
                    },
                  );
                }}
              >
                重新授权并继续
              </button>
            </div>
          </div>
        ) : null}
      <PlanRevisionDialog
        open={Boolean(legacyArtifactActionsEnabled && pendingPlanRevisionChoice && pendingPlanRevisionChoice.conversationId === currentConversationId)}
        feedback={pendingPlanRevisionChoice?.feedback || ""}
        onConfirm={(mode) => void handleConfirmPlanRevisionMode(mode)}
        onCancel={handleCancelPlanRevisionMode}
      />
      </div>
    </div>
  );
}

export { WorkspacePage as LegacyWorkspace };
