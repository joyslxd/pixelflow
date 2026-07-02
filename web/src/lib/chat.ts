import type {
  AnalyzeStoryboardsResponse,
  CreationIntent,
  CreativeDirectionResponse,
  GenerateSceneVideosResponse,
  ImageEditModelSelection,
  ImageGenerateResponse,
  ImageModelParamConfig,
  ImagePrepareResponse,
  MergeSceneVideosResponse,
  PlanMarkdownResponse,
  PrepareScenePackagesResponse,
  PptContentJsonResult,
  PptFileResult,
  PptImagesResult,
  PptSummaryResult,
  VideoFlawAnalysisResponse,
} from "./api";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "./types";

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
      | "image_result"
      | "video_scene_packages"
      | "video_flaw_analysis"
      | "video_analysis_result"
      | "video_result"
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
    imagePrepare?: ImagePrepareResponse;
    imageResult?: ImageGenerateResponse;
    imageEditRequest?: Record<string, unknown>;
    imageEditModelConfigs?: ImageModelParamConfig[];
    imageEditRequestedParams?: Record<string, unknown>;
    imageEditConfirmedSelection?: ImageEditModelSelection;
    imageRevisionFeedback?: string;
    videoScenePackages?: PrepareScenePackagesResponse;
    sceneAssetFailures?: Array<Record<string, unknown>>;
    generatedSceneVideos?: GenerateSceneVideosResponse;
    mergedVideo?: MergeSceneVideosResponse;
    videoScenePackageEditedSceneIds?: string[];
    videoFlawAnalysis?: VideoFlawAnalysisResponse;
    videoAnalysis?: AnalyzeStoryboardsResponse;
    videoRevisionFeedback?: string;
    pptSummary?: PptSummaryResult;
    pptContentJson?: PptContentJsonResult;
    pptImages?: PptImagesResult;
    pptFile?: PptFileResult;
    pptStyle?: string;
    smartPptProjectId?: number | null;
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
  qcReport?: {
    passed?: boolean;
    score?: number;
    check_results?: Array<{ item?: string; status?: string; message?: string }>;
  };
  timeline?: FlowTimelineEntry[];
  estCost?: number;
  actualCost?: number;
}
