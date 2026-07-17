export type WorkflowIntent = "video" | "image" | "ppt";

export type WorkflowFlowKind = "standard" | "direct_image_edit";

export type WorkflowTaskItemStatus =
  | "completed"
  | "processing"
  | "waiting"
  | "waiting_download"
  | "pending"
  | "skipped"
  | "paused"
  | "failed"
  | "cancelled";

export interface WorkflowProgressSnapshot {
  version: 1;
  intent: WorkflowIntent | null;
  flow_kind: WorkflowFlowKind;
  source_message_id: string;
  last_phase: string;
  scene_package_stage?: "prepare_scene_packages" | "generate_scene_assets" | "completed" | string | null;
  updated_at: string;
}

export interface WorkflowTaskItem {
  id: string;
  label: string;
  status: WorkflowTaskItemStatus;
}

export interface WorkflowTaskBoardModel {
  workflowId: string;
  intent: WorkflowIntent;
  flowKind: WorkflowFlowKind;
  steps: WorkflowTaskItem[];
  currentStep: WorkflowTaskItem;
}

interface WorkflowArtifactSignal {
  type?: string;
  intent?: string;
  deliveryDownloadedAt?: string;
  imageResult?: {
    ok?: boolean;
    images?: Array<Record<string, unknown>>;
    quota_insufficient?: boolean;
  };
  mergedVideo?: {
    ok?: boolean;
    merged_video_url?: string | null;
    quota_insufficient?: boolean;
  };
  videoScenePackages?: object;
  pptSummary?: { ok?: boolean; quota_insufficient?: boolean };
  pptImages?: { ok?: boolean; quota_insufficient?: boolean };
  pptFile?: { ok?: boolean; ppt_url?: string | null; quota_insufficient?: boolean };
  sceneGlobalAssetEditReview?: object;
}

export interface WorkflowMessageSignal {
  id: string;
  role?: string;
  artifact?: WorkflowArtifactSignal;
}

export interface WorkflowTaskBoardInput {
  progress?: WorkflowProgressSnapshot | null;
  lastPhase?: string | null;
  fallbackIntent?: WorkflowIntent | null;
  messages?: WorkflowMessageSignal[];
}

const STEP_DEFINITIONS: Record<WorkflowIntent, Array<{ id: string; label: string }>> = {
  video: [
    { id: "requirements", label: "需求收集" },
    { id: "creative", label: "创意规划" },
    { id: "creation", label: "创作规划" },
    { id: "execution", label: "执行规划" },
    { id: "materials", label: "素材生成" },
    { id: "generation", label: "视频生成" },
    { id: "delivery", label: "导出交付" },
  ],
  ppt: [
    { id: "requirements", label: "需求收集" },
    { id: "content", label: "内容规划" },
    { id: "outline", label: "大纲规划" },
    { id: "pages", label: "页面生成" },
    { id: "generation", label: "PPT生成" },
    { id: "delivery", label: "导出交付" },
  ],
  image: [
    { id: "requirements", label: "需求收集" },
    { id: "creative", label: "创意规划" },
    { id: "execution", label: "执行规划" },
    { id: "generation", label: "图片生成" },
    { id: "delivery", label: "导出交付" },
  ],
};

function scopedMessages(messages: WorkflowMessageSignal[], sourceMessageId: string): WorkflowMessageSignal[] {
  if (!sourceMessageId) return messages;
  const sourceIndex = messages.findIndex((message) => message.id === sourceMessageId);
  return sourceIndex >= 0 ? messages.slice(sourceIndex) : messages;
}

function creationIntent(value: unknown): WorkflowIntent | null {
  return value === "video" || value === "image" || value === "ppt" ? value : null;
}

function inferIntentFromPhase(phase: string): WorkflowIntent | null {
  if (phase.startsWith("ppt_")) return "ppt";
  if (phase.startsWith("scene_") || phase.startsWith("video_")) return "video";
  if (phase.startsWith("image_")) return "image";
  return null;
}

function inferIntentFromMessages(messages: WorkflowMessageSignal[]): WorkflowIntent | null {
  for (const message of [...messages].reverse()) {
    const artifact = message.artifact;
    if (!artifact) continue;
    if (artifact.type === "ppt_outline" || artifact.type === "ppt_images" || artifact.type === "ppt_file") return "ppt";
    if (artifact.type === "video_scene_packages" || artifact.type === "video_result" || artifact.type === "video_quality_review") return "video";
    if (artifact.type === "image_result" && !artifact.sceneGlobalAssetEditReview) return "image";
    const intent = creationIntent(artifact.intent);
    if (intent) return intent;
  }
  return null;
}

function currentStatusForPhase(phase: string, currentIndex: number, lastIndex: number): WorkflowTaskItemStatus {
  if (phase === "form_cancelled") return "cancelled";
  if (/quota_paused|blocked/.test(phase)) return "paused";
  if (/failed/.test(phase)) return "failed";
  if (currentIndex === lastIndex) return "waiting_download";
  if (/running|analyze|generation|merge/.test(phase)) return "processing";
  return "waiting";
}

function phaseIndexForVideo(phase: string, scenePackageStage: string): number | null {
  if (/^(?:message_|intake_)|form_cancelled/.test(phase) || phase.endsWith("_form_pending")) return 0;
  if (/direction|creative_directions/.test(phase)) return 1;
  if (/^plan_|plan_review/.test(phase)) return 2;
  if (/^scene_global_asset|^scene_asset/.test(phase)) return 4;
  if (/^scene_package/.test(phase)) {
    return scenePackageStage === "generate_scene_assets" || scenePackageStage === "completed" || /ready|asset|quota/.test(phase) ? 4 : 3;
  }
  if (/^video_(?:generated|regenerated|accepted)$/.test(phase)) return 6;
  if (/^video_/.test(phase)) return 5;
  return null;
}

function phaseIndexForPpt(phase: string): number | null {
  if (/^(?:message_|intake_)|form_cancelled/.test(phase) || phase === "ppt_form_pending") return 0;
  if (/^ppt_outline_running|^ppt_outline_failed/.test(phase)) return 1;
  if (/^ppt_outline/.test(phase)) return 2;
  if (/^ppt_content_json|^ppt_images|^ppt_image_/.test(phase)) return 3;
  if (/^ppt_file_ready|^ppt_done/.test(phase)) return 5;
  if (/^ppt_file|^ppt_job/.test(phase)) return 4;
  return null;
}

function phaseIndexForImage(phase: string, directEdit: boolean): number | null {
  if (/^(?:message_|intake_)|form_cancelled/.test(phase) || phase.endsWith("_form_pending")) return 0;
  if (directEdit && /^image_edit_(?:waiting|options|model)/.test(phase)) return 0;
  if (/direction|creative_directions/.test(phase)) return 1;
  if (/^plan_|plan_review|image_generation_blocked|image_prepare/.test(phase)) return 2;
  if (/^image_(?:generated|regenerated|accepted|edit_done)$/.test(phase)) return 4;
  if (/^image_/.test(phase)) return 3;
  return null;
}

function phaseIndex(intent: WorkflowIntent, phase: string, flowKind: WorkflowFlowKind, scenePackageStage: string): number | null {
  if (intent === "video") return phaseIndexForVideo(phase, scenePackageStage);
  if (intent === "ppt") return phaseIndexForPpt(phase);
  return phaseIndexForImage(phase, flowKind === "direct_image_edit");
}

function latestArtifact(messages: WorkflowMessageSignal[], predicate: (artifact: WorkflowArtifactSignal) => boolean): WorkflowArtifactSignal | null {
  for (const message of [...messages].reverse()) {
    if (message.artifact && predicate(message.artifact)) return message.artifact;
  }
  return null;
}

function artifactIndexAndStatus(intent: WorkflowIntent, messages: WorkflowMessageSignal[]): { index: number; status: WorkflowTaskItemStatus } | null {
  if (intent === "video") {
    const result = latestArtifact(messages, (artifact) => artifact.type === "video_result" || Boolean(artifact.mergedVideo));
    if (result?.mergedVideo?.ok && result.mergedVideo.merged_video_url) {
      return { index: 6, status: result.deliveryDownloadedAt ? "completed" : "waiting_download" };
    }
    if (result?.mergedVideo && !result.mergedVideo.ok) {
      return { index: 5, status: result.mergedVideo.quota_insufficient ? "paused" : "failed" };
    }
    if (latestArtifact(messages, (artifact) => artifact.type === "video_scene_packages" && Boolean(artifact.videoScenePackages))) {
      return { index: 4, status: "waiting" };
    }
    if (latestArtifact(messages, (artifact) => artifact.type === "plan" && artifact.intent === "video")) return { index: 2, status: "waiting" };
    if (latestArtifact(messages, (artifact) => artifact.type === "directions" && artifact.intent === "video")) return { index: 1, status: "waiting" };
    return null;
  }

  if (intent === "ppt") {
    const file = latestArtifact(messages, (artifact) => artifact.type === "ppt_file" && Boolean(artifact.pptFile));
    if (file?.pptFile?.ok && file.pptFile.ppt_url) return { index: 5, status: file.deliveryDownloadedAt ? "completed" : "waiting_download" };
    if (file?.pptFile && !file.pptFile.ok) return { index: 4, status: file.pptFile.quota_insufficient ? "paused" : "failed" };
    const images = latestArtifact(messages, (artifact) => artifact.type === "ppt_images" && Boolean(artifact.pptImages));
    if (images?.pptImages) return { index: 3, status: images.pptImages.ok ? "waiting" : images.pptImages.quota_insufficient ? "paused" : "failed" };
    if (latestArtifact(messages, (artifact) => artifact.type === "ppt_outline" && Boolean(artifact.pptSummary))) return { index: 2, status: "waiting" };
    return null;
  }

  const result = latestArtifact(
    messages,
    (artifact) => artifact.type === "image_result" && Boolean(artifact.imageResult) && !artifact.sceneGlobalAssetEditReview,
  );
  if (result?.imageResult?.ok && (result.imageResult.images?.length || 0) > 0) {
    return { index: 4, status: result.deliveryDownloadedAt ? "completed" : "waiting_download" };
  }
  if (result?.imageResult && !result.imageResult.ok) {
    return { index: 3, status: result.imageResult.quota_insufficient ? "paused" : "failed" };
  }
  if (latestArtifact(messages, (artifact) => artifact.type === "plan" && artifact.intent === "image")) return { index: 2, status: "waiting" };
  if (latestArtifact(messages, (artifact) => artifact.type === "directions" && artifact.intent === "image")) return { index: 1, status: "waiting" };
  return null;
}

export function deriveWorkflowTaskBoard(input: WorkflowTaskBoardInput): WorkflowTaskBoardModel | null {
  const progress = input.progress || null;
  const phase = String(progress?.last_phase || input.lastPhase || "").trim().toLowerCase();
  if (progress && !progress.intent) return null;
  if (phase.startsWith("video_analysis") || phase === "intake_unknown") return null;
  const allMessages = input.messages || [];
  const messages = scopedMessages(allMessages, progress?.source_message_id || "");
  const phaseIntent = inferIntentFromPhase(phase);
  const intent = progress?.intent || input.fallbackIntent || phaseIntent || inferIntentFromMessages(messages);
  if (!intent) return null;

  const directEdit = progress?.flow_kind === "direct_image_edit" || (intent === "image" && /^image_edit_/.test(phase));
  const flowKind: WorkflowFlowKind = directEdit ? "direct_image_edit" : "standard";
  const definitions = STEP_DEFINITIONS[intent];
  const fromArtifact = artifactIndexAndStatus(intent, messages);
  const fromPhase = phaseIndex(intent, phase, flowKind, String(progress?.scene_package_stage || ""));
  const hasExplicitPhase = fromPhase != null;
  const index = fromPhase ?? fromArtifact?.index ?? 0;
  let currentStatus = hasExplicitPhase
    ? currentStatusForPhase(phase, index, definitions.length - 1)
    : fromArtifact?.status || "waiting";

  if (fromArtifact?.index === definitions.length - 1 && index === definitions.length - 1) {
    currentStatus = fromArtifact.status;
  }
  if (phase === "form_cancelled") currentStatus = "cancelled";

  const steps = definitions.map((definition, stepIndex): WorkflowTaskItem => {
    if (flowKind === "direct_image_edit" && intent === "image" && (stepIndex === 1 || stepIndex === 2)) {
      return { ...definition, status: "skipped" };
    }
    if (stepIndex < index) return { ...definition, status: "completed" };
    if (stepIndex === index) return { ...definition, status: currentStatus };
    return { ...definition, status: "pending" };
  });
  const currentStep = steps[index] || steps[steps.length - 1];
  return {
    workflowId: progress?.source_message_id || intent,
    intent,
    flowKind,
    steps,
    currentStep,
  };
}

export function workflowStatusLabel(status: WorkflowTaskItemStatus): string {
  const labels: Record<WorkflowTaskItemStatus, string> = {
    completed: "已完成",
    processing: "处理中",
    waiting: "待确认",
    waiting_download: "待下载",
    pending: "待处理",
    skipped: "已跳过",
    paused: "已暂停",
    failed: "需处理",
    cancelled: "已取消",
  };
  return labels[status];
}
