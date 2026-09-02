/** Gateway 与浏览器共享的 Harness Runtime 协议；禁止引入 Sidecar 私有 DTO。 */

export type RunStatusV1 =
  | "accepted"
  | "running"
  | "suspended_operation"
  | "suspended_confirmation"
  | "suspended_authorization"
  | "completed"
  | "failed"
  | "cancelled";

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
  | "agent.artifact.updated"
  | "agent.response.delta"
  | "agent.response.completed"
  | "agent.confirmation.requested"
  | "error.raised";

export type TurnStartV1 = {
  client_input_id: string;
  workspace_id: string;
  expected_workspace_revision: number;
  content: string;
  materials?: TurnMaterialV1[];
  max_output_tokens?: number;
};

/** 已上传文件的公开引用；二进制仅在 content-app/TOS，浏览器不把文件内容发送给 Gateway。 */
export type TurnMaterialV1 = {
  material_id: string;
  kind: "image" | "video" | "audio" | "file";
  name: string;
  reference_label: string;
  content_type: string;
  url: string;
  asset_id?: string;
};

/** 浏览器只消费 Gateway 投影的中断摘要，不持有 Tool 参数、授权或 Provider 状态。 */
export type PublicInterruptV1 = {
  interrupt_id: string;
  kind: "awaiting_confirmation" | "authorization_required" | "quota" | "form";
  title: string;
  description: string;
  status: "open" | "submitting";
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
