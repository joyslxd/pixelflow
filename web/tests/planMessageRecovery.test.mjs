import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const moduleUrl = pathToFileURL(
  path.join(os.tmpdir(), "pixelflow-plan-message-recovery-test", "planMessageRecovery.js"),
).href;
const { classifyPlanMessageResume, planContextFromSavedMessage, resumePlanMessageJobStep } = await import(moduleUrl);

test("Plan 消息轮询网络失败、隐藏与超时都保留 pending，只有明确 failed 才清理", () => {
  assert.equal(classifyPlanMessageResume({}), "retain_pending");
  assert.equal(classifyPlanMessageResume({ hidden: true }), "retain_pending");
  assert.equal(classifyPlanMessageResume({ errorStatus: 408 }), "retain_pending");
  assert.equal(classifyPlanMessageResume({ status: "running" }), "retain_pending");
  assert.equal(classifyPlanMessageResume({ status: "failed" }), "clear_failed");
  assert.equal(classifyPlanMessageResume({ status: "completed", hasResult: true }), "complete");
});

test("Plan 消息 job 404 必须以相同 client_message_id 重新启动而不是生成新消息 ID", () => {
  assert.equal(classifyPlanMessageResume({ errorStatus: 404 }), "restart_same_client");

  const pendingRequest = {
    role: "assistant",
    content: "plan.md v1",
    payload: {
      client_message_id: "stable-plan-client-id",
      artifact: { type: "plan" },
    },
  };
  const restartRequest = structuredClone(pendingRequest);

  assert.equal(restartRequest.payload.client_message_id, pendingRequest.payload.client_message_id);
  assert.deepEqual(restartRequest, pendingRequest);
});

test("恢复完成后七字段必须取自服务端保存的 Plan artifact 而不是 optimistic payload", () => {
  const serverSavedMessage = {
    id: "stable-plan-client-id",
    role: "assistant",
    content: "服务端保存的 plan.md v3",
    time: "10:00",
    artifact: {
      type: "plan",
      selectedDirection: { direction_id: "server-direction", title: "服务端方向", description: "权威" },
      plan: {
        plan_markdown: "# server plan v3",
        plan_version: 3,
        plan_history: [
          { version: 1, plan_markdown: "# v1" },
          { version: 2, plan_markdown: "# v2" },
          { version: 3, plan_markdown: "# server plan v3" },
        ],
        creation_contract: { video_duration_sec: 20, video_model: "seedance-1.5-pro" },
        scene_durations_sec: [10, 10],
        restored_from_version: null,
      },
    },
  };
  const context = planContextFromSavedMessage(serverSavedMessage, {
    flowDraft: null,
    plan_markdown: "# optimistic plan v1",
    plan_version: 1,
  });

  assert.deepEqual(context, {
    flowDraft: null,
    selected_direction: { direction_id: "server-direction", title: "服务端方向", description: "权威" },
    plan_markdown: "# server plan v3",
    plan_version: 3,
    plan_history: [
      { version: 1, plan_markdown: "# v1" },
      { version: 2, plan_markdown: "# v2" },
      { version: 3, plan_markdown: "# server plan v3" },
    ],
    creation_contract: { video_duration_sec: 20, video_model: "seedance-1.5-pro" },
    scene_durations_sec: [10, 10],
    restored_from_version: null,
  });
});

test("服务端完成结果缺少 Plan artifact 时拒绝写入 context", () => {
  assert.throws(
    () => planContextFromSavedMessage({ id: "bad", artifact: { type: "directions" } }, {}),
    /服务端 Plan 消息缺少权威 artifact/,
  );
});

test("start 成功后 poll 网络失败可用同一 job 恢复，最终只有一条服务端 Plan 且 context 一致", async () => {
  const request = {
    role: "assistant",
    content: "plan.md v1",
    payload: { client_message_id: "stable-plan-id", artifact: { type: "plan" } },
  };
  const pending = { job_id: "job-1", request };
  const serverMessages = new Map();
  let pollAttempts = 0;
  const savedResult = {
    message_id: "server-message-id",
    payload: {
      client_message_id: "stable-plan-id",
      artifact: {
        type: "plan",
        selectedDirection: { direction_id: "d1", title: "方向", description: "权威" },
        plan: {
          plan_markdown: "# server v1",
          plan_version: 1,
          plan_history: [{ version: 1, plan_markdown: "# server v1" }],
          creation_contract: { video_duration_sec: 20 },
          scene_durations_sec: [10, 10],
          restored_from_version: null,
        },
      },
    },
  };
  const dependencies = {
    shouldContinue: () => true,
    getStatus: async () =>
      serverMessages.has("stable-plan-id")
        ? { status: "completed", result: serverMessages.get("stable-plan-id") }
        : { status: "running", result: null },
    pollStatus: async () => {
      pollAttempts += 1;
      serverMessages.set("stable-plan-id", savedResult);
      throw new TypeError("Failed to fetch");
    },
    restart: async () => {
      throw new Error("网络失败不应创建新 job");
    },
  };

  const unknown = await resumePlanMessageJobStep(pending, dependencies);
  assert.equal(unknown.kind, "pending");
  assert.equal(unknown.pending.job_id, "job-1");

  const completed = await resumePlanMessageJobStep(unknown.pending, dependencies);
  assert.equal(completed.kind, "completed");
  assert.equal(pollAttempts, 1);
  assert.equal(serverMessages.size, 1);
  const savedMessage = {
    id: completed.result.payload.client_message_id,
    artifact: completed.result.payload.artifact,
  };
  const context = planContextFromSavedMessage(savedMessage, {});
  assert.equal(context.plan_markdown, "# server v1");
  assert.deepEqual(context.scene_durations_sec, [10, 10]);
});

test("job 404 单步恢复复用原 request 并只替换 job_id", async () => {
  const request = {
    role: "assistant",
    content: "plan.md v1",
    payload: { client_message_id: "stable-plan-id", artifact: { type: "plan" } },
  };
  let restartedRequest;
  const result = await resumePlanMessageJobStep(
    { job_id: "expired-job", request, started_at: "old" },
    {
      shouldContinue: () => true,
      getStatus: async () => {
        throw Object.assign(new Error("404 expired"), { status: 404 });
      },
      pollStatus: async () => ({ status: "running", result: null }),
      restart: async (body) => {
        restartedRequest = body;
        return { job_id: "replacement-job" };
      },
    },
  );

  assert.equal(result.kind, "pending");
  assert.equal(result.pending.job_id, "replacement-job");
  assert.equal(result.pending.request.payload.client_message_id, "stable-plan-id");
  assert.deepEqual(restartedRequest, request);
});
