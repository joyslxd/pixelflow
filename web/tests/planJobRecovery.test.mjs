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
  clearPendingPlanJobRecovery,
  continueStartedPlanJob,
  loadPendingPlanJobRecovery,
  planJobResumeDelayMs,
  savePendingPlanJobRecovery,
  shouldRetryPlanJobPersistence,
} = await import(moduleUrl);

function makeStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, value);
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

function makePendingPlanJob(jobId = "plan-job-1") {
  return {
    job_id: jobId,
    conversation_id: "conversation-1",
    source_message_id: "message-1",
    kind: "plan_generation",
    started_at: "2026-07-30T00:00:00.000Z",
    request: { intent: "video" },
    context: { intent: "video", materials: [] },
  };
}

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

test("Plan 恢复句柄写入当前标签页并按对话、job 精确清理", () => {
  const storage = makeStorage();
  const pending = makePendingPlanJob();
  assert.equal(savePendingPlanJobRecovery(storage, pending), true);
  assert.deepEqual(
    loadPendingPlanJobRecovery(storage, pending.conversation_id),
    pending,
  );
  clearPendingPlanJobRecovery(storage, pending.conversation_id, "another-job");
  assert.deepEqual(
    loadPendingPlanJobRecovery(storage, pending.conversation_id),
    pending,
  );
  clearPendingPlanJobRecovery(storage, pending.conversation_id, pending.job_id);
  assert.equal(loadPendingPlanJobRecovery(storage, pending.conversation_id), null);
});

test("Plan 手工编辑任务使用同一套恢复句柄", () => {
  const storage = makeStorage();
  const pending = {
    ...makePendingPlanJob("manual-edit-job-1"),
    kind: "plan_manual_edit",
    request: {
      intent: "video",
      current_plan_markdown: "# v1",
      edited_plan_markdown: "# v2",
    },
  };
  assert.equal(savePendingPlanJobRecovery(storage, pending), true);
  assert.deepEqual(loadPendingPlanJobRecovery(storage, pending.conversation_id), pending);
});

test("Plan 恢复句柄拒绝跨对话和畸形缓存", () => {
  const storage = makeStorage();
  const pending = makePendingPlanJob();
  savePendingPlanJobRecovery(storage, pending);
  assert.equal(loadPendingPlanJobRecovery(storage, "conversation-2"), null);

  storage.setItem(
    "pixelflow:pending-plan-job:conversation-3",
    JSON.stringify({ version: 1, pending: { conversation_id: "conversation-3" } }),
  );
  assert.equal(loadPendingPlanJobRecovery(storage, "conversation-3"), null);
});

test("Plan 已启动后首次持久化失败仍继续原 job 并安排句柄重试", async () => {
  const pending = makePendingPlanJob();
  const calls = [];
  await continueStartedPlanJob({
    pendingPlanJob: pending,
    saveRecovery: () => calls.push("save"),
    persistPending: async () => {
      calls.push("persist");
      throw new Error("conversation update unavailable");
    },
    notifyRecovery: () => calls.push("notify"),
    schedulePersistenceRetry: () => calls.push("schedule"),
    resumePending: async (received) => {
      assert.equal(received.job_id, pending.job_id);
      calls.push("resume");
    },
  });
  assert.deepEqual(calls, ["save", "persist", "notify", "schedule", "resume"]);
});

test("Plan 句柄持久化重试在隐藏页面暂停并受 job 恢复窗口限制", () => {
  const startedAt = "2026-07-30T00:00:00.000Z";
  assert.equal(
    shouldRetryPlanJobPersistence({
      hidden: true,
      startedAt,
      nowMs: Date.parse("2026-07-30T00:01:00.000Z"),
    }),
    false,
  );
  assert.equal(
    shouldRetryPlanJobPersistence({
      hidden: false,
      startedAt,
      nowMs: Date.parse("2026-07-30T00:24:59.000Z"),
    }),
    true,
  );
  assert.equal(
    shouldRetryPlanJobPersistence({
      hidden: false,
      startedAt,
      nowMs: Date.parse("2026-07-30T00:25:01.000Z"),
    }),
    false,
  );
});
