/** Turn、Snapshot 与公开 SSE 的 Runtime Client。 */

import type {
  AgentSnapshotV1,
  PublicAgentEventV1,
  TurnStartV1,
  WorkspaceCommandV1,
} from "./contracts";
import { agentApiUrl, agentHeaders, agentRequest } from "./http";

export type StartedHarnessRunV1 = {
  run_id: string;
  status: "accepted";
  message_id: string;
  workspace_revision: number;
};

export type HarnessRunCancellationV1 = {
  run_id: string;
  status: "completed" | "failed" | "cancelled";
  termination_reason: string | null;
};

export type HarnessRunRecoveryV1 = {
  recovery_event_id: string;
  recovery_run_id: string;
};

export type HarnessWorkspaceCommandResultV1 = {
  client_command_id: string;
  workspace: NonNullable<AgentSnapshotV1["workspace"]>;
};

export type HarnessInterruptResultV1 = {
  interrupt_id: string;
  run_id: string | null;
  status: "accepted" | "cancelled";
  workspace_revision: number;
};

export type VideoPlanPublicGoalUpdateV1 = {
  plan_id: string;
  revision: number;
  public_goal: string | null;
};

export function startHarnessTurn(
  conversationId: string,
  body: TurnStartV1,
): Promise<StartedHarnessRunV1> {
  /** 原子提交用户输入与稳定 client_input_id；失败重试必须复用同一个 ID。 */

  return agentRequest<StartedHarnessRunV1>(
    `/conversations/${encodeURIComponent(conversationId)}/harness-turns/start`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export function getHarnessSnapshot(conversationId: string, runId: string): Promise<AgentSnapshotV1> {
  /** 读取唯一权威 Run Snapshot，用于首屏、gap 与刷新恢复。 */

  return agentRequest<AgentSnapshotV1>(
    `/conversations/${encodeURIComponent(conversationId)}/harness-runs/${encodeURIComponent(runId)}/snapshot`,
  );
}

export function cancelHarnessRun(conversationId: string, runId: string): Promise<HarnessRunCancellationV1> {
  /** 请求取消当前模型循环；结果仍以后续 Snapshot/SSE 为准。 */

  return agentRequest<HarnessRunCancellationV1>(
    `/conversations/${encodeURIComponent(conversationId)}/harness-runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
}

export function recoverHarnessRun(conversationId: string, runId: string): Promise<HarnessRunRecoveryV1> {
  /** 仅恢复 Gateway 标记为安全可重放的 Run；恢复身份由后端唯一事件去重。 */

  return agentRequest<HarnessRunRecoveryV1>(
    `/conversations/${encodeURIComponent(conversationId)}/harness-runs/${encodeURIComponent(runId)}/recover`,
    { method: "POST" },
  );
}

export function applyHarnessWorkspaceCommand(
  conversationId: string,
  body: WorkspaceCommandV1,
): Promise<HarnessWorkspaceCommandResultV1> {
  /** 以稳定命令 ID 与 revision 修改权威工作区；Provider/额度字段由 Gateway 拒绝直写。 */

  return agentRequest<HarnessWorkspaceCommandResultV1>(
    `/conversations/${encodeURIComponent(conversationId)}/workspaces/commands`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export function updateVideoPlanPublicGoal(
  conversationId: string,
  planId: string,
  body: { expected_revision: number; public_goal: string | null },
): Promise<VideoPlanPublicGoalUpdateV1> {
  /** Plan 独立于 Workspace 保存 revision；冲突由 409/current_revision 明确返回。 */

  return agentRequest<VideoPlanPublicGoalUpdateV1>(
    `/conversations/${encodeURIComponent(conversationId)}/plans/${encodeURIComponent(planId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export function respondToHarnessInterrupt(
  conversationId: string,
  workspaceId: string,
  interruptId: string,
  body: {
    client_response_id: string;
    expected_workspace_revision: number;
    action: "submit" | "confirm" | "form_cancelled";
    content?: string;
    fields?: Record<string, string>;
  },
): Promise<HarnessInterruptResultV1> {
  /** 统一提交表单/内容确认；重复请求必须复用 client_response_id。 */

  return agentRequest<HarnessInterruptResultV1>(
    `/conversations/${encodeURIComponent(conversationId)}/workspaces/${encodeURIComponent(workspaceId)}/interrupts/${encodeURIComponent(interruptId)}/responses`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export function resumeHarnessInterruptAuthorization(
  conversationId: string,
  workspaceId: string,
  interruptId: string,
  body: { client_response_id: string; expected_workspace_revision: number },
): Promise<HarnessInterruptResultV1> {
  /** Authorization 仅随本次请求发往 Gateway，浏览器不把它写入中断 payload。 */

  return agentRequest<HarnessInterruptResultV1>(
    `/conversations/${encodeURIComponent(conversationId)}/workspaces/${encodeURIComponent(workspaceId)}/interrupts/${encodeURIComponent(interruptId)}/authorizations`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
}

export type PublicSseFrameV1 = {
  eventId: string | null;
  event: PublicAgentEventV1;
};

function parseEvent(data: string): PublicAgentEventV1 | null {
  /** 拒绝不具备最小 sequence/run_id/type 的 SSE 数据，触发上层 Snapshot 重载。 */

  try {
    const value: unknown = JSON.parse(data);
    if (
      typeof value !== "object"
      || value === null
      || !Number.isInteger((value as { sequence?: unknown }).sequence)
      || typeof (value as { run_id?: unknown }).run_id !== "string"
      || typeof (value as { type?: unknown }).type !== "string"
    ) return null;
    const event = value as PublicAgentEventV1;
    return {
      ...event,
      conversation_id: typeof event.conversation_id === "string" ? event.conversation_id : "",
      cursor: typeof event.cursor === "string" ? event.cursor : "",
    };
  } catch {
    return null;
  }
}

export async function* streamHarnessEvents(
  conversationId: string,
  runId: string,
  afterSequence: number,
  signal: AbortSignal,
): AsyncGenerator<PublicSseFrameV1> {
  /** 用 fetch 消费认证 SSE；每次连接只从调用方已提交的 sequence 之后读取。 */

  const response = await fetch(
    agentApiUrl(
      `/conversations/${encodeURIComponent(conversationId)}/harness-runs/${encodeURIComponent(runId)}/events?after_sequence=${afterSequence}`,
    ),
    { headers: agentHeaders({ Accept: "text/event-stream" }), signal },
  );
  if (!response.ok || response.body === null) throw new Error("harness_event_stream_unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const chunk = await reader.read();
      if (chunk.done) return;
      buffer += decoder.decode(chunk.value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const lines = frame.split("\n");
        const data = lines.find((line) => line.startsWith("data: "))?.slice(6);
        if (!data) continue;
        const event = parseEvent(data);
        if (event === null) throw new Error("harness_event_invalid");
        yield {
          eventId: lines.find((line) => line.startsWith("id: "))?.slice(4) ?? null,
          event,
        };
      }
    }
  } finally {
    reader.releaseLock();
  }
}
