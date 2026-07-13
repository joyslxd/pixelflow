import type { Brief, CanvasState } from "./chat";

export type WorkflowNodeStatus = "pending" | "running" | "review" | "success" | "error";

export type WorkflowNodeKind = "input" | "agent" | "model" | "edit" | "review" | "export";

export interface WorkflowNodeData {
  [key: string]: unknown;
  title: string;
  subtitle: string;
  kind: WorkflowNodeKind;
  status: WorkflowNodeStatus;
  model?: string;
  description?: string;
  inputs?: string[];
  outputs?: string[];
  error?: string;
}

export interface WorkflowNode {
  id: string;
  position: { x: number; y: number };
  data: WorkflowNodeData;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
}

export type GraphPatch =
  | { op: "add_node"; node: WorkflowNode }
  | { op: "update_node"; id: string; data: Partial<WorkflowNodeData> }
  | { op: "add_edge"; edge: WorkflowEdge }
  | { op: "rerun_node"; id: string };

const ORDER = ["intake", "creative", "brief_review", "storyboard_review", "generate", "segment_review", "edit", "edit_review", "qc", "qc_review", "done"];

function phaseIndex(phase: CanvasState["phase"]) {
  const index = ORDER.indexOf(String(phase));
  return index === -1 ? -1 : index;
}

function statusFor(state: CanvasState, phase: string, reviewPhase?: string): WorkflowNodeStatus {
  if (state.phase === "idle") return "pending";
  if (reviewPhase && state.phase === reviewPhase) return "review";
  const current = phaseIndex(state.phase);
  const target = ORDER.indexOf(phase);
  if (current === -1 || target === -1) return "pending";
  if (current > target || state.phase === "done") return "success";
  if (current === target) return "running";
  return "pending";
}

function briefOutputs(brief?: Brief) {
  if (!brief) return [];
  return [`${brief.shots.length} 个分镜`, `${brief.durationSec || 0}s`, brief.ratio || "9:16"].filter(Boolean);
}

export function buildWorkflowGraph(state: CanvasState): { nodes: WorkflowNode[]; edges: WorkflowEdge[] } {
  const resultCount = state.results.length;
  const nodes: WorkflowNode[] = [
    {
      id: "product",
      position: { x: 0, y: 80 },
      data: {
        title: "商品输入",
        subtitle: "Product Intake",
        kind: "input",
        status: statusFor(state, "intake"),
        description: "商品信息、卖点、平台参数与参考素材。",
        outputs: ["商品参数", "创意诉求"],
      },
    },
    {
      id: "brief",
      position: { x: 260, y: 80 },
      data: {
        title: "策划 Brief",
        subtitle: "Storyboard Agent",
        kind: "agent",
        status: statusFor(state, "creative", "brief_review"),
        description: state.brief?.globalVisual?.overallStyle || "生成分镜、旁白、视觉风格和投放参数。",
        outputs: briefOutputs(state.brief),
      },
    },
    {
      id: "seedream",
      position: { x: 520, y: 0 },
      data: {
        title: "参考图生成",
        subtitle: "Seedream",
        kind: "model",
        status: statusFor(state, "generate"),
        model: "doubao-seedream-5.0-lite",
        description: "按分镜生成参考图、首尾帧或商品视觉素材。",
        inputs: ["Brief", "商品图"],
      },
    },
    {
      id: "seedance",
      position: { x: 520, y: 170 },
      data: {
        title: "视频片段生成",
        subtitle: "Seedance / Veo",
        kind: "model",
        status: statusFor(state, "generate", "segment_review"),
        model: "doubao-seedance-2.0",
        description: "按分镜、参考图、参考视频与音频生成视频片段。",
        inputs: ["Prompt", "参考素材"],
        outputs: resultCount > 0 ? [`${resultCount} 个素材`] : [],
      },
    },
    {
      id: "edit",
      position: { x: 780, y: 80 },
      data: {
        title: "剪辑合成",
        subtitle: "Edit / Compose",
        kind: "edit",
        status: statusFor(state, "edit", "edit_review"),
        description: "片段排序、转场、字幕、音频和成片合成。",
        inputs: ["视频片段", "音频", "字幕"],
      },
    },
    {
      id: "qc",
      position: { x: 1040, y: 80 },
      data: {
        title: "质检确认",
        subtitle: "QC Review",
        kind: "review",
        status: statusFor(state, "qc", "qc_review"),
        description: "检查时长、比例、禁用元素、卖点覆盖和生成质量。",
        outputs: state.qcReport?.score != null ? [`评分 ${Math.round(state.qcReport.score * 100)}`] : [],
      },
    },
    {
      id: "export",
      position: { x: 1300, y: 80 },
      data: {
        title: "导出交付",
        subtitle: "Final Assets",
        kind: "export",
        status: state.phase === "done" ? "success" : "pending",
        description: "生成最终视频、草稿工程和可回溯素材。",
        outputs: resultCount > 0 ? [`${resultCount} 个结果`] : [],
      },
    },
  ];

  const edges: WorkflowEdge[] = [
    { id: "product-brief", source: "product", target: "brief" },
    { id: "brief-seedream", source: "brief", target: "seedream" },
    { id: "brief-seedance", source: "brief", target: "seedance" },
    { id: "seedream-seedance", source: "seedream", target: "seedance" },
    { id: "seedance-edit", source: "seedance", target: "edit" },
    { id: "edit-qc", source: "edit", target: "qc" },
    { id: "qc-export", source: "qc", target: "export" },
  ];

  return { nodes, edges };
}
