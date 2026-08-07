import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE;
if (!moduleUrl) {
  throw new Error("缺少 Supervisor legacyAdapter 测试模块路径");
}

const { resolveAssistHandoffAction } = await import(moduleUrl);

test("frontend_v2 已登记 Turn 不再 continue_legacy 到旧采集", () => {
  const action = resolveAssistHandoffAction({
    orchestrationMode: "frontend_v2",
    primaryExecutionReady: false,
    registrationStatus: "registered",
    serverInputStatus: "accepted",
    serverRunStatus: "completed",
    continueLegacy: true,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  });
  assert.equal(action, "acknowledge");
});

test("video_agent_v2 活跃 Turn 只等待执行器", () => {
  const action = resolveAssistHandoffAction({
    orchestrationMode: "video_agent_v2",
    primaryExecutionReady: true,
    registrationStatus: "registered",
    serverInputStatus: "accepted",
    serverRunStatus: "running",
    continueLegacy: false,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  });
  assert.equal(action, "wait");
});
