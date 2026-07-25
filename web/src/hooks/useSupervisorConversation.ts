import {
  useCallback,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";

import {
  supervisorApi,
  type SupervisorApiTransport,
} from "../lib/supervisor/api.js";
import type {
  JsonObject,
  JsonValue,
  TurnStartRequest,
} from "../lib/supervisor/contracts.js";
import {
  supervisorEventStream,
  type SupervisorEventStreamClient,
  type SupervisorEventSubscription,
  type SupervisorEventResumePoint,
} from "../lib/supervisor/events.js";
import {
  createSupervisorRuntimeState,
  supervisorRuntimeReducer,
  type SupervisorRuntimeAction,
  type SupervisorRuntimeProjection,
  type SupervisorRuntimeState,
} from "../lib/supervisor/reducer.js";

export type SupervisorSnapshotProjector = (
  snapshot: JsonValue,
  conversationId: string,
) => SupervisorRuntimeProjection;

export interface SupervisorConversationControllerOptions {
  conversationId: string;
  api?: SupervisorApiTransport;
  eventStream?: SupervisorEventStreamClient;
  projectSnapshot?: SupervisorSnapshotProjector;
}

export interface SupervisorConversationController {
  getState(): SupervisorRuntimeState;
  getContextVersion(): number | null;
  subscribe(listener: () => void): () => void;
  start(): Promise<void>;
  refreshSnapshot(): Promise<SupervisorEventResumePoint>;
  startTurn(request: TurnStartRequest): Promise<JsonValue>;
  respondToInterrupt(interruptId: string, request: JsonObject): Promise<JsonValue>;
  getRunStatus(runId: string): Promise<JsonValue>;
  dispose(): void;
}

export interface UseSupervisorConversationOptions {
  enabled?: boolean;
  api?: SupervisorApiTransport;
  eventStream?: SupervisorEventStreamClient;
  projectSnapshot?: SupervisorSnapshotProjector;
}

export interface UseSupervisorConversationResult {
  state: SupervisorRuntimeState;
  contextVersion: number | null;
  getContextVersion(): number | null;
  refreshSnapshot(): Promise<SupervisorEventResumePoint>;
  startTurn(request: TurnStartRequest): Promise<JsonValue>;
  respondToInterrupt(interruptId: string, request: JsonObject): Promise<JsonValue>;
  getRunStatus(runId: string): Promise<JsonValue>;
}

function defaultProjectSnapshot(snapshot: JsonValue): SupervisorRuntimeProjection {
  return snapshot as unknown as SupervisorRuntimeProjection;
}

function readContextVersion(snapshot: JsonValue): number {
  if (snapshot === null || typeof snapshot !== "object" || Array.isArray(snapshot)) {
    throw new TypeError("Supervisor Snapshot 上下文版本不合法");
  }
  const value = snapshot.context_version;
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new TypeError("Supervisor Snapshot 上下文版本不合法");
  }
  return value as number;
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError";
}

function cancellationError(): DOMException {
  return new DOMException("请求已取消", "AbortError");
}

export function createSupervisorConversationController(
  options: SupervisorConversationControllerOptions,
): SupervisorConversationController {
  const conversationId = options.conversationId.trim();
  if (!conversationId) throw new TypeError("对话 ID 不能为空");

  const api = options.api ?? supervisorApi;
  const eventStream = options.eventStream ?? supervisorEventStream;
  const projectSnapshot = options.projectSnapshot ?? defaultProjectSnapshot;
  const listeners = new Set<() => void>();
  let state = createSupervisorRuntimeState(conversationId);
  let lifecycleController: AbortController | null = null;
  let eventSubscription: SupervisorEventSubscription | null = null;
  let generation = 0;
  let started = false;
  let disposed = false;
  let contextVersion: number | null = null;

  const publish = (nextState: SupervisorRuntimeState) => {
    if (nextState === state) return;
    state = nextState;
    for (const listener of [...listeners]) listener();
  };

  const dispatch = (action: SupervisorRuntimeAction) => {
    publish(supervisorRuntimeReducer(state, action));
  };

  const isCurrent = (session: number) => !disposed && generation === session;

  const activeSignal = () => {
    if (!started || disposed || !lifecycleController) throw cancellationError();
    return lifecycleController.signal;
  };

  const loadSnapshot = async (
    session: number,
    signal: AbortSignal,
  ): Promise<SupervisorEventResumePoint> => {
    let snapshot: JsonValue;
    try {
      snapshot = await api.getSnapshot(conversationId, { signal });
    } catch (error) {
      if (!isCurrent(session) || signal.aborted || isAbortError(error)) {
        throw cancellationError();
      }
      throw error;
    }
    if (!isCurrent(session) || signal.aborted) throw cancellationError();

    let snapshotContextVersion: number;
    try {
      snapshotContextVersion = readContextVersion(snapshot);
    } catch {
      dispatch({ type: "connection.state_changed", status: "fatal" });
      throw new Error("Supervisor Snapshot 状态不合法");
    }

    let projection: SupervisorRuntimeProjection;
    try {
      projection = projectSnapshot(snapshot, conversationId);
    } catch {
      dispatch({ type: "connection.state_changed", status: "fatal" });
      throw new Error("Supervisor Snapshot 状态不合法");
    }
    if (!isCurrent(session) || signal.aborted) throw cancellationError();
    if (projection.conversationId !== conversationId) {
      dispatch({ type: "connection.state_changed", status: "fatal" });
      throw new Error("Supervisor Snapshot 状态不合法");
    }

    contextVersion = snapshotContextVersion;
    dispatch({ type: "snapshot.hydrated", snapshot: projection });
    if (!isCurrent(session) || signal.aborted) throw cancellationError();
    if (state.connection.status === "fatal") {
      contextVersion = null;
      throw new Error("Supervisor Snapshot 状态不合法");
    }
    return { ...state.resume };
  };

  const markFatal = (session: number) => {
    if (!isCurrent(session)) return;
    dispatch({ type: "connection.state_changed", status: "fatal" });
  };

  const start = async () => {
    if (started) return;
    disposed = false;
    started = true;
    const session = ++generation;
    lifecycleController = new AbortController();
    const signal = lifecycleController.signal;
    dispatch({ type: "connection.state_changed", status: "idle" });
    dispatch({ type: "connection.state_changed", status: "connecting" });

    try {
      const resume = await loadSnapshot(session, signal);
      if (!isCurrent(session) || signal.aborted) return;
      eventSubscription = eventStream.subscribe({
        conversationId,
        cursor: resume.cursor,
        sequence: resume.sequence,
        signal,
        onEvent: (event) => {
          if (!isCurrent(session) || event.conversation_id !== conversationId) return;
          const previousSequence = state.resume.sequence;
          if (event.sequence <= previousSequence) return;
          dispatch({ type: "event.received", event });
          if (!isCurrent(session) || signal.aborted) throw cancellationError();
          if (state.resume.sequence !== event.sequence || state.resume.cursor !== event.cursor) {
            throw new Error("Supervisor 事件状态不合法");
          }
        },
        reloadSnapshot: async (_gap, reloadSignal) => {
          return loadSnapshot(session, reloadSignal);
        },
        onError: () => markFatal(session),
      });
      if (!isCurrent(session) || signal.aborted) {
        eventSubscription.close();
        eventSubscription = null;
        return;
      }
      dispatch({ type: "connection.state_changed", status: "connected" });
    } catch (error) {
      if (signal.aborted || isAbortError(error) || !isCurrent(session)) return;
      markFatal(session);
    }
  };

  const refreshSnapshot = async () => {
    const signal = activeSignal();
    const session = generation;
    return loadSnapshot(session, signal);
  };

  const runScopedRequest = async <T>(
    operation: (signal: AbortSignal) => Promise<T>,
    onFailure?: () => void,
  ): Promise<T> => {
    const signal = activeSignal();
    const session = generation;
    try {
      const result = await operation(signal);
      if (!isCurrent(session) || signal.aborted) throw cancellationError();
      return result;
    } catch (error) {
      if (!isCurrent(session) || signal.aborted || isAbortError(error)) {
        throw cancellationError();
      }
      onFailure?.();
      throw error;
    }
  };

  const startTurn = async (request: TurnStartRequest) => {
    return runScopedRequest(
      (signal) => {
        dispatch({ type: "input.sending", clientInputId: request.client_input_id });
        return api.startTurn(conversationId, request, { signal });
      },
      () => {
        dispatch({ type: "input.submit_failed", clientInputId: request.client_input_id });
      },
    );
  };

  const respondToInterrupt = (interruptId: string, request: JsonObject) => runScopedRequest(
    (signal) => api.respondToInterrupt(conversationId, interruptId, request, { signal }),
  );

  const getRunStatus = (runId: string) => runScopedRequest(
    (signal) => api.getRunStatus(conversationId, runId, { signal }),
  );

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    started = false;
    generation += 1;
    contextVersion = null;
    lifecycleController?.abort(cancellationError());
    lifecycleController = null;
    eventSubscription?.close();
    eventSubscription = null;
  };

  return {
    getState: () => state,
    getContextVersion: () => contextVersion,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start,
    refreshSnapshot,
    startTurn,
    respondToInterrupt,
    getRunStatus,
    dispose,
  };
}

export function useSupervisorConversation(
  conversationId: string,
  options: UseSupervisorConversationOptions = {},
): UseSupervisorConversationResult {
  const enabled = options.enabled ?? true;
  const api = options.api ?? supervisorApi;
  const eventStream = options.eventStream ?? supervisorEventStream;
  const projectSnapshot = options.projectSnapshot ?? defaultProjectSnapshot;
  const controller = useMemo(
    () => createSupervisorConversationController({
      conversationId,
      api,
      eventStream,
      projectSnapshot,
    }),
    [api, conversationId, enabled, eventStream, projectSnapshot],
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getState,
    controller.getState,
  );
  const contextVersion = controller.getContextVersion();
  const getContextVersion = useCallback(
    () => controller.getContextVersion(),
    [controller],
  );

  useEffect(() => {
    if (enabled) void controller.start();
    return controller.dispose;
  }, [controller, enabled]);

  const refreshSnapshot = useCallback(
    () => controller.refreshSnapshot(),
    [controller],
  );
  const startTurn = useCallback(
    (request: TurnStartRequest) => controller.startTurn(request),
    [controller],
  );
  const respondToInterrupt = useCallback(
    (interruptId: string, request: JsonObject) => controller.respondToInterrupt(
      interruptId,
      request,
    ),
    [controller],
  );
  const getRunStatus = useCallback(
    (runId: string) => controller.getRunStatus(runId),
    [controller],
  );

  return useMemo(() => ({
    state,
    contextVersion,
    getContextVersion,
    refreshSnapshot,
    startTurn,
    respondToInterrupt,
    getRunStatus,
  }), [contextVersion, getContextVersion, getRunStatus, refreshSnapshot, respondToInterrupt, startTurn, state]);
}
