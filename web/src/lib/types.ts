/** 与后端 /api/tasks 对齐的最小类型(按需扩展)。 */

export type TaskPhase =
  | "intake"
  | "creative"
  | "brief_review"
  | "generate"
  | "segment_review"
  | "edit"
  | "edit_review"
  | "qc"
  | "qc_review"
  | "done";

export interface GenParams {
  mode: string; // 视频生成 / 图片生成
  model: string; // seedance-2.0
  reference: string; // 全能参考
  ratio: string; // 9:16
  resolution: string; // 1080p
  durationSec: number; // 5
  count: number; // 1
  sound: boolean; // 输出声音
}

export interface VideoResult {
  id: string;
  url: string;
  assetType?: "generated_video" | "final_video" | string;
  thumbUrl?: string;
  durationSec?: number;
  status: "success" | "pending" | "failed";
}

export interface TaskSummary {
  taskId: string;
  title: string;
  resultCount: number;
  createdAt: string;
}
