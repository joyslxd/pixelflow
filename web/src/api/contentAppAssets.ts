/** content-app 图片上传与资产库 Client；文件不经过 Gateway、Sidecar 或浏览器业务状态。 */

import { getBrowserAuthorization } from "@/lib/authStorage";
import { contentAppRequestUrl } from "@/lib/contentAppOrigin";

/**
 * content-app 上传入口：同域站点可直连；独立 Agent 前端通过 Nginx 同源 /api 代理，
 * 避免 test-video 只允许自身 Origin 时阻断浏览器 CORS 预检。
 */
function contentAppUrl(path: string): string {
  return contentAppRequestUrl({
    browserOrigin: window.location.origin,
    contentAppOrigin: import.meta.env.VITE_CONTENT_APP_ORIGIN || "",
    path,
  });
}

type ContentAppEnvelope = {
  success?: boolean;
  message?: string;
  error?: string;
  data?: unknown;
  projects?: unknown;
  url?: unknown;
  path?: unknown;
  id?: unknown;
};

export type UploadedContentAppFile = {
  url: string;
  name: string;
  contentType: string;
  assetId?: string;
};

function authorizationHeaders(): HeadersInit {
  const authorization = getBrowserAuthorization();
  if (!authorization) throw new Error("缺少 content-app Authorization，请先登录后再上传参考图。");
  return { Authorization: authorization };
}

function object(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
}

async function contentAppRequest(path: string, init: RequestInit): Promise<ContentAppEnvelope> {
  const response = await fetch(contentAppUrl(path), {
    ...init,
    credentials: "include",
    headers: { ...authorizationHeaders(), ...(init.headers ?? {}) },
  });
  const payload = object(await response.json().catch(() => ({}))) as ContentAppEnvelope;
  if (!response.ok || payload.success === false) {
    throw new Error(typeof payload.message === "string" ? payload.message : "content-app 图片上传失败。");
  }
  return payload;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

async function defaultProjectId(): Promise<string | number> {
  const payload = await contentAppRequest("/api/projects", { method: "GET" });
  const projects = Array.isArray(payload.projects)
    ? payload.projects
    : Array.isArray(object(payload.data).projects) ? object(payload.data).projects as unknown[] : [];
  const first = object(projects[0]);
  const id = first.id;
  if ((typeof id !== "string" && typeof id !== "number") || `${id}`.trim() === "") {
    throw new Error("content-app 中没有可用于保存图片资产的项目。");
  }
  return id;
}

export async function uploadContentAppFile(file: File, displayName = file.name): Promise<UploadedContentAppFile> {
  /** 所有文件直传 content-app；图片额外创建当前用户长期素材资产。 */

  const form = new FormData();
  form.append("file", file);
  const uploaded = await contentAppRequest("/api/upload", { method: "POST", body: form });
  const uploadData = object(uploaded.data);
  const rawUrl = text(uploadData.url) || text(uploadData.path) || text(uploaded.url) || text(uploaded.path);
  // content-app 测试环境会返回 HTTP TOS 地址；该域已验证同时支持 HTTPS，统一写入 Provider 可接收的安全协议。
  const url = rawUrl.replace(/^http:\/\//u, "https://");
  if (!/^https?:\/\//u.test(url)) throw new Error("content-app 上传成功但未返回可引用的图片地址。");

  if (!file.type.startsWith("image/")) return { url, name: displayName, contentType: file.type || "application/octet-stream" };

  const asset = await contentAppRequest("/api/asset/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      assetType: "image",
      assetSource: "upload",
      projectId: await defaultProjectId(),
      name: displayName.slice(0, 255),
      refrenceUrl: url,
    }),
  });
  const assetId = object(asset.data).id ?? asset.id;
  if ((typeof assetId !== "string" && typeof assetId !== "number") || `${assetId}`.trim() === "") {
    throw new Error("content-app 未返回已创建图片资产的身份。");
  }
  return { url, name: displayName, contentType: file.type || "image/*", assetId: String(assetId) };
}
