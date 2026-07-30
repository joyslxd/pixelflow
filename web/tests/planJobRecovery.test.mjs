import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const moduleUrl = process.env.PLAN_JOB_RECOVERY_TEST_MODULE || pathToFileURL(
  path.join(os.tmpdir(), "pixelflow-plan-job-recovery-test", "planJobRecovery.js"),
).href;
const {
  classifyPlanJobResume,
  planJobResumeDelayMs,
} = await import(moduleUrl);

test("Plan 查询临时失败和页面隐藏时保留 pending", () => {
  assert.equal(classifyPlanJobResume({ errorStatus: 408 }), "retain_pending");
  assert.equal(classifyPlanJobResume({ errorStatus: 503 }), "retain_pending");
  assert.equal(classifyPlanJobResume({ hidden: true }), "retain_pending");
  assert.equal(classifyPlanJobResume({ status: "running" }), "retain_pending");
});

test("Plan 只在权威终态或明确协议失败时清理", () => {
  assert.equal(classifyPlanJobResume({ status: "completed", hasResult: true }), "complete");
  assert.equal(classifyPlanJobResume({ status: "completed", hasResult: false }), "clear_failed");
  assert.equal(classifyPlanJobResume({ status: "failed" }), "clear_failed");
  assert.equal(classifyPlanJobResume({ errorStatus: 404 }), "clear_not_found");
  assert.equal(classifyPlanJobResume({ errorStatus: 409 }), "clear_failed");
  assert.equal(classifyPlanJobResume({ errorStatus: 422 }), "clear_failed");
});

test("Plan 恢复退避有上限", () => {
  assert.equal(planJobResumeDelayMs(0), 1000);
  assert.equal(planJobResumeDelayMs(1), 2000);
  assert.equal(planJobResumeDelayMs(20), 30000);
});
