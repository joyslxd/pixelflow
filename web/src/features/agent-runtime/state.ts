/** AgentWorkspace 的唯一权威状态投影，不保存任务、Provider 或 Workspace 业务副本。 */

import type { AgentSnapshotV1, PublicAgentEventV1 } from "@/api/contracts";

export type ConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "disconnected";

export type AgentWorkspaceState = {
  snapshot: AgentSnapshotV1 | null;
  connection: ConnectionState;
};

export type EventApplyResult = "applied" | "ignored" | "gap";

export const initialAgentWorkspaceState: AgentWorkspaceState = {
  snapshot: null,
  connection: "idle",
};

export function hydrateSnapshot(snapshot: AgentSnapshotV1): AgentWorkspaceState {
  /** 用后端完整 Snapshot 替换局部投影；事件顺序不合法时宁可拒绝而不猜测。 */

  const events = [...snapshot.events].sort((left, right) => left.sequence - right.sequence);
  let previous = 0;
  for (const event of events) {
    if (event.sequence <= previous) throw new Error("snapshot_event_sequence_invalid");
    previous = event.sequence;
  }
  if (snapshot.last_sequence < previous) throw new Error("snapshot_sequence_invalid");
  return { snapshot: { ...snapshot, events }, connection: "connected" };
}

export function applyPublicEvent(
  state: AgentWorkspaceState,
  event: PublicAgentEventV1,
): [AgentWorkspaceState, EventApplyResult] {
  /** 仅接受连续新事件；重复/旧事件忽略，gap 交给调用方重载权威 Snapshot。 */

  const snapshot = state.snapshot;
  if (snapshot === null || snapshot.run_id !== event.run_id) return [state, "ignored"];
  if (event.sequence <= snapshot.last_sequence) return [state, "ignored"];
  if (event.sequence !== snapshot.last_sequence + 1) return [state, "gap"];
  return [
    {
      ...state,
      snapshot: {
        ...snapshot,
        last_sequence: event.sequence,
        events: [...snapshot.events, event],
      },
    },
    "applied",
  ];
}

export function isTerminalSnapshot(snapshot: AgentSnapshotV1 | null): boolean {
  /** Run 终态后不再建立浏览器重连，刷新仍由用户显式触发。 */

  return snapshot?.status === "completed" || snapshot?.status === "failed" || snapshot?.status === "cancelled";
}
