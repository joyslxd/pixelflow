export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];

export interface JsonObject {
  [key: string]: JsonValue;
}

export const ORCHESTRATION_MODE_VALUES = ["frontend_v2", "supervisor_v1"] as const;

export type OrchestrationMode = (typeof ORCHESTRATION_MODE_VALUES)[number];

export interface ConversationOrchestration {
  orchestration_mode: OrchestrationMode;
  orchestration_version: 1;
}

export const ACTION_VALUES = [
  "answer_only",
  "continue_workflow",
  "modify_workflow",
  "regenerate_stage",
  "retry_failed",
  "start_workflow",
  "switch_workflow",
  "cancel_workflow",
  "clarify",
] as const;

export type AgentAction = (typeof ACTION_VALUES)[number];

export const INTENT_VALUES = ["image", "video", "ppt", "video_analysis", "general"] as const;

export type AgentIntent = (typeof INTENT_VALUES)[number];

export const WORKFLOW_KIND_VALUES = ["image", "video", "ppt", "video_analysis"] as const;

export type WorkflowKind = (typeof WORKFLOW_KIND_VALUES)[number];

export interface ActionDecision {
  action: AgentAction;
  intent: AgentIntent;
  target_workflow_id: string | null;
  target_stage: string | null;
  target_artifact_ref: string | null;
  confidence: number;
  requires_confirmation: boolean;
  clarification_question: string | null;
  patch: JsonObject;
  reason_code: string;
  idempotency_key: string;
}

export const WORKFLOW_STATUS_VALUES = [
  "draft",
  "awaiting_user",
  "running",
  "paused_quota",
  "failed",
  "completed",
  "cancelled",
] as const;

export type WorkflowStatus = (typeof WORKFLOW_STATUS_VALUES)[number];

export const TURN_STATUS_VALUES = [
  "accepted",
  "queued",
  "processing",
  "waiting_user",
  "completed",
  "failed",
] as const;

export type TurnStatus = (typeof TURN_STATUS_VALUES)[number];

export const EXTERNAL_JOB_STATUS_VALUES = [
  "created",
  "polling",
  "succeeded",
  "failed",
  "timeout",
  "expired",
] as const;

export type ExternalJobStatus = (typeof EXTERNAL_JOB_STATUS_VALUES)[number];

export interface ExternalJobRef {
  job_id: string;
  provider_job_id: string | null;
  workflow_id: string;
  stage: string;
  status: ExternalJobStatus;
  attempt: number;
  idempotency_key: string;
  next_poll_at: string | null;
  lease_owner: string | null;
  lease_expires_at: string | null;
}

export interface WorkflowRecord {
  workflow_id: string;
  conversation_id: string;
  kind: WorkflowKind;
  status: WorkflowStatus;
  current_stage: string;
  stage_version: number;
  creation_contract_snapshot: JsonObject;
  pending_external_job: ExternalJobRef | null;
  latest_artifact_refs: string[];
  context_version: number;
  created_at: string;
  updated_at: string;
}

export interface TurnRecord {
  turn_id: string;
  conversation_id: string;
  client_input_id: string;
  status: TurnStatus;
  target_workflow_id: string | null;
  decision: ActionDecision | null;
  expected_context_version: number;
  created_at: string;
}

export interface ContextEnvelope {
  current_input: string;
  active_or_target_workflow: WorkflowRecord | null;
  recent_messages: JsonObject[];
  conversation_summary: ContextSummary | null;
  related_workflow_summaries: ContextSummary[];
  relevant_long_term_memories: JsonObject[];
  artifact_evidence_refs: string[];
  unresolved_questions: string[];
  budget_report: ContextBudgetReport;
}

export interface ContextSummary {
  summary_id: string;
  conversation_id: string;
  version: number;
  previous_summary_id: string | null;
  content_hash: string;
  user_goals: string[];
  confirmed_decisions: string[];
  negative_constraints: string[];
  workflow_states: Record<string, string>;
  unresolved_questions: string[];
  artifact_evidence_refs: string[];
  covered_message_ids: string[];
  covered_sequence_start: number | null;
  covered_sequence_end: number | null;
  compression_model: string;
  created_at: string;
}

export interface ContextBudgetReport {
  estimated_input_tokens: number;
  effective_context_tokens: number;
  usable_input_tokens: number;
  max_output_tokens: number;
  safety_reserve_tokens: number;
  utilization: number;
  compaction_level: number;
}

export const EVENT_TYPE_VALUES = [
  "run.state_changed",
  "context.compression_started",
  "context.compression_progressed",
  "context.compression_completed",
  "context.compression_failed",
  "input.state_changed",
  "message.upserted",
  "workflow.progressed",
  "interrupt.opened",
  "interrupt.closed",
  "external_job.state_changed",
  "error.raised",
] as const;

export type AgentEventType = (typeof EVENT_TYPE_VALUES)[number];

export interface AgentEventEnvelope<
  TType extends AgentEventType = AgentEventType,
  TPayload extends JsonObject = JsonObject,
> {
  schema_version: 1;
  event_id: string;
  sequence: number;
  cursor: string;
  conversation_id: string;
  run_id: string;
  occurred_at: string;
  type: TType;
  payload: TPayload;
}

export interface TurnStartRequest {
  client_input_id: string;
  content: string;
  materials: JsonObject[];
  reply_to_message_id: string | null;
  artifact_refs: string[];
  expected_context_version: number;
}

export interface OperationRequest {
  workflow_id: string;
  stage: string;
  stage_version: number;
  attempt: number;
  request_hash: string;
  idempotency_key: string;
}

export interface ContextRequest {
  conversation_id: string;
  user_id: string;
  current_input: string;
  target_workflow_id: string | null;
  artifact_refs: string[];
  expected_context_version: number;
}

const ISO_8601_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u;
const AGENT_EVENT_KEYS = new Set([
  "schema_version",
  "event_id",
  "sequence",
  "cursor",
  "conversation_id",
  "run_id",
  "occurred_at",
  "type",
  "payload",
]);

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isIso8601(value: unknown): value is string {
  return isNonEmptyString(value) && ISO_8601_PATTERN.test(value) && Number.isFinite(Date.parse(value));
}

function isJsonValue(value: unknown, ancestors: WeakSet<object>): value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return true;
  }
  if (typeof value === "number") {
    return Number.isFinite(value);
  }
  if (typeof value !== "object" || ancestors.has(value)) {
    return false;
  }

  ancestors.add(value);
  const valid = Array.isArray(value)
    ? value.every((item) => isJsonValue(item, ancestors))
    : Object.values(value).every((item) => isJsonValue(item, ancestors));
  ancestors.delete(value);
  return valid;
}

function isJsonObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value) && isJsonValue(value, new WeakSet());
}

function isAgentEventType(value: unknown): value is AgentEventType {
  return typeof value === "string" && (EVENT_TYPE_VALUES as readonly string[]).includes(value);
}

export function isAgentEventEnvelope(value: unknown): value is AgentEventEnvelope {
  if (!isJsonObject(value)) {
    return false;
  }

  return Object.keys(value).length === AGENT_EVENT_KEYS.size
    && Object.keys(value).every((key) => AGENT_EVENT_KEYS.has(key))
    && value.schema_version === 1
    && Number.isSafeInteger(value.sequence)
    && typeof value.sequence === "number"
    && value.sequence >= 1
    && isNonEmptyString(value.event_id)
    && isNonEmptyString(value.cursor)
    && isNonEmptyString(value.conversation_id)
    && isNonEmptyString(value.run_id)
    && isIso8601(value.occurred_at)
    && isAgentEventType(value.type)
    && isJsonObject(value.payload);
}

export function parseAgentEventEnvelope(value: unknown): AgentEventEnvelope {
  if (!isAgentEventEnvelope(value)) {
    throw new TypeError("Agent 事件信封不符合 contracts-v1 合同");
  }
  return value;
}
