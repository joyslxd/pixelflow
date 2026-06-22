/** PixelFlow 后端 API Client，对齐 /agent/flows 契约。开发环境下 /agent 由 Vite 代理到后端。 */

import { getBrowserAuthorization } from "@/lib/authStorage";

const AGENT_API_PREFIX = "/agent";
const FLOW_BASE = "/flows";

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

function authHeaders(): Record<string, string> {
  const authorization = getAuthorizationHeader();
  if (!authorization) {
    throw new ApiError(401, "缺少 content-app Authorization，请先从 content-app 登录入口进入 PixelFlow");
  }
  return { Authorization: authorization };
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  // 统一请求模板：自动带 content-app Authorization，并把非 2xx 响应转换成 ApiError。
  // 可以把它类比成前端侧的后端 Client 拦截器。
  const headers: Record<string, string> = { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers as Record<string, string>) };
  const res = await fetch(`${AGENT_API_PREFIX}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, `${res.status} ${path}: ${text.slice(0, 200)}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  getCurrentUser: () => req<{ authenticated: boolean; id: string; username: string }>("/auth/me"),

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
    const res = await fetch(`${AGENT_API_PREFIX}${path}`, { headers: authHeaders() });
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
      const res = await fetch(url, { headers: authHeaders(), signal: controller.signal });
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
