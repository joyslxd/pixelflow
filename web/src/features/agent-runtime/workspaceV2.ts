/** Workspace V2 安全投影：优先消费 V2 摘要，缺失时回退 V1；成片只透传白名单 TOS 播放地址。 */

export type WorkspaceV2Asset = {
  assetId: string;
  slot: string;
  kind: string;
  role: string;
  origin: "existing_material" | "planned_generation" | "provider_output";
    generationPrompt: string;
    state: "planned" | "generating" | "ready" | "failed";
    referenceAssetIds: string[];
    usableForVideo: boolean;
    artifactRef: string;
    generationStatus: string;
    failureReasonCode: string;
  };

export type WorkspaceV2Package = {
  segmentId: string;
  sequence: number;
  durationSec: number | null;
  generationMode: string;
  promptSummary: string;
  promptCharCount: number | null;
  promptTruncated: boolean;
  referenceAssetIds: string[];
  continuityFrom: string;
  transitionOut: string;
  era: string;
  camera: string;
  sound: string;
  hardConstraints: string[];
  state: string;
  hasPreview: boolean;
  previewUrl: string;
};

export type WorkspaceV2GenerationJob = {
  jobId: string;
  status: string;
  itemId: string;
  kind: string;
};

export type WorkspaceV2Projection = {
  schemaVersion: number;
  creativeBrief: Record<string, string | number>;
  narrativePlan: Record<string, string>;
  assets: WorkspaceV2Asset[];
  packages: WorkspaceV2Package[];
  generationJobs: WorkspaceV2GenerationJob[];
  outputs: Array<{ outputId: string; kind: string; status: string; title: string }>;
  mergedPreviewUrl: string;
  mergedReady: boolean;
  awaitingProductionConstraints: boolean;
};

type RecordValue = Record<string, unknown>;

const CREATIVE_FIELDS = ["brand", "product", "audience", "platform", "aspect_ratio", "target_duration_sec", "audio", "cta", "creative_direction", "tone", "visual_style", "delivery", "reference_strategy"] as const;
const NARRATIVE_FIELDS = ["concept", "outline", "character_arc", "era", "narration", "dialogue", "sound", "brand_closure", "script", "status", "version"] as const;
const STATE_VALUES = new Set(["planned", "generating", "ready", "failed"]);
const PREVIEW_HOST_SUFFIXES = [".tos-cn-beijing.volces.com", ".vitamazing.top"];

function record(value: unknown): RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as RecordValue : {};
}

function records(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length > 0) : [];
}

function string(value: unknown, max = 2_000): string {
  return typeof value === "string" ? value.trim().slice(0, max) : "";
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function strings(value: unknown, max = 32): string[] {
  return Array.isArray(value) ? value.map((item) => string(item, 256)).filter(Boolean).slice(0, max) : [];
}

function previewUrl(value: unknown): string {
  const candidate = string(value, 4_096);
  try {
    const parsed = new URL(candidate);
    const host = parsed.hostname.toLowerCase();
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return "";
    if (!PREVIEW_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix))) return "";
    return candidate;
  } catch {
    return "";
  }
}

function status(value: unknown): WorkspaceV2Asset["state"] {
  const candidate = string(value).toLowerCase();
  return STATE_VALUES.has(candidate) ? candidate as WorkspaceV2Asset["state"] : "planned";
}

function assetOrigin(value: unknown): WorkspaceV2Asset["origin"] {
  const candidate = string(value).toLowerCase();
  return candidate === "existing_material" || candidate === "provider_output" ? candidate : "planned_generation";
}

function creativeBrief(summary: RecordValue): Record<string, string | number> {
  const source = record(summary.creative_brief);
  const legacy = record(summary.product_info);
  const result: Record<string, string | number> = {};
  for (const field of CREATIVE_FIELDS) {
    const value = source[field] ?? (field === "product" ? legacy.name : legacy[field]);
    if (field === "aspect_ratio") {
      const ratio = string(value || summary.video_ratio, 32);
      if (ratio) result[field] = ratio;
    } else if (field === "target_duration_sec") {
      const duration = integer(value);
      if (duration !== null) result[field] = duration;
    } else {
      const text = string(value);
      if (text) result[field] = text;
    }
  }
  // V2 后端已白名单投影扩展字段；保留它们避免创意方向等信息被固定表单键丢弃。
  for (const [key, value] of Object.entries(source)) {
    if (key in result || Object.keys(result).length >= 24) continue;
    if (typeof value === "number" && Number.isFinite(value)) result[key] = value;
    else {
      const text = string(value);
      if (text) result[key] = text;
    }
  }
  return result;
}

function narrativePlan(summary: RecordValue): Record<string, string> {
  const source = record(summary.narrative_plan);
  const result: Record<string, string> = {};
  for (const field of NARRATIVE_FIELDS) {
    const fallback = field === "script" ? summary.script_editor_content ?? summary.script_preview : undefined;
    const value = string(source[field] ?? fallback, field === "script" ? 8_000 : 2_000);
    if (value) result[field] = value;
  }
  // 与创意层相同：只使用 Gateway 已过滤的文本，避免未知但合法的叙事字段在前端消失。
  for (const [key, value] of Object.entries(source)) {
    if (key in result || Object.keys(result).length >= 24) continue;
    const text = string(value, key === "script" ? 8_000 : 2_000);
    if (text) result[key] = text;
  }
  return result;
}

function assets(summary: RecordValue): WorkspaceV2Asset[] {
  const v2 = records(summary.asset_registry);
  const source: RecordValue[] = v2.length > 0 ? v2 : [
    ...records(summary.character_summaries).map((item): RecordValue => ({ ...item, kind: "character" })),
    ...records(summary.scene_asset_summaries).map((item): RecordValue => ({ ...item, kind: "scene" })),
    ...records(summary.prop_summaries).map((item): RecordValue => ({ ...item, kind: "prop" })),
  ];
  return source.map((item, index) => ({
    assetId: string(item.asset_id ?? item.id, 128) || `asset-${index + 1}`,
    slot: string(item.slot, 64) || `@资产${index + 1}`,
    kind: string(item.kind ?? item.asset_type, 64) || "asset",
    role: string(item.role ?? item.name ?? item.title, 256) || "未命名资产",
    origin: assetOrigin(item.origin),
    generationPrompt: string(item.generation_prompt, 8_000),
    state: status(item.state === "planned" && string(item.generation_job_id, 128) ? "generating" : item.state),
    referenceAssetIds: strings(item.reference_asset_ids),
    usableForVideo: item.usable_for_video === true,
    artifactRef: string(item.artifact_ref ?? item.provider_artifact_ref, 256),
    generationStatus: string(item.generation_job_status ?? item.generation_status, 64),
    failureReasonCode: string(item.failure_reason_code, 128),
  }));
}

function packages(summary: RecordValue): WorkspaceV2Package[] {
  const v2 = records(summary.prompt_packages);
  const source = v2.length > 0 ? v2 : records(summary.scene_summaries);
  return source.map((item, index) => ({
    segmentId: string(item.segment_id ?? item.scene_id, 128) || `segment-${index + 1}`,
    sequence: integer(item.sequence ?? item.scene_index) ?? index + 1,
    durationSec: integer(item.duration_sec ?? item.duration),
    generationMode: string(item.generation_mode, 64) || "independent",
    promptSummary: string(item.prompt_summary ?? item.prompt ?? item.title, 8_000),
    promptCharCount: integer(item.prompt_char_count),
    promptTruncated: item.prompt_truncated === true,
    referenceAssetIds: strings(item.reference_asset_ids),
    continuityFrom: string(item.continuity_from, 128),
    transitionOut: string(item.transition_out, 512),
    era: string(item.era, 512),
    camera: string(item.camera, 1_000),
    sound: string(item.sound, 1_000),
    hardConstraints: strings(item.hard_constraints, 64),
    state: string(item.state, 64) || "planned",
    previewUrl: previewUrl(item.preview_url),
    hasPreview: Boolean(previewUrl(item.preview_url)),
  })).sort((left, right) => left.sequence - right.sequence);
}

function generationJobs(summary: RecordValue): WorkspaceV2GenerationJob[] {
  const explicit = records(summary.generation_jobs ?? summary.jobs);
  const assetJobs = records(summary.asset_registry)
    .filter((asset) => string(asset.generation_job_id, 128))
    .map((asset) => {
      const assetState = string(asset.state, 32).toLowerCase();
      const jobStatus = string(asset.generation_job_status ?? asset.generation_status, 64);
      const status = assetState === "failed" ? "failed"
        : assetState === "ready" ? "succeeded"
        : jobStatus;
      return {
        generation_job_id: asset.generation_job_id,
        kind: "image",
        item_id: asset.asset_id,
        status,
      };
    });
  const sceneJobs = records(summary.scene_summaries)
    .flatMap((scene) => records(scene.generation_jobs).map((job) => ({
      ...job,
      kind: job.kind ?? "video",
      item_id: job.item_id ?? scene.scene_id,
    })));
  const allJobs: RecordValue[] = [...explicit, ...assetJobs, ...sceneJobs];
  return allJobs.map((job, index) => ({
    jobId: string(job.generation_job_id ?? job.job_id ?? job.id, 128) || `generation-job-${index + 1}`,
    status: string(job.status, 64) || "queued",
    itemId: string(job.item_id ?? job.scene_id ?? job.asset_id, 128),
    kind: string(job.kind, 32) || "generation",
  }));
}

function mergedVideo(summary: RecordValue): { mergedPreviewUrl: string; mergedReady: boolean } {
  const source = record(summary.merged_video);
  const url = previewUrl(source.preview_url);
  return {
    mergedPreviewUrl: url,
    mergedReady: source.ok === true || Boolean(url),
  };
}


function outputs(summary: RecordValue): WorkspaceV2Projection["outputs"] {
  return records(summary.outputs).map((item, index) => ({
    outputId: string(item.output_id ?? item.asset_id ?? item.id, 128) || `output-${index + 1}`,
    kind: string(item.kind ?? item.output_type, 64) || "output",
    status: string(item.status, 64) || "ready",
    title: string(item.title ?? item.name, 256) || "未命名输出",
  }));
}

export function projectWorkspaceV2(rawSummary: Record<string, unknown>): WorkspaceV2Projection {
  /** 只投影文档明确允许的安全字段；V2 缺失时降级到已有 V1 摘要。 */

  const summary = record(rawSummary);
  const merged = mergedVideo(summary);
  return {
    schemaVersion: integer(summary.workspace_schema_version) ?? 1,
    creativeBrief: creativeBrief(summary),
    narrativePlan: narrativePlan(summary),
    assets: assets(summary),
    packages: packages(summary),
    generationJobs: generationJobs(summary),
    outputs: outputs(summary),
    mergedPreviewUrl: merged.mergedPreviewUrl,
    mergedReady: merged.mergedReady,
    awaitingProductionConstraints: summary.awaiting_production_constraints === true,
  };
}

export function generationJobCounts(jobs: WorkspaceV2GenerationJob[]): Record<string, number> {
  /** 同一 GenerationJob 只统计一次；一个失败任务不会覆盖其它镜头或资产的状态。 */

  const seen = new Set<string>();
  const counts: Record<string, number> = { queued: 0, polling: 0, succeeded: 0, failed: 0, paused: 0 };
  for (const job of jobs) {
    if (seen.has(job.jobId)) continue;
    seen.add(job.jobId);
    const normalized = job.status.toLowerCase();
    if (normalized.includes("fail") || normalized.includes("error") || normalized.includes("timeout") || normalized === "indeterminate") counts.failed += 1;
    else if (normalized.includes("success") || normalized.includes("succeed") || normalized.includes("complete")) counts.succeeded += 1;
    else if (normalized.includes("pause") || normalized.includes("authorization") || normalized.includes("quota")) counts.paused += 1;
    else if (normalized.includes("poll") || normalized.includes("running") || normalized === "starting" || normalized === "generating") counts.polling += 1;
    else counts.queued += 1;
  }
  return counts;
}

export function workspaceHasInFlightGeneration(rawSummary: Record<string, unknown>): boolean {
  /** 只根据 Gateway 公开摘要判断是否还有未完成生成，浏览器不自建任务列表。 */

  const projection = projectWorkspaceV2(rawSummary);
  if (projection.assets.some((asset) => asset.state === "generating")) return true;
  const counts = generationJobCounts(projection.generationJobs);
  if (counts.queued + counts.polling > 0) return true;
  const pollingScenes = integer(rawSummary.scene_videos_polling_count);
  return pollingScenes !== null && pollingScenes > 0;
}

export function generationProgressText(rawSummary: Record<string, unknown>): string {
  /** 任务看板只展示公开计数，不含 URL、凭据或 Provider 原文。 */

  const projection = projectWorkspaceV2(rawSummary);
  const videoCounts = generationJobCounts(projection.generationJobs.filter((job) => job.kind === "video"));
  const imageCounts = generationJobCounts(projection.generationJobs.filter((job) => job.kind !== "video"));
  const generating = projection.assets.filter((asset) => asset.state === "generating");
  const pollingScenes = integer(rawSummary.scene_videos_polling_count) ?? 0;
  const videoInFlight = videoCounts.queued + videoCounts.polling || pollingScenes;
  if (generating.length > 0) {
    const names = generating.map((asset) => asset.slot || asset.role).filter(Boolean).slice(0, 3).join("、");
    const target = names || `${generating.length} 项`;
    return `正在生成 ${target} · 完成 ${imageCounts.succeeded} · 失败 ${imageCounts.failed} · 进行中 ${generating.length}`;
  }
  if (videoInFlight > 0) {
    return `正在生成 ${videoInFlight} 个分镜视频 · 完成 ${videoCounts.succeeded} · 失败 ${videoCounts.failed}`;
  }
  if (videoCounts.failed > 0) return `分镜视频结束 · 成功 ${videoCounts.succeeded} · 失败 ${videoCounts.failed}`;
  if (videoCounts.succeeded > 0) return `分镜视频完成 ${videoCounts.succeeded} 项`;
  if (imageCounts.failed > 0) return `生成结束 · 成功 ${imageCounts.succeeded} · 失败 ${imageCounts.failed}`;
  if (imageCounts.succeeded > 0) return `生成完成 ${imageCounts.succeeded} 项`;
  return "";
}
