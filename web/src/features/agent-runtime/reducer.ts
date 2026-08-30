/** AgentWorkspace 的唯一权威状态 reducer；不保存任务、Provider 或 Workspace 业务副本。 */

import type {
  AgentSnapshotV1,
  PublicAgentEventTypeV1,
  PublicAgentEventV1,
  PublicMessageV1,
  PublicInterruptV1,
  PublicOperationV1,
  RunStatusV1,
  VideoWorkspaceProjectionV1,
} from "../../api/contracts";

export type ConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "disconnected";

export type InputStatus = "idle" | "sending" | "queued" | "processing";

export type EventApplyResult = "applied" | "ignored" | "gap";

export type AgentWorkspaceState = {
  conversationId: string | null;
  messages: PublicMessageV1[];
  snapshot: AgentSnapshotV1 | null;
  inputStatus: InputStatus;
  currentRun: { runId: string; status: RunStatusV1 } | null;
  thinkingStreamsByRun: Record<string, string>;
  responseStreamsByMessage: Record<string, string>;
  videoWorkspace: VideoWorkspaceProjectionV1 | null;
  connection: ConnectionState;
  progressLines: string[];
  interrupts: PublicInterruptV1[];
  operations: PublicOperationV1[];
};

export const initialAgentWorkspaceState: AgentWorkspaceState = {
  conversationId: null,
  messages: [],
  snapshot: null,
  inputStatus: "idle",
  currentRun: null,
  thinkingStreamsByRun: {},
  responseStreamsByMessage: {},
  videoWorkspace: null,
  connection: "idle",
  progressLines: [],
  interrupts: [],
  operations: [],
};

const EVENT_TYPE_ALIASES: Record<string, PublicAgentEventTypeV1> = {
  "tool.completed": "agent.tool.completed",
  "tool.started": "agent.tool.started",
  "tool.progress": "agent.tool.progress",
  "tool.failed": "agent.tool.failed",
  "response.completed": "agent.response.completed",
  "response.delta": "agent.response.delta",
};

const SNAPSHOT_RELOAD_TYPES = new Set<PublicAgentEventTypeV1>([
  "agent.tool.completed",
  "agent.artifact.updated",
]);

export function normalizeEventType(type: string): PublicAgentEventTypeV1 {
  /** Sidecar 内部短名只在公开边界规范化，浏览器状态只认 AgentEventType。 */

  return EVENT_TYPE_ALIASES[type] ?? (type as PublicAgentEventTypeV1);
}

export function normalizePublicEvent(event: PublicAgentEventV1): PublicAgentEventV1 {
  /** 复制事件并改写类型别名，避免调用方误把 Sidecar 私有名写进状态。 */

  return { ...event, type: normalizeEventType(event.type) };
}

export function isTerminalStatus(status: string | null | undefined): boolean {
  /** Run 终态后不再建立浏览器重连，刷新仍由用户显式触发。 */

  return status === "completed"
    || status === "failed"
    || status === "cancelled"
    || status === "suspended_operation"
    || status === "suspended_confirmation"
    || status === "suspended_authorization";
}

export function isTerminalSnapshot(snapshot: AgentSnapshotV1 | null): boolean {
  /** Sidecar 挂起不属于旧 RunStatus 枚举，需从已冻结的公开状态事件判断停止重连。 */

  if (isTerminalStatus(snapshot?.status)) return true;
  const lastState = [...(snapshot?.events ?? [])]
    .reverse()
    .find((event) => normalizeEventType(event.type) === "run.state_changed");
  return lastState !== undefined && isTerminalStatus(payloadText(lastState.payload, "status"));
}

export function isRecoveryRequired(snapshot: AgentSnapshotV1 | null): boolean {
  /** 只接受 Gateway 冻结的恢复码；未知失败仍按普通失败展示。 */

  if (snapshot?.status !== "failed") return false;
  return snapshot.events.some((event) => (
    normalizeEventType(event.type) === "run.state_changed"
    && payloadText(event.payload, "status") === "failed"
    && payloadText(event.payload, "code") === "harness_run_recovery_required"
  ));
}

export function shouldReloadSnapshot(
  event: PublicAgentEventV1,
  result: EventApplyResult,
): boolean {
  /** gap、Tool 完成、Artifact 更新和 Run 终态必须回读权威 Snapshot，其余增量应用。 */

  if (result === "gap") return true;
  if (result !== "applied") return false;
  const type = normalizeEventType(event.type);
  if (SNAPSHOT_RELOAD_TYPES.has(type)) return true;
  if (type === "run.state_changed") {
    return isTerminalStatus(payloadStatus(event.payload));
  }
  return false;
}

export function applyPublicEvent(
  state: AgentWorkspaceState,
  rawEvent: PublicAgentEventV1,
): [AgentWorkspaceState, EventApplyResult] {
  /** 仅接受连续新事件；重复/旧事件忽略，gap 交给调用方重载权威 Snapshot。 */

  const event = normalizePublicEvent(rawEvent);
  const snapshot = state.snapshot;
  if (snapshot === null || snapshot.run_id !== event.run_id) return [state, "ignored"];
  if (event.sequence <= snapshot.last_sequence) return [state, "ignored"];
  if (event.sequence !== snapshot.last_sequence + 1) return [state, "gap"];
  return [foldAppliedEvent(state, event), "applied"];
}

export function foldAppliedEvent(
  state: AgentWorkspaceState,
  rawEvent: PublicAgentEventV1,
): AgentWorkspaceState {
  /** 在序号已经合法的前提下折叠一条公开事件；hydrate 与增量 SSE 共用。 */

  const event = normalizePublicEvent(rawEvent);
  const snapshot = state.snapshot;
  if (snapshot === null) return state;
  const nextSnapshot: AgentSnapshotV1 = {
    ...snapshot,
    last_sequence: event.sequence,
    last_cursor: event.cursor || snapshot.last_cursor,
    events: [...snapshot.events, event],
  };
  let next: AgentWorkspaceState = {
    ...state,
    snapshot: nextSnapshot,
    conversationId: event.conversation_id || state.conversationId,
  };
  switch (event.type) {
    case "run.state_changed": {
      const status = payloadStatus(event.payload);
      if (status !== null) {
        next = {
          ...next,
          snapshot: { ...nextSnapshot, status },
          currentRun: { runId: event.run_id, status },
          inputStatus: status === "running" ? "processing" : state.inputStatus === "sending" ? "idle" : state.inputStatus,
        };
        if (isTerminalStatus(status)) {
          next = { ...next, inputStatus: "idle" };
        }
      }
      if (event.payload.status === "suspended_confirmation") {
        const interrupt = payloadInterrupt(event);
        if (interrupt !== null) {
          next = { ...next, interrupts: upsertInterrupt(next.interrupts, interrupt) };
        }
      }
      if (event.payload.status === "suspended_authorization") {
        const interrupt = payloadInterrupt(event, "authorization_required");
        if (interrupt !== null) {
          next = { ...next, interrupts: upsertInterrupt(next.interrupts, interrupt) };
        }
      }
      return next;
    }
    case "input.state_changed": {
      const inputStatus = payloadInputStatus(event.payload);
      return inputStatus === null ? next : { ...next, inputStatus };
    }
    case "interrupt.opened":
    case "agent.confirmation.requested": {
      const interrupt = payloadInterrupt(event);
      if (interrupt === null) return next;
      return { ...next, interrupts: upsertInterrupt(state.interrupts, interrupt) };
    }
    case "interrupt.responded":
    case "interrupt.closed": {
      const interruptId = payloadText(event.payload, "interrupt_id") || payloadText(event.payload, "confirmation_id");
      if (interruptId === null) return next;
      return {
        ...next,
        interrupts: state.interrupts.filter((interrupt) => interrupt.interrupt_id !== interruptId),
      };
    }
    case "agent.tool.completed": {
      const summary = payloadText(event.payload, "public_summary")
        || payloadText(event.payload, "tool_name");
      return summary === null ? next : { ...next, progressLines: [...state.progressLines, summary] };
    }
    case "external_job.state_changed":
    case "agent.operation.updated": {
      const operation = payloadOperation(event.payload);
      return operation === null ? next : { ...next, operations: upsertOperation(state.operations, operation) };
    }
    case "agent.thinking.delta":
    case "agent.reasoning_summary.delta": {
      const delta = payloadText(event.payload, "delta") || payloadText(event.payload, "text") || "";
      const existing = state.thinkingStreamsByRun[event.run_id] ?? "";
      return {
        ...next,
        thinkingStreamsByRun: {
          ...state.thinkingStreamsByRun,
          [event.run_id]: existing + delta,
        },
      };
    }
    case "agent.thinking.completed":
    case "agent.reasoning_summary.completed": {
      const summary = payloadText(event.payload, "summary") || payloadText(event.payload, "text");
      if (summary === null) return next;
      return {
        ...next,
        thinkingStreamsByRun: {
          ...state.thinkingStreamsByRun,
          [event.run_id]: summary,
        },
      };
    }
    case "agent.response.delta": {
      const delta = payloadText(event.payload, "delta") || payloadText(event.payload, "text") || "";
      const messageId = payloadText(event.payload, "message_id") || event.run_id;
      const existing = state.responseStreamsByMessage[messageId] ?? "";
      return {
        ...next,
        responseStreamsByMessage: {
          ...state.responseStreamsByMessage,
          [messageId]: existing + delta,
        },
      };
    }
    case "agent.response.completed": {
      const response = payloadText(event.payload, "response");
      if (response === null) return next;
      const messageId = payloadText(event.payload, "message_id") || `harness_response_${event.run_id.slice(5)}`;
      const message: PublicMessageV1 = {
        message_id: messageId,
        role: "assistant",
        content: response,
      };
      const messages = upsertMessage(state.messages, message);
      return {
        ...next,
        messages,
        snapshot: { ...nextSnapshot, messages },
        responseStreamsByMessage: {
          ...state.responseStreamsByMessage,
          [messageId]: response,
        },
      };
    }
    case "message.upserted": {
      const message = payloadMessage(event.payload);
      if (message === null) return next;
      const messages = upsertMessage(state.messages, message);
      return { ...next, messages, snapshot: { ...nextSnapshot, messages } };
    }
    default:
      return next;
  }
}

export function mergeMessages(
  base: PublicMessageV1[],
  incoming: PublicMessageV1[],
): PublicMessageV1[] {
  /** 按 message_id 幂等合并会话消息与 Snapshot 消息，不维护第二份业务正文。 */

  let result = [...base];
  for (const message of incoming) {
    result = upsertMessage(result, message);
  }
  return result;
}

export function replaceVideoWorkspace(
  state: AgentWorkspaceState,
  workspace: VideoWorkspaceProjectionV1 | null,
): AgentWorkspaceState {
  /** 工作区只来自 get-or-create 或 Snapshot 投影，不在浏览器创建业务副本。 */

  if (workspace === null) return state;
  const snapshot = state.snapshot;
  return {
    ...state,
    videoWorkspace: workspace,
    snapshot: snapshot === null ? snapshot : { ...snapshot, workspace },
  };
}

export function setConnection(
  state: AgentWorkspaceState,
  connection: ConnectionState,
): AgentWorkspaceState {
  return { ...state, connection };
}

function upsertMessage(messages: PublicMessageV1[], message: PublicMessageV1): PublicMessageV1[] {
  const byId = messages.findIndex((item) => item.message_id === message.message_id);
  if (byId >= 0) {
    return messages.map((item, index) => (index === byId ? message : item));
  }
  if (messages.some((item) => item.role === message.role && item.content === message.content)) {
    return messages;
  }
  return [...messages, message];
}

function upsertInterrupt(interrupts: PublicInterruptV1[], interrupt: PublicInterruptV1): PublicInterruptV1[] {
  const index = interrupts.findIndex((item) => item.interrupt_id === interrupt.interrupt_id);
  if (index < 0) return [...interrupts, interrupt];
  return interrupts.map((item, itemIndex) => (itemIndex === index ? { ...item, ...interrupt } : item));
}

function upsertOperation(operations: PublicOperationV1[], operation: PublicOperationV1): PublicOperationV1[] {
  const index = operations.findIndex((item) => item.operation_id === operation.operation_id);
  if (index < 0) return [...operations, operation];
  return operations.map((item, itemIndex) => (itemIndex === index ? operation : item));
}

function payloadInterrupt(
  event: PublicAgentEventV1,
  defaultKind: PublicInterruptV1["kind"] = "awaiting_confirmation",
): PublicInterruptV1 | null {
  /** 只接受冻结的公开字段；未知 payload 不得生成可提交的人工操作。 */

  const payload = event.payload;
  const interruptId = payloadText(payload, "interrupt_id") || payloadText(payload, "confirmation_id");
  if (interruptId === null) {
    // 授权挂起不带可持久化授权凭据；以 Run 身份构造仅用于当前 Snapshot 的展示键。
    if (defaultKind !== "authorization_required") return null;
    return {
      interrupt_id: `authorization:${event.run_id}`,
      kind: "authorization_required",
      title: "需要重新授权",
      description: "请重新发起需要授权的操作。",
      status: "open",
    };
  }
  const rawKind = payloadText(payload, "kind");
  const kind = event.type === "agent.confirmation.requested"
    ? "awaiting_confirmation"
    : rawKind === "authorization_required" || rawKind === "quota" || rawKind === "form"
      ? rawKind
      : defaultKind;
  const title = payloadText(payload, "title") || (
    kind === "awaiting_confirmation" ? "请确认继续执行" : "需要你的处理"
  );
  const description = payloadText(payload, "cost_summary")
    || payloadText(payload, "public_summary")
    || payloadText(payload, "reason_code")
    || "请根据当前工作区状态完成处理。";
  return { interrupt_id: interruptId, kind, title, description, status: "open" };
}

function payloadOperation(payload: Record<string, unknown>): PublicOperationV1 | null {
  /** 仅投影稳定进度，无法识别的外部事件必须忽略而非猜测供应商状态。 */

  const operationId = payloadText(payload, "operation_id") || payloadText(payload, "job_id");
  if (operationId === null) return null;
  const rawStatus = payloadText(payload, "status");
  const status = rawStatus === "queued" || rawStatus === "pending"
    ? "queued"
    : rawStatus === "running" || rawStatus === "polling"
      ? "running"
      : rawStatus === "paused" || rawStatus === "quota_paused"
        ? "paused"
        : rawStatus === "completed" || rawStatus === "succeeded"
          ? "completed"
          : rawStatus === "failed" || rawStatus === "timeout" || rawStatus === "expired"
            ? "failed"
            : null;
  if (status === null) return null;
  const completed = payloadCount(payload, "completed");
  const total = payloadCount(payload, "total");
  return { operation_id: operationId, status, completed, total };
}

function payloadCount(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function payloadStatus(payload: Record<string, unknown>): RunStatusV1 | null {
  const status = payload.status;
  if (
    status === "accepted"
    || status === "running"
    || status === "completed"
    || status === "failed"
    || status === "cancelled"
  ) return status;
  return null;
}

function payloadInputStatus(payload: Record<string, unknown>): InputStatus | null {
  const status = payload.status;
  if (status === "idle" || status === "sending" || status === "queued" || status === "processing") {
    return status;
  }
  return null;
}

function payloadText(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function payloadMessage(payload: Record<string, unknown>): PublicMessageV1 | null {
  const messageId = payload.message_id;
  const role = payload.role;
  const content = payload.content;
  if (typeof messageId !== "string" || messageId.length === 0) return null;
  if (typeof role !== "string" || role.length === 0) return null;
  if (typeof content !== "string") return null;
  return { message_id: messageId, role, content };
}
