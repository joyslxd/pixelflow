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
});

assert.deepEqual(registered.map((tool) => tool.name), ["inspect_video_workspace", "patch_scene"]);
assert.deepEqual(registered[1].parameters, { type: "object", properties: { scene_id: { type: "string" } } });
