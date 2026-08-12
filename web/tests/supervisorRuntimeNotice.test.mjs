import assert from "node:assert/strict";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

const modulePath = process.env.SUPERVISOR_RUNTIME_NOTICE_TEST_MODULE;
if (!modulePath) throw new Error("缺少 SUPERVISOR_RUNTIME_NOTICE_TEST_MODULE");

const { resolveSupervisorRuntimeNotice } = await import(
  modulePath.startsWith("file:") ? modulePath : pathToFileURL(modulePath).href
);

test("Turn 处理中显示等待提示", () => {
  const notice = resolveSupervisorRuntimeNotice({
    enabled: true,
    runStatus: "running",
    runUpdatedAt: "2026-08-12T06:00:00Z",
    compression: {
      status: "idle",
      progressPercent: null,
      queuedInputCount: 0,
      lastOutcome: null,
      updatedAt: null,
    },
    inputQueue: [{
      clientInputId: "input-1",
      turnId: "turn-1",
      status: "processing",
      queuePosition: null,
      updatedAt: "2026-08-12T06:00:00Z",
    }],
  });
  assert.equal(notice?.tone, "working");
  assert.match(notice?.title || "", /正在处理中/);
});
