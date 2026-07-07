import type { TaskPhase, VideoResult } from "./types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  artifact?: {
    type: "brief" | "storyboard" | "results" | "segments" | "edit" | "qc";
    title: string;
    description: string;
    actionLabel: string;
    thumbnails?: string[];
  };
}

export interface BriefShot {
  shotId: string;
  timeRange: string;
  sceneType: string;
  durationSec: number;
  shotType?: string;
  cameraMovement?: string;
  visualDescription?: string;
  generationPrompt?: string;
  narration: string;
  onscreen: string;
  assetStrategy?: string;
  transitionIn?: string;
  transitionOut?: string;
  audio?: {
    bgmVibe?: string | null;
    sfx?: string | null;
    ttsVoice?: string | null;
  };
}

export interface Brief {
  title: string;
  platform: string;
  durationSec: number;
  ratio: string;
  size?: string;
  globalVisual?: {
    subjectType?: string;
    environment?: string;
    lighting?: string;
    characterStyle?: string;
    overallStyle?: string;
    forbiddenElements?: string;
  };
  shots: BriefShot[];
}

/** Canvas 当前要渲染的内容(随 agent 阶段切换)。 */
export interface CanvasState {
  phase: TaskPhase | "idle";
  brief?: Brief;
  productName?: string;
  productImageUrl?: string;
  results: VideoResult[];
  qcReport?: {
    passed?: boolean;
    score?: number;
    check_results?: Array<{ item?: string; status?: string; message?: string }>;
  };
  estCost?: number;
  actualCost?: number;
}
