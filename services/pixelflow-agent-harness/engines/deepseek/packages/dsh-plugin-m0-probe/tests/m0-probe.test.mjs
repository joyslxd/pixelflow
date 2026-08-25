/** 验证 M0 Probe Tool 使用官方 defineTool 后仍保持严格参数与稳定输出。 */

import assert from "node:assert/strict";
import { apply } from "../dist/index.js";

let registeredTool;
await apply({
  tools: {
    register(tool) {
      registeredTool = tool;
    },
    schemas() {
      return [{ name: "skill" }];
    },
  },
  skills: {
    async list() {
      return [{ name: "m0-probe-skill" }];
    },
    async get(name) {
      return name === "m0-probe-skill" ? { content: "M0 隔离 Skill 正文" } : undefined;
    },
  },
});

assert.ok(registeredTool, "Plugin 必须注册 inspect_video_workspace Tool");
await assert.rejects(
  () => registeredTool.execute({ workspace_ref: "opaque:workspace", hidden: "forbidden" }, {}),
  /不接受未知参数/,
);
await assert.rejects(
  () => registeredTool.execute({ workspace_ref: "not-opaque" }, {}),
  /workspace_ref 必须是 opaque 引用/,
);
assert.deepEqual(
  await registeredTool.execute({ workspace_ref: "opaque:workspace" }, {}),
  {
    code: "workspace_inspected",
    public_summary: "已读取模拟视频工作区摘要",
    workspace_revision: 0,
  },
);
