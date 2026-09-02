/** Snapshot -> AgentWorkspaceState；与逐条 applyPublicEvent 必须产生同一可见结果。 */

import type {
  AgentSnapshotV1,
  PublicMessageV1,
  VideoWorkspaceProjectionV1,
} from "../../api/contracts";

import {
  foldAppliedEvent,
  initialAgentWorkspaceState,
  mergeMessages,
  type AgentWorkspaceState,
} from "./reducer.js";

export type VisibleProjection = {
  runStatus: string;
  messageTexts: string[];
  progressLines: string[];
  workspaceRevision: number | null;
  thinkingPreview: string;
  responsePreview: string;
};

export function preferWorkspace(
  snapshotWorkspace: VideoWorkspaceProjectionV1 | null | undefined,
  liveWorkspace: VideoWorkspaceProjectionV1 | null | undefined,
): VideoWorkspaceProjectionV1 | null {
  /** 挂起快照可能落后于 Worker 回写；revision 更大的公开投影才是当前进度。 */

  if (snapshotWorkspace == null) return liveWorkspace ?? null;
  if (liveWorkspace == null) return snapshotWorkspace;
  return liveWorkspace.revision > snapshotWorkspace.revision ? liveWorkspace : snapshotWorkspace;
}

export function hydrateSnapshot(
  snapshot: AgentSnapshotV1,
  extras: {
    videoWorkspace?: VideoWorkspaceProjectionV1 | null;
    messages?: PublicMessageV1[];
    connection?: AgentWorkspaceState["connection"];
  } = {},
): AgentWorkspaceState {
  /** 用后端完整 Snapshot 替换局部投影；事件顺序不合法时宁可拒绝而不猜测。 */

  const events = [...snapshot.events].sort((left, right) => left.sequence - right.sequence);
  let previous = 0;
  for (const event of events) {
    if (event.sequence <= previous) throw new Error("snapshot_event_sequence_invalid");
    previous = event.sequence;
  }
  if (snapshot.last_sequence < previous) throw new Error("snapshot_sequence_invalid");

  const workspace = preferWorkspace(snapshot.workspace, extras.videoWorkspace);
  let state: AgentWorkspaceState = {
    ...initialAgentWorkspaceState,
    conversationId: snapshot.conversation_id || null,
    messages: extras.messages === undefined
      ? [...snapshot.messages]
      : mergeMessages(extras.messages, snapshot.messages),
    snapshot: {
      ...snapshot,
      events: [],
      last_sequence: 0,
      last_cursor: "",
      workspace,
    },
    currentRun: { runId: snapshot.run_id, status: "accepted" },
    videoWorkspace: workspace,
    connection: extras.connection ?? "connected",
  };
  for (const event of events) {
    state = foldAppliedEvent(state, event);
  }
  if (state.snapshot === null) throw new Error("snapshot_sequence_invalid");
  return {
    ...state,
    snapshot: {
      ...state.snapshot,
      status: snapshot.status,
      last_sequence: snapshot.last_sequence,
      last_cursor: snapshot.last_cursor || state.snapshot.last_cursor,
      context_version: snapshot.context_version,
      conversation_id: snapshot.conversation_id,
      workspace,
      messages: state.messages,
    },
    currentRun: { runId: snapshot.run_id, status: snapshot.status },
    videoWorkspace: workspace,
  };
}

export function projectVisible(state: AgentWorkspaceState): VisibleProjection {
  /** F0 合同可见结果：消息、进度、工作区版本和公开流式预览。 */

  const runId = state.snapshot?.run_id;
  const thinkingPreview = runId === undefined ? "" : (state.thinkingStreamsByRun[runId] ?? "");
  const responsePreview = latestResponsePreview(state);
  return {
    runStatus: state.snapshot?.status ?? "idle",
    messageTexts: state.messages.map((message) => message.content),
    progressLines: [...state.progressLines],
    workspaceRevision: state.videoWorkspace?.revision ?? state.snapshot?.workspace?.revision ?? null,
    thinkingPreview,
    responsePreview,
  };
}

function latestResponsePreview(state: AgentWorkspaceState): string {
  const streams = Object.values(state.responseStreamsByMessage);
  if (streams.length > 0) return streams[streams.length - 1] ?? "";
  for (let index = state.messages.length - 1; index >= 0; index -= 1) {
    const message = state.messages[index];
    if (message?.role === "assistant") return message.content;
  }
  return "";
}
