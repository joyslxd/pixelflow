/** Gateway 与浏览器共享的 Harness Runtime 协议；禁止引入 Sidecar 私有 DTO。 */

export type RunStatusV1 = "accepted" | "running" | "completed" | "failed" | "cancelled";

export type PublicAgentEventTypeV1 =
  | "run.state_changed"
  | "context.compression_started"
  | "context.compression_progressed"
  | "context.compression_completed"
  | "context.compression_failed"
  | "input.state_changed"
  | "message.upserted"
  | "workflow.progressed"
  | "interrupt.opened"
  | "interrupt.responded"
  | "interrupt.closed"
  | "external_job.state_changed"
  | "external_job.quota_state_changed"
  | "agent.plan.created"
  | "agent.plan.updated"
  | "agent.step.started"
  | "agent.step.progressed"
  | "agent.step.completed"
  | "agent.step.failed"
  | "agent.thinking.started"
  | "agent.thinking.delta"
  | "agent.thinking.completed"
  | "agent.reasoning_summary.delta"
  | "agent.reasoning_summary.completed"
  | "agent.tool.started"
  | "agent.tool.progress"
  | "agent.tool.completed"
  | "agent.tool.failed"
  | "agent.operation.updated"
  | "agent.artifact.updated"
  | "agent.response.delta"
  | "agent.response.completed"
  | "agent.confirmation.requested"
  | "agent.route.decided"
  | "error.raised";

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
    reply_to_message_id?: string;
    artifact_refs?: string[];
    explicit_action?: {
      action: string;
      intent?: string | null;
      workflow_id?: string | null;
      stage?: string | null;
      artifact_ref?: string | null;
      patch: Record<string, unknown>;
    } | null;
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
  type: PublicAgentEventTypeV1;
  occurred_at: string;
  payload: Record<string, unknown>;
  conversation_id: string;
  cursor: string;
};

export type PublicMessageV1 = {
  message_id: string;
  role: string;
  content: string;
};

export type VideoWorkspaceProjectionV1 = {
  workspace_id: string;
  revision: number;
  summary: Record<string, unknown>;
};

export type AgentSnapshotV1 = {
  run_id: string;
  status: RunStatusV1;
  last_sequence: number;
  events: PublicAgentEventV1[];
  messages: PublicMessageV1[];
  workspace?: VideoWorkspaceProjectionV1 | null;
  conversation_id: string;
  context_version: number;
  last_cursor: string;
};
