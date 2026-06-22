/** 与后端 /agent/flows 对齐的最小前端类型，后续按页面需要扩展。 */

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
  mode: string; // 生成模式，例如视频生成 / 图片生成。
  model: string; // 前端展示的模型名，例如 seedance-2.0。
  reference: string; // 参考素材模式，例如全能参考。
  ratio: string; // 画面比例，例如 9:16。
  resolution: string; // 清晰度，例如 1080p；提交时会转成后端 size。
  durationSec: number; // 目标时长，单位秒。
  count: number; // 期望生成数量；当前 WorkspacePage 尚未透传到后端。
  sound: boolean; // 是否输出声音；当前 WorkspacePage 尚未透传到后端。
}

export interface VideoResult {
  id: string;
  url: string;
  assetType?: "generated_video" | "final_video" | string;
  thumbUrl?: string;
  durationSec?: number;
  status: "success" | "pending" | "failed";
}

export type FlowTimelineEventType =
  | "step_started"
  | "step_finished"
  | "llm_summary"
  | "vendor_call_started"
  | "vendor_call_finished"
  | "asset_ready";

export interface FlowTimelineEntry {
  id: string;
  event: FlowTimelineEventType;
  title: string;
  summary: string;
  phase?: TaskPhase | string;
  status?: string;
  time: string;
}

export interface TaskSummary {
  taskId: string;
  title: string;
  resultCount: number;
  createdAt: string;
}
