import type { TaskPhase, VideoResult } from "./types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
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
  estCost?: number;
  actualCost?: number;
}
