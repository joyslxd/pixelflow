/** PixelFlow 后端 client(对齐 /api/tasks 契约)。dev 下 /api 由 vite 代理到后端。 */

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
  asset_type: string; // generated_video | jianying_draft | final_video
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
  product_info?: Record<string, unknown>; // product_name, main_image_url, ...
  video_params?: {
    platform?: string;
    duration_sec?: number;
    ratio?: string;
    size?: string;
    business_goal?: string;
  };
  reference_videos?: string[];
  creative_direction?: Record<string, unknown>; // core_message, creative_style, ...
  user_message?: string;
  auto_start?: boolean;
}

export interface TaskEvent {
  id: number;
  event: string; // task_created | phase_change | brief_ready | task_done | ...
  data: Record<string, unknown>;
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string>) };
  // 双提交 CSRF:写操作回传 csrf_token cookie。
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = csrfToken();
  const res = await fetch(`/api${path}`, { credentials: "include", ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, `${res.status} ${path}: ${text.slice(0, 200)}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** 认证:本地 session(cookie) + CSRF。 */
export const auth = {
  async me(): Promise<{ authenticated: boolean }> {
    try {
      await req("/tasks?limit=1");
      return { authenticated: true };
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return { authenticated: false };
      throw e;
    }
  },
  async login(email: string, password: string): Promise<void> {
    const body = new URLSearchParams({ username: email, password });
    const res = await fetch("/api/v1/auth/login/local", { method: "POST", credentials: "include", body });
    if (!res.ok) {
      const t = await res.text().catch(() => "");
      throw new ApiError(res.status, t.slice(0, 200) || "登录失败");
    }
  },
  async logout(): Promise<void> {
    await req("/v1/auth/logout", { method: "POST" }).catch(() => {});
  },
};

export const api = {
  createTask: (body: CreateTaskBody) =>
    req<TaskResponse>("/tasks", { method: "POST", body: JSON.stringify({ task_type: "ecom_video", auto_start: true, ...body }) }),

  getTask: (id: string) => req<TaskResponse>(`/tasks/${id}`),

  getResult: (id: string) =>
    req<{ task_id: string; status: string; phase: string; result: Record<string, unknown>; error: string | null }>(`/tasks/${id}/result`),

  listAssets: (id: string) => req<AssetResponse[]>(`/tasks/${id}/assets`),

  confirmBrief: (id: string, approved: boolean) =>
    req<TaskResponse>(`/tasks/${id}/brief/confirm`, { method: "POST", body: JSON.stringify({ approved }) }),

  reviseBrief: (id: string, briefPatch: Record<string, unknown>, feedback: string) =>
    req<TaskResponse>(`/tasks/${id}/brief/revise`, { method: "POST", body: JSON.stringify({ brief_patch: briefPatch, feedback }) }),

  eventsHistory: (id: string, afterId?: number) =>
    req<{ data: TaskEvent[] }>(`/tasks/${id}/events/history${afterId != null ? `?after_id=${afterId}` : ""}`),
};

/**
 * 订阅任务 SSE 事件流。返回取消函数。
 * 后端事件格式:`event: <name>` + `data: <json>` + `id: <num>`。
 */
export function subscribeTaskEvents(
  taskId: string,
  onEvent: (e: TaskEvent) => void,
  afterId?: number,
): () => void {
  const url = `/api/tasks/${taskId}/events${afterId != null ? `?after_id=${afterId}` : ""}`;
  const es = new EventSource(url);
  const handler = (ev: MessageEvent) => {
    try {
      onEvent({
        id: Number(ev.lastEventId || 0),
        event: (ev as MessageEvent).type,
        data: JSON.parse(ev.data),
      });
    } catch {
      /* 忽略非 JSON 心跳 */
    }
  };
  // 后端用具名事件(event: phase_change 等),需对已知类型分别监听;
  // 同时监听默认 message 兜底。
  const NAMES = [
    "task_created",
    "run_started",
    "phase_change",
    "brief_ready",
    "brief_confirmed",
    "brief_rejected",
    "brief_revised",
    "preferences_updated",
    "task_done",
    "run_finished",
    "task_failed",
  ];
  NAMES.forEach((n) => es.addEventListener(n, handler as EventListener));
  es.addEventListener("message", handler as EventListener);
  return () => es.close();
}
