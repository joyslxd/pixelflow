import type {
  ActionDecision,
  AgentEventEnvelope,
  ContextBudgetReport,
  ContextEnvelope,
  ContextRequest,
  ContextSummary,
  ConversationOrchestration,
  ExternalJobRef,
  OperationRequest,
  TurnRecord,
  TurnStartRequest,
  WorkflowRecord,
} from "../src/lib/supervisor/contracts";

const orchestration = {
  orchestration_mode: "supervisor_v1",
  orchestration_version: 1,
} satisfies ConversationOrchestration;

const externalJob = {
  job_id: "job_001",
  provider_job_id: "provider_001",
  workflow_id: "wf_001",
  stage: "image_generate",
  status: "polling",
  attempt: 1,
  idempotency_key: "workflow-stage-1",
  next_poll_at: "2026-07-23T12:00:02+08:00",
  lease_owner: null,
  lease_expires_at: null,
} satisfies ExternalJobRef;

const createdExternalJob = {
  ...externalJob,
  status: "created",
  provider_job_id: null,
  next_poll_at: null,
} satisfies ExternalJobRef;

const workflow = {
  workflow_id: "wf_001",
  conversation_id: "conv_001",
  kind: "image",
  status: "running",
  current_stage: "image_generate",
  stage_version: 1,
  creation_contract_snapshot: {},
  pending_external_job: externalJob,
  latest_artifact_refs: [],
  context_version: 12,
  created_at: "2026-07-23T12:00:00+08:00",
  updated_at: "2026-07-23T12:00:01+08:00",
} satisfies WorkflowRecord;

const decision = {
  action: "continue_workflow",
  intent: "image",
  target_workflow_id: "wf_001",
  target_stage: "image_generate",
  target_artifact_ref: null,
  confidence: 0.99,
  requires_confirmation: false,
  clarification_question: null,
  patch: {},
  reason_code: "single_open_workflow",
  idempotency_key: "decision-001",
} satisfies ActionDecision;

const turn = {
  turn_id: "turn_001",
  conversation_id: "conv_001",
  client_input_id: "client-001",
  status: "processing",
  target_workflow_id: "wf_001",
  decision,
  expected_context_version: 12,
  created_at: "2026-07-23T12:00:00+08:00",
} satisfies TurnRecord;

const contextEnvelope = {
  current_input: "继续",
  active_or_target_workflow: workflow,
  recent_messages: [],
  conversation_summary: {
    summary_id: "summary_001",
    conversation_id: "conv_001",
    version: 1,
    previous_summary_id: null,
    content_hash: "sha256:summary",
    user_goals: ["制作商品主图"],
    confirmed_decisions: ["图片比例为1:1"],
    negative_constraints: ["不要修改包装文字"],
    workflow_states: { wf_001: "正在生成图片" },
    unresolved_questions: [],
    artifact_evidence_refs: ["artifact:image:1"],
    covered_message_ids: ["message_001"],
    covered_sequence_start: 1,
    covered_sequence_end: 1,
    compression_model: "deepseek-v4-pro",
    created_at: "2026-07-23T12:00:00+08:00",
  } satisfies ContextSummary,
  related_workflow_summaries: [],
  relevant_long_term_memories: [],
  artifact_evidence_refs: ["artifact:image:1"],
  unresolved_questions: [],
  budget_report: {
    estimated_input_tokens: 18000,
    effective_context_tokens: 131072,
    usable_input_tokens: 90112,
    max_output_tokens: 8192,
    safety_reserve_tokens: 32768,
    utilization: 0.19975142,
    compaction_level: 0,
  } satisfies ContextBudgetReport,
} satisfies ContextEnvelope;

const event = {
  schema_version: 1,
  event_id: "evt_001",
  sequence: 1,
  cursor: "cursor-1",
  conversation_id: "conv_001",
  run_id: "run_001",
  occurred_at: "2026-07-23T12:00:00+08:00",
  type: "workflow.progressed",
  payload: {},
} satisfies AgentEventEnvelope;

const request = {
  client_input_id: "client-001",
  content: "继续",
  materials: [],
  reply_to_message_id: null,
  artifact_refs: [],
  expected_context_version: 12,
} satisfies TurnStartRequest;

const operationRequest = {
  workflow_id: "wf_001",
  stage: "image_generate",
  stage_version: 1,
  attempt: 1,
  request_hash: "sha256:request",
  idempotency_key: "operation:wf_001:image_generate:1:1",
} satisfies OperationRequest;

const contextRequest = {
  conversation_id: "conv_001",
  user_id: "user_001",
  current_input: "继续",
  target_workflow_id: "wf_001",
  artifact_refs: ["artifact:image:1"],
  expected_context_version: 12,
} satisfies ContextRequest;

void [orchestration, createdExternalJob, turn, contextEnvelope, event, request, operationRequest, contextRequest];
