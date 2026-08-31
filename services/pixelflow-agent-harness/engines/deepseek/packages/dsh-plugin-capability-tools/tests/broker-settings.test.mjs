/** 验证 Compose 内网 Gateway 地址可通过 Capability Plugin 的运行时校验。 */

import assert from "node:assert/strict";

process.env.PIXELFLOW_TOOL_BROKER_BASE_URL = "http://gateway:8001";
process.env.PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY = "k".repeat(32);
process.env.PIXELFLOW_SIDECAR_INSTANCE_ID = "sidecar-test";
process.env.PIXELFLOW_HARNESS_RUN_ID = "hrun_0123456789abcdef";
process.env.PIXELFLOW_HARNESS_SESSION_ID = "pfh_broker_settings_test";
process.env.PIXELFLOW_HARNESS_CONTEXT_DIGEST = `sha256:${"a".repeat(64)}`;
process.env.PIXELFLOW_HARNESS_TOOLSET_VERSION = "agent-tools-v1";
process.env.PIXELFLOW_HARNESS_WORKSPACE_REVISION = "1";
process.env.PIXELFLOW_HARNESS_MAX_BILLABLE_BATCH_STARTS = "0";
process.env.PIXELFLOW_HARNESS_TOOL_MANIFEST_JSON = JSON.stringify({
  protocol_version: "v1",
  version: "agent-tools-v1",
  digest: `sha256:${"b".repeat(64)}`,
  tools: [{
    name: "inspect_video_workspace",
    description: "读取视频工作区",
    parameters_schema: { type: "object", properties: {} },
    cost_level: "none",
    confirmation_required: false,
  }],
});

let requestedUrl = "";
const requestedBodies = [];
globalThis.fetch = async (url, init) => {
  requestedUrl = String(url);
  requestedBodies.push(JSON.parse(String(init.body)));
  return new Response(JSON.stringify({
    protocol_version: "v1",
    status: "completed",
    public_summary: "已读取工作区。",
    model_observation: { workspace_revision: 2 },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
};

const { apply } = await import("../dist/index.js");
const registered = [];
const suspensions = [];
apply({
  tools: { register(tool) { registered.push(tool); } },
  pixelflowRunPolicy: {
    assertBillableBatchStart() {},
    suspend(kind) { suspensions.push(kind); },
  },
});

const result = await registered[0].execute({}, { callId: "call-1" });
assert.equal(requestedUrl, "http://gateway:8001/agent/internal/agent-tools/calls");
assert.equal(result.status, "completed");

globalThis.fetch = async (_url, init) => {
  requestedBodies.push(JSON.parse(String(init.body)));
  return new Response(JSON.stringify({
  protocol_version: "v1",
  status: "failed",
  public_summary: "该 Tool 调用未完成，请基于当前工作区继续",
  model_observation: { code: "tool_call_failed" },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
};
const failedResult = await registered[0].execute({}, { callId: "call-2" });
assert.equal(failedResult.status, "completed");
assert.equal(failedResult.model_observation.code, "tool_call_failed");
assert.equal(requestedBodies[0].expected_workspace_revision, 1);
assert.equal(requestedBodies[1].expected_workspace_revision, 2);

// 人工确认中断必须完整保留 Gateway 生成的身份，供 Sidecar 投影为浏览器可提交的卡片。
globalThis.fetch = async (_url, init) => {
  requestedBodies.push(JSON.parse(String(init.body)));
  return new Response(JSON.stringify({
    protocol_version: "v1",
    status: "awaiting_confirmation",
    public_summary: "该操作需要你的确认后才能继续",
    model_observation: { code: "tool_confirmation_required" },
    suspension: {
      kind: "awaiting_confirmation",
      interrupt_id: "hint_confirmation_test",
    },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
};
const confirmationResult = await registered[0].execute({}, { callId: "call-3" });
assert.equal(confirmationResult.status, "awaiting_confirmation");
assert.equal(confirmationResult.suspension.interrupt_id, "hint_confirmation_test");
assert.deepEqual(suspensions, ["awaiting_confirmation"]);
assert.equal(requestedBodies[2].expected_workspace_revision, 2);
