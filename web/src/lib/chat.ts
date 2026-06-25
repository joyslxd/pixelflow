import type {
  AnalyzeStoryboardsResponse,
  CreationIntent,
  CreativeDirectionResponse,
  GenerateSceneVideosResponse,
  ImageGenerateResponse,
  ImagePrepareResponse,
  MergeSceneVideosResponse,
  PlanMarkdownResponse,
  PrepareScenePackagesResponse,
  VideoFlawAnalysisResponse,
} from "./api";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "./types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
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
      | "image_result"
      | "video_scene_packages"
      | "video_flaw_analysis"
      | "video_analysis_result"
      | "video_result";
    title: string;
    description: string;
    actionLabel: string;
    directions?: CreativeDirectionResponse[];
    intent?: CreationIntent | "video_analysis";
    formValues?: Record<string, unknown>;
    coreMessage?: string;
    selectedDirection?: CreativeDirectionResponse;
    plan?: PlanMarkdownResponse;
    imagePrepare?: ImagePrepareResponse;
    imageResult?: ImageGenerateResponse;
    imageRevisionFeedback?: string;
    videoScenePackages?: PrepareScenePackagesResponse;
    sceneAssetFailures?: Array<Record<string, unknown>>;
    generatedSceneVideos?: GenerateSceneVideosResponse;
    mergedVideo?: MergeSceneVideosResponse;
    videoFlawAnalysis?: VideoFlawAnalysisResponse;
    videoAnalysis?: AnalyzeStoryboardsResponse;
    videoRevisionFeedback?: string;
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
  qcReport?: {
    passed?: boolean;
    score?: number;
    check_results?: Array<{ item?: string; status?: string; message?: string }>;
  };
  timeline?: FlowTimelineEntry[];
  estCost?: number;
  actualCost?: number;
}
