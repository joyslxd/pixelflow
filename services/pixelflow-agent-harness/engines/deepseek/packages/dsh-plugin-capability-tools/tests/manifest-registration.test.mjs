/** 验证 Capability Plugin 只注册已冻结 Manifest 中明确声明边界的 Tool。 */

import assert from "node:assert/strict";
import { apply } from "../dist/index.js";

process.env.PIXELFLOW_HARNESS_TOOLSET_VERSION = "agent-tools-v1";
process.env.PIXELFLOW_HARNESS_MAX_BILLABLE_BATCH_STARTS = "1";
process.env.PIXELFLOW_HARNESS_TOOL_MANIFEST_JSON = JSON.stringify({
  protocol_version: "v1",
  version: "agent-tools-v1",
  digest: `sha256:${"a".repeat(64)}`,
  tools: [
    {
      name: "analyze_video",
      description: "分析已授权视频",
      parameters_schema: { type: "object", properties: { video_url: { type: "string" } } },
      cost_level: "external_read",
      confirmation_required: false,
    },
    {
      name: "inspect_video_workspace",
      description: "读取视频工作区",
      parameters_schema: { type: "object", properties: {} },
      cost_level: "none",
      confirmation_required: false,
    },
    {
      name: "patch_scene",
      description: "修改视频镜头",
      parameters_schema: { type: "object", properties: { scene_id: { type: "string" } } },
      cost_level: "none",
      confirmation_required: false,
    },
  ],
});

const registered = [];
apply({
  tools: {
    register(tool) {
      registered.push(tool);
    },
  },
  pixelflowRunPolicy: {
    assertBillableBatchStart() {},
    suspend() {},
  },
  pixelflowEventBridge: { publish(event) { return event; } },
});

assert.deepEqual(registered.map((tool) => tool.name), ["analyze_video", "inspect_video_workspace", "patch_scene"]);
assert.deepEqual(registered[2].parameters, { type: "object", properties: { scene_id: { type: "string" } } });
assert.deepEqual(registered[0].output.schema.required, ["status", "public_summary", "model_observation"]);
assert.deepEqual(registered[0].output.schema.properties.status.enum, [
  "completed",
  "pending_operation",
  "awaiting_confirmation",
  "authorization_required",
]);
