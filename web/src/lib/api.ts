/** PixelFlow 后端 API Client，对齐 /agent/flows 契约。开发环境下 /agent 由 Vite 代理到后端。 */

import { getBrowserAuthorization } from "@/lib/authStorage";

const AGENT_API_PREFIX = "/agent";
const FLOW_BASE = "/flows";
const AUTHORIZATION_READY_EVENT = "contentAppAuthorizationReady";
const AUTHORIZATION_WAIT_TIMEOUT_MS = 2500;
const SCENE_VIDEO_JOB_POLL_INTERVAL_MS = 3000;
const SCENE_VIDEO_JOB_TIMEOUT_MS = 60 * 60 * 1000;
const VIDEO_MERGE_JOB_POLL_INTERVAL_MS = 3000;
const VIDEO_MERGE_JOB_TIMEOUT_MS = 60 * 60 * 1000;
const VIDEO_QUALITY_REVIEW_JOB_POLL_INTERVAL_MS = 3000;
const VIDEO_QUALITY_REVIEW_JOB_TIMEOUT_MS = 60 * 60 * 1000;
const IMAGE_JOB_POLL_INTERVAL_MS = 3000;
const IMAGE_JOB_TIMEOUT_MS = 10 * 60 * 1000;
const INTAKE_ANALYZE_JOB_POLL_INTERVAL_MS = 3000;
const INTAKE_ANALYZE_JOB_TIMEOUT_MS = 10 * 60 * 1000;
const CONVERSATION_MESSAGE_JOB_POLL_INTERVAL_MS = 1000;
const CONVERSATION_MESSAGE_JOB_TIMEOUT_MS = 2 * 60 * 1000;
const CREATIVE_DIRECTION_JOB_POLL_INTERVAL_MS = 3000;
const CREATIVE_DIRECTION_JOB_TIMEOUT_MS = 10 * 60 * 1000;
const SCENE_PACKAGE_JOB_POLL_INTERVAL_MS = 3000;
const SCENE_PACKAGE_JOB_TIMEOUT_MS = 60 * 60 * 1000;
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

export interface ConversationMessageJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | string;
  message: string;
}

export interface ConversationMessageJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | string;
  result: ConversationMessageResponse | null;
  error: string | null;
  message: string;
}

export interface ConversationListResponse {
  items: ConversationSummaryResponse[];
  next_cursor: string | null;
}

export interface ConversationDetailResponse {
  conversation: ConversationSummaryResponse;
  messages: ConversationMessageResponse[];
}

export interface ConversationTraceEvent {
  id: number;
  conversation_id: string;
  event: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface ConversationTraceResponse {
  items: ConversationTraceEvent[];
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

export interface IntakeAnalyzeJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | string;
  message: string;
}

export interface IntakeAnalyzeJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | string;
  result: IntakeIntentResponse | null;
  error: string | null;
  message: string;
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

export interface CreativeDirectionsJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | string;
  message: string;
}

export interface CreativeDirectionsJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | string;
  result: CreativeDirectionsResponse | null;
  error: string | null;
  message: string;
}

export interface PlanMarkdownResponse {
  output_type: CreationIntent;
  plan_markdown: string;
  template_path: string;
  consistency_issues: string[];
  review_timeout_sec: number | null;
  plan_version: number;
  plan_history: Array<{ version: number; plan_markdown: string; restored_from_version?: number }>;
  creation_contract: Record<string, unknown>;
  scene_durations_sec: number[];
  llm_used: boolean;
  model_name: string;
  error: string | null;
  restored_from_version: number | null;
}

export interface VideoCreationContract extends Record<string, unknown> {
  video_duration_sec: number;
  video_ratio: string;
  video_model: string;
  video_size: string;
  video_sound: string;
  image_model: string;
  scene_image_ratio?: string | null;
  scene_image_size?: string | null;
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

export interface ImageGenerateJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | "quota_paused" | string;
  message: string;
}

export interface ImageGenerateJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | "quota_paused" | string;
  result: ImageGenerateResponse | null;
  error: string | null;
  message: string;
}

export interface ImageAssetEditResponse {
  ok: boolean;
  method: "image_edit" | "multi_reference_image_generation";
  endpoint: string;
  source_image_url: string;
  edited_image: { asset_id?: string; url?: string; download_url?: string };
  asset_id: string;
  asset_group: string;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface ImageAssetFusionResponse {
  ok: boolean;
  method: "multi_image_fusion";
  endpoint: string;
  source_image_url: string;
  fused_image: { asset_id?: string; url?: string; download_url?: string };
  asset_id: string;
  asset_group: string;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface ImageAssetEditJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | "quota_paused" | string;
  message: string;
}

export interface ImageAssetEditJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | "quota_paused" | string;
  result: ImageAssetEditResponse | null;
  error: string | null;
  message: string;
}

export interface ImageAssetFusionJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | "quota_paused" | string;
  message: string;
}

export interface ImageAssetFusionJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "running" | "completed" | "failed" | "quota_paused" | string;
  result: ImageAssetFusionResponse | null;
  error: string | null;
  message: string;
}

export interface ImageModelParamConfig {
  id?: number;
  modelType: string;
  modelCategoryType?: string;
  paramConfig?: {
    sizeList?: string[];
    aspectRatioList?: string[];
    onSoundList?: string[];
    videoDurationList?: string[];
    imageNumList?: string[];
    modelGenerateTypeList?: string[];
    uploadFileTypeList?: string[];
  };
  isEnabled?: boolean;
}

export type VideoModelParamConfig = ImageModelParamConfig;

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
  creation_contract?: VideoCreationContract | null;
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

export interface PrepareScenePackagesJobResult {
  ok: boolean;
  videoScenePackages: PrepareScenePackagesResponse | null;
  sceneAssetFailures: Array<Record<string, unknown>>;
  quota_insufficient?: boolean;
  message: string;
}

export interface PrepareScenePackagesJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "quota_paused" | string;
  stage: "prepare_scene_packages" | "generate_scene_assets" | "completed" | string;
  message: string;
}

export interface PrepareScenePackagesJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "quota_paused" | string;
  stage: "prepare_scene_packages" | "generate_scene_assets" | "completed" | string;
  result: PrepareScenePackagesJobResult | null;
  error: string | null;
  message: string;
}

export interface GenerateSceneAssetsJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "quota_paused" | string;
  stage: "generate_scene_assets" | "completed" | string;
  message: string;
}

export interface GenerateSceneAssetsJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "quota_paused" | string;
  stage: "generate_scene_assets" | "completed" | string;
  result: GenerateSceneAssetsResponse | null;
  error: string | null;
  message: string;
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

export interface MergeSceneVideosJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "quota_paused" | string;
  message: string;
}

export interface MergeSceneVideosJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "quota_paused" | string;
  result: MergeSceneVideosResponse | null;
  error: string | null;
  message: string;
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

export interface VideoQualityReviewResponse {
  ok: boolean;
  endpoint: string;
  task_id: string | null;
  passed: boolean;
  score: number;
  summary_markdown: string;
  quality_report_markdown: string;
  issues: Array<Record<string, unknown>>;
  affected_scene_ids: string[];
  target_scene_ids?: string[];
  excluded_scene_ids?: string[];
  revision_prompt: string;
  check_results: Array<Record<string, unknown>>;
  error: string | null;
  message: string;
  quota_insufficient?: boolean;
  raw: Record<string, unknown>;
}

export interface VideoQualityReviewJobStartResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  message: string;
}

export interface VideoQualityReviewJobStatusResponse {
  ok: boolean;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "quota_paused" | string;
  result: VideoQualityReviewResponse | null;
  error: string | null;
  message: string;
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

// 当前活跃对话 id；只用来给内部调试用的 trace 埋点在请求头上带 X-Conversation-Id，
// 不影响业务请求本身。由 WorkspacePage 在切换/创建对话时调用 setActiveConversationId 维护。
let activeConversationIdForTrace: string | null = null;

export function setActiveConversationId(conversationId: string | null): void {
  activeConversationIdForTrace = conversationId;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  // 统一请求模板：自动带 content-app Authorization，并把非 2xx 响应转换成 ApiError。
  // 可以把它类比成前端侧的后端 Client 拦截器。
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authHeadersFromAuthorization(await waitForAuthorizationHeader()),
    ...(activeConversationIdForTrace ? { "X-Conversation-Id": activeConversationIdForTrace } : {}),
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

async function pollConversationMessageJob(
  conversationId: string,
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<ConversationMessageResponse | null> {
  const deadline = Date.now() + CONVERSATION_MESSAGE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<ConversationMessageJobStatusResponse>(
      `/conversations/${encodeURIComponent(conversationId)}/messages/jobs/${encodeURIComponent(jobId)}`,
    );
    if (!shouldContinue()) return null;
    if (status.status === "completed" && status.result) return status.result;
    if (status.status === "failed") {
      throw new ApiError(500, status.error || status.message || "对话消息保存失败");
    }
    await delay(CONVERSATION_MESSAGE_JOB_POLL_INTERVAL_MS);
  }
  throw new ApiError(408, "对话消息保存轮询超时");
}

async function pollIntakeAnalyzeJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<IntakeIntentResponse | null> {
  const deadline = Date.now() + INTAKE_ANALYZE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<IntakeAnalyzeJobStatusResponse>(`${FLOW_BASE}/intake/analyze/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if (status.status === "completed" && status.result) return status.result;
    if (status.status === "failed") {
      throw new ApiError(500, status.error || status.message || "采集 Agent 意图识别失败");
    }
    await delay(INTAKE_ANALYZE_JOB_POLL_INTERVAL_MS);
  }
  throw new ApiError(408, "采集 Agent 意图识别轮询超时");
}

async function pollCreativeDirectionsJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<CreativeDirectionsResponse | null> {
  const deadline = Date.now() + CREATIVE_DIRECTION_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<CreativeDirectionsJobStatusResponse>(`${FLOW_BASE}/intake/directions/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if (status.status === "completed" && status.result) return status.result;
    if (status.status === "failed") {
      throw new ApiError(500, status.error || status.message || "创意方向生成失败");
    }
    await delay(CREATIVE_DIRECTION_JOB_POLL_INTERVAL_MS);
  }
  throw new ApiError(408, "创意方向生成轮询超时");
}

async function pollSceneVideoJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<GenerateSceneVideosResponse | null> {
  const deadline = Date.now() + SCENE_VIDEO_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<GenerateSceneVideosJobStatusResponse>(`${FLOW_BASE}/video/generate-scenes/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
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

async function pollMergeSceneVideoJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<MergeSceneVideosResponse | null> {
  const deadline = Date.now() + VIDEO_MERGE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<MergeSceneVideosJobStatusResponse>(`${FLOW_BASE}/video/merge/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result;
    if (status.status === "failed") {
      if (status.result) return status.result;
      return {
        ok: false,
        endpoint: "/api/video/merge",
        merged_video_url: null,
        task_id: null,
        scene_videos: [],
        error: status.error || status.message || "视频合并失败",
        message: status.error || status.message || "视频合并失败",
        raw: {},
      };
    }
    await delay(VIDEO_MERGE_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    endpoint: "/api/video/merge",
    merged_video_url: null,
    task_id: null,
    scene_videos: [],
    error: "视频合并轮询超时",
    message: "视频合并轮询超时",
    raw: {},
  };
}

async function pollVideoQualityReviewJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<VideoQualityReviewResponse | null> {
  const deadline = Date.now() + VIDEO_QUALITY_REVIEW_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<VideoQualityReviewJobStatusResponse>(`${FLOW_BASE}/video/quality-review/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result;
    if (status.status === "failed") {
      if (status.result) return status.result;
      return {
        ok: false,
        endpoint: "/api/creative/video_quality_review",
        task_id: null,
        passed: false,
        score: 0,
        summary_markdown: "",
        quality_report_markdown: "",
        issues: [],
        affected_scene_ids: [],
        target_scene_ids: [],
        excluded_scene_ids: [],
        revision_prompt: "",
        check_results: [],
        error: status.error || status.message || "视频质检失败",
        message: status.error || status.message || "视频质检失败",
        raw: {},
      };
    }
    await delay(VIDEO_QUALITY_REVIEW_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    endpoint: "/api/creative/video_quality_review",
    task_id: null,
    passed: false,
    score: 0,
    summary_markdown: "",
    quality_report_markdown: "",
    issues: [],
    affected_scene_ids: [],
    target_scene_ids: [],
    excluded_scene_ids: [],
    revision_prompt: "",
    check_results: [],
    error: "视频质检轮询超时",
    message: "视频质检轮询超时",
    raw: {},
  };
}

async function pollImageGenerationJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<ImageGenerateResponse | null> {
  const deadline = Date.now() + IMAGE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<ImageGenerateJobStatusResponse>(`${FLOW_BASE}/image/generate/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result;
    if (status.status === "failed") {
      return {
        ok: false,
        method: "text_to_image",
        endpoint: "/api/picture/text_to_image",
        task_id: null,
        images: [],
        error: status.error || status.message || "图片生成失败",
        message: status.error || status.message || "图片生成失败",
        raw: {},
      };
    }
    await delay(IMAGE_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    method: "text_to_image",
    endpoint: "/api/picture/text_to_image",
    task_id: null,
    images: [],
    error: "图片生成轮询超时",
    message: "图片生成轮询超时",
    raw: {},
  };
}

async function pollImageAssetEditJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<ImageAssetEditResponse | null> {
  const deadline = Date.now() + IMAGE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<ImageAssetEditJobStatusResponse>(`${FLOW_BASE}/image/edit-asset/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result;
    if (status.status === "failed") {
      return {
        ok: false,
        method: "image_edit",
        endpoint: "/api/picture/image_edit",
        source_image_url: "",
        edited_image: {},
        asset_id: "",
        asset_group: "",
        message: status.error || status.message || "素材图片编辑失败",
        quota_insufficient: false,
        raw: {},
      };
    }
    await delay(IMAGE_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    method: "image_edit",
    endpoint: "/api/picture/image_edit",
    source_image_url: "",
    edited_image: {},
    asset_id: "",
    asset_group: "",
    message: "素材图片编辑轮询超时",
    quota_insufficient: false,
    raw: {},
  };
}

async function pollImageAssetFusionJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<ImageAssetFusionResponse | null> {
  const deadline = Date.now() + IMAGE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<ImageAssetFusionJobStatusResponse>(`${FLOW_BASE}/image/fuse-asset/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result;
    if (status.status === "failed") {
      return {
        ok: false,
        method: "multi_image_fusion",
        endpoint: "/api/picture/multi_image_fusion",
        source_image_url: "",
        fused_image: {},
        asset_id: "",
        asset_group: "",
        message: status.error || status.message || "素材图片融合失败",
        quota_insufficient: false,
        raw: {},
      };
    }
    await delay(IMAGE_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    method: "multi_image_fusion",
    endpoint: "/api/picture/multi_image_fusion",
    source_image_url: "",
    fused_image: {},
    asset_id: "",
    asset_group: "",
    message: "素材图片融合轮询超时",
    quota_insufficient: false,
    raw: {},
  };
}

async function pollPrepareScenePackagesJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<PrepareScenePackagesJobResult | null> {
  const deadline = Date.now() + SCENE_PACKAGE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<PrepareScenePackagesJobStatusResponse>(`${FLOW_BASE}/video/prepare-scene-packages/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result;
    if (status.status === "failed") {
      return {
        ok: false,
        videoScenePackages: null,
        sceneAssetFailures: [{ error: status.error || status.message || "视频场景包生成失败" }],
        message: status.error || status.message || "视频场景包生成失败",
      };
    }
    await delay(SCENE_PACKAGE_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    videoScenePackages: null,
    sceneAssetFailures: [{ error: "视频场景包生成轮询超时" }],
    message: "视频场景包生成轮询超时",
  };
}

async function pollSceneAssetsJob(
  jobId: string,
  shouldContinue: () => boolean = () => true,
): Promise<GenerateSceneAssetsResponse | null> {
  const deadline = Date.now() + SCENE_PACKAGE_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<GenerateSceneAssetsJobStatusResponse>(`${FLOW_BASE}/video/generate-scene-assets/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result;
    if (status.status === "failed") {
      return {
        ok: false,
        endpoint: "/api/picture/text_to_image",
        global_assets: {},
        scene_packages: [],
        failed_assets: [{ error: status.error || status.message || "场景参考图生成失败" }],
        message: status.error || status.message || "场景参考图生成失败",
      };
    }
    await delay(SCENE_PACKAGE_JOB_POLL_INTERVAL_MS);
  }
  return {
    ok: false,
    endpoint: "/api/picture/text_to_image",
    global_assets: {},
    scene_packages: [],
    failed_assets: [{ error: "场景参考图生成轮询超时" }],
    message: "场景参考图生成轮询超时",
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

async function pollPptJob<T extends Record<string, unknown>>(
  jobId: string,
  onStatus?: PptJobStatusCallback,
  shouldContinue: () => boolean = () => true,
): Promise<T | null> {
  const deadline = Date.now() + PPT_JOB_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!shouldContinue()) return null;
    const status = await req<PptJobStatusResponse>(`${FLOW_BASE}/ppt/jobs/${encodeURIComponent(jobId)}`);
    if (!shouldContinue()) return null;
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

  listVideoGenerateModelConfigs: () =>
    contentAppReq<VideoModelParamConfig[]>("/api/modelParamConfig/listByCategory/video_generate"),

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

  updateConversationMessage: (
    conversationId: string,
    messageId: string,
    body: { content?: string; payload?: Record<string, unknown> },
  ) =>
    req<ConversationMessageResponse>(`/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  startConversationMessageJob: (
    conversationId: string,
    body: { role: "user" | "assistant" | "system"; content: string; payload?: Record<string, unknown> },
  ) =>
    req<ConversationMessageJobStartResponse>(`/conversations/${encodeURIComponent(conversationId)}/messages/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getConversationMessageJob: (conversationId: string, jobId: string) =>
    req<ConversationMessageJobStatusResponse>(
      `/conversations/${encodeURIComponent(conversationId)}/messages/jobs/${encodeURIComponent(jobId)}`,
    ),

  pollConversationMessageJob,

  resumeConversation: (conversationId: string) => req<ConversationDetailResponse>(`/conversations/${encodeURIComponent(conversationId)}/resume`, { method: "POST" }),

  // 内部调试专用：需要 content-app ROLE_ADMIN，普通用户调用会 403。
  fetchConversationTrace: (conversationId: string) =>
    req<ConversationTraceResponse>(`/conversations/${encodeURIComponent(conversationId)}/trace`),

  analyzeIntakeIntent: (body: { prompt: string; materials?: Array<Record<string, unknown>> }) =>
    req<IntakeIntentResponse>(`${FLOW_BASE}/intake/analyze`, { method: "POST", body: JSON.stringify(body) }),

  startIntakeAnalyzeJob: (body: { prompt: string; materials?: Array<Record<string, unknown>> }) =>
    req<IntakeAnalyzeJobStartResponse>(`${FLOW_BASE}/intake/analyze/start`, { method: "POST", body: JSON.stringify(body) }),

  getIntakeAnalyzeJob: (jobId: string) =>
    req<IntakeAnalyzeJobStatusResponse>(`${FLOW_BASE}/intake/analyze/jobs/${encodeURIComponent(jobId)}`),

  pollIntakeAnalyzeJob,

  generateCreativeDirections: (body: {
    intent: CreationIntent;
    values: Record<string, unknown>;
    intake_rounds?: number;
    product_creative_profile?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
  }) => req<CreativeDirectionsResponse>(`${FLOW_BASE}/intake/directions`, { method: "POST", body: JSON.stringify(body) }),

  startCreativeDirectionsJob: (body: {
    intent: CreationIntent;
    values: Record<string, unknown>;
    intake_rounds?: number;
    product_creative_profile?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
  }) => req<CreativeDirectionsJobStartResponse>(`${FLOW_BASE}/intake/directions/start`, { method: "POST", body: JSON.stringify(body) }),

  getCreativeDirectionsJob: (jobId: string) =>
    req<CreativeDirectionsJobStatusResponse>(`${FLOW_BASE}/intake/directions/jobs/${encodeURIComponent(jobId)}`),

  pollCreativeDirectionsJob,

  createPlanMarkdown: (body: {
    intent: CreationIntent;
    form_values: Record<string, unknown>;
    selected_direction: Record<string, unknown>;
    product_creative_profile?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
  }) => req<PlanMarkdownResponse>(`${FLOW_BASE}/planning/plan`, { method: "POST", body: JSON.stringify(body) }),

  revisePlanMarkdown: (body: {
    intent: CreationIntent;
    form_values: Record<string, unknown>;
    selected_direction: Record<string, unknown>;
    current_plan_markdown: string;
    current_plan_version: number;
    plan_history: PlanMarkdownResponse["plan_history"];
    revision_feedback: string;
    creation_contract?: Record<string, unknown>;
    product_creative_profile?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
  }) => req<PlanMarkdownResponse>(`${FLOW_BASE}/planning/plan/revise`, { method: "POST", body: JSON.stringify(body) }),

  restorePlanMarkdown: (body: {
    intent: CreationIntent;
    current_plan_markdown: string;
    current_plan_version: number;
    plan_history: PlanMarkdownResponse["plan_history"];
    restore_version: number;
    creation_contract?: Record<string, unknown>;
    scene_durations_sec?: number[];
  }) => req<PlanMarkdownResponse>(`${FLOW_BASE}/planning/plan/restore`, { method: "POST", body: JSON.stringify(body) }),

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

  startImageGenerationJob: (body: {
    method: ImagePrepareResponse["method"];
    prompt: string;
    negative_prompt?: string;
    params: Record<string, unknown>;
  }) => req<ImageGenerateJobStartResponse>(`${FLOW_BASE}/image/generate/start`, { method: "POST", body: JSON.stringify(body) }),

  getImageGenerationJob: (jobId: string) =>
    req<ImageGenerateJobStatusResponse>(`${FLOW_BASE}/image/generate/jobs/${encodeURIComponent(jobId)}`),

  pollImageGenerationJob,

  editImageAsset: (body: {
    asset_id: string;
    asset_name?: string;
    asset_group: string;
    source_image_url: string;
    prompt: string;
    materials?: Array<Record<string, unknown>>;
    reference_image_urls?: string[];
    ratio?: string;
    size?: string;
    model?: string | null;
  }) => req<ImageAssetEditResponse>(`${FLOW_BASE}/image/edit-asset`, { method: "POST", body: JSON.stringify(body) }),

  startImageAssetEditJob: (body: {
    asset_id: string;
    asset_name?: string;
    asset_group: string;
    source_image_url: string;
    prompt: string;
    materials?: Array<Record<string, unknown>>;
    reference_image_urls?: string[];
    ratio?: string;
    size?: string;
    model?: string | null;
  }) => req<ImageAssetEditJobStartResponse>(`${FLOW_BASE}/image/edit-asset/start`, { method: "POST", body: JSON.stringify(body) }),

  getImageAssetEditJob: (jobId: string) =>
    req<ImageAssetEditJobStatusResponse>(`${FLOW_BASE}/image/edit-asset/jobs/${encodeURIComponent(jobId)}`),

  pollImageAssetEditJob,

  fuseImageAsset: (body: {
    asset_id: string;
    asset_name?: string;
    asset_group: string;
    source_image_url: string;
    prompt: string;
    materials?: Array<Record<string, unknown>>;
    reference_image_urls?: string[];
    ratio?: string;
    size?: string;
    model?: string | null;
  }) => req<ImageAssetFusionResponse>(`${FLOW_BASE}/image/fuse-asset`, { method: "POST", body: JSON.stringify(body) }),

  startImageAssetFusionJob: (body: {
    asset_id: string;
    asset_name?: string;
    asset_group: string;
    source_image_url: string;
    prompt: string;
    materials?: Array<Record<string, unknown>>;
    reference_image_urls?: string[];
    ratio?: string;
    size?: string;
    model?: string | null;
  }) => req<ImageAssetFusionJobStartResponse>(`${FLOW_BASE}/image/fuse-asset/start`, { method: "POST", body: JSON.stringify(body) }),

  getImageAssetFusionJob: (jobId: string) =>
    req<ImageAssetFusionJobStatusResponse>(`${FLOW_BASE}/image/fuse-asset/jobs/${encodeURIComponent(jobId)}`),

  pollImageAssetFusionJob,

  prepareVideoScenePackages: (body: {
    form_values: Record<string, unknown>;
    plan_markdown: string;
    selected_direction: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
    target_duration_ms?: number;
    creation_contract?: VideoCreationContract | Record<string, unknown>;
  }) => req<PrepareScenePackagesResponse>(`${FLOW_BASE}/video/prepare-scene-packages`, { method: "POST", body: JSON.stringify(body) }),

  startPrepareScenePackagesJob: (body: {
    form_values: Record<string, unknown>;
    plan_markdown: string;
    selected_direction: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
    target_duration_ms?: number;
    creation_contract?: VideoCreationContract | Record<string, unknown>;
  }) =>
    req<PrepareScenePackagesJobStartResponse>(`${FLOW_BASE}/video/prepare-scene-packages/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getPrepareScenePackagesJob: (jobId: string) =>
    req<PrepareScenePackagesJobStatusResponse>(`${FLOW_BASE}/video/prepare-scene-packages/jobs/${encodeURIComponent(jobId)}`),

  pollPrepareScenePackagesJob,

  generateSceneAssets: (body: {
    global_assets?: Record<string, unknown>;
    scene_packages: PrepareScenePackagesResponse["scene_packages"];
    materials?: Array<Record<string, unknown>>;
    image_ratio?: string;
    image_size?: string;
    model?: string | null;
    creation_contract?: VideoCreationContract | Record<string, unknown>;
  }) => req<GenerateSceneAssetsResponse>(`${FLOW_BASE}/video/generate-scene-assets`, { method: "POST", body: JSON.stringify(body) }),

  startSceneAssetsJob: (body: {
    global_assets?: Record<string, unknown>;
    scene_packages: PrepareScenePackagesResponse["scene_packages"];
    materials?: Array<Record<string, unknown>>;
    image_ratio?: string;
    image_size?: string;
    model?: string | null;
    creation_contract?: VideoCreationContract | Record<string, unknown>;
  }) =>
    req<GenerateSceneAssetsJobStartResponse>(`${FLOW_BASE}/video/generate-scene-assets/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getSceneAssetsJob: (jobId: string) =>
    req<GenerateSceneAssetsJobStatusResponse>(`${FLOW_BASE}/video/generate-scene-assets/jobs/${encodeURIComponent(jobId)}`),

  pollSceneAssetsJob,

  startSceneVideosJob: (body: {
    scenes: SceneGenerationPayload[];
    ratio?: string;
    size?: string;
    model?: string | null;
    sound?: string;
    creation_contract?: VideoCreationContract | Record<string, unknown>;
  }) =>
    req<GenerateSceneVideosJobStartResponse>(`${FLOW_BASE}/video/generate-scenes/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getSceneVideosJob: (jobId: string) =>
    req<GenerateSceneVideosJobStatusResponse>(`${FLOW_BASE}/video/generate-scenes/jobs/${encodeURIComponent(jobId)}`),

  pollSceneVideoJob,

  generateSceneVideos: async (body: {
    scenes: SceneGenerationPayload[];
    ratio?: string;
    size?: string;
    model?: string | null;
    sound?: string;
  }) => {
    const started = await api.startSceneVideosJob(body);
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

  mergeSceneVideos: async (body: {
    scene_videos: SceneVideoPayload[];
    duration?: number;
    size?: string;
    model?: string | null;
  }) => {
    const started = await api.startMergeSceneVideosJob(body);
    return pollMergeSceneVideoJob(started.job_id);
  },

  startMergeSceneVideosJob: (body: {
    scene_videos: SceneVideoPayload[];
    duration?: number;
    size?: string;
    model?: string | null;
  }) =>
    req<MergeSceneVideosJobStartResponse>(`${FLOW_BASE}/video/merge/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getMergeSceneVideosJob: (jobId: string) =>
    req<MergeSceneVideosJobStatusResponse>(`${FLOW_BASE}/video/merge/jobs/${encodeURIComponent(jobId)}`),

  pollMergeSceneVideoJob,

  reviewVideoQuality: async (body: {
    merged_video_url: string;
    scene_videos: SceneVideoPayload[];
    scene_packages?: Array<Record<string, unknown>>;
    original_scene_packages?: Array<Record<string, unknown>>;
    plan?: Record<string, unknown>;
    form_values?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    selected_direction?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
    user_feedback?: string | null;
  }) => {
    const started = await api.startVideoQualityReviewJob(body);
    const result = await pollVideoQualityReviewJob(started.job_id);
    return result ?? {
      ok: false,
      endpoint: "/api/creative/video_quality_review",
      task_id: null,
      passed: false,
      score: 0,
      summary_markdown: "",
      quality_report_markdown: "",
      issues: [],
      affected_scene_ids: [],
      target_scene_ids: [],
      excluded_scene_ids: [],
      revision_prompt: "",
      check_results: [],
      error: "视频质检轮询已中断",
      message: "视频质检轮询已中断",
      raw: {},
    };
  },

  startVideoQualityReviewJob: (body: {
    merged_video_url: string;
    scene_videos: SceneVideoPayload[];
    scene_packages?: Array<Record<string, unknown>>;
    original_scene_packages?: Array<Record<string, unknown>>;
    plan?: Record<string, unknown>;
    form_values?: Record<string, unknown>;
    intake_context?: Record<string, unknown>;
    selected_direction?: Record<string, unknown>;
    materials?: Array<Record<string, unknown>>;
    user_feedback?: string | null;
  }) => req<VideoQualityReviewJobStartResponse>(`${FLOW_BASE}/video/quality-review/start`, { method: "POST", body: JSON.stringify(body) }),

  getVideoQualityReviewJob: (jobId: string) =>
    req<VideoQualityReviewJobStatusResponse>(`${FLOW_BASE}/video/quality-review/jobs/${encodeURIComponent(jobId)}`),

  pollVideoQualityReviewJob,

  analyzeStoryboards: (body: {
    prompt?: string;
    materials?: Array<Record<string, unknown>>;
    video_urls?: string[];
  }) => req<AnalyzeStoryboardsResponse>(`${FLOW_BASE}/video/analyze-storyboards`, { method: "POST", body: JSON.stringify(body) }),

  createPptSummaryJob: (body: {
    ppt_topic: string;
    ppt_style: string;
    attachments: Array<Record<string, unknown>>;
    smart_ppt_project_id?: number | null;
  }) =>
    req<PptJobStartResponse>(`${FLOW_BASE}/ppt/summary/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createPptSummaryUpdateJob: (body: {
    original_outline: string;
    modification_opinion: string;
    smart_ppt_project_id: number;
  }) =>
    req<PptJobStartResponse>(`${FLOW_BASE}/ppt/summary/update/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createPptContentJsonJob: (body: {
    original_outline: string;
    ppt_style: string;
    smart_ppt_project_id: number;
  }) =>
    req<PptJobStartResponse>(`${FLOW_BASE}/ppt/content-json/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createPptImagesJob: (body: {
    content_json: unknown;
    smart_ppt_project_id: number;
  }) =>
    req<PptJobStartResponse>(`${FLOW_BASE}/ppt/images/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createPptImageRegenerationJob: (body: {
    page_index: number;
    page_json: Record<string, unknown>;
    smart_ppt_project_id: number;
  }) =>
    req<PptJobStartResponse>(`${FLOW_BASE}/ppt/images/regenerate/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createPptFileJob: (body: {
    file_urls: string[];
    smart_ppt_project_id: number;
  }) =>
    req<PptJobStartResponse>(`${FLOW_BASE}/ppt/file/start`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getPptJob: (jobId: string) =>
    req<PptJobStatusResponse>(`${FLOW_BASE}/ppt/jobs/${encodeURIComponent(jobId)}`),

  pollPptJob,

  startPptSummaryJob: async (body: {
    ppt_topic: string;
    ppt_style: string;
    attachments: Array<Record<string, unknown>>;
    smart_ppt_project_id?: number | null;
  }) => {
    const started = await api.createPptSummaryJob(body);
    return pollPptJob<PptSummaryResult>(started.job_id);
  },

  updatePptSummaryJob: async (body: {
    original_outline: string;
    modification_opinion: string;
    smart_ppt_project_id: number;
  }) => {
    const started = await api.createPptSummaryUpdateJob(body);
    return pollPptJob<PptSummaryResult>(started.job_id);
  },

  startPptContentJsonJob: async (body: {
    original_outline: string;
    ppt_style: string;
    smart_ppt_project_id: number;
  }) => {
    const started = await api.createPptContentJsonJob(body);
    return pollPptJob<PptContentJsonResult>(started.job_id);
  },

  startPptImagesJob: async (body: {
    content_json: unknown;
    smart_ppt_project_id: number;
  }, onStatus?: PptJobStatusCallback) => {
    const started = await api.createPptImagesJob(body);
    return pollPptJob<PptImagesResult>(started.job_id, onStatus);
  },

  regeneratePptImageJob: async (body: {
    page_index: number;
    page_json: Record<string, unknown>;
    smart_ppt_project_id: number;
  }) => {
    const started = await api.createPptImageRegenerationJob(body);
    return pollPptJob<Record<string, unknown>>(started.job_id);
  },

  startPptFileJob: async (body: {
    file_urls: string[];
    smart_ppt_project_id: number;
  }) => {
    const started = await api.createPptFileJob(body);
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
