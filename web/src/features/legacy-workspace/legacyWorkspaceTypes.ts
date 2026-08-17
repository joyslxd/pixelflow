/**
 * LegacyWorkspace 模块级类型定义（从 LegacyWorkspace.tsx 提取，行为不变）。
 */
import type { GenParamsForm } from "@/components/composer/GenParamsDialog";
import type {
  CreativeDirectionResponse,
  ImageEditModelSelection,
  IntakeIntentResponse,
  JianyingDraftStartRequest,
  PlanManualEditRequest,
  PlanMarkdownResponse,
  PrepareScenePackagesResponse,
  ScenePackageAssetRevisionRequest,
  SceneGenerationPayload,
  SceneVideoPayload,
  VideoCreationContract,
} from "@/lib/api";
import type { ChatMessage, JianyingDraftRecordMap } from "@/lib/chat";
import type { SceneGlobalAssetReference } from "@/lib/scenePackages";
import type { FlowTimelineEntry, TaskPhase } from "@/lib/types";
import type {
  ExplicitActionSignal,
  JsonObject,
  JsonValue,
  OrchestrationMode,
} from "@/lib/supervisor/contracts";
import type { WorkspaceAgentRuntimeMode } from "@/lib/supervisor/legacyAdapter";
import type { ImagePrepareResponse } from "@/lib/api";
import type { ImageModelParamConfig } from "@/lib/api";
import type { WorkflowProgressSnapshot } from "@/lib/workflowTaskBoard";
import type { CreationIntent } from "@/components/composer/GenParamsDialog";

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
  conversation_id?: string;
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
  conversation_id?: string;
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
  reference_brief?: string;
  asset_reference_bindings?: Array<{
    asset_id?: string;
    asset_type?: string;
    reference_urls?: string[];
  }>;
  conversation_id?: string;
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
  conversation_id?: string;
}

interface MergeSceneVideosJobRequest {
  scene_videos: SceneVideoPayload[];
  duration?: number;
  size?: string;
  model?: string | null;
  conversation_id?: string;
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
