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
globalThis.fetch = async (url) => {
  requestedUrl = String(url);
  return new Response(JSON.stringify({
    protocol_version: "v1",
    status: "completed",
    public_summary: "已读取工作区。",
    model_observation: {},
  }), { status: 200, headers: { "Content-Type": "application/json" } });
};

const { apply } = await import("../dist/index.js");
const registered = [];
apply({
  tools: { register(tool) { registered.push(tool); } },
  pixelflowRunPolicy: { assertBillableBatchStart() {}, suspend() {} },
});

const result = await registered[0].execute({}, { callId: "call-1" });
assert.equal(requestedUrl, "http://gateway:8001/agent/internal/agent-tools/calls");
assert.equal(result.status, "completed");
