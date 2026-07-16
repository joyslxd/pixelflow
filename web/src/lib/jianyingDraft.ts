export type JianyingDraftStatus = "not_configured" | "queued" | "running" | "succeeded" | "failed" | "timeout";

export interface JianyingDraftCapability {
  available: boolean;
  reason: string;
  poll_interval_seconds: number;
}

export interface JianyingDraftScene {
  scene_id: string;
  scene_index: number;
  video_url: string;
  task_id?: string | null;
}

export interface JianyingDraftStartRequest {
  conversation_id: string;
  storyboard_version_id: string;
  scenes: JianyingDraftScene[];
  video_task_id?: string | null;
  project_name?: string | null;
  retry_failed?: boolean;
}

export interface JianyingDraftJobResponse {
  status: JianyingDraftStatus;
  job_id: string | null;
  provider_task_id: string | null;
  conversation_id: string | null;
  storyboard_version_id: string | null;
  download_url: string | null;
  file_name: string | null;
  expire_at: string | null;
  message: string;
}

export interface DraftButtonScene {
  scene_id?: string;
  scene_index?: number | null;
  task_id?: string | null;
  video_url?: string | null;
}

export interface DraftButtonStateInput {
  providerAvailable: boolean;
  pendingJob?: Pick<JianyingDraftJobResponse, "status"> | null;
  scenes: readonly DraftButtonScene[];
  failedSceneIds?: readonly string[];
  result?: JianyingDraftJobResponse | null;
  now?: Date;
}

export interface DraftButtonState {
  enabled: boolean;
  label: string;
  reason: string;
}

const FNV1A64_OFFSET_BASIS = 0xCBF29CE484222325n;
const FNV1A64_PRIME = 0x100000001B3n;
const FNV1A64_BITS = 64;

/** 按对话和分镜版本阻止浏览器端重复启动任务。 */
export class JianyingDraftStartGuard {
  private readonly inFlightKeys = new Set<string>();

  tryAcquire(conversationId: string, storyboardVersionId: string): boolean {
    const key = `${conversationId}\u0000${storyboardVersionId}`;
    if (this.inFlightKeys.has(key)) return false;
    this.inFlightKeys.add(key);
    return true;
  }

  release(conversationId: string, storyboardVersionId: string): void {
    this.inFlightKeys.delete(`${conversationId}\u0000${storyboardVersionId}`);
  }
}

export interface PatchJianyingDraftTargetConversationOptions<T> {
  targetConversationId: string;
  expectedJobId: string;
  isCurrentConversation: (conversationId: string) => boolean;
  syncCurrentConversation: (result?: T) => void;
  patchTargetConversation: (conversationId: string, expectedJobId: string) => Promise<T>;
}

/** 将异步持久化始终绑定到触发任务的对话，并在 await 后重新校验当前对话。 */
export async function patchJianyingDraftTargetConversation<T>({
  targetConversationId,
  expectedJobId,
  isCurrentConversation,
  syncCurrentConversation,
  patchTargetConversation,
}: PatchJianyingDraftTargetConversationOptions<T>): Promise<T> {
  if (isCurrentConversation(targetConversationId)) syncCurrentConversation();
  const result = await patchTargetConversation(targetConversationId, expectedJobId);
  if (isCurrentConversation(targetConversationId)) syncCurrentConversation(result);
  return result;
}

export interface JianyingDraftConversationContextPatch {
  pendingJianyingDraftJob: unknown | null;
  jianyingDraftRecords: Record<string, JianyingDraftJobResponse>;
  jianyingDraftJobResumeError?: string | null;
}

function draftRecordsFromContext(value: unknown): Record<string, JianyingDraftJobResponse> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, JianyingDraftJobResponse>)
    : {};
}

/** 仅更新目标对话中的剪映草稿字段，保留其余业务上下文。 */
export function patchJianyingDraftConversationContext(
  context: Record<string, unknown>,
  {
    pendingJianyingDraftJob,
    jianyingDraftRecords,
    jianyingDraftJobResumeError,
  }: JianyingDraftConversationContextPatch,
): Record<string, unknown> {
  const mergedRecords = {
    ...draftRecordsFromContext(context.jianying_draft_records),
    ...draftRecordsFromContext(context.jianyingDraftRecords),
    ...jianyingDraftRecords,
  };
  const patchedContext = {
    ...context,
    pendingJianyingDraftJob,
    pending_jianying_draft_job: pendingJianyingDraftJob,
    jianyingDraftRecords: mergedRecords,
    jianying_draft_records: mergedRecords,
  };
  return jianyingDraftJobResumeError === undefined
    ? patchedContext
    : { ...patchedContext, jianying_draft_job_resume_error: jianyingDraftJobResumeError };
}

function canonicalVideoUrl(value: unknown): string {
  if (typeof value !== "string") throw new TypeError("video_url must be an HTTPS URL");
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") {
      throw new TypeError("video_url must be an HTTPS URL");
    }
    return url.href;
  } catch {
    throw new TypeError("video_url must be an HTTPS URL");
  }
}

function isHttpsUrl(value: unknown): boolean {
  try {
    return typeof value === "string" && new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

export function jianyingDraftPublicErrorMessage(stage: "capability" | "start" | "poll"): string {
  const messages = {
    capability: "暂时无法获取剪映草稿服务状态，请稍后重试。",
    start: "剪映草稿任务启动失败，请稍后重试。",
    poll: "继续查询剪映草稿任务失败，请稍后重试。",
  };
  return messages[stage];
}

function normalizedScene(scene: JianyingDraftScene): Required<JianyingDraftScene> {
  if (!scene || typeof scene.scene_id !== "string" || scene.scene_id.length === 0) {
    throw new TypeError("scene_id must be a non-empty string");
  }
  if (!Number.isInteger(scene.scene_index) || scene.scene_index < 1) {
    throw new TypeError("scene_index must be an integer greater than or equal to 1");
  }
  if (scene.task_id != null && typeof scene.task_id !== "string") {
    throw new TypeError("task_id must be a string or null");
  }
  return {
    scene_id: scene.scene_id,
    scene_index: scene.scene_index,
    task_id: scene.task_id ?? "",
    video_url: canonicalVideoUrl(scene.video_url),
  };
}

/** Computes the same stable storyboard version ID used by the Python gateway. */
export function storyboardVersionId(scenes: readonly JianyingDraftScene[]): string {
  if (scenes.length === 0) throw new Error("scenes cannot be empty");

  const normalized = scenes.map(normalizedScene);
  const indexes = new Set<number>();
  for (const scene of normalized) {
    if (indexes.has(scene.scene_index)) throw new Error("scene_index values must be unique");
    indexes.add(scene.scene_index);
  }

  const canonical = JSON.stringify(
    normalized
      .sort((left, right) => left.scene_index - right.scene_index)
      .map(({ scene_id, scene_index, task_id, video_url }) => ({ scene_id, scene_index, task_id, video_url })),
  );
  let value = FNV1A64_OFFSET_BASIS;
  for (const byte of new TextEncoder().encode(canonical)) {
    value = BigInt.asUintN(FNV1A64_BITS, (value ^ BigInt(byte)) * FNV1A64_PRIME);
  }
  return `storyboard-${value.toString(16).padStart(16, "0")}`;
}

function hasUsableVideoUrl(scene: DraftButtonScene): boolean {
  try {
    canonicalVideoUrl(scene.video_url);
    return true;
  } catch {
    return false;
  }
}

export function isJianyingDraftSucceededResultValid(
  result: (Pick<JianyingDraftJobResponse, "status" | "expire_at"> & Partial<Pick<JianyingDraftJobResponse, "download_url">>) | null | undefined,
  now = new Date(),
): boolean {
  if (result?.status !== "succeeded") return false;
  if (result.download_url && !isHttpsUrl(result.download_url)) return false;
  if (!result.expire_at) return true;
  const expireAt = Date.parse(result.expire_at);
  return !Number.isFinite(expireAt) || expireAt > now.getTime();
}

/** Resolves the final-video draft action without coupling it to Workspace state. */
export function draftButtonState({
  providerAvailable,
  pendingJob,
  scenes,
  failedSceneIds = [],
  result,
  now = new Date(),
}: DraftButtonStateInput): DraftButtonState {
  if (!providerAvailable) {
    return { enabled: false, label: "生成剪映草稿", reason: "剪映草稿服务待接入" };
  }
  if (pendingJob) {
    return { enabled: false, label: "剪映草稿生成中", reason: "剪映草稿正在生成中" };
  }
  if (scenes.length === 0) {
    return { enabled: false, label: "生成剪映草稿", reason: "暂无可用视频分镜" };
  }
  if (failedSceneIds.length > 0) {
    return { enabled: false, label: "重新生成剪映草稿", reason: "存在生成失败的分镜" };
  }
  if (!scenes.every(hasUsableVideoUrl)) {
    return { enabled: false, label: "生成剪映草稿", reason: "存在缺少视频地址的分镜" };
  }
  if (result?.status === "succeeded") {
    if (!isJianyingDraftSucceededResultValid(result, now)) {
      if (result.download_url && !isHttpsUrl(result.download_url)) {
        return { enabled: true, label: "重新生成剪映草稿", reason: "剪映草稿下载地址无效，请重新生成" };
      }
      return { enabled: true, label: "重新生成剪映草稿", reason: "剪映草稿已过期，请重新生成" };
    }
    return result.download_url
      ? { enabled: true, label: "下载剪映草稿", reason: "剪映草稿已生成" }
      : { enabled: false, label: "剪映草稿已生成", reason: "剪映草稿已生成" };
  }
  if (result?.status === "failed" || result?.status === "timeout") {
    return {
      enabled: true,
      label: "重新生成剪映草稿",
      reason: result.message || "剪映草稿生成失败，请重新生成",
    };
  }
  return { enabled: true, label: "生成剪映草稿", reason: "" };
}
