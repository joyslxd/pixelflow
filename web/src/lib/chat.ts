import type { TaskPhase, VideoResult } from "./types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  artifact?: {
    type: "brief" | "results" | "segments" | "edit" | "qc";
    title: string;
    description: string;
    actionLabel: string;
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
  estCost?: number;
  actualCost?: number;
}
