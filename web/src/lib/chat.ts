import type {
  AnalyzeStoryboardsResponse,
  CreationIntent,
  CreativeDirectionResponse,
  GenerateSceneVideosResponse,
  ImageAssetEditResponse,
  ImageAssetFusionResponse,
  ImageEditModelSelection,
  ImageGenerateResponse,
  ImageModelParamConfig,
  ImagePrepareResponse,
  JianyingDraftJobResponse,
  JianyingDraftStartRequest,
  MergeSceneVideosResponse,
  PlanMarkdownResponse,
  PrepareScenePackagesResponse,
  PptContentJsonResult,
  PptFileResult,
  PptImagesResult,
  PptSummaryResult,
  VideoQualityReviewResponse,
} from "./api";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "./types";

export interface SceneGlobalAssetEditReview {
  asset_id: string;
  asset_group: "characters" | "scenes" | "props";
  asset_name?: string;
  original_image_url: string;
  source_image_url: string;
  edited_image_url: string;
  source_message_id: string;
  storyboard_message_id?: string;
  videoScenePackages: PrepareScenePackagesResponse;
  originalVideoScenePackages?: PrepareScenePackagesResponse;
  editResult?: ImageAssetEditResponse | ImageAssetFusionResponse;
  request?: Record<string, unknown>;
  selection?: ImageEditModelSelection;
  prompt?: string;
  is_fusion?: boolean;
}

export interface PendingJianyingDraftJobPayload {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  storyboard_version_id: string;
  started_at: string;
  request: JianyingDraftStartRequest;
}

export type JianyingDraftRecordMap = Record<string, JianyingDraftJobResponse>;

export interface ChatMessage {
  id: string;
  conversationId?: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  materials?: Array<Record<string, unknown>>;
  artifact?: {
    type:
      | "brief"
      | "results"
      | "segments"
      | "edit"
      | "qc"
      | "directions"
      | "plan"
      | "image_prepare"
      | "image_edit_options"
      | "scene_asset_model_options"
      | "image_result"
      | "video_scene_packages"
      | "video_quality_review"
      | "video_analysis_result"
      | "video_result"
      | "jianying_draft"
      | "ppt_outline"
      | "ppt_images"
      | "ppt_file";
    title: string;
    description: string;
    actionLabel: string;
    directions?: CreativeDirectionResponse[];
    intent?: CreationIntent | "video_analysis";
    formValues?: Record<string, unknown>;
    intakeContext?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
    coreMessage?: string;
    selectedDirection?: CreativeDirectionResponse;
    plan?: PlanMarkdownResponse;
    planVersion?: number;
    planHistory?: PlanMarkdownResponse["plan_history"];
    planJobId?: string;
    creationContract?: Record<string, unknown>;
    restoredFromVersion?: number | null;
    imagePrepare?: ImagePrepareResponse;
    imageResult?: ImageGenerateResponse;
    imageAccepted?: boolean;
    imageEditRequest?: Record<string, unknown>;
    imageEditModelConfigs?: ImageModelParamConfig[];
    imageEditRequestedParams?: Record<string, unknown>;
    imageEditConfirmedSelection?: ImageEditModelSelection;
    imageRevisionFeedback?: string;
    sceneGlobalAssetEditReview?: SceneGlobalAssetEditReview;
    videoScenePackages?: PrepareScenePackagesResponse;
    originalVideoScenePackages?: PrepareScenePackagesResponse;
    sceneAssetFailures?: Array<Record<string, unknown>>;
    /** 参考图仍在生成中：可查看结构，暂不可确认成片 */
    sceneAssetsGenerating?: boolean;
    /** 结构已就绪，等待选择生图模型 */
    sceneAssetsAwaitingModel?: boolean;
    /** 本批 early 进度卡已归档（结果见 media-result 卡），勿再显示「待生成」 */
    sceneAssetProgressArchived?: boolean;
    /** 资产包生图模型选择卡 */
    sceneAssetModelConfigs?: ImageModelParamConfig[];
    sceneAssetModelConfirmed?: boolean;
    sceneAssetReferenceMaterials?: Array<Record<string, unknown>>;
    sceneAssetReferenceBrief?: string;
    generatedSceneVideos?: GenerateSceneVideosResponse;
    /** 分镜视频仍在生成中：可预览已完成片段 */
    sceneVideosGenerating?: boolean;
    mergedVideo?: MergeSceneVideosResponse;
    jianyingDraft?: JianyingDraftJobResponse;
    pendingJianyingDraftJob?: PendingJianyingDraftJobPayload;
    jianyingDraftSceneCount?: number;
    videoScenePackageEditedSceneIds?: string[];
    videoQualityReview?: VideoQualityReviewResponse;
    videoAnalysis?: AnalyzeStoryboardsResponse;
    videoRevisionFeedback?: string;
    videoAccepted?: boolean;
    reviewRequestedAt?: string;
    reviewExpiresAt?: string;
    pptSummary?: PptSummaryResult;
    pptContentJson?: PptContentJsonResult;
    pptImages?: PptImagesResult;
    pptFile?: PptFileResult;
    pptDone?: boolean;
    deliveryDownloadedAt?: string;
    deliveryDownloadedUrl?: string;
    pptStyle?: string;
    smartPptProjectId?: number | null;
    /** Video Agent：脚本方案卡片，点「同意方案」=确认并生成资产包 */
    scriptPlanConfirmForAssets?: boolean;
    /** 已确认脚本方案，隐藏同意按钮 */
    scriptPlanConfirmed?: boolean;
  };
}

export interface BriefShot {
  shotId: string;
  timeRange: string;
  sceneType: string;
  durationSec: number;
  narration: string;
  onscreen: string;
}

export interface Brief {
  title: string;
  platform: string;
  durationSec: number;
  ratio: string;
  shots: BriefShot[];
}

/** Canvas 当前要渲染的内容(随 agent 阶段切换)。 */
export interface CanvasState {
  phase: TaskPhase | "idle";
  brief?: Brief;
  results: VideoResult[];
  selectedVideo?: VideoResult | null;
  selectedVideoSourceMessageId?: string;
  qcReport?: {
    passed?: boolean;
    score?: number;
    check_results?: Array<{ item?: string; status?: string; message?: string }>;
  };
  timeline?: FlowTimelineEntry[];
  estCost?: number;
  actualCost?: number;
}
