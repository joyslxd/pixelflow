/** PixelFlow 后端 API Client，对齐 /agent/flows 契约。开发环境下 /agent 由 Vite 代理到后端。 */

import { getBrowserAuthorization } from "@/lib/authStorage";

const AGENT_API_PREFIX = "/agent";
const FLOW_BASE = "/flows";
const AUTHORIZATION_READY_EVENT = "contentAppAuthorizationReady";
const AUTHORIZATION_WAIT_TIMEOUT_MS = 2500;
const SCENE_VIDEO_JOB_POLL_INTERVAL_MS = 3000;
const SCENE_VIDEO_JOB_TIMEOUT_MS = 60 * 60 * 1000;
const DIRECT_VIDEO_JOB_POLL_INTERVAL_MS = 3000;
const DIRECT_VIDEO_JOB_TIMEOUT_MS = 60 * 60 * 1000;
const PPT_JOB_POLL_INTERVAL_MS = 3000;
const PPT_JOB_TIMEOUT_MS = 2 * 60 * 60 * 1000;

export type CreationIntent = "video" | "image" | "ppt";
export type IntakeIntent = CreationIntent | "video_analysis" | "unknown";

export interface TaskResponse {
  task_id: string;
  status: string;
  phase: string;
  thread_id: string;
  product_info: Record<string, unknown>;
  video_params: Record<string, unknown>;
  reference_videos: Array<Record<string, unknown>>;
  creative_direction: Record<string, unknown>;
  brief: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetResponse {
  asset_id: string;
  task_id: string;
  asset_type: string; // 资产类型枚举：generated_video | jianying_draft | final_video。
  status: string;
  phase: string;
  shot_id: string | null;
  url: string;
  local_path: string;
  metadata: Record<string, unknown>;
  error: string | null;
}

export interface CreateTaskBody {
  product_url?: string;
  product_info?: Record<string, unknown>; // 商品信息，如 product_name、main_image_url。
  video_params?: {
    platform?: string;
    duration_sec?: number;
    ratio?: string;
    size?: string;
    business_goal?: string;
  };
  reference_videos?: string[];
  creative_direction?: Record<string, unknown>; // 创意方向，如 core_message、creative_style。
  user_message?: string;
  auto_start?: boolean;
}

export interface TaskEvent {
  id: number;
  event: string; // 事件名，如 phase_change、llm_summary、vendor_call_started、asset_ready。
  data: Record<string, unknown>;
  created_at?: string;
}

export interface SessionContextResponse {
  task_id: string;
  user_id: string | null;
  context: Record<string, unknown>;
  updated_at: string;
}

export interface ConversationSummaryResponse {
  conversation_id: string;
  user_id: string | null;
  title: string;
  current_task_id: string | null;
  last_phase: string;
  context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessageResponse {
  message_id: string;
  conversation_id: string;
  user_id: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ConversationListResponse {
  items: ConversationSummaryResponse[];
  next_cursor: string | null;
}

export interface ConversationDetailResponse {
  conversation: ConversationSummaryResponse;
  messages: ConversationMessageResponse[];
}

export interface IntakeValidationResponse {
  intent: CreationIntent;
  schema: Record<string, unknown>;
  values: Record<string, unknown>;
  missing_fields: string[];
  intake_rounds: number;
  is_complete: boolean;
  terminated: boolean;
  message: string;
  creative_directions: CreativeDirectionResponse[];
}

export interface IntakeIntentResponse {
  intent: IntakeIntent;
  confidence: number;
  reason: string;
  values: Record<string, unknown>;
  intake_context: Record<string, unknown>;
  llm_used: boolean;
  model_name: string;
  error: string | null;
}

export interface CreativeDirectionResponse {
  direction_id: string;
  title: string;
  description: string;
  recommended: boolean;
  tags: string[];
  data: Record<string, unknown>;
}

export interface CreativeDirectionsResponse {
  validation: IntakeValidationResponse;
  creative_directions: CreativeDirectionResponse[];
  intake_context: Record<string, unknown>;
}

export interface PlanMarkdownResponse {
  output_type: CreationIntent;
  plan_markdown: string;
  template_path: string;
  consistency_issues: string[];
  review_timeout_sec: number;
}

export interface ImagePrepareResponse {
  ok: boolean;
  method: "text_to_image" | "multi_reference_image_generation" | "image_edit" | "multi_image_fusion";
  endpoint: string;
  prompt: string;
  negative_prompt: string;
  params: Record<string, unknown>;
  images: Array<Record<string, unknown>>;
  message: string;
  review_timeout_sec: number;
}

export interface ImageGenerateResponse {
  ok: boolean;
  method: ImagePrepareResponse["method"];
  endpoint: string;
  task_id: string | null;
  images: Array<{ asset_id?: string; url?: string; download_url?: string }>;
  error: string | null;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface ImageAssetEditResponse {
  ok: boolean;
  method: "image_edit";
  endpoint: string;
  source_image_url: string;
  edited_image: { asset_id?: string; url?: string; download_url?: string };
  asset_id: string;
  asset_group: string;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface ImageModelParamConfig {
  id?: number;
  modelType: string;
  modelCategoryType?: string;
  paramConfig?: {
    sizeList?: string[];
    aspectRatioList?: string[];
    imageNumList?: string[];
    modelGenerateTypeList?: string[];
    uploadFileTypeList?: string[];
  };
  isEnabled?: boolean;
}

export interface ImageEditModelSelection {
  model: string;
  ratio: string;
  size: string;
}

export interface UploadedAttachment extends Record<string, unknown> {
  name: string;
  filename: string;
  size: number;
  type: string;
  mimeType: string;
  url: string;
  path: string;
  raw?: Record<string, unknown>;
}

export interface SceneVideoPayload {
  scene_id: string;
  scene_index?: number | null;
  video_url: string;
}

export interface SceneGenerationPayload {
  scene_id: string;
  scene_index: number;
  duration_ms: number;
  prompt: string;
  storyline?: string;
  shot_description?: Record<string, unknown>;
  narration?: string;
  generation_mode?: DirectVideoMode | null;
  image_urls?: string[];
  video_urls?: string[];
  audio_urls?: string[];
}

export interface PrepareScenePackagesResponse {
  ok: boolean;
  message: string;
  requires_confirmation: boolean;
  review_timeout_sec: number | null;
  target_duration_ms: number;
  global_assets: Record<string, unknown>;
  scene_packages: Array<SceneGenerationPayload & {
    title?: string;
    storyline?: string;
    narration?: string;
    shot_description?: Record<string, unknown>;
    reference_asset_ids?: string[];
    generation_mode?: DirectVideoMode | null;
    characters?: Array<Record<string, unknown>>;
    scene_images?: Array<Record<string, unknown>>;
    prop_images?: Array<Record<string, unknown>>;
  }>;
}

export interface GenerateSceneAssetsResponse {
  ok: boolean;
  endpoint: string;
  global_assets: Record<string, unknown>;
  scene_packages: PrepareScenePackagesResponse["scene_packages"];
  failed_assets: Array<Record<string, unknown>>;
  message: string;
  quota_insufficient?: boolean;
}

export interface GenerateSceneVideosResponse {
  ok: boolean;
  endpoint: string;
  scene_videos: Array<{
    scene_id: string;
    scene_index: number;
    duration_ms: number;
    mode?: string;
    endpoint?: string;
    video_url: string;
    task_id?: string | null;
    raw?: Record<string, unknown>;
  }>;
  failed_scenes: Array<Record<string, unknown>>;
  message: string;
  quota_insufficient?: boolean;
}

export interface GenerateSceneVideosJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  message: string;
}

export interface GenerateSceneVideosJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  result: GenerateSceneVideosResponse | null;
  error: string | null;
  message: string;
}

export interface MergeSceneVideosResponse {
  ok: boolean;
  endpoint: string;
  merged_video_url: string | null;
  task_id: string | null;
  scene_videos: SceneVideoPayload[];
  error: string | null;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export type DirectVideoMode =
  | "text_to_video"
  | "image_to_video"
  | "two_image_to_video"
  | "reference_mode_video"
  | "edit_video"
  | "extend_video";

export interface GenerateDirectVideoResponse {
  ok: boolean;
  mode: DirectVideoMode;
  endpoint: string;
  video_url: string | null;
  task_id: string | null;
  error: string | null;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface GenerateDirectVideoJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  message: string;
}

export interface GenerateDirectVideoJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  result: GenerateDirectVideoResponse | null;
  error: string | null;
  message: string;
}

export interface VideoFlawAnalysisResponse {
  ok: boolean;
  endpoint: string;
  task_id: string | null;
  flaw_analysis_markdown: string;
  issues: Array<Record<string, unknown>>;
  affected_scene_ids: string[];
  revision_prompt: string;
  error: string | null;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface AnalyzeStoryboardsResponse {
  ok: boolean;
  mode: "single" | "batch" | "";
  extract_endpoint: string;
  endpoint: string;
  video_urls: string[];
  storyboards: Array<Record<string, unknown>>;
  task_id: string | null;
  error: string | null;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface PptJobStartResponse {
  ok: boolean;
  job_id: string;
  status: string;
  message: string;
}

export interface PptJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | "quota_paused" | string;
  result: Record<string, unknown> | null;
  error: string | null;
  message: string;
}

export type PptJobStatusCallback = (status: PptJobStatusResponse) => void;

export interface PptPageImage {
  page_index: number;
  title?: string;
  json_content?: Record<string, unknown>;
  status: "running" | "completed" | "failed" | string;
  image_url?: string | null;
  task_id?: string | null;
  error?: string | null;
  quota_insufficient?: boolean;
  raw?: Record<string, unknown>;
}

export interface PptSummaryResult extends Record<string, unknown> {
  ok: boolean;
  smart_ppt_project_id?: number | null;
  summary?: string;
  message?: string;
  quota_insufficient?: boolean;
}

export interface PptContentJsonResult extends Record<string, unknown> {
  ok: boolean;
  smart_ppt_project_id?: number | null;
  content_json?: unknown;
  pages?: Array<Record<string, unknown>>;
  message?: string;
  quota_insufficient?: boolean;
}

export interface PptImagesResult extends Record<string, unknown> {
  ok: boolean;
  smart_ppt_project_id?: number | null;
  pages: PptPageImage[];
  message?: string;
  quota_insufficient?: boolean;
}

export interface PptFileResult extends Record<string, unknown> {
  ok: boolean;
  smart_ppt_project_id?: number | null;
  ppt_url?: string | null;
  filename?: string | null;
  slide_count?: number | null;
  message?: string;
  quota_insufficient?: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function getAuthorizationHeader(): string {
  // content-app 登录后进入 pixelflow 时，最理想是由宿主页面直接写入 Authorization。
  // 本地联调页面会把 token 存到 localStorage.Authorization，普通 API 调用统一从这里读取。
  return getBrowserAuthorization();
}

function authHeadersFromAuthorization(authorization: string): Record<string, string> {
  if (!authorization) {
    throw new ApiError(401, "缺少 content-app Authorization，请先从 content-app 登录入口进入 PixelFlow");
  }
  return { Authorization: authorization };
}

export async function waitForAuthorizationHeader(timeoutMs = AUTHORIZATION_WAIT_TIMEOUT_MS): Promise<string> {
  const current = getAuthorizationHeader();
  if (current) return current;
  if (typeof window === "undefined") return "";

  return new Promise((resolve) => {
    let settled = false;
    let timer: number | undefined;

    const finish = (authorization: string) => {
      if (settled) return;
      settled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener(AUTHORIZATION_READY_EVENT, onReady);
      resolve(authorization);
    };

    const onReady = () => finish(getAuthorizationHeader());

    window.addEventListener(AUTHORIZATION_READY_EVENT, onReady, { once: true });
    timer = window.setTimeout(() => finish(getAuthorizationHeader()), timeoutMs);
  });
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  // 统一请求模板：自动带 content-app Authorization，并把非 2xx 响应转换成 ApiError。
  // 可以把它类比成前端侧的后端 Client 拦截器。
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authHeadersFromAuthorization(await waitForAuthorizationHeader()),
    ...(init?.headers as Record<string, string>),
  };
  const res = await fetch(`${AGENT_API_PREFIX}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(res.status, await responseErrorMessage(res, path));
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

async function contentAppReq<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...authHeadersFromAuthorization(await waitForAuthorizationHeader()),
    ...(init?.headers as Record<string, string>),
  };
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(res.status, await responseErrorMessage(res, path));
  }
  const raw = (await res.json()) as { success?: boolean; message?: string; data?: T };
  if (raw.success === false) {
    throw new ApiError(400, raw.message || `${path} 调用失败`);
  }
  return (raw.data ?? raw) as T;
}

async function responseErrorMessage(res: Response, path: string): Promise<string> {
  const text = await res.text().catch(() => "");
  const title = text.match(/<title>(.*?)<\/title>/is)?.[1] || text.match(/<h1>(.*?)<\/h1>/is)?.[1];
  const message = (title || text).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return `${res.status} ${path}: ${(message || res.statusText).slice(0, 200)}`;
}

async function uploadFileToContentApp(file: File): Promise<UploadedAttachment> {
  const formData = new FormData();
  formData.append("file", file);
  const path = "/api/upload";
  const res = await fetch(path, {
    method: "POST",
    headers: authHeadersFromAuthorization(await waitForAuthorizationHeader()),
    body: formData,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await responseErrorMessage(res, path));
  }
  const raw = (await res.json()) as Record<string, unknown>;
  if (raw.success === false) {
    throw new ApiError(400, String(raw.error || raw.message || "上传失败"));
  }
  const url = stringField(raw.url) || stringField(raw.path);
  if (!url) {
    throw new ApiError(500, "上传成功但没有返回文件 URL");
  }
  const filename = stringField(raw.filename) || file.name;
  const mimeType = file.type || stringField(raw.contentType);
  return {
    name: filename,
    filename,
    size: numberField(raw.size) || file.size,
    type: attachmentType(mimeType, filename),
    mimeType,
    url,
    path: stringField(raw.path) || url,
  };
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberField(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function attachmentType(mimeType: string, filename: string): string {
  const normalized = mimeType.toLowerCase();
  if (normalized.startsWith("image/")) return "image";
  if (normalized.startsWith("video/")) return "video";
  if (normalized.startsWith("audio/")) return "audio";
  const name = filename.toLowerCase();
  if (/\\.(png|jpe?g|gif|webp|bmp)$/.test(name)) return "image";
  if (/\\.(mp4|mov|mkv|webm)$/.test(name)) return "video";
  if (/\\.(mp3|wav|aac|m4a)$/.test(name)) return "audio";
  return "file";
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function pollSceneVideoJob(jobId: string): Promise<GenerateSceneVideosResponse> {
  const deadline = Date.now() + SCENE_VIDEO_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const status = await req<GenerateSceneVideosJobStatusResponse>(`${FLOW_BASE}/video/generate-scenes/jobs/${encodeURIComponent(jobId)}`);
    if (status.status === "completed" && status.result) return status.result;
    if (status.status === "failed") {
      return {
        ok: false,
        endpoint: "/api/video/reference-mode-video",
        scene_videos: [],
        failed_scenes: [{ error: status.error || status.message || "场景视频生成失败" }],
        message: status.error || status.message || "场景视频生成失败",
      };
    }
    await delay(SCENE_VIDEO_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    endpoint: "/api/video/reference-mode-video",
    scene_videos: [],
    failed_scenes: [{ error: "场景视频生成轮询超时" }],
    message: "场景视频生成轮询超时",
  };
}

async function pollDirectVideoJob(jobId: string): Promise<GenerateDirectVideoResponse> {
  const deadline = Date.now() + DIRECT_VIDEO_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const status = await req<GenerateDirectVideoJobStatusResponse>(`${FLOW_BASE}/video/generate-direct/jobs/${encodeURIComponent(jobId)}`);
    if (status.status === "completed" && status.result) return status.result;
    if (status.status === "failed") {
      return {
        ok: false,
        mode: "reference_mode_video",
        endpoint: "/api/video/reference-mode-video",
        video_url: null,
        task_id: null,
        error: status.error || status.message || "直接视频生成失败",
        message: status.error || status.message || "直接视频生成失败",
        raw: {},
      };
    }
    await delay(DIRECT_VIDEO_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    mode: "reference_mode_video",
    endpoint: "/api/video/reference-mode-video",
    video_url: null,
    task_id: null,
    error: "直接视频生成轮询超时",
    message: "直接视频生成轮询超时",
    raw: {},
  };
}

async function pollPptJob<T extends Record<string, unknown>>(jobId: string, onStatus?: PptJobStatusCallback): Promise<T> {
  const deadline = Date.now() + PPT_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const status = await req<PptJobStatusResponse>(`${FLOW_BASE}/ppt/jobs/${encodeURIComponent(jobId)}`);
    onStatus?.(status);
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result as T;
    if (status.status === "failed") {
      return {
        ok: false,
        message: status.error || status.message || "PPT 生成失败",
        error: status.error || status.message || "PPT 生成失败",
      } as unknown as T;
    }
    await delay(PPT_JOB_POLL_INTERVAL_MS);
  }
  return { ok: false, message: "PPT 生成轮询超时", error: "PPT 生成轮询超时" } as unknown as T;
}

export const api = {
  getCurrentUser: () => req<{ authenticated: boolean; id: string; username: string }>("/auth/me"),

  uploadAttachment: (file: File) => uploadFileToContentApp(file),

  listImageGenerateModelConfigs: () =>
    contentAppReq<ImageModelParamConfig[]>("/api/modelParamConfig/listByCategory/image_generate"),

  createTask: (body: CreateTaskBody) =>
    req<TaskResponse>(FLOW_BASE, { method: "POST", body: JSON.stringify({ task_type: "ecom_video", auto_start: true, ...body }) }),

  getTask: (id: string) => req<TaskResponse>(`${FLOW_BASE}/${id}`),

  getResult: (id: string) =>
    req<{ task_id: string; status: string; phase: string; result: Record<string, unknown>; error: string | null }>(`${FLOW_BASE}/${id}/result`),

  listAssets: (id: string) => req<AssetResponse[]>(`${FLOW_BASE}/${id}/assets`),

  assetContentUrl: (taskId: string, assetId: string) => `${AGENT_API_PREFIX}${FLOW_BASE}/${taskId}/assets/${encodeURIComponent(assetId)}/content`,

  async assetContentBlobUrl(taskId: string, assetId: string): Promise<string> {
    // <video src> 不能自定义 Authorization header，所以受保护的本地成片需要先
    // 用 fetch 带 header 拉成 Blob，再转成本页可播放的 object URL。
    const path = `${FLOW_BASE}/${taskId}/assets/${encodeURIComponent(assetId)}/content`;
    const res = await fetch(`${AGENT_API_PREFIX}${path}`, {
      headers: authHeadersFromAuthorization(await waitForAuthorizationHeader()),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, `${res.status} ${path}: ${text.slice(0, 200)}`);
    }
    return URL.createObjectURL(await res.blob());
  },

  confirmBrief: (id: string, approved: boolean) =>
    req<TaskResponse>(`${FLOW_BASE}/${id}/brief/confirm`, { method: "POST", body: JSON.stringify({ approved }) }),

  confirmStage: (id: string, stage: "segments" | "edit" | "qc", approved: boolean) =>
    req<TaskResponse>(`${FLOW_BASE}/${id}/stages/${stage}/confirm`, { method: "POST", body: JSON.stringify({ approved }) }),

  reviseBrief: (id: string, briefPatch: Record<string, unknown>, feedback: string) =>
    req<TaskResponse>(`${FLOW_BASE}/${id}/brief/revise`, { method: "POST", body: JSON.stringify({ brief_patch: briefPatch, feedback }) }),

  eventsHistory: (id: string, afterId?: number) =>
    req<{ data: TaskEvent[] }>(`${FLOW_BASE}/${id}/events/history${afterId != null ? `?after_id=${afterId}` : ""}`),

  getSessionContext: (taskId?: string) =>
    req<SessionContextResponse | null>(`${FLOW_BASE}/session/context${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`),

  saveSessionContext: (taskId: string, context: Record<string, unknown>) =>
    req<SessionContextResponse>(`${FLOW_BASE}/session/context`, { method: "PUT", body: JSON.stringify({ task_id: taskId, context }) }),

  createConversation: (body: { title?: string; current_task_id?: string | null; last_phase?: string; context?: Record<string, unknown> } = {}) =>
    req<ConversationSummaryResponse>("/conversations", { method: "POST", body: JSON.stringify(body) }),

  listConversations: ({ pageSize = 5, cursor }: { pageSize?: number; cursor?: string | null } = {}) => {
    const params = new URLSearchParams({ page_size: String(pageSize) });
    if (cursor) params.set("cursor", cursor);
    return req<ConversationListResponse>(`/conversations?${params.toString()}`);
  },

  getConversation: (conversationId: string) => req<ConversationDetailResponse>(`/conversations/${encodeURIComponent(conversationId)}`),

  updateConversation: (
    conversationId: string,
    body: { title?: string; current_task_id?: string | null; last_phase?: string; context?: Record<string, unknown> },
  ) => req<ConversationSummaryResponse>(`/conversations/${encodeURIComponent(conversationId)}`, { method: "PUT", body: JSON.stringify(body) }),

  appendConversationMessage: (
    conversationId: string,
    body: { role: "user" | "assistant" | "system"; content: string; payload?: Record<string, unknown> },
  ) => req<ConversationMessageResponse>(`/conversations/${encodeURIComponent(conversationId)}/messages`, { method: "POST", body: JSON.stringify(body) }),

  resumeConversation: (conversationId: string) => req<ConversationDetailResponse>(`/conversations/${encodeURIComponent(conversationId)}/resume`, { method: "POST" }),

  analyzeIntakeIntent: (body: { prompt: string; materials?: Array<Record<string, unknown>> }) =>
    req<IntakeIntentResponse>(`${FLOW_BASE}/intake/analyze`, { method: "POST", body: JSON.stringify(body) }),

  generateCreativeDirections: (body: {
    intent: CreationIntent;
    values: Record<string, unknown>;
    intake_rounds?: number;
    product_creative_profile?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
  }) => req<CreativeDirectionsResponse>(`${FLOW_BASE}/intake/directions`, { method: "POST", body: JSON.stringify(body) }),

  createPlanMarkdown: (body: {
    intent: CreationIntent;
    form_values: Record<string, unknown>;
    selected_direction: Record<string, unknown>;
    product_creative_profile?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
  }) => req<PlanMarkdownResponse>(`${FLOW_BASE}/planning/plan`, { method: "POST", body: JSON.stringify(body) }),

  prepareImageGeneration: (body: {
    form_values: Record<string, unknown>;
    plan_markdown: string;
    selected_direction: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
    revision_feedback?: string | null;
    intake_context?: Record<string, unknown>;
  }) => req<ImagePrepareResponse>(`${FLOW_BASE}/image/prepare`, { method: "POST", body: JSON.stringify(body) }),

  generateImage: (body: {
    method: ImagePrepareResponse["method"];
    prompt: string;
    negative_prompt?: string;
    params: Record<string, unknown>;
  }) => req<ImageGenerateResponse>(`${FLOW_BASE}/image/generate`, { method: "POST", body: JSON.stringify(body) }),

  editImageAsset: (body: {
    asset_id: string;
    asset_name?: string;
    asset_group: string;
    source_image_url: string;
    prompt: string;
    ratio?: string;
    size?: string;
    model?: string | null;
  }) => req<ImageAssetEditResponse>(`${FLOW_BASE}/image/edit-asset`, { method: "POST", body: JSON.stringify(body) }),

  prepareVideoScenePackages: (body: {
    form_values: Record<string, unknown>;
    plan_markdown: string;
    selected_direction: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
    target_duration_ms?: number;
  }) => req<PrepareScenePackagesResponse>(`${FLOW_BASE}/video/prepare-scene-packages`, { method: "POST", body: JSON.stringify(body) }),

  generateSceneAssets: (body: {
    global_assets?: Record<string, unknown>;
    scene_packages: PrepareScenePackagesResponse["scene_packages"];
    image_size?: string;
    model?: string | null;
  }) => req<GenerateSceneAssetsResponse>(`${FLOW_BASE}/video/generate-scene-assets`, { method: "POST", body: JSON.stringify(body) }),

  generateSceneVideos: async (body: {
    scenes: SceneGenerationPayload[];
    ratio?: string;
    size?: string;
    model?: string | null;
    sound?: string;
  }) => {
    const started = await req<GenerateSceneVideosJobStartResponse>(`${FLOW_BASE}/video/generate-scenes/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollSceneVideoJob(started.job_id);
  },

  generateDirectVideo: async (body: {
    mode: DirectVideoMode;
    prompt?: string;
    image_url?: string;
    first_frame_image_url?: string;
    last_frame_image_url?: string;
    image_urls?: string[];
    video_urls?: string[];
    audio_urls?: string[];
    video_url?: string;
    ref_video?: string;
    ref_image?: string;
    duration?: number;
    ratio?: string;
    size?: string;
    model?: string | null;
    sound?: string;
  }) => {
    const started = await req<GenerateDirectVideoJobStartResponse>(`${FLOW_BASE}/video/generate-direct/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollDirectVideoJob(started.job_id);
  },

  mergeSceneVideos: (body: {
    scene_videos: SceneVideoPayload[];
    duration?: number;
    size?: string;
    model?: string | null;
  }) => req<MergeSceneVideosResponse>(`${FLOW_BASE}/video/merge`, { method: "POST", body: JSON.stringify(body) }),

  analyzeVideoFlaws: (body: {
    merged_video_url: string;
    scene_videos: SceneVideoPayload[];
    scene_packages?: Array<Record<string, unknown>>;
    materials?: Array<Record<string, unknown>>;
    user_feedback?: string | null;
  }) => req<VideoFlawAnalysisResponse>(`${FLOW_BASE}/video/analyze-flaws`, { method: "POST", body: JSON.stringify(body) }),

  analyzeStoryboards: (body: {
    prompt?: string;
    materials?: Array<Record<string, unknown>>;
    video_urls?: string[];
  }) => req<AnalyzeStoryboardsResponse>(`${FLOW_BASE}/video/analyze-storyboards`, { method: "POST", body: JSON.stringify(body) }),

  startPptSummaryJob: async (body: {
    ppt_topic: string;
    ppt_style: string;
    attachments: Array<Record<string, unknown>>;
    smart_ppt_project_id?: number | null;
  }) => {
    const started = await req<PptJobStartResponse>(`${FLOW_BASE}/ppt/summary/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollPptJob<PptSummaryResult>(started.job_id);
  },

  updatePptSummaryJob: async (body: {
    original_outline: string;
    modification_opinion: string;
    smart_ppt_project_id: number;
  }) => {
    const started = await req<PptJobStartResponse>(`${FLOW_BASE}/ppt/summary/update/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollPptJob<PptSummaryResult>(started.job_id);
  },

  startPptContentJsonJob: async (body: {
    original_outline: string;
    ppt_style: string;
    smart_ppt_project_id: number;
  }) => {
    const started = await req<PptJobStartResponse>(`${FLOW_BASE}/ppt/content-json/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollPptJob<PptContentJsonResult>(started.job_id);
  },

  startPptImagesJob: async (body: {
    content_json: unknown;
    smart_ppt_project_id: number;
  }, onStatus?: PptJobStatusCallback) => {
    const started = await req<PptJobStartResponse>(`${FLOW_BASE}/ppt/images/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollPptJob<PptImagesResult>(started.job_id, onStatus);
  },

  regeneratePptImageJob: async (body: {
    page_index: number;
    page_json: Record<string, unknown>;
    smart_ppt_project_id: number;
  }) => {
    const started = await req<PptJobStartResponse>(`${FLOW_BASE}/ppt/images/regenerate/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollPptJob<Record<string, unknown>>(started.job_id);
  },

  startPptFileJob: async (body: {
    file_urls: string[];
    smart_ppt_project_id: number;
  }) => {
    const started = await req<PptJobStartResponse>(`${FLOW_BASE}/ppt/file/start`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return pollPptJob<PptFileResult>(started.job_id);
  },
};

/**
 * 订阅任务 SSE 事件流。返回取消函数。
 * 后端事件格式:`event: <name>` + `data: <json>` + `id: <num>`。
 * 原生 EventSource 不能自定义 Authorization header，因此这里用 fetch 读取
 * text/event-stream。afterId 用于断点续订，事件 id 进入 onEvent 做前端去重。
 */
export function subscribeTaskEvents(
  taskId: string,
  onEvent: (e: TaskEvent) => void,
  afterId?: number,
): () => void {
  const url = `${AGENT_API_PREFIX}${FLOW_BASE}/${taskId}/events${afterId != null ? `?after_id=${afterId}` : ""}`;
  const controller = new AbortController();

  const dispatchBlock = (block: string) => {
    let event = "message";
    let id = 0;
    const dataLines: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue;
      const sep = line.indexOf(":");
      const field = sep >= 0 ? line.slice(0, sep) : line;
      const value = sep >= 0 ? line.slice(sep + 1).trimStart() : "";
      if (field === "event") event = value || "message";
      if (field === "id") id = Number(value || 0);
      if (field === "data") dataLines.push(value);
    }
    if (dataLines.length === 0) return;
    try {
      onEvent({ id, event, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      /* 忽略非 JSON 心跳 */
    }
  };

  void (async () => {
    try {
      const res = await fetch(url, {
        headers: authHeadersFromAuthorization(await waitForAuthorizationHeader()),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        throw new ApiError(res.status, `SSE 连接失败: ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || "";
        blocks.forEach(dispatchBlock);
      }
      if (buffer.trim()) dispatchBlock(buffer);
    } catch {
      if (!controller.signal.aborted) {
        onEvent({ id: 0, event: "task_failed", data: { error: "任务事件流连接失败" } });
      }
    }
  })();

  return () => controller.abort();
}
