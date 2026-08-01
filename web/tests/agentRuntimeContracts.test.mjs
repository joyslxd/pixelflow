import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const moduleUrl = process.env.AGENT_RUNTIME_CONTRACTS_TEST_MODULE;
assert.ok(moduleUrl, "AGENT_RUNTIME_CONTRACTS_TEST_MODULE 必须指向编译后的合同模块");
const fixturePath = process.env.AGENT_RUNTIME_CONTRACT_FIXTURE;
assert.ok(fixturePath, "AGENT_RUNTIME_CONTRACT_FIXTURE 必须指向 Python 唯一规范 fixture");
const contractFixture = JSON.parse(readFileSync(fixturePath, "utf8"));
const generatedTypeTestPath = process.env.AGENT_RUNTIME_GENERATED_TYPE_TEST;
assert.ok(generatedTypeTestPath, "必须从 Python 唯一 fixture 生成 TypeScript 类型检查文件");

const {
  ACTION_VALUES,
  EVENT_TYPE_VALUES,
  EXTERNAL_JOB_STATUS_VALUES,
  INTENT_VALUES,
  ORCHESTRATION_MODE_VALUES,
  TURN_STATUS_VALUES,
  WORKFLOW_KIND_VALUES,
  WORKFLOW_STATUS_VALUES,
  isAgentEventEnvelope,
  isInterruptResponseRequest,
  isTurnStartRequest,
  parseAgentEventEnvelope,
  parseInterruptResponseRequest,
  parseTurnStartRequest,
} = await import(moduleUrl);

const validEvent = contractFixture.event;

function assertExactKeys(value, expectedKeys, label) {
  assert.deepEqual(Object.keys(value).sort(), [...expectedKeys].sort(), label);
}

test("TypeScript 合同直接读取 Python 唯一规范 fixture", () => {
  assert.equal(contractFixture.schema_version, 1);
  assertExactKeys(contractFixture, [
    "schema_version",
    "orchestration",
    "action_decision",
    "external_job_ref",
    "workflow_record",
    "turn_record",
    "context_summary",
    "context_envelope",
    "event",
    "turn_start_request",
    "interrupt_response_request",
    "interrupt_projection",
    "operation_request",
    "context_request",
  ], "fixture 根字段漂移");
});

test("测试运行器从唯一 fixture 生成全 DTO TypeScript 检查", () => {
  assert.equal(existsSync(generatedTypeTestPath), true);
  const generatedSource = readFileSync(generatedTypeTestPath, "utf8");
  assert.match(generatedSource, /type CanonicalFixture =/u);
  assert.match(generatedSource, /const fixture: CanonicalFixture =/u);
  assert.match(generatedSource, /"workflow_record":/u);
  assert.match(generatedSource, /"context_envelope":/u);
});

test("TypeScript 镜像覆盖 fixture 中全部冻结 DTO 字段", () => {
  const shapes = [
    [contractFixture.orchestration, ["orchestration_mode", "orchestration_version"]],
    [contractFixture.action_decision, [
      "action",
      "intent",
      "target_workflow_id",
      "target_stage",
      "target_artifact_ref",
      "confidence",
      "requires_confirmation",
      "clarification_question",
      "patch",
      "reason_code",
      "idempotency_key",
    ]],
    [contractFixture.external_job_ref, [
      "job_id",
      "provider_job_id",
      "workflow_id",
      "stage",
      "status",
      "attempt",
      "idempotency_key",
      "next_poll_at",
      "lease_owner",
      "lease_expires_at",
    ]],
    [contractFixture.workflow_record, [
      "workflow_id",
      "conversation_id",
      "kind",
      "status",
      "current_stage",
      "stage_version",
      "creation_contract_snapshot",
      "pending_external_job",
      "latest_artifact_refs",
      "context_version",
      "created_at",
      "updated_at",
    ]],
    [contractFixture.turn_record, [
      "turn_id",
      "conversation_id",
      "client_input_id",
      "status",
      "target_workflow_id",
      "decision",
      "expected_context_version",
      "created_at",
    ]],
    [contractFixture.context_summary, [
      "summary_id",
      "conversation_id",
      "version",
      "previous_summary_id",
      "content_hash",
      "user_goals",
      "confirmed_decisions",
      "negative_constraints",
      "workflow_states",
      "unresolved_questions",
      "artifact_evidence_refs",
      "covered_message_ids",
      "covered_sequence_start",
      "covered_sequence_end",
      "compression_model",
      "created_at",
    ]],
    [contractFixture.context_envelope, [
      "validated_context_version",
      "current_input",
      "active_or_target_workflow",
      "recent_messages",
      "conversation_summary",
      "related_workflow_summaries",
      "relevant_long_term_memories",
      "artifact_evidence_refs",
      "unresolved_questions",
      "budget_report",
    ]],
    [contractFixture.context_envelope.budget_report, [
      "estimated_input_tokens",
      "effective_context_tokens",
      "usable_input_tokens",
      "max_output_tokens",
      "safety_reserve_tokens",
      "utilization",
      "compaction_level",
    ]],
    [contractFixture.event, [
      "schema_version",
      "event_id",
      "sequence",
      "cursor",
      "conversation_id",
      "run_id",
      "occurred_at",
      "type",
      "payload",
    ]],
    [contractFixture.turn_start_request, [
      "client_input_id",
      "content",
      "materials",
      "reply_to_message_id",
      "artifact_refs",
      "expected_context_version",
      "explicit_action",
    ]],
    [contractFixture.turn_start_request.explicit_action, [
      "action",
      "intent",
      "workflow_id",
      "stage",
      "artifact_ref",
      "patch",
    ]],
    [contractFixture.interrupt_response_request, [
      "client_response_id",
      "value",
    ]],
    [contractFixture.interrupt_response_request.value, [
      "content",
      "materials",
      "reply_to_message_id",
      "artifact_refs",
      "explicit_action",
    ]],
    [contractFixture.interrupt_projection, [
      "interrupt_id",
      "conversation_id",
      "workflow_id",
      "turn_id",
      "kind",
      "reason_code",
      "payload",
      "opened_at",
    ]],
    [contractFixture.operation_request, [
      "workflow_id",
      "stage",
      "stage_version",
      "attempt",
      "request_hash",
      "idempotency_key",
    ]],
    [contractFixture.context_request, [
      "conversation_id",
      "user_id",
      "current_input",
      "target_workflow_id",
      "artifact_refs",
      "expected_context_version",
    ]],
  ];

  for (const [value, expectedKeys] of shapes) {
    assertExactKeys(value, expectedKeys, expectedKeys[0]);
  }
});

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
  assert.deepEqual(WORKFLOW_KIND_VALUES, ["image", "video", "ppt", "video_analysis"]);
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

test("Turn 与 interrupt response 解析器接受唯一 fixture", () => {
  assert.deepEqual(
    parseTurnStartRequest(contractFixture.turn_start_request),
    contractFixture.turn_start_request,
  );
  assert.deepEqual(
    parseInterruptResponseRequest(contractFixture.interrupt_response_request),
    contractFixture.interrupt_response_request,
  );
});

test("live 请求解析器拒绝额外键、空目标、非 JSON patch 和非 UUID 响应 ID", () => {
  const validTurn = contractFixture.turn_start_request;
  const validResponse = contractFixture.interrupt_response_request;

  assert.throws(
    () => parseTurnStartRequest({ ...validTurn, unexpected: true }),
    /Turn 请求不符合 contracts-v1 合同/,
  );
  assert.throws(
    () => parseTurnStartRequest({
      ...validTurn,
      explicit_action: { ...validTurn.explicit_action, workflow_id: "" },
    }),
    /Turn 请求不符合 contracts-v1 合同/,
  );
  assert.throws(
    () => parseTurnStartRequest({
      ...validTurn,
      explicit_action: { ...validTurn.explicit_action, patch: { invalid: undefined } },
    }),
    /Turn 请求不符合 contracts-v1 合同/,
  );
  assert.throws(
    () => parseInterruptResponseRequest({ ...validResponse, client_response_id: "not-a-uuid" }),
    /interrupt response 不符合 contracts-v1 合同/,
  );
});

test("live 请求解析器拒绝所有非 JSON patch 值", () => {
  const validTurn = contractFixture.turn_start_request;
  const cyclic = {};
  cyclic.self = cyclic;
  class NonJsonRecord {
    constructor() {
      this.value = "not-json";
    }
  }
  const invalidValues = [
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    new Date("2026-07-31T12:00:00Z"),
    new Map([["value", "map-like"]]),
    new NonJsonRecord(),
    cyclic,
  ];

  for (const invalidValue of invalidValues) {
    assert.throws(
      () => parseTurnStartRequest({
        ...validTurn,
        explicit_action: {
          ...validTurn.explicit_action,
          patch: { invalid: invalidValue },
        },
      }),
      /Turn 请求不符合 contracts-v1 合同/,
    );
  }
});

test("live 请求解析器拒绝 materials 中的非法原型对象", () => {
  const validTurn = contractFixture.turn_start_request;
  const validResponse = contractFixture.interrupt_response_request;

  for (const invalidMaterial of [new Date(), new Map(), new (class Material {})()]) {
    assert.throws(
      () => parseTurnStartRequest({ ...validTurn, materials: [invalidMaterial] }),
      /Turn 请求不符合 contracts-v1 合同/,
    );
    assert.throws(
      () => parseInterruptResponseRequest({
        ...validResponse,
        value: { ...validResponse.value, materials: [invalidMaterial] },
      }),
      /interrupt response 不符合 contracts-v1 合同/,
    );
  }
});

test("live guard 与 parser 拒绝 materials、artifact_refs 和 patch 中的稀疏数组", () => {
  const validTurn = contractFixture.turn_start_request;
  const validResponse = contractFixture.interrupt_response_request;
  const sparseMaterials = [];
  const sparseArtifactRefs = [];
  const sparsePatchItems = [];
  sparseMaterials.length = 1;
  sparseArtifactRefs.length = 1;
  sparsePatchItems.length = 1;

  const cases = [
    {
      guard: isTurnStartRequest,
      parse: parseTurnStartRequest,
      value: { ...validTurn, materials: sparseMaterials },
      message: "Turn 请求不符合 contracts-v1 合同",
    },
    {
      guard: isInterruptResponseRequest,
      parse: parseInterruptResponseRequest,
      value: {
        ...validResponse,
        value: { ...validResponse.value, materials: sparseMaterials },
      },
      message: "interrupt response 不符合 contracts-v1 合同",
    },
    {
      guard: isTurnStartRequest,
      parse: parseTurnStartRequest,
      value: { ...validTurn, artifact_refs: sparseArtifactRefs },
      message: "Turn 请求不符合 contracts-v1 合同",
    },
    {
      guard: isInterruptResponseRequest,
      parse: parseInterruptResponseRequest,
      value: {
        ...validResponse,
        value: { ...validResponse.value, artifact_refs: sparseArtifactRefs },
      },
      message: "interrupt response 不符合 contracts-v1 合同",
    },
    {
      guard: isTurnStartRequest,
      parse: parseTurnStartRequest,
      value: {
        ...validTurn,
        explicit_action: {
          ...validTurn.explicit_action,
          patch: { items: sparsePatchItems },
        },
      },
      message: "Turn 请求不符合 contracts-v1 合同",
    },
    {
      guard: isInterruptResponseRequest,
      parse: parseInterruptResponseRequest,
      value: {
        ...validResponse,
        value: {
          ...validResponse.value,
          explicit_action: {
            ...validResponse.value.explicit_action,
            patch: { items: sparsePatchItems },
          },
        },
      },
      message: "interrupt response 不符合 contracts-v1 合同",
    },
  ];

  for (const candidate of cases) {
    assert.equal(candidate.guard(candidate.value), false);
    assert.throws(
      () => candidate.parse(candidate.value),
      { name: "TypeError", message: candidate.message },
    );
  }
});

test("Turn parser 对状态型 action getter 只返回合同内动作", () => {
  const validTurn = contractFixture.turn_start_request;
  const action = { ...validTurn.explicit_action };
  let reads = 0;
  Object.defineProperty(action, "action", {
    enumerable: true,
    get() {
      reads += 1;
      return reads <= 3 ? "continue_workflow" : "invalid_after_validation";
    },
  });

  const parsed = parseTurnStartRequest({ ...validTurn, explicit_action: action });

  assert.equal(ACTION_VALUES.includes(parsed.explicit_action.action), true);
});

test("interrupt parser 对状态型 action getter 只返回合同内动作", () => {
  const validResponse = contractFixture.interrupt_response_request;
  const action = { ...validResponse.value.explicit_action };
  let reads = 0;
  Object.defineProperty(action, "action", {
    enumerable: true,
    get() {
      reads += 1;
      return reads <= 4 ? "continue_workflow" : "invalid_after_validation";
    },
  });

  const parsed = parseInterruptResponseRequest({
    ...validResponse,
    value: { ...validResponse.value, explicit_action: action },
  });

  assert.equal(
    ACTION_VALUES.includes(parsed.value.explicit_action.action),
    true,
  );
});

test("live parser 返回与原输入隔离的递归冻结快照", () => {
  const turnInput = structuredClone(contractFixture.turn_start_request);
  const responseInput = structuredClone(contractFixture.interrupt_response_request);

  const parsedTurn = parseTurnStartRequest(turnInput);
  const parsedResponse = parseInterruptResponseRequest(responseInput);

  assert.notEqual(parsedTurn, turnInput);
  assert.notEqual(parsedTurn.explicit_action.patch, turnInput.explicit_action.patch);
  assert.notEqual(parsedResponse, responseInput);
  assert.notEqual(
    parsedResponse.value.explicit_action.patch,
    responseInput.value.explicit_action.patch,
  );
  assert.equal(Object.isFrozen(parsedTurn), true);
  assert.equal(Object.isFrozen(parsedTurn.explicit_action.patch), true);
  assert.equal(Object.isFrozen(parsedResponse), true);
  assert.equal(Object.isFrozen(parsedResponse.value.explicit_action.patch), true);

  turnInput.explicit_action.patch.approved = false;
  turnInput.materials.push({ changed: true });
  responseInput.value.explicit_action.patch.approved = false;
  responseInput.value.artifact_refs.push("artifact:changed");

  assert.equal(parsedTurn.explicit_action.patch.approved, true);
  assert.deepEqual(parsedTurn.materials, []);
  assert.equal(parsedResponse.value.explicit_action.patch.approved, true);
  assert.deepEqual(parsedResponse.value.artifact_refs, [
    "artifact:video-plan:wf-1:v1",
  ]);
});
