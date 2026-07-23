import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.AGENT_RUNTIME_CONTRACTS_TEST_MODULE;
assert.ok(moduleUrl, "AGENT_RUNTIME_CONTRACTS_TEST_MODULE 必须指向编译后的合同模块");

const {
  ACTION_VALUES,
  EVENT_TYPE_VALUES,
  EXTERNAL_JOB_STATUS_VALUES,
  INTENT_VALUES,
  ORCHESTRATION_MODE_VALUES,
  TURN_STATUS_VALUES,
  WORKFLOW_STATUS_VALUES,
  isAgentEventEnvelope,
  parseAgentEventEnvelope,
} = await import(moduleUrl);

const validEvent = {
  schema_version: 1,
  event_id: "evt_001",
  sequence: 42,
  cursor: "cursor-42",
  conversation_id: "conv_001",
  run_id: "run_001",
  occurred_at: "2026-07-23T12:00:00+08:00",
  type: "context.compression_started",
  payload: { status: "processing" },
};

test("镜像合同枚举与 contracts-v1.md 冻结值一致", () => {
  assert.deepEqual(ORCHESTRATION_MODE_VALUES, ["frontend_v2", "supervisor_v1"]);
  assert.deepEqual(ACTION_VALUES, [
    "answer_only",
    "continue_workflow",
    "modify_workflow",
    "regenerate_stage",
    "retry_failed",
    "start_workflow",
    "switch_workflow",
    "cancel_workflow",
    "clarify",
  ]);
  assert.deepEqual(INTENT_VALUES, ["image", "video", "ppt", "video_analysis", "general"]);
  assert.deepEqual(WORKFLOW_STATUS_VALUES, [
    "draft",
    "awaiting_user",
    "running",
    "paused_quota",
    "failed",
    "completed",
    "cancelled",
  ]);
  assert.deepEqual(TURN_STATUS_VALUES, [
    "accepted",
    "queued",
    "processing",
    "waiting_user",
    "completed",
    "failed",
  ]);
  assert.deepEqual(EXTERNAL_JOB_STATUS_VALUES, [
    "created",
    "polling",
    "succeeded",
    "failed",
    "timeout",
    "expired",
  ]);
  assert.deepEqual(EVENT_TYPE_VALUES, [
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
  ]);
});

test("wire event 校验接受完整的 v1 事件信封", () => {
  assert.equal(isAgentEventEnvelope(validEvent), true);
  assert.deepEqual(parseAgentEventEnvelope(validEvent), validEvent);
});

test("wire event 校验拒绝未知版本、类型和非法序号", () => {
  assert.equal(isAgentEventEnvelope({ ...validEvent, schema_version: 2 }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, type: "context.hidden_chain" }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, sequence: 0 }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, sequence: -1 }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, sequence: 1.5 }), false);
});

test("wire event 校验拒绝 v1 未冻结的顶层字段", () => {
  assert.equal(isAgentEventEnvelope({ ...validEvent, unexpected: "schema-drift" }), false);
});

test("wire event 校验拒绝缺失标识、非法时间和非对象 payload", () => {
  assert.equal(isAgentEventEnvelope({ ...validEvent, event_id: "" }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, conversation_id: null }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, occurred_at: "2026-07-23" }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, payload: [] }), false);
  assert.equal(isAgentEventEnvelope({ ...validEvent, payload: null }), false);
});

test("解析非法 wire event 时返回稳定的公开错误", () => {
  assert.throws(
    () => parseAgentEventEnvelope({ ...validEvent, cursor: "" }),
    /Agent 事件信封不符合 contracts-v1 合同/,
  );
});
