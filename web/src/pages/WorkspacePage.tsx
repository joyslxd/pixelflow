import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { StoryboardPanel } from "@/components/canvas/StoryboardPanel";
import { GenParamsDialog, type CreationIntent, type GenParamsForm } from "@/components/composer/GenParamsDialog";
import {
  api,
  setActiveConversationId as setActiveConversationIdForTrace,
  subscribeTaskEvents,
  type ConversationDetailResponse,
  type ConversationMessageResponse,
  type CreativeDirectionResponse,
  type CreativeDirectionsResponse,
  type ImageEditModelSelection,
  type ImageAssetEditResponse,
  type ImageGenerateResponse,
  type ImageModelParamConfig,
  type ImagePrepareResponse,
  type IntakeIntentResponse,
  type MergeSceneVideosResponse,
  type PlanMarkdownResponse,
  type PptFileResult,
  type PptImagesResult,
  type PptJobStatusResponse,
  type PptPageImage,
  type PptContentJsonResult,
  type PptSummaryResult,
  type GenerateSceneAssetsResponse,
  type PrepareScenePackagesJobResult,
  type PrepareScenePackagesResponse,
  type SceneGenerationPayload,
  type SceneVideoPayload,
  type TaskEvent,
} from "@/lib/api";
import type { ChatMessage, CanvasState, Brief, BriefShot } from "@/lib/chat";
import type { AgentUserMessagePayload } from "@/lib/authStorage";
import {
  appendVisibleConversationMessage,
  messageConversationId,
  replaceMessageById,
  restoredConversationMessages,
  shouldApplyVisibleConversationSideEffect,
} from "@/lib/conversationRouting";
import { buildImageRevisionPreparePayload, canAcceptImageResult, imageResultSummary } from "@/lib/imageReview";
import {
  deleteGlobalSceneAssetReference,
  inferTargetDurationMs,
  replaceGlobalSceneAssetImage,
  sceneGenerationPayloadFromPackage,
  sceneIdsForRevision,
  scenePackagesWithRevisionContract,
  scenePackagesWithoutRevisionContract,
  syncScenePackageMentionImageUrls,
  updateScenePackageField,
  type GlobalSceneAssetGroup,
  type SceneGlobalAssetReference,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import { formatClockTime } from "@/lib/time";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "@/lib/types";

let seq = 0;
const clientMessagePrefix = Math.random().toString(36).slice(2, 8);
const uid = () => `m${Date.now().toString(36)}-${clientMessagePrefix}-${++seq}`;
const now = () => formatClockTime(new Date().toISOString());

const isCreationIntent = (value: unknown): value is CreationIntent => value === "video" || value === "image" || value === "ppt";
const AUTO_CONFIRM_TIMEOUT_MS = 60_000;
const AUTO_CONFIRM_TIMEOUT_SECONDS = AUTO_CONFIRM_TIMEOUT_MS / 1000;
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
    time: formatClockTime(event.created_at, "zh-CN", undefined, now()),
  };
}

interface WorkspaceSnapshot {
  taskId: string;
  messages?: ChatMessage[];
  pendingMaterials: Array<Record<string, unknown>>;
  flowDraft?: FlowDraft | null;
  pendingDirectionJob?: PendingDirectionJob | null;
  pending_direction_job?: PendingDirectionJob | null;
  pendingImageEditRequest?: PendingImageEditRequest | null;
  imageEditConfirmedSelections?: Record<string, ImageEditModelSelection>;
  pendingImageJob?: PendingImageJob | null;
  pending_image_job?: PendingImageJob | null;
  pendingScenePackageJob?: PendingScenePackageJob | null;
  pending_scene_package_job?: PendingScenePackageJob | null;
  pendingVideoJob?: PendingVideoJob | null;
  pending_video_job?: PendingVideoJob | null;
  pendingPptJob?: PendingPptJob | null;
  pending_ppt_job?: PendingPptJob | null;
  ppt_done?: boolean;
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
}

type ChatArtifact = NonNullable<ChatMessage["artifact"]>;

interface PendingConversationArtifact {
  conversationId: string;
  artifact: ChatArtifact;
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
  "video_flaw_analysis",
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
  "pendingDirectionJob",
  "pending_direction_job",
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

interface PendingImageEditRequest {
  conversationId: string;
  prompt: string;
  formValues: Record<string, unknown>;
  intakeContext: Record<string, unknown>;
  materials: Array<Record<string, unknown>>;
  selection?: ImageEditModelSelection;
}

type PendingImageJobKind = "image_generation" | "image_regeneration" | "direct_image_edit" | "scene_global_asset_edit";
type PendingImageJobApi = "generate" | "edit_asset";

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
  ratio?: string;
  size?: string;
  model?: string | null;
}

interface PendingImageJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: PendingImageJobKind;
  job_api: PendingImageJobApi;
  started_at: string;
  request: ImageGenerationJobRequest | ImageAssetEditJobRequest;
  artifact: ChatArtifact;
  imagePrepare?: ImagePrepareResponse;
  sceneGlobalAssetReference?: SceneGlobalAssetReference;
  storyboard_message_id?: string;
  revision_feedback?: string;
}

type PendingScenePackageJobKind = "scene_package_generation" | "scene_asset_generation";

interface PrepareScenePackagesJobRequest {
  form_values: Record<string, unknown>;
  plan_markdown: string;
  selected_direction: Record<string, unknown>;
  materials?: Array<Record<string, unknown>>;
  target_duration_ms?: number;
}

interface SceneAssetsJobRequest {
  global_assets?: Record<string, unknown>;
  scene_packages: PrepareScenePackagesResponse["scene_packages"];
  materials?: Array<Record<string, unknown>>;
  image_size?: string;
  model?: string | null;
}

interface PendingScenePackageJob {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: PendingScenePackageJobKind;
  started_at: string;
  request: PrepareScenePackagesJobRequest | SceneAssetsJobRequest;
  artifact: ChatArtifact;
}

type PendingVideoJobKind = "scene_generation" | "scene_regeneration" | "scene_failed_retry" | "video_merge";

interface SceneVideosJobRequest {
  scenes: SceneGenerationPayload[];
  ratio?: string;
  size?: string;
  model?: string | null;
  sound?: string;
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
  use_flaw_analysis?: boolean;
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
): Set<string> {
  const sceneIds = new Set<string>();
  for (const failedScene of generatedSceneVideos?.failed_scenes || []) {
    const sceneId = String(failedScene.scene_id || failedScene.sceneId || "");
    if (sceneId) sceneIds.add(sceneId);
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
  if (!assetId || !isGlobalSceneAssetGroup(assetGroup) || !sourceImageUrl) return null;
  return {
    ...material,
    source: "scene_global_asset",
    asset_id: assetId,
    asset_group: assetGroup,
    scene_global_asset_action: material.scene_global_asset_action === "delete" ? "delete" : "edit",
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
  return result.edited_image.url || result.edited_image.download_url || "";
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

function quotaMessage(fallback: string) {
  return `${fallback} 当前操作已暂停，充值后回到本对话可以继续执行。`;
}

function processedArtifactKey(message: Pick<ChatMessage, "id">, conversationId: string): string {
  return `${conversationId || "local"}:${message.id}`;
}

function scenePackageJobMessageId(job: Pick<PendingScenePackageJob, "kind" | "job_id">): string {
  return `scene-package-job:${job.kind}:${job.job_id}`;
}

function hasMaterializedScenePackageJob(messages: ChatMessage[], job: PendingScenePackageJob): boolean {
  const expectedMessageId = scenePackageJobMessageId(job);
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
    time: formatClockTime(message.created_at),
    artifact,
  };
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
  const latestIndex = [...messages]
    .reverse()
    .findIndex((message) => message.artifact?.type === "video_scene_packages" && Boolean(message.artifact.videoScenePackages));
  if (latestIndex < 0) return messages;
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
          global_assets: (globalAssets || latestVideoResultArtifact?.videoScenePackages?.global_assets || videoScenePackages.global_assets) as typeof videoScenePackages.global_assets,
          scene_packages: (Array.isArray(scenePackages)
            ? scenePackages
            : latestVideoResultArtifact?.videoScenePackages?.scene_packages || videoScenePackages.scene_packages) as typeof videoScenePackages.scene_packages,
        },
        generatedSceneVideos: generatedSceneVideos || message.artifact.generatedSceneVideos,
        mergedVideo: mergedVideo || message.artifact.mergedVideo,
        videoScenePackageEditedSceneIds: editedSceneIds || message.artifact.videoScenePackageEditedSceneIds,
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
  // 页面可渲染状态：聊天消息、右侧画布、参数弹窗、流程 busy 态和 Brief 确认态。
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvas, setCanvas] = useState<CanvasState>(EMPTY_CANVAS);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [selectedStoryboardMessageId, setSelectedStoryboardMessageId] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [pendingCore, setPendingCore] = useState("");
  const [pendingIntent, setPendingIntent] = useState<CreationIntent>("video");
  const [pendingFormValues, setPendingFormValues] = useState<Record<string, unknown>>({});
  const [pendingMaterials, setPendingMaterials] = useState<Array<Record<string, unknown>>>([]);
  const [referencedMaterials, setReferencedMaterials] = useState<SceneGlobalAssetReference[]>([]);
  const [composerPrefillRequest, setComposerPrefillRequest] = useState<{ id: string; content: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [briefConfirmed, setBriefConfirmed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState("");

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
  }, []);

  // 运行中上下文：这些值主要给异步 SSE 回调读取，不需要每次变化都触发 React 重渲染。
  // 可以类比后端 Service 内部字段，保存当前 taskId、事件去重集合和取消订阅函数。
  const [currentTaskId, setCurrentTaskId] = useState("");
  const messagesRef = useRef<ChatMessage[]>([]);
  const conversationIdRef = useRef<string>("");
  const routeConversationIdRef = useRef<string>("");
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const processedArtifactIdsRef = useRef(new Set<string>());
  const pendingDialogContextRef = useRef<PendingDialogContext | null>(null);
  const flowDraftRef = useRef<FlowDraft | null>(null);
  const pendingDirectionJobRef = useRef<PendingDirectionJob | null>(null);
  const activeDirectionJobPollsRef = useRef(new Set<string>());
  const pendingImageEditRequestRef = useRef<PendingImageEditRequest | null>(null);
  const imageEditConfirmedSelectionsRef = useRef<Record<string, ImageEditModelSelection>>({});
  const pendingImageJobRef = useRef<PendingImageJob | null>(null);
  const activeImageJobPollsRef = useRef(new Set<string>());
  const planRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const pptOutlineRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const imageRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const videoRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const pendingScenePackageJobRef = useRef<PendingScenePackageJob | null>(null);
  const activeScenePackageJobPollsRef = useRef(new Set<string>());
  const pendingVideoJobRef = useRef<PendingVideoJob | null>(null);
  const activeVideoJobPollsRef = useRef(new Set<string>());
  const pendingPptJobRef = useRef<PendingPptJob | null>(null);
  const activePptJobPollsRef = useRef(new Set<string>());
  const pptDoneConversationIdsRef = useRef(new Set<string>());
  const briefReadyShownRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const pageVisibleRef = useRef(true);
  const restoringRef = useRef(false);
  const saveTimerRef = useRef<number | undefined>(undefined);
  const skipRouteRestoreConversationRef = useRef("");
  const unsubRef = useRef<() => void>(() => {});
  routeConversationIdRef.current = conversationId || "";

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

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
    conversationIdRef.current = id;
    setCurrentConversationId(id);
    setActiveConversationIdForTrace(id || null);
  };

  const isCurrentConversation = (targetConversationId: string) => {
    const activeConversationId = conversationIdRef.current || routeConversationIdRef.current;
    return shouldApplyVisibleConversationSideEffect(activeConversationId, targetConversationId);
  };

  const isVisibleConversation = (targetConversationId: string) => {
    return pageVisibleRef.current && isCurrentConversation(targetConversationId);
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
    const key = processedArtifactKey(msg, targetConversationId);
    if (processedArtifactIdsRef.current.has(key)) return "";
    processedArtifactIdsRef.current.add(key);
    return key;
  };

  const releaseArtifactAction = (key: string) => {
    if (key) processedArtifactIdsRef.current.delete(key);
  };

  const persistChatMessage = async (conversation: string, message: ChatMessage): Promise<ChatMessage> => {
    const saved = await api.appendConversationMessage(conversation, {
      role: message.role,
      content: message.content,
      payload: { artifact: message.artifact, materials: message.materials || [], client_message_id: message.id },
    });
    return {
      ...message,
      id: message.id,
      conversationId: conversation,
      time: formatClockTime(saved.created_at),
    };
  };

  const appendMessageForConversation = async (message: ChatMessage, targetConversationId: string): Promise<ChatMessage> => {
    if (targetConversationId) {
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
      try {
        const savedMessage = await persistChatMessage(targetConversationId, optimisticMessage);
        setMessages((items) => {
          const currentMessage = items.find((item) => item.id === optimisticMessage.id);
          const nextItems = replaceMessageById(items, optimisticMessage.id, {
            ...savedMessage,
            artifact: currentMessage?.artifact || savedMessage.artifact,
            materials: currentMessage?.materials || savedMessage.materials,
          });
          messagesRef.current = nextItems;
          return nextItems;
        });
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

  const pushAssistant = (content: string, targetConversationId = conversationIdRef.current) => {
    const message: ChatMessage = { id: uid(), conversationId: targetConversationId || undefined, role: "assistant", content, time: "" };
    void appendMessageForConversation(message, targetConversationId);
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

  const shouldAutoSelectDirection = (message: ChatMessage, targetConversationId: string): boolean => {
    if (!isVisibleConversation(targetConversationId)) return false;
    if (processedArtifactIdsRef.current.has(processedArtifactKey(message, targetConversationId))) return false;
    if (hasLaterDirectionSuccessor(messagesRef.current, targetConversationId, message)) return false;
    return messagesRef.current.some(
      (item) =>
        item.id === message.id &&
        messageConversationId(item, targetConversationId) === targetConversationId &&
        item.artifact?.type === "directions",
    );
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
      void api
        .updateConversation(targetConversationId, {
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
      void api
        .updateConversation(targetConversationId, {
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
    setMessages((items) =>
      items.map((message) => {
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
      }),
    );
    return updatedPackages;
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
      void api
        .updateConversation(targetConversationId, {
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
  ): ChatMessage | undefined => {
    const selectedCandidate = selectedStoryboardMessageId
      ? messages.find((item) => item.id === selectedStoryboardMessageId && item.artifact?.videoScenePackages)
      : undefined;
    if (storyboardMessageHasGlobalAsset(selectedCandidate, reference)) return selectedCandidate;
    const latestCandidate = [...messages]
      .reverse()
      .find(
        (item) =>
          messageConversationId(item, targetConversationId) === targetConversationId &&
          Boolean(item.artifact?.videoScenePackages) &&
          storyboardMessageHasGlobalAsset(item, reference),
      );
    if (latestCandidate) return latestCandidate;
    const referencedCandidate = reference.storyboard_message_id
      ? messages.find((item) => item.id === reference.storyboard_message_id && item.artifact?.videoScenePackages)
      : undefined;
    return storyboardMessageHasGlobalAsset(referencedCandidate, reference) ? referencedCandidate : undefined;
  };

  const handleUpdateVideoScenePackage = (msg: ChatMessage, sceneId: string, patch: ScenePackagePatch) => {
    updateVideoScenePackagesInMessage(msg.id, (scenePackages) => updateScenePackageField(scenePackages, sceneId, patch), sceneId);
  };

  const handleReferenceGlobalAsset = (asset: SceneGlobalAssetReference) => {
    const material = selectedStoryboardMessageId ? { ...asset, scene_global_asset_action: "edit" as const, storyboard_message_id: selectedStoryboardMessageId } : { ...asset, scene_global_asset_action: "edit" as const };
    setReferencedMaterials((items) => {
      const next = items.filter((item) => item.asset_id !== material.asset_id);
      return [material, ...next].slice(0, 1);
    });
  };

  const handleDeleteGlobalAsset = (asset: SceneGlobalAssetReference) => {
    const material = selectedStoryboardMessageId ? { ...asset, storyboard_message_id: selectedStoryboardMessageId } : asset;
    const deleteMaterial = { ...material, scene_global_asset_action: "delete" as const };
    setReferencedMaterials((items) => {
      const next = items.filter((item) => item.asset_id !== deleteMaterial.asset_id);
      return [deleteMaterial, ...next].slice(0, 1);
    });
    setComposerPrefillRequest({ id: uid(), content: SCENE_GLOBAL_ASSET_DELETE_PROMPT(deleteMaterial.name) });
  };

  const handleRemoveReferencedMaterial = (key: string) => {
    setReferencedMaterials((items) => items.filter((item) => sceneGlobalAssetMaterialKey(item) !== key));
  };

  const handleEditReferencedGlobalAsset = async (
    reference: SceneGlobalAssetReference,
    prompt: string,
    targetConversationId: string,
  ): Promise<boolean> => {
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId);
    if (!storyboardMessage?.artifact?.videoScenePackages) {
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后再编辑。", targetConversationId);
      return true;
    }
    if (!prompt.trim()) {
      pushAssistant("已引用素材，请在输入框里写清楚要怎么修改这张图片。", targetConversationId);
      return true;
    }

    setReferencedMaterials((items) => items.filter((item) => item.asset_id !== reference.asset_id));
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`正在调用图片编辑接口修改「${reference.name}」…`, targetConversationId);
    try {
      const request: ImageAssetEditJobRequest = {
        asset_id: reference.asset_id,
        asset_name: reference.name,
        asset_group: reference.asset_group,
        source_image_url: reference.source_image_url,
        prompt,
      };
      const started = await api.startImageAssetEditJob(request);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: storyboardMessage.id,
        kind: "scene_global_asset_edit",
        job_api: "edit_asset",
        started_at: new Date().toISOString(),
        request,
        artifact: storyboardMessage.artifact,
        sceneGlobalAssetReference: reference,
        storyboard_message_id: storyboardMessage.id,
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, "scene_global_asset_edit_running", {
        scene_global_asset_reference: reference,
        scene_global_asset_edit_prompt: prompt,
      });
      await resumePendingImageJob(pendingImageJob);
    } catch (err) {
      pushAssistant(`全局素材图片编辑失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
    return true;
  };

  const handleDeleteReferencedGlobalAsset = async (
    reference: SceneGlobalAssetReference,
    targetConversationId: string,
  ): Promise<boolean> => {
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId);
    if (!storyboardMessage?.artifact?.videoScenePackages) {
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后再删除。", targetConversationId);
      return true;
    }

    const updated = deleteGlobalSceneAssetReference(
      storyboardMessage.artifact.videoScenePackages.global_assets,
      storyboardMessage.artifact.videoScenePackages.scene_packages as ScenePackageRecord[],
      {
        assetId: reference.asset_id,
        assetGroup: reference.asset_group,
        assetName: reference.name,
        sourceImageUrl: reference.source_image_url,
      },
    );
    const updatedPackages = {
      ...storyboardMessage.artifact.videoScenePackages,
      global_assets: updated.global_assets,
      scene_packages: updated.scene_packages as typeof storyboardMessage.artifact.videoScenePackages.scene_packages,
    };

    setReferencedMaterials((items) => items.filter((item) => item.asset_id !== reference.asset_id));
    updateVideoScenePackageArtifactInMessage(storyboardMessage.id, () => updatedPackages);
    if (isVisibleConversation(targetConversationId)) {
      setSelectedStoryboardMessageId(storyboardMessage.id);
      setCanvasOpen(true);
    }
    pushAssistant(`已在当前场景包中删除「${reference.name}」的素材引用，并保留空占位符。`, targetConversationId);

    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "scene_global_asset_deleted",
          context: {
            ...makeSnapshot(),
            global_assets: updatedPackages.global_assets,
            scene_packages: updatedPackages.scene_packages,
            scene_global_asset_deleted: {
              asset_id: reference.asset_id,
              asset_group: reference.asset_group,
              name: reference.name,
            },
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
    return true;
  };

  const showImageEditOptions = async (request: PendingImageEditRequest): Promise<void> => {
    const targetConversationId = request.conversationId;
    const flowMaterials = request.materials || [];
    if (!hasImageMaterial(flowMaterials)) {
      pendingImageEditRequestRef.current = request;
      pushAssistant("我识别到这是图片编辑需求，请上传需要编辑的图片后提交，我会先让你确认图片编辑模型和参数。", targetConversationId);
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
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
      pushAssistant(`图片模型配置读取失败，已使用默认模型 gpt-image-2。${err instanceof Error ? err.message : String(err)}`, targetConversationId);
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
      void api
        .updateConversation(targetConversationId, {
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
        void api
          .updateConversation(targetConversationId, {
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
    };
    recordImageEditConfirmedSelection(msg.id, targetConversationId, selection);
    pendingImageEditRequestRef.current = null;
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
    options: { autoConfirm?: boolean } = {},
  ) => {
    const message = pushArtifact(`已根据表单生成 3 个创意方向，请选择一个进入 plan.md 策划。${AUTO_CONFIRM_TIMEOUT_SECONDS} 秒未选择将采用推荐方向。`, {
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
    const recommended = directions.find((direction) => direction.recommended) || directions[0];
    if ((options.autoConfirm ?? true) && recommended) {
      window.setTimeout(() => {
        if (!shouldAutoSelectDirection(message, targetConversationId)) return;
        void handleSelectDirection(message, recommended, true);
      }, AUTO_CONFIRM_TIMEOUT_MS);
    }
    return message;
  };

  const pushPlanArtifact = (
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
  ) => {
    pushArtifact("plan.md 创作方案已生成，请审核后点击「同意方案」继续。", {
      type: "plan",
      title: "plan.md 创作方案",
      description: `基于「${selectedDirection.title}」生成，模板来自项目内 plan.md`,
      actionLabel: "审核",
      plan,
      selectedDirection,
      intent: context.intent,
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
    }, targetConversationId);
  };

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

  const persistPendingImageJob = async (
    pendingImageJob: PendingImageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingImageJobRef.current = pendingImageJob;
    if (!targetConversationId) return;
    const baseSnapshot = makeSnapshot(targetConversationId);
    await api.updateConversation(targetConversationId, {
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
    await api.updateConversation(targetConversationId, {
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

  const persistPendingScenePackageJob = async (
    pendingScenePackageJob: PendingScenePackageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingScenePackageJobRef.current = pendingScenePackageJob;
    if (!targetConversationId) return;
    const baseSnapshot = makeSnapshot(targetConversationId);
    await api.updateConversation(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...baseSnapshot,
        ...extraContext,
        pendingScenePackageJob,
        pending_scene_package_job: pendingScenePackageJob,
      } as unknown as Record<string, unknown>,
    });
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
    await api.updateConversation(targetConversationId, {
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
    await api.updateConversation(targetConversationId, {
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
    if (!hasDirectionsArtifactForDraft(messagesRef.current, targetConversationId, draft)) {
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
  });

  const handleCompletedScenePackageJob = async (
    pendingScenePackageJob: PendingScenePackageJob,
    result: PrepareScenePackagesJobResult,
    processedKey: string,
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

    if (!videoScenePackages.ok || quotaPaused) releaseArtifactAction(processedKey);
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
    }, targetConversationId);
    if (canAcceptImageResult(imageResult)) {
      window.setTimeout(() => {
        void handleAcceptImageResult(imageResultMessage, true);
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
    editResult: ImageAssetEditResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingImageJob.conversation_id;
    const reference = pendingImageJob.sceneGlobalAssetReference;
    const storyboardMessage = reference ? findStoryboardMessageForGlobalAsset(reference, targetConversationId) : undefined;
    if (!reference || !storyboardMessage?.artifact?.videoScenePackages) {
      releaseArtifactAction(processedKey);
      pushAssistant("素材图片编辑任务完成，但没有找到对应的场景包卡片，请从当前场景包手动重试。", targetConversationId);
      await clearPendingImageJob(targetConversationId, "scene_global_asset_edit_failed", {
        scene_global_asset_edit_error: "缺少对应的场景包 artifact",
      }).catch(() => {});
      return;
    }
    const nextUrl = editedImageUrl(editResult);
    const quotaInsufficient = isQuotaInsufficientPayload(editResult);
    if (!editResult.ok || !nextUrl) {
      releaseArtifactAction(processedKey);
      pushArtifact("全局素材图片编辑失败，请查看错误信息。", {
        type: "image_result",
        title: "全局素材图片编辑结果",
        description: quotaInsufficient ? quotaMessage(editResult.message || "图片编辑额度不足。") : editResult.message,
        actionLabel: "查看",
        imageResult: {
          ok: false,
          method: "image_edit",
          endpoint: editResult.endpoint,
          task_id: null,
          images: nextUrl ? [editResult.edited_image] : [],
          error: editResult.message,
          message: editResult.message,
          quota_insufficient: quotaInsufficient,
          raw: editResult.raw,
        },
        intent: "image",
        materials: [reference],
        imageRevisionFeedback: String((pendingImageJob.request as ImageAssetEditJobRequest).prompt || ""),
      }, targetConversationId);
      await clearPendingImageJob(targetConversationId, quotaInsufficient ? "scene_global_asset_edit_quota_paused" : "scene_global_asset_edit_failed", {
        scene_global_asset_edit: editResult,
      }).catch(() => {});
      return;
    }

    const updatedPackages = {
      ...storyboardMessage.artifact.videoScenePackages,
      global_assets: replaceGlobalSceneAssetImage(storyboardMessage.artifact.videoScenePackages.global_assets, {
        assetId: reference.asset_id,
        assetGroup: reference.asset_group,
        editedImageUrl: nextUrl,
      }),
      scene_packages: syncScenePackageMentionImageUrls(storyboardMessage.artifact.videoScenePackages.scene_packages as ScenePackageRecord[], {
        assetId: reference.asset_id,
        editedImageUrl: nextUrl,
      }) as typeof storyboardMessage.artifact.videoScenePackages.scene_packages,
    };
    updateVideoScenePackageArtifactInMessage(storyboardMessage.id, () => updatedPackages);

    const updatedScenePackageMessageId = uid();
    pushArtifact("全局素材图片已编辑完成，并已替换到当前场景包中。", {
      type: "image_result",
      title: "全局素材图片编辑结果",
      description: `已更新「${reference.name}」，后续生成场景视频会使用新图。`,
      actionLabel: "查看",
      imageResult: {
        ok: true,
        method: "image_edit",
        endpoint: editResult.endpoint,
        task_id: null,
        images: [editResult.edited_image],
        error: null,
        message: editResult.message,
        quota_insufficient: false,
        raw: editResult.raw,
      },
      intent: "image",
      materials: [{ ...reference, url: nextUrl, source_image_url: nextUrl, storyboard_message_id: updatedScenePackageMessageId }],
      imageRevisionFeedback: String((pendingImageJob.request as ImageAssetEditJobRequest).prompt || ""),
    }, targetConversationId);
    const updatedScenePackageMessage = pushArtifact("已把编辑后的图片同步回视频场景包，请继续确认或生成分镜视频。", {
      type: "video_scene_packages",
      title: "视频场景包",
      description: `已更新「${reference.name}」，后续生成场景视频会使用新图。`,
      actionLabel: "确认",
      videoScenePackages: updatedPackages,
      originalVideoScenePackages: storyboardMessage.artifact.originalVideoScenePackages || storyboardMessage.artifact.videoScenePackages,
      sceneAssetFailures: storyboardMessage.artifact.sceneAssetFailures || [],
      intent: "video",
      formValues: storyboardMessage.artifact.formValues,
      intakeContext: storyboardMessage.artifact.intakeContext,
      materials: storyboardMessage.artifact.materials || [],
      selectedDirection: storyboardMessage.artifact.selectedDirection,
      plan: storyboardMessage.artifact.plan,
    }, targetConversationId, updatedScenePackageMessageId);
    if (isVisibleConversation(targetConversationId)) {
      setSelectedStoryboardMessageId(updatedScenePackageMessage.id);
      setCanvasOpen(true);
    }

    await clearPendingImageJob(targetConversationId, "scene_global_asset_edited", {
      global_assets: updatedPackages.global_assets,
      scene_packages: updatedPackages.scene_packages,
      scene_global_asset_edit: editResult,
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
    if (!sceneAssets.ok || quotaPaused) releaseArtifactAction(processedKey);
    pushArtifact(sceneAssets.ok ? "场景参考图已继续生成完成，请确认后生成视频。" : "场景参考图继续生成失败，请查看失败项。", {
      type: "video_scene_packages",
      title: "视频场景包",
      description: quotaPaused
        ? quotaMessage(sceneAssets.message || "场景参考图生成额度不足。")
        : `${nextPackages.scene_packages.length} 个场景片段，生成视频前必须确认。`,
      actionLabel: quotaPaused ? "继续" : "确认",
      videoScenePackages: nextPackages,
      originalVideoScenePackages: artifact.originalVideoScenePackages || videoScenePackages,
      sceneAssetFailures: sceneAssets.failed_assets,
      intent: "video",
      formValues: artifact.formValues,
      intakeContext: artifact.intakeContext,
      materials: artifact.materials || [],
      selectedDirection: artifact.selectedDirection,
      plan: artifact.plan,
    }, targetConversationId, scenePackageJobMessageId(pendingScenePackageJob));

    await clearPendingScenePackageJob(
      targetConversationId,
      sceneAssets.ok ? "scene_package_ready" : quotaPaused ? "scene_asset_quota_paused" : "scene_asset_failed",
      scenePackageContext(artifact, nextPackages, sceneAssets.failed_assets),
    ).catch(() => {});
  };

  const sceneVideoRequestFromPackages = (
    videoScenePackages: PrepareScenePackagesResponse,
    sceneIds?: Set<string>,
    editedSceneIds: Set<string> = sceneIds || new Set<string>(),
  ): SceneVideosJobRequest => ({
    scenes: videoScenePackages.scene_packages
      .filter((scene) => !sceneIds || sceneIds.has(scene.scene_id))
      .map((scene) =>
        sceneGenerationPayloadFromPackage(scene, videoScenePackages.global_assets, {
          edited: editedSceneIds.has(scene.scene_id),
        }) as SceneGenerationPayload,
      ),
    ratio: "9:16",
    size: "720p",
    sound: "on",
  });

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
    const videoResultMessage = pushArtifact(
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
    if (mergedVideo.ok) {
      window.setTimeout(() => {
        void handleAcceptVideoResult(videoResultMessage, true);
      }, AUTO_CONFIRM_TIMEOUT_MS);
    }
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
        video_revision_use_flaw_analysis: pendingVideoJob.use_flaw_analysis,
        affected_scene_ids: pendingVideoJob.affected_scene_ids || [],
        global_assets: videoScenePackages.global_assets,
        intake_context: artifact.intakeContext,
        scene_packages: videoScenePackages.scene_packages,
        generated_scene_videos: generatedSceneVideos.scene_videos,
        merged_video: mergedVideo,
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
        video_revision_use_flaw_analysis: pendingVideoJob.use_flaw_analysis,
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
    try {
      if (stopIfHidden()) return;
      if (pendingScenePackageJob.kind === "scene_asset_generation") {
        const status = await api.getSceneAssetsJob(pendingScenePackageJob.job_id);
        if (stopIfHidden()) return;
        const sceneAssets =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollSceneAssetsJob(pendingScenePackageJob.job_id, shouldContinuePolling);
        if (!sceneAssets || stopIfHidden()) return;
        await handleCompletedSceneAssetJob(pendingScenePackageJob, sceneAssets, processedKey);
      } else {
        const status = await api.getPrepareScenePackagesJob(pendingScenePackageJob.job_id);
        if (stopIfHidden()) return;
        const result =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollPrepareScenePackagesJob(pendingScenePackageJob.job_id, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedScenePackageJob(pendingScenePackageJob, result, processedKey);
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的场景包或参考图生成任务不存在或已过期。为避免重复生成，我没有自动重启，请从最新 plan 或场景包卡片手动重试。"
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

  const applySnapshot = (snapshot: Partial<WorkspaceSnapshot>) => {
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
    pendingDirectionJobRef.current = snapshot.pendingDirectionJob || snapshot.pending_direction_job || null;
    pendingImageEditRequestRef.current = snapshot.pendingImageEditRequest || null;
    imageEditConfirmedSelectionsRef.current = snapshot.imageEditConfirmedSelections || {};
    pendingImageJobRef.current = snapshot.pendingImageJob || snapshot.pending_image_job || null;
    pendingScenePackageJobRef.current = snapshot.pendingScenePackageJob || snapshot.pending_scene_package_job || null;
    pendingVideoJobRef.current = snapshot.pendingVideoJob || snapshot.pending_video_job || null;
    pendingPptJobRef.current = snapshot.pendingPptJob || snapshot.pending_ppt_job || null;
    setPptDoneForConversation(conversationIdRef.current, snapshot.ppt_done === true);
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
    return {
      taskId: currentTaskId,
      pendingMaterials,
      flowDraft: flowDraftRef.current,
      pendingDirectionJob:
        pendingDirectionJobRef.current?.conversation_id === snapshotConversationId ? pendingDirectionJobRef.current : null,
      pending_direction_job:
        pendingDirectionJobRef.current?.conversation_id === snapshotConversationId ? pendingDirectionJobRef.current : null,
      pendingImageEditRequest:
        pendingImageEditRequestRef.current?.conversationId === snapshotConversationId ? pendingImageEditRequestRef.current : null,
      imageEditConfirmedSelections: imageEditConfirmedSelectionsRef.current,
      pendingImageJob:
        pendingImageJobRef.current?.conversation_id === snapshotConversationId ? pendingImageJobRef.current : null,
      pending_image_job:
        pendingImageJobRef.current?.conversation_id === snapshotConversationId ? pendingImageJobRef.current : null,
      pendingScenePackageJob:
        pendingScenePackageJobRef.current?.conversation_id === snapshotConversationId ? pendingScenePackageJobRef.current : null,
      pending_scene_package_job:
        pendingScenePackageJobRef.current?.conversation_id === snapshotConversationId ? pendingScenePackageJobRef.current : null,
      pendingVideoJob:
        pendingVideoJobRef.current?.conversation_id === snapshotConversationId ? pendingVideoJobRef.current : null,
      pending_video_job:
        pendingVideoJobRef.current?.conversation_id === snapshotConversationId ? pendingVideoJobRef.current : null,
      pendingPptJob:
        pendingPptJobRef.current?.conversation_id === snapshotConversationId ? pendingPptJobRef.current : null,
      pending_ppt_job:
        pendingPptJobRef.current?.conversation_id === snapshotConversationId ? pendingPptJobRef.current : null,
      ppt_done: isPptDoneForConversation(snapshotConversationId),
      canvas,
      canvasOpen,
      briefConfirmed,
      lastEventId: lastEventIdRef.current,
      announcedPhases: Array.from(announcedPhasesRef.current),
      briefReadyShown: briefReadyShownRef.current,
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
    setBriefConfirmed(false);
    briefConfirmedRef.current = false;
    seenEventIdsRef.current = new Set();
    announcedPhasesRef.current = new Set();
    processedArtifactIdsRef.current = new Set();
    pendingDialogContextRef.current = null;
    flowDraftRef.current = null;
    pendingDirectionJobRef.current = null;
    pendingImageEditRequestRef.current = null;
    imageEditConfirmedSelectionsRef.current = {};
    pendingImageJobRef.current = null;
    pendingScenePackageJobRef.current = null;
    pendingVideoJobRef.current = null;
    pendingPptJobRef.current = null;
    pptDoneConversationIdsRef.current = new Set();
    planRevisionArtifactRef.current = null;
    imageRevisionArtifactRef.current = null;
    videoRevisionArtifactRef.current = null;
    briefReadyShownRef.current = false;
    lastEventIdRef.current = 0;
  };

  const applyConversation = async (detail: ConversationDetailResponse) => {
    const snapshot = (detail.conversation.context || {}) as Partial<WorkspaceSnapshot>;
    const pendingImageEditRequest =
      snapshot.pendingImageEditRequest || ((detail.conversation.context || {}) as Record<string, unknown>).pending_image_edit_request || null;
    const imageEditConfirmedSelections = snapshot.imageEditConfirmedSelections || {};
    const flowDraft = snapshot.flowDraft || null;
    const pendingDirectionJob = snapshot.pendingDirectionJob || snapshot.pending_direction_job || null;
    const pendingImageJob = snapshot.pendingImageJob || snapshot.pending_image_job || null;
    const pendingScenePackageJob = snapshot.pendingScenePackageJob || snapshot.pending_scene_package_job || null;
    const pendingPptJob = snapshot.pendingPptJob || snapshot.pending_ppt_job || null;
    const restoredMessages = detail.messages
      .map((message) => messageFromResponse(message, detail.conversation.conversation_id))
      .filter((m): m is ChatMessage => Boolean(m));
    const restoredMessagesWithImageEditSelections = applyImageEditConfirmedSelectionsToMessages(restoredMessages, imageEditConfirmedSelections);
    const contextMessages = restoreLatestVideoScenePackagesFromContext(
      restoredConversationMessages(undefined, restoredMessagesWithImageEditSelections),
      snapshot as Partial<Record<string, unknown>>,
    );
    const pptAwareMessages = markLatestPptFileDoneFromContext(contextMessages, snapshot as Partial<Record<string, unknown>>);
    const normalizedMessages = normalizeRestoredMessageReferences(dedupeRestoredScenePackageMessages(pptAwareMessages));
    applySnapshot({
      ...snapshot,
      pendingScenePackageJob: pendingScenePackageJob && hasMaterializedScenePackageJob(normalizedMessages, pendingScenePackageJob) ? null : pendingScenePackageJob,
      pending_scene_package_job: pendingScenePackageJob && hasMaterializedScenePackageJob(normalizedMessages, pendingScenePackageJob) ? null : pendingScenePackageJob,
      flowDraft,
      pendingDirectionJob,
      pending_direction_job: pendingDirectionJob,
      pendingImageEditRequest: pendingImageEditRequest as PendingImageEditRequest | null,
      pendingImageJob,
      pending_image_job: pendingImageJob,
      pendingPptJob,
      pending_ppt_job: pendingPptJob,
      imageEditConfirmedSelections,
      messages: normalizedMessages,
    });
    if (pendingDirectionJob?.job_id && pendingDirectionJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingDirectionJob(pendingDirectionJob);
      }, 0);
    } else if (
      flowDraft?.stage === "directions_ready" &&
      flowDraft.creativeDirections?.length &&
      isCreationIntent(flowDraft.intent) &&
      !hasPostDirectionArtifactForDirections(normalizedMessages, detail.conversation.conversation_id, flowDraft.creativeDirections) &&
      !hasDirectionsArtifactForDraft(normalizedMessages, detail.conversation.conversation_id, flowDraft)
    ) {
      pushDirectionsArtifact(flowDraft.creativeDirections, {
        intent: flowDraft.intent,
        formValues: flowDraft.formValues || (flowDraft.form ? valuesFromForm(flowDraft.form) : {}),
        materials: flowDraft.materials || [],
        coreMessage: flowDraft.coreMessage || "",
        intakeContext: flowDraft.intakeContext,
      }, detail.conversation.conversation_id, { autoConfirm: false });
    } else if (
      flowDraft?.stage === "form_pending" &&
      !hasPassedRequirementCollection(
        normalizedMessages,
        detail.conversation.conversation_id,
        snapshot as Partial<WorkspaceSnapshot> & Record<string, unknown>,
        detail.conversation.last_phase,
      )
    ) {
      window.setTimeout(() => {
        restoreFormDraft(flowDraft, detail.conversation.conversation_id);
      }, 0);
    }
    if (pendingImageJob?.job_id && pendingImageJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingImageJob(pendingImageJob);
      }, 0);
    }
    if (
      pendingScenePackageJob?.job_id &&
      pendingScenePackageJob.conversation_id === detail.conversation.conversation_id &&
      !hasMaterializedScenePackageJob(normalizedMessages, pendingScenePackageJob)
    ) {
      window.setTimeout(() => {
        void resumePendingScenePackageJob(pendingScenePackageJob);
      }, 0);
    } else if (pendingScenePackageJob?.job_id && pendingScenePackageJob.conversation_id === detail.conversation.conversation_id) {
      const scenePackageMessage = normalizedMessages
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
    const taskId = snapshot.taskId || detail.conversation.current_task_id || "";
    if (taskId) {
      setActiveTaskId(taskId);
      unsubRef.current = subscribeTaskEvents(taskId, onEvent, snapshot.lastEventId || undefined);
      await reconcileTaskFromServer(taskId);
    }
  };

  const resumeVisiblePendingJobs = () => {
    const activeConversationId = conversationIdRef.current;
    if (!activeConversationId || !pageVisibleRef.current) return;
    const pendingDirectionJob = pendingDirectionJobRef.current;
    if (pendingDirectionJob?.job_id && pendingDirectionJob.conversation_id === activeConversationId) {
      void resumePendingDirectionJob(pendingDirectionJob);
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
  };

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
        return;
      }
      if (skipRouteRestoreConversationRef.current === conversationId) {
        skipRouteRestoreConversationRef.current = "";
        restoringRef.current = false;
        return;
      }
      setDialogOpen(false);
      setPendingCore("");
      setPendingFormValues({});
      setPendingMaterials([]);
      pendingDialogContextRef.current = null;
      unsubRef.current();
      seenEventIdsRef.current = new Set();
      announcedPhasesRef.current = new Set();
      briefReadyShownRef.current = false;
      lastEventIdRef.current = 0;
      setActiveConversationId(conversationId);
      setBusy(true);
      try {
        const detail = await api.resumeConversation(conversationId);
        if (cancelled) return;
        await applyConversation(detail);
      } catch (err) {
        if (!cancelled) {
          resetWorkspace();
          pushAssistant(`历史对话恢复失败:${err instanceof Error ? err.message : String(err)}`);
        }
      } finally {
        if (!cancelled) {
          restoringRef.current = false;
          setBusy(false);
        }
      }
    };
    void restoreConversation();
    return () => {
      cancelled = true;
      if (conversationIdRef.current === conversationId) {
        setActiveConversationId("");
      }
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    };
  }, [conversationId]);

  useEffect(() => {
    if (restoringRef.current || !currentConversationId) return;
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      const snapshot: WorkspaceSnapshot = {
        taskId: currentTaskId,
        pendingMaterials,
        flowDraft: flowDraftRef.current,
        pendingDirectionJob:
          pendingDirectionJobRef.current?.conversation_id === currentConversationId ? pendingDirectionJobRef.current : null,
        pending_direction_job:
          pendingDirectionJobRef.current?.conversation_id === currentConversationId ? pendingDirectionJobRef.current : null,
        pendingImageEditRequest:
          pendingImageEditRequestRef.current?.conversationId === currentConversationId ? pendingImageEditRequestRef.current : null,
        imageEditConfirmedSelections: imageEditConfirmedSelectionsRef.current,
        pendingImageJob:
          pendingImageJobRef.current?.conversation_id === currentConversationId ? pendingImageJobRef.current : null,
        pending_image_job:
          pendingImageJobRef.current?.conversation_id === currentConversationId ? pendingImageJobRef.current : null,
        pendingScenePackageJob:
          pendingScenePackageJobRef.current?.conversation_id === currentConversationId ? pendingScenePackageJobRef.current : null,
        pending_scene_package_job:
          pendingScenePackageJobRef.current?.conversation_id === currentConversationId ? pendingScenePackageJobRef.current : null,
        pendingVideoJob:
          pendingVideoJobRef.current?.conversation_id === currentConversationId ? pendingVideoJobRef.current : null,
        pending_video_job:
          pendingVideoJobRef.current?.conversation_id === currentConversationId ? pendingVideoJobRef.current : null,
        pendingPptJob:
          pendingPptJobRef.current?.conversation_id === currentConversationId ? pendingPptJobRef.current : null,
        pending_ppt_job:
          pendingPptJobRef.current?.conversation_id === currentConversationId ? pendingPptJobRef.current : null,
        ppt_done: isPptDoneForConversation(currentConversationId),
        canvas,
        canvasOpen,
        briefConfirmed,
        lastEventId: lastEventIdRef.current,
        announcedPhases: Array.from(announcedPhasesRef.current),
        briefReadyShown: briefReadyShownRef.current,
      };
      void api
        .updateConversation(currentConversationId, {
          current_task_id: currentTaskId || null,
          last_phase: String(canvas.phase || "idle"),
          context: snapshot as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }, 400);
  }, [pendingMaterials, canvas, canvasOpen, briefConfirmed, currentTaskId, currentConversationId]);

  const titleFromPrompt = (text: string) => {
    const normalized = text.trim() || "带附件对话";
    return normalized.length > 18 ? `${normalized.slice(0, 18)}...` : normalized;
  };

  const ensureConversation = async (title: string): Promise<string> => {
    if (conversationIdRef.current) return conversationIdRef.current;
    const created = await api.createConversation({
      title: titleFromPrompt(title),
      last_phase: String(canvas.phase || "idle"),
      current_task_id: currentTaskId || null,
      context: makeSnapshot() as unknown as Record<string, unknown>,
    });
    skipRouteRestoreConversationRef.current = created.conversation_id;
    setActiveConversationId(created.conversation_id);
    window.dispatchEvent(new Event("pixelflow-conversations-updated"));
    notifyContentAppConversationsUpdated(created.conversation_id);
    return created.conversation_id;
  };

  const normalizeSendInput = (input: string | AgentUserMessagePayload): AgentUserMessagePayload => {
    if (typeof input === "string") return { content: input, materials: [] };
    return { content: input.content, materials: Array.isArray(input.materials) ? input.materials : [] };
  };

  const handleSend = async (input: string | AgentUserMessagePayload) => {
    const { content: text, materials = [] } = normalizeSendInput(input);
    let activeConversation = conversationIdRef.current;
    const message: ChatMessage = { id: uid(), conversationId: activeConversation || undefined, role: "user", content: text, materials, time: "" };
    try {
      activeConversation = await ensureConversation(text);
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
        await handleEditReferencedGlobalAsset(sceneGlobalAssetReference, text, activeConversation);
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
      const revisionIntent = revisionArtifact.intent;
      const revisionFormValues = revisionArtifact.formValues;
      const flowMaterials = mergeMaterials(revisionArtifact.materials, materials);
      if (!isCreationIntent(revisionIntent) || !revisionFormValues) return;
      planRevisionArtifactRef.current = null;
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到修改意见，正在回到采集 Agent 重新生成 3 个创意方向…", activeConversation);
      try {
        await startDirectionJob(
          activeConversation,
          {
            intent: revisionIntent,
            values: revisionFormValues,
            materials: flowMaterials,
            product_creative_profile: { revision_feedback: text },
            intake_context: revisionArtifact.intakeContext,
          },
          {
            intent: revisionIntent,
            formValues: revisionFormValues,
            materials: flowMaterials,
            coreMessage: `${revisionArtifact.coreMessage || pendingCore}\n修改意见：${text}`,
            intakeContext: revisionArtifact.intakeContext,
            revisionFeedback: text,
          },
          "directions_running",
        );
      } catch (err) {
        pushAssistant(`重新生成创意方向失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
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
      await handleEditReferencedGlobalAsset(pendingSceneGlobalAssetReference, text, activeConversation);
      return;
    }
    if (pendingImageRevision?.conversationId === activeConversation && pendingImageRevisionArtifact?.imagePrepare && pendingImageRevisionArtifact.imageResult) {
      const flowMaterials = mergeMaterials(pendingImageRevisionArtifact.materials, materials);
      imageRevisionArtifactRef.current = null;
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
        });
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
      pushAssistant("已收到视频修改意见，正在调用视频综合质检 Skill…", activeConversation);
      try {
        const flawAnalysis = await api.analyzeVideoFlaws({
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
        const affectedSceneIds = new Set(flawAnalysis.affected_scene_ids || []);
        const affectedSceneLabel = formatSceneIndexesForMessage(videoScenePackages.scene_packages, affectedSceneIds);
        pushArtifact(flawAnalysis.ok ? "视频综合质检已完成，请选择本轮修改策略。" : "视频综合质检失败，可选择只按用户意见继续修改。", {
          type: "video_flaw_analysis",
          title: "视频综合质检",
          description: flawAnalysis.ok
            ? `质检定位：${affectedSceneLabel}。`
            : flawAnalysis.message,
          actionLabel: "选择",
          videoFlawAnalysis: flawAnalysis,
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
          void api
            .updateConversation(activeConversation, {
              last_phase: flawAnalysis.ok ? "video_flaw_analysis_ready" : "video_flaw_analysis_failed",
              context: {
                ...makeSnapshot(),
                video_revision_feedback: text,
                intake_context: revisionArtifact.intakeContext,
                materials: flowMaterials,
                video_flaw_analysis: flawAnalysis,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`视频综合质检失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    setBusyForConversation(activeConversation, true);
    pushAssistant("正在调用采集 Agent 识别意图，并抽取可自动填充的表单字段…", activeConversation);
    try {
      const intake = await api.analyzeIntakeIntent({ prompt: text, materials });
      if (intake.intent === "video_analysis") {
        pushAssistant("已识别为视频分析/拆解需求，正在识别媒体链接并调用视频分析 Skill…", activeConversation);
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
        }, activeConversation);
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed",
              context: {
                ...makeSnapshot(),
                intent: "video_analysis",
                materials,
                intake_intent: intake,
                video_analysis: videoAnalysis,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        return;
      }
      if (intake.intent === "ppt") {
        const flowDraft = makeFlowDraft("form_pending", {
          intent: "ppt",
          coreMessage: text,
          materials,
          intakeIntent: intake,
          intakeContext: intake.intake_context,
          formValues: initialValuesFromIntake(intake),
        });
        flowDraftRef.current = flowDraft;
        if (isVisibleConversation(activeConversation)) {
          setPendingCore(text);
          setPendingIntent("ppt");
          setPendingFormValues(initialValuesFromIntake(intake));
          setPendingMaterials(materials);
          pendingDialogContextRef.current = {
            conversationId: activeConversation,
            coreMessage: text,
            materials,
            intakeContext: intake.intake_context,
          };
        }
        pushAssistant("采集 Agent 判断这是PPT制作需求，已把能识别的信息自动填进表单。请补充确认并上传 Word、Excel 或 PDF 附件。", activeConversation);
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: "ppt_form_pending",
            context: {
              ...makeSnapshot(),
              flowDraft,
              intent: "ppt",
              materials,
                intake_intent: intake,
                intake_context: intake.intake_context,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        if (isVisibleConversation(activeConversation)) setDialogOpen(true);
        return;
      }
      if (intake.intent === "image" && isImageEditIntake(intake, text)) {
        const imageEditRequest: PendingImageEditRequest = {
          conversationId: activeConversation,
          prompt: text,
          formValues: intake.values || {},
          intakeContext: intake.intake_context || {},
          materials,
        };
        if (!hasImageMaterial(materials)) {
          pendingImageEditRequestRef.current = imageEditRequest;
          pushAssistant("我识别到这是图片编辑需求，请上传需要编辑的图片后提交，我会先让你确认图片编辑模型和参数。", activeConversation);
          if (activeConversation) {
            void api
              .updateConversation(activeConversation, {
                last_phase: "image_edit_waiting_source_image",
                context: {
                  ...makeSnapshot(),
                  intent: "image",
                  materials,
                  intake_intent: intake,
                  intake_context: intake.intake_context,
                  pendingImageEditRequest: imageEditRequest,
                  pending_image_edit_request: imageEditRequest,
                } as unknown as Record<string, unknown>,
              })
              .catch(() => {});
          }
          return;
        }
        await showImageEditOptions(imageEditRequest);
        return;
      }
      if (isCreationIntent(intake.intent)) {
        const flowDraft = makeFlowDraft("form_pending", {
          intent: intake.intent,
          coreMessage: text,
          materials,
          intakeIntent: intake,
          intakeContext: intake.intake_context,
          formValues: initialValuesFromIntake(intake),
        });
        flowDraftRef.current = flowDraft;
        if (isVisibleConversation(activeConversation)) {
          setPendingCore(text);
          setPendingIntent(intake.intent);
          setPendingFormValues(initialValuesFromIntake(intake));
          setPendingMaterials(materials);
          pendingDialogContextRef.current = {
            conversationId: activeConversation,
            coreMessage: text,
            materials,
            intakeContext: intake.intake_context,
          };
        }
        pushAssistant(`采集 Agent 判断这是${intake.intent === "video" ? "视频生成" : "图片生成"}需求，已把能识别的信息自动填进表单。请补充确认。`, activeConversation);
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: "intake_form_pending",
            context: {
              ...makeSnapshot(),
              flowDraft,
              intent: intake.intent,
              materials,
                intake_intent: intake,
                intake_context: intake.intake_context,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        if (isVisibleConversation(activeConversation)) setDialogOpen(true);
        return;
      }
      pushAssistant(intake.reason || "我可以帮你生成图片、生成电商带货短视频，或分析已有视频。请再描述一下需求。", activeConversation);
      if (activeConversation) {
        void api
          .updateConversation(activeConversation, {
            last_phase: "intake_unknown",
            context: {
              ...makeSnapshot(),
              intake_intent: intake,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`采集 Agent 意图识别失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
    } finally {
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
        void api
          .updateConversation(targetConversationId, {
            last_phase: videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed",
            context: {
              ...makeSnapshot(),
              intent: "video_analysis",
              materials,
              video_analysis: videoAnalysis,
            } as unknown as Record<string, unknown>,
          })
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
      void api
        .updateConversation(targetConversationId, {
          last_phase: "form_cancelled",
          context: {
            ...makeSnapshot(),
            flowDraft,
            intent: cancelledIntent,
            form_cancelled: true,
          } as unknown as Record<string, unknown>,
        })
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
    void api
      .updateConversation(targetConversationId, {
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

  const handleSelectDirection = async (msg: ChatMessage, direction: CreativeDirectionResponse, auto = false) => {
    if (!isCreationIntent(msg.artifact?.intent) || !msg.artifact?.formValues) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (auto && !shouldAutoSelectDirection(msg, targetConversationId)) return;
    if (hasLaterDirectionSuccessor(messagesRef.current, targetConversationId, msg)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(auto ? `${AUTO_CONFIRM_TIMEOUT_SECONDS} 秒未选择，已默认采用推荐方向「${direction.title}」，正在生成 plan.md…` : `已选择创意方向「${direction.title}」，正在生成 plan.md…`, targetConversationId);
    try {
      const plan = await api.createPlanMarkdown({
        intent: msg.artifact.intent,
        form_values: msg.artifact.formValues,
        selected_direction: direction as unknown as Record<string, unknown>,
        product_creative_profile: { core_message: msg.artifact.coreMessage || pendingCore },
        intake_context: msg.artifact.intakeContext,
        materials: msg.artifact.materials || [],
      });
      pushPlanArtifact(plan, direction, {
        intent: msg.artifact.intent,
        formValues: msg.artifact.formValues,
        materials: msg.artifact.materials || [],
        coreMessage: msg.artifact.coreMessage || pendingCore,
        intakeContext: msg.artifact.intakeContext,
      }, targetConversationId);
      flowDraftRef.current = null;
      pendingDirectionJobRef.current = null;
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: "plan_review",
            context: {
              ...makeSnapshot(),
              flowDraft: null,
              pendingDirectionJob: null,
              pending_direction_job: null,
              intent: msg.artifact.intent,
              form_values: msg.artifact.formValues,
              intake_context: msg.artifact.intakeContext,
              materials: msg.artifact.materials || [],
              selected_direction: direction,
              plan_markdown: plan.plan_markdown,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleApprovePlan = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.plan || !artifact.intent || !artifact.formValues || !artifact.selectedDirection) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    if (artifact.intent === "image") {
      setBusyForConversation(targetConversationId, true);
      pushAssistant("图片 plan.md 已同意，正在准备图片生成参数…", targetConversationId);
      try {
        const imagePrepare = await api.prepareImageGeneration({
          form_values: artifact.formValues,
          plan_markdown: artifact.plan.plan_markdown,
          selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
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
            void api
              .updateConversation(targetConversationId, {
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
    const formValues = artifact.formValues;
    const selectedDirection = artifact.selectedDirection;
    const targetDurationMs = inferTargetDurationMs([
      artifact.coreMessage,
      artifact.plan.plan_markdown,
      selectedDirection.title,
      selectedDirection.description,
    ]);
    pushAssistant("视频 plan.md 已同意，正在准备可编辑场景包…", targetConversationId);
    try {
      const request: PrepareScenePackagesJobRequest = {
        form_values: formValues,
        plan_markdown: artifact.plan.plan_markdown,
        selected_direction: selectedDirection as unknown as Record<string, unknown>,
        materials: artifact.materials || [],
        target_duration_ms: targetDurationMs,
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
      await persistPendingScenePackageJob(pendingScenePackageJob, targetConversationId, "scene_package_generation_running", {
        form_values: formValues,
        intake_context: artifact.intakeContext,
        materials: artifact.materials || [],
        selected_direction: selectedDirection,
        plan_markdown: artifact.plan.plan_markdown,
        plan_approved: true,
      });
      await resumePendingScenePackageJob(pendingScenePackageJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频场景包准备失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetrySceneAssets = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    const hasSceneAssetFailures = Boolean(artifact?.sceneAssetFailures?.length);
    if (!artifact || !videoScenePackages?.scene_packages.length || !hasSceneAssetFailures) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在继续生成场景参考图…", targetConversationId);
    try {
      const request: SceneAssetsJobRequest = {
        global_assets: videoScenePackages.global_assets,
        scene_packages: videoScenePackages.scene_packages,
        materials: artifact.materials || [],
        image_size: "1080p",
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
    planRevisionArtifactRef.current = msg.artifact ? { conversationId: targetConversationId, artifact: msg.artifact } : null;
    pushAssistant("已暂停当前 plan.md。请在输入框填写修改意见，我会回到采集 Agent 重新生成创意方向。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "plan_revision_requested",
          context: { ...makeSnapshot(), plan_approved: false, plan_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
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
    const imagePrepare = msg.artifact?.imagePrepare;
    if (!imagePrepare || !msg.artifact?.imageResult || canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    if (imagePrepare.method === "image_edit" && msg.artifact) {
      const imageEditRequest = imageEditRequestFromArtifact(msg.artifact, targetConversationId);
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
      const artifact: ChatArtifact = msg.artifact;
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
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    pushAssistant(auto ? `${AUTO_CONFIRM_TIMEOUT_SECONDS} 秒未收到图片修改意见，已默认满意并结束流程。` : "已确认图片满意，流程结束。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "image_accepted",
          context: { ...makeSnapshot(), image_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseImageResult(msg: ChatMessage) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    imageRevisionArtifactRef.current = { conversationId: targetConversationId, artifact: msg.artifact };
    const sceneGlobalAssetReference = sceneGlobalAssetReferenceFromMaterials(msg.artifact.materials || []);
    pushAssistant(
      sceneGlobalAssetReference
        ? `请在输入框填写「${sceneGlobalAssetReference.name}」的图片修改意见，我会继续编辑这张全局素材并替换回场景包。`
        : "请在输入框填写图片修改意见，我会基于当前 plan.md 和图片参数重新生成。",
      targetConversationId,
    );
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
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

  const handleGenerateVideoFromScenePackages = async (msg: ChatMessage) => {
    const latestMessage =
      messagesRef.current.find(
        (message) => message.id === msg.id && message.artifact?.videoScenePackages,
      ) || msg;
    const artifact = latestMessage.artifact;
    if (!artifact) return;
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages?.ok || videoScenePackages.scene_packages.length === 0) return;
    const targetConversationId = messageConversationId(latestMessage, conversationIdRef.current);
    const dirtySceneIds = new Set(artifact.videoScenePackageEditedSceneIds || []);
    const retrySceneIds = failedSceneIdsFromGeneratedSceneVideos(artifact.generatedSceneVideos);
    const isDirtySceneRegeneration = canReuseUneditedSceneVideos(videoScenePackages, artifact.generatedSceneVideos, dirtySceneIds);
    const hasGeneratedSceneVideos = Boolean(artifact.generatedSceneVideos?.scene_videos.length);
    const isFailedSceneRetry = Boolean(artifact.generatedSceneVideos && !artifact.generatedSceneVideos.ok && retrySceneIds.size > 0);
    if (hasGeneratedSceneVideos && dirtySceneIds.size === 0) {
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

  async function handleAcceptVideoResult(msg: ChatMessage, auto = false) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    pushAssistant(auto ? `${AUTO_CONFIRM_TIMEOUT_SECONDS} 秒未收到视频修改意见，已默认无意见并结束流程。` : "已确认视频无修改意见，流程结束。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "video_accepted",
          context: { ...makeSnapshot(), video_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseVideoResult(msg: ChatMessage) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    videoRevisionArtifactRef.current = {
      conversationId: targetConversationId,
      artifact: {
        ...msg.artifact,
        originalVideoScenePackages: msg.artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId),
      },
    };
    pushAssistant("请在输入框填写视频修改意见。我会先做综合质检，再让你选择是否结合质检结果重生成受影响场景。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "video_revision_requested",
          context: { ...makeSnapshot(), video_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  async function handleRegenerateVideoWithRevision(msg: ChatMessage, useFlawAnalysis: boolean) {
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
      artifact.videoFlawAnalysis,
      useFlawAnalysis,
    );
    if (affectedSceneIds.size === 0) {
      releaseArtifactAction(processedKey);
      pushAssistant("综合质检没有定位到具体分镜。为了避免误把整条视频重做，请在修改意见里明确写出要修改的分镜，例如“只修改第2个分镜”。", targetConversationId);
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
          useFlawAnalysis ? artifact.videoFlawAnalysis : undefined,
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
        use_flaw_analysis: useFlawAnalysis,
      };
      await persistPendingVideoJob(pendingVideoJob, targetConversationId, "video_regeneration_running", {
        video_revision_feedback: artifact.videoRevisionFeedback,
        video_revision_use_flaw_analysis: useFlawAnalysis,
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

  const handleOpenVideoResult = (_msg: ChatMessage, video: VideoResult, results: VideoResult[]) => {
    setSelectedStoryboardMessageId("");
    setCanvasOpen(true);
    setCanvas((current) => ({
      ...current,
      phase: "done",
      results: results.length > 0 ? results : current.results,
      selectedVideo: video,
    }));
  };

  const selectedStoryboardMessage = selectedStoryboardMessageId
    ? messages.find((message) => message.id === selectedStoryboardMessageId && message.artifact?.videoScenePackages)
    : undefined;

  return (
    <div className="flex h-full min-h-0">
      <ChatPanel
        messages={messages}
        onSubmit={handleSend}
        referencedMaterials={referencedMaterials}
        onRemoveReferencedMaterial={handleRemoveReferencedMaterial}
        composerPrefillRequest={composerPrefillRequest}
        busy={busy || dialogOpen}
        onSelectDirection={handleSelectDirection}
        onApprovePlan={handleApprovePlan}
        onRevisePlan={handleRevisePlan}
        onGenerateImage={handleGenerateImage}
        onConfirmImageEditOptions={handleConfirmImageEditOptions}
        onAcceptImageResult={handleAcceptImageResult}
        onReviseImageResult={handleReviseImageResult}
        onGenerateVideoFromScenePackages={handleGenerateVideoFromScenePackages}
        onAcceptVideoResult={handleAcceptVideoResult}
        onReviseVideoResult={handleReviseVideoResult}
        onOpenVideoResult={handleOpenVideoResult}
        onRegenerateVideoWithRevision={handleRegenerateVideoWithRevision}
        onRetryImageResult={handleRetryImageResult}
        onRetrySceneAssets={handleRetrySceneAssets}
        onRetryVideoMerge={handleRetryVideoMerge}
        onRetryVideoAnalysis={handleRetryVideoAnalysis}
        onApprovePptOutline={handleApprovePptOutline}
        onRevisePptOutline={handleRevisePptOutline}
        onRegeneratePptImage={handleRegeneratePptImage}
        onGeneratePptFile={handleGeneratePptFile}
        onAcceptPptFile={handleAcceptPptFile}
        onRegeneratePptFile={handleRegeneratePptFile}
        onOpenArtifact={(msg) => {
          if (!msg.artifact) return;
          setCanvasOpen(true);
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
      {canvasOpen && selectedStoryboardMessage?.artifact?.videoScenePackages ? (
        <StoryboardPanel
          msg={selectedStoryboardMessage}
          onUpdateVideoScenePackage={(sceneId, patch) => handleUpdateVideoScenePackage(selectedStoryboardMessage, sceneId, patch)}
          onReferenceGlobalAsset={handleReferenceGlobalAsset}
          onDeleteGlobalAsset={handleDeleteGlobalAsset}
          onGenerateVideo={() => handleGenerateVideoFromScenePackages(selectedStoryboardMessage)}
          onRetrySceneAssets={() => handleRetrySceneAssets(selectedStoryboardMessage)}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
          }}
        />
      ) : canvasOpen && (
        <CanvasPanel
          state={canvas}
          onApprove={handleApprove}
          onRevise={handleRevise}
          onConfirmStage={handleConfirmStage}
          onSelectVideo={(video) => setCanvas((current) => ({ ...current, selectedVideo: video }))}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
          }}
          briefConfirmed={briefConfirmed}
        />
      )}
      {dialogOpen && (
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
    </div>
  );
}
