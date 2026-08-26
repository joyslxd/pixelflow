/** PixelFlow 公开 API 的认证、错误与 JSON 传输边界。 */

import { getBrowserAuthorization } from "@/lib/authStorage";

const AGENT_API_PREFIX = "/agent";

export class AgentApiError extends Error {
  /** 保存固定公开错误码与 HTTP 状态，不保留服务端异常正文。 */

  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`PixelFlow 请求失败：${code}`);
    this.name = "AgentApiError";
  }
}

export function agentApiUrl(path: string): string {
  /** 拼接唯一 Gateway 公开前缀；浏览器永不直连 Sidecar。 */

  return `${AGENT_API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
}

export function agentHeaders(headers?: HeadersInit): Headers {
  /** 仅在当前公开 API 请求边界读取 Authorization，不写入任何业务状态。 */

  const result = new Headers(headers);
  const authorization = getBrowserAuthorization();
  if (authorization) result.set("Authorization", authorization);
  return result;
}

export async function agentRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  /** 调用 Gateway JSON API，并把不可信错误正文收敛为固定公开错误码。 */

  const response = await fetch(agentApiUrl(path), {
    ...init,
    headers: agentHeaders(init.headers),
  });
  if (!response.ok) {
    let code = `http_${response.status}`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const detail = (body as { detail?: unknown }).detail;
        if (typeof detail === "object" && detail !== null && "code" in detail) {
          const candidate = (detail as { code?: unknown }).code;
          if (typeof candidate === "string" && /^[a-z0-9_]{1,120}$/u.test(candidate)) code = candidate;
        }
      }
    } catch {
      // 响应正文不是可靠合同；保留 HTTP 派生错误码即可。
    }
    throw new AgentApiError(response.status, code);
  }
  return response.json() as Promise<T>;
}
