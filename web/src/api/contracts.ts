/** Gateway 与浏览器共享的 Harness Runtime 协议；禁止引入 Sidecar 私有 DTO。 */

export type TurnStartV1 = {
  client_input_id: string;
  workspace_id: string;
  expected_workspace_revision: number;
  content: string;
  max_output_tokens?: number;
};

export type InterruptResponseV1 = {
  client_response_id: string;
  value: {
    content: string;
    materials?: Array<Record<string, unknown>>;
    artifact_refs?: string[];
  };
};

export type WorkspaceCommandV1 = {
  client_command_id: string;
  workspace_id: string;
  expected_workspace_revision: number;
  patch: Record<string, unknown>;
};

export type PublicAgentEventV1 = {
  event_id: string;
  sequence: number;
  run_id: string;
  type: "run.state_changed" | "tool.completed" | "response.completed";
  occurred_at: string;
  payload: Record<string, unknown>;
};

export type AgentSnapshotV1 = {
  run_id: string;
  status: "accepted" | "running" | "completed" | "failed";
  last_sequence: number;
  events: PublicAgentEventV1[];
  messages: Array<{ message_id?: string; role: string; content: string }>;
};
