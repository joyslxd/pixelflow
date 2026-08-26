/** Turn、Snapshot 与公开 SSE 的 Runtime Client。 */

import type { AgentSnapshotV1, PublicAgentEventV1, TurnStartV1 } from "./contracts";
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
    return value as PublicAgentEventV1;
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
