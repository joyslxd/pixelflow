import { getBrowserAuthorization, normalizeAuthorization } from "../authStorage.js";
import type {
  InterruptResponseRequest,
  JsonObject,
  JsonValue,
  TurnStartRequest,
  VideoAgentConfirmationResponseRequest,
  VideoAgentQuotaResponseRequest,
  VideoAgentScriptSaveRequest,
} from "./contracts.js";

const AGENT_API_PREFIX = "/agent2";
const AUTHORIZATION_READY_EVENT = "contentAppAuthorizationReady";
const AUTHORIZATION_WAIT_TIMEOUT_MS = 2500;

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type AuthorizationProvider = () => string | Promise<string>;

export interface SupervisorApiTransportOptions {
  fetchImpl?: FetchLike;
  getAuthorization?: AuthorizationProvider;
}

export interface SupervisorRequestOptions {
  signal?: AbortSignal;
}

export interface SupervisorApiTransport {
  getSnapshot<TResponse extends JsonValue = JsonObject>(
    conversationId: string,
    options?: SupervisorRequestOptions,
  ): Promise<TResponse>;
  startTurn<TResponse extends JsonValue = JsonObject>(
    conversationId: string,
    request: TurnStartRequest,
    options?: SupervisorRequestOptions,
  ): Promise<TResponse>;
  respondToInterrupt<TResponse extends JsonValue = JsonObject>(
    conversationId: string,
    interruptId: string,
    request: InterruptResponseRequest,
    options?: SupervisorRequestOptions,
  ): Promise<TResponse>;
  respondToVideoAgentConfirmation<TResponse extends JsonValue = JsonObject>(
    conversationId: string,
    confirmationId: string,
    request: VideoAgentConfirmationResponseRequest,
    options?: SupervisorRequestOptions,
  ): Promise<TResponse>;
  respondToVideoAgentQuota<TResponse extends JsonValue = JsonObject>(
    conversationId: string,
    quotaInterruptId: string,
    request: VideoAgentQuotaResponseRequest,
    options?: SupervisorRequestOptions,
  ): Promise<TResponse>;
  saveVideoAgentScript<TResponse extends JsonValue = JsonObject>(
    conversationId: string,
    request: VideoAgentScriptSaveRequest,
    options?: SupervisorRequestOptions,
  ): Promise<TResponse>;
  getRunStatus<TResponse extends JsonValue = JsonObject>(
    conversationId: string,
    runId: string,
    options?: SupervisorRequestOptions,
  ): Promise<TResponse>;
}

export class SupervisorApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "SupervisorApiError";
  }
}

function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  if (signal.reason !== undefined) throw signal.reason;
  throw new DOMException("请求已取消", "AbortError");
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError";
}

function waitForAbortable<T>(value: T | Promise<T>, signal?: AbortSignal): Promise<T> {
  throwIfAborted(signal);
  if (!signal) return Promise.resolve(value);

  return new Promise((resolve, reject) => {
    let settled = false;
    const resolveOnce = (result: T) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      resolve(result);
    };
    const rejectOnce = (error: unknown) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      reject(error);
    };
    const onAbort = () => rejectOnce(signal.reason ?? new DOMException("请求已取消", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
    Promise.resolve(value).then(resolveOnce, rejectOnce);
  });
}

function waitForBrowserAuthorization(signal?: AbortSignal): Promise<string> {
  const current = getBrowserAuthorization();
  if (current || typeof window === "undefined") return Promise.resolve(current);

  return new Promise((resolve, reject) => {
    let settled = false;
    let timer: number | undefined;
    const cleanup = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener(AUTHORIZATION_READY_EVENT, onReady);
      signal?.removeEventListener("abort", onAbort);
    };
    const finish = (authorization: string) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(authorization);
    };
    const onReady = () => finish(getBrowserAuthorization());
    const onAbort = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(signal?.reason ?? new DOMException("请求已取消", "AbortError"));
    };

    throwIfAborted(signal);
    window.addEventListener(AUTHORIZATION_READY_EVENT, onReady, { once: true });
    signal?.addEventListener("abort", onAbort, { once: true });
    timer = window.setTimeout(() => finish(getBrowserAuthorization()), AUTHORIZATION_WAIT_TIMEOUT_MS);
  });
}

export function createSupervisorApiTransport(
  options: SupervisorApiTransportOptions = {},
): SupervisorApiTransport {
  const fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
  const getAuthorization = options.getAuthorization;

  async function request<TResponse extends JsonValue>(
    path: string,
    method: "GET" | "POST" | "PUT",
    body: InterruptResponseRequest | JsonObject | TurnStartRequest | undefined,
    requestOptions: SupervisorRequestOptions,
  ): Promise<TResponse> {
    throwIfAborted(requestOptions.signal);
    const rawAuthorization = getAuthorization
      ? await waitForAbortable(getAuthorization(), requestOptions.signal)
      : await waitForBrowserAuthorization(requestOptions.signal);
    const authorization = normalizeAuthorization(rawAuthorization);
    throwIfAborted(requestOptions.signal);
    if (!authorization) {
      throw new SupervisorApiError(
        401,
        "缺少 content-app Authorization，请先从 content-app 登录入口进入 PixelFlow",
      );
    }

    const headers: Record<string, string> = { Authorization: authorization };
    const init: RequestInit = {
      method,
      headers,
      signal: requestOptions.signal,
    };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }

    const response = await fetchImpl(`${AGENT_API_PREFIX}${path}`, init);
    if (!response.ok) {
      throw new SupervisorApiError(
        response.status,
        `Supervisor API 请求失败（HTTP ${response.status}）`,
      );
    }
    if (response.status === 204) {
      throw new SupervisorApiError(502, "Supervisor API 返回空响应");
    }
    try {
      return await response.json() as TResponse;
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new SupervisorApiError(502, "Supervisor API 返回无效 JSON 响应");
    }
  }

  return {
    getSnapshot: (conversationId, requestOptions = {}) => request(
      `/conversations/${encodeURIComponent(conversationId)}/agent-snapshot`,
      "GET",
      undefined,
      requestOptions,
    ),
    startTurn: (conversationId, turnRequest, requestOptions = {}) => request(
      `/conversations/${encodeURIComponent(conversationId)}/turns/start`,
      "POST",
      turnRequest,
      requestOptions,
    ),
    respondToInterrupt: (conversationId, interruptId, responseRequest, requestOptions = {}) => request(
      `/conversations/${encodeURIComponent(conversationId)}/interrupts/${encodeURIComponent(interruptId)}/responses`,
      "POST",
      responseRequest,
      requestOptions,
    ),
    respondToVideoAgentConfirmation: (
      conversationId,
      confirmationId,
      responseRequest,
      requestOptions = {},
    ) => request(
      `/conversations/${encodeURIComponent(conversationId)}/video-agent/confirmations/${encodeURIComponent(confirmationId)}/responses`,
      "POST",
      responseRequest,
      requestOptions,
    ),
    respondToVideoAgentQuota: (
      conversationId,
      quotaInterruptId,
      responseRequest,
      requestOptions = {},
    ) => request(
      `/conversations/${encodeURIComponent(conversationId)}/video-agent/quota/${encodeURIComponent(quotaInterruptId)}/responses`,
      "POST",
      responseRequest,
      requestOptions,
    ),
    saveVideoAgentScript: (
      conversationId,
      body,
      requestOptions = {},
    ) => request(
      `/conversations/${encodeURIComponent(conversationId)}/video-agent/script`,
      "PUT",
      body,
      requestOptions,
    ),
    getRunStatus: (conversationId, runId, requestOptions = {}) => request(
      `/conversations/${encodeURIComponent(conversationId)}/turns/jobs/${encodeURIComponent(runId)}`,
      "GET",
      undefined,
      requestOptions,
    ),
  };
}

export const supervisorApi = createSupervisorApiTransport();
