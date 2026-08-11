import { getBrowserAuthorization, normalizeAuthorization } from "../authStorage.js";
import {
  parseAgentEventEnvelope,
  type AgentEventEnvelope,
} from "./contracts.js";

const AGENT_API_PREFIX = "/agent2";
const AUTHORIZATION_READY_EVENT = "contentAppAuthorizationReady";
const AUTHORIZATION_WAIT_TIMEOUT_MS = 2500;
const DEFAULT_RECONNECT_DELAY_MS = 1000;
const DEFAULT_MAX_FRAME_CHARACTERS = 1024 * 1024;
const RECENT_EVENT_ID_LIMIT = 1024;

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type AuthorizationProvider = () => string | Promise<string>;
type ReconnectWaiter = (delayMs: number, signal: AbortSignal) => Promise<void>;

export interface SupervisorEventResumePoint {
  cursor: string | null;
  sequence: number;
}

export interface SupervisorSequenceGap {
  expectedSequence: number;
  receivedSequence: number;
  cursor: string | null;
}

export interface SupervisorEventStreamClientOptions {
  fetchImpl?: FetchLike;
  getAuthorization?: AuthorizationProvider;
  reconnectDelayMs?: number;
  maxReconnectAttempts?: number;
  maxFrameCharacters?: number;
  waitForReconnect?: ReconnectWaiter;
}

export interface SupervisorEventSubscriptionOptions extends SupervisorEventResumePoint {
  conversationId: string;
  signal?: AbortSignal;
  onEvent(event: AgentEventEnvelope): void;
  reloadSnapshot(
    gap: SupervisorSequenceGap,
    signal: AbortSignal,
  ): Promise<SupervisorEventResumePoint>;
  onError(error: SupervisorEventStreamError): void;
}

export interface SupervisorEventSubscription {
  close(): void;
  readonly done: Promise<void>;
}

export interface SupervisorEventStreamClient {
  subscribe(options: SupervisorEventSubscriptionOptions): SupervisorEventSubscription;
}

export class SupervisorEventStreamError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "SupervisorEventStreamError";
  }
}

interface StreamState extends SupervisorEventResumePoint {
  recentEventIds: Set<string>;
  recentEventIdQueue: string[];
}

function throwIfAborted(signal: AbortSignal): void {
  if (!signal.aborted) return;
  if (signal.reason !== undefined) throw signal.reason;
  throw new DOMException("请求已取消", "AbortError");
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError";
}

function waitForAbortable<T>(value: T | Promise<T>, signal: AbortSignal): Promise<T> {
  throwIfAborted(signal);
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

function waitForBrowserAuthorization(signal: AbortSignal): Promise<string> {
  const current = getBrowserAuthorization();
  if (current || typeof window === "undefined") return Promise.resolve(current);

  return new Promise((resolve, reject) => {
    let settled = false;
    let timer: number | undefined;
    const cleanup = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener(AUTHORIZATION_READY_EVENT, onReady);
      signal.removeEventListener("abort", onAbort);
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
      reject(signal.reason ?? new DOMException("请求已取消", "AbortError"));
    };

    throwIfAborted(signal);
    window.addEventListener(AUTHORIZATION_READY_EVENT, onReady, { once: true });
    signal.addEventListener("abort", onAbort, { once: true });
    timer = window.setTimeout(() => finish(getBrowserAuthorization()), AUTHORIZATION_WAIT_TIMEOUT_MS);
  });
}

function defaultReconnectWait(delayMs: number, signal: AbortSignal): Promise<void> {
  throwIfAborted(signal);
  if (delayMs === 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason ?? new DOMException("请求已取消", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function validateResumePoint(value: SupervisorEventResumePoint): SupervisorEventResumePoint {
  const cursorValid = value.cursor === null
    || (typeof value.cursor === "string" && value.cursor.trim().length > 0);
  if (!cursorValid || !Number.isSafeInteger(value.sequence) || value.sequence < 0) {
    throw new SupervisorEventStreamError(502, "Supervisor Snapshot 返回了无效恢复点");
  }
  return { cursor: value.cursor, sequence: value.sequence };
}

function buildEventUrl(conversationId: string, cursor: string | null): string {
  const path = `${AGENT_API_PREFIX}/conversations/${encodeURIComponent(conversationId)}/agent-events`;
  if (cursor === null) return path;
  const query = new URLSearchParams({ cursor });
  return `${path}?${query.toString()}`;
}

function contentTypeIsEventStream(response: Response): boolean {
  return response.headers.get("Content-Type")
    ?.split(";", 1)[0]
    ?.trim()
    .toLowerCase() === "text/event-stream";
}

function parseSseData(block: string): string | null {
  const dataLines: string[] = [];
  for (const line of block.split(/\r\n|\n|\r/u)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator >= 0 ? line.slice(0, separator) : line;
    let value = separator >= 0 ? line.slice(separator + 1) : "";
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") dataLines.push(value);
  }
  return dataLines.length > 0 ? dataLines.join("\n") : null;
}

function nextFrameBoundary(buffer: string): RegExpExecArray | null {
  return /\r\n\r\n|\n\n|\r\r/u.exec(buffer);
}

function rememberEventId(state: StreamState, eventId: string): void {
  state.recentEventIds.add(eventId);
  state.recentEventIdQueue.push(eventId);
  if (state.recentEventIdQueue.length <= RECENT_EVENT_ID_LIMIT) return;
  const oldest = state.recentEventIdQueue.shift();
  if (oldest !== undefined) state.recentEventIds.delete(oldest);
}

async function applyFrame(
  block: string,
  state: StreamState,
  options: SupervisorEventSubscriptionOptions,
  signal: AbortSignal,
): Promise<"continue" | "reconnect"> {
  const data = parseSseData(block);
  if (data === null) return "continue";

  let event: AgentEventEnvelope;
  try {
    event = parseAgentEventEnvelope(JSON.parse(data));
  } catch {
    throw new SupervisorEventStreamError(502, "Supervisor 事件流返回了无效事件");
  }
  if (event.conversation_id !== options.conversationId) {
    throw new SupervisorEventStreamError(502, "Supervisor 事件流返回了其他对话的事件");
  }
  if (state.recentEventIds.has(event.event_id) || event.sequence <= state.sequence) {
    return "continue";
  }
  if (event.sequence > state.sequence + 1) {
    let snapshotResumePoint: SupervisorEventResumePoint;
    try {
      snapshotResumePoint = await waitForAbortable(options.reloadSnapshot({
        expectedSequence: state.sequence + 1,
        receivedSequence: event.sequence,
        cursor: state.cursor,
      }, signal), signal);
    } catch (error) {
      if (signal.aborted || isAbortError(error)) throw error;
      throw new SupervisorEventStreamError(0, "Supervisor Snapshot 恢复失败");
    }
    const resumePoint = validateResumePoint(snapshotResumePoint);
    state.cursor = resumePoint.cursor;
    state.sequence = resumePoint.sequence;
    state.recentEventIds.clear();
    state.recentEventIdQueue.length = 0;
    return "reconnect";
  }

  try {
    options.onEvent(event);
  } catch {
    throw new SupervisorEventStreamError(0, "Supervisor 事件处理失败");
  }
  throwIfAborted(signal);
  state.cursor = event.cursor;
  state.sequence = event.sequence;
  rememberEventId(state, event.event_id);
  return "continue";
}

async function consumeEventStream(
  response: Response,
  state: StreamState,
  options: SupervisorEventSubscriptionOptions,
  signal: AbortSignal,
  maxFrameCharacters: number,
): Promise<void> {
  if (!response.ok) {
    throw new SupervisorEventStreamError(
      response.status,
      `Supervisor 事件流连接失败（HTTP ${response.status}）`,
    );
  }
  if (!response.body || !contentTypeIsEventStream(response)) {
    throw new SupervisorEventStreamError(502, "Supervisor 事件流返回了无效响应");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let readerFinished = false;
  try {
    while (true) {
      throwIfAborted(signal);
      const { done, value } = await reader.read();
      if (done) {
        readerFinished = true;
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      let boundary = nextFrameBoundary(buffer);
      while (boundary !== null) {
        if (boundary.index > maxFrameCharacters) {
          throw new SupervisorEventStreamError(502, "Supervisor 事件流单个事件过大");
        }
        const block = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (await applyFrame(block, state, options, signal) === "reconnect") {
          await reader.cancel().catch(() => undefined);
          readerFinished = true;
          return;
        }
        boundary = nextFrameBoundary(buffer);
      }
      if (buffer.length > maxFrameCharacters) {
        throw new SupervisorEventStreamError(502, "Supervisor 事件流单个事件过大");
      }
    }

    decoder.decode();
  } finally {
    if (!readerFinished) await reader.cancel(signal.reason).catch(() => undefined);
    reader.releaseLock();
  }
}

export function createSupervisorEventStreamClient(
  clientOptions: SupervisorEventStreamClientOptions = {},
): SupervisorEventStreamClient {
  const fetchImpl = clientOptions.fetchImpl ?? ((input, init) => fetch(input, init));
  const getAuthorization = clientOptions.getAuthorization;
  const reconnectDelayMs = clientOptions.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS;
  const maxReconnectAttempts = clientOptions.maxReconnectAttempts ?? Number.POSITIVE_INFINITY;
  const maxFrameCharacters = clientOptions.maxFrameCharacters ?? DEFAULT_MAX_FRAME_CHARACTERS;
  const waitForReconnect = clientOptions.waitForReconnect ?? defaultReconnectWait;

  if (!Number.isFinite(reconnectDelayMs) || reconnectDelayMs < 0) {
    throw new TypeError("重连等待时间必须是非负有限数");
  }
  if (!(maxReconnectAttempts === Number.POSITIVE_INFINITY
    || (Number.isSafeInteger(maxReconnectAttempts) && maxReconnectAttempts >= 0))) {
    throw new TypeError("最大重连次数必须是非负整数或无穷大");
  }
  if (!Number.isSafeInteger(maxFrameCharacters) || maxFrameCharacters < 1) {
    throw new TypeError("事件大小上限必须是正整数");
  }

  return {
    subscribe(subscriptionOptions) {
      if (!subscriptionOptions.conversationId.trim()) {
        throw new TypeError("对话 ID 不能为空");
      }
      const initialResumePoint = validateResumePoint(subscriptionOptions);
      const state: StreamState = {
        ...initialResumePoint,
        recentEventIds: new Set(),
        recentEventIdQueue: [],
      };
      const controller = new AbortController();
      const externalSignal = subscriptionOptions.signal;
      const onExternalAbort = () => controller.abort(
        externalSignal?.reason ?? new DOMException("请求已取消", "AbortError"),
      );
      if (externalSignal?.aborted) onExternalAbort();
      else externalSignal?.addEventListener("abort", onExternalAbort, { once: true });

      const done = (async () => {
        let reconnectAttempts = 0;
        while (!controller.signal.aborted) {
          try {
            const rawAuthorization = getAuthorization
              ? await waitForAbortable(getAuthorization(), controller.signal)
              : await waitForBrowserAuthorization(controller.signal);
            const authorization = normalizeAuthorization(rawAuthorization);
            if (!authorization) {
              throw new SupervisorEventStreamError(
                401,
                "缺少 content-app Authorization，请先从 content-app 登录入口进入 PixelFlow",
              );
            }

            const response = await fetchImpl(buildEventUrl(
              subscriptionOptions.conversationId,
              state.cursor,
            ), {
              method: "GET",
              headers: {
                Accept: "text/event-stream",
                Authorization: authorization,
              },
              signal: controller.signal,
            });
            await consumeEventStream(
              response,
              state,
              subscriptionOptions,
              controller.signal,
              maxFrameCharacters,
            );
            reconnectAttempts = 0;
          } catch (error) {
            if (isAbortError(error) || controller.signal.aborted) return;
            if (error instanceof SupervisorEventStreamError) {
              subscriptionOptions.onError(error);
              return;
            }
            reconnectAttempts += 1;
            if (reconnectAttempts > maxReconnectAttempts) {
              subscriptionOptions.onError(new SupervisorEventStreamError(
                0,
                "Supervisor 事件流连接中断，无法自动恢复",
              ));
              return;
            }
          }
          await waitForReconnect(reconnectDelayMs, controller.signal).catch((error: unknown) => {
            if (!isAbortError(error)) throw error;
          });
        }
      })().catch((error: unknown) => {
        if (isAbortError(error) || controller.signal.aborted) return;
        subscriptionOptions.onError(new SupervisorEventStreamError(
          0,
          "Supervisor 事件流连接中断，无法自动恢复",
        ));
      }).finally(() => {
        externalSignal?.removeEventListener("abort", onExternalAbort);
      });

      return {
        close: () => controller.abort(new DOMException("请求已取消", "AbortError")),
        done,
      };
    },
  };
}

export const supervisorEventStream = createSupervisorEventStreamClient();
