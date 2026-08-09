import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.VIDEO_AGENT_TIMELINE_REDUCER_TEST_MODULE;
assert.ok(moduleUrl, "VIDEO_AGENT_TIMELINE_REDUCER_TEST_MODULE 必须指向编译后的 VideoAgent reducer 模块");

const { createVideoAgentTimelineState, reduceVideoAgentEvent } = await import(moduleUrl);

test("completed step event projects public summary and persisted duration", () => {
  const planned = reduceVideoAgentEvent(createVideoAgentTimelineState(), {
    type: "agent.plan.created",
    payload: {
      plan_id: "plan-1",
      workspace_id: "workspace-1",
      status: "planning",
      public_goal: "生成商品视频",
    },
  });

  const state = reduceVideoAgentEvent(planned, {
    type: "agent.step.completed",
    payload: {
      plan_id: "plan-1",
      step_id: "step-1",
      sequence: 1,
      title: "读取项目",
      status: "completed",
      public_summary: "项目资料已读取",
      artifact_refs: ["artifact:workspace-1"],
      started_at: "2026-08-04T00:00:00Z",
      completed_at: "2026-08-04T00:00:03Z",
      duration_ms: 3000,
    },
  });

  assert.equal(state.plans["plan-1"].steps["step-1"].status, "completed");
  assert.equal(state.plans["plan-1"].steps["step-1"].durationMs, 3000);
  assert.equal(state.plans["plan-1"].steps["step-1"].publicSummary, "项目资料已读取");
});

test("confirmation requested event pauses the persisted plan step", () => {
  const planned = reduceVideoAgentEvent(createVideoAgentTimelineState(), {
    type: "agent.plan.created",
    payload: {
      plan_id: "plan-1",
      workspace_id: "workspace-1",
      status: "running",
      public_goal: "生成商品视频",
    },
  });
  const started = reduceVideoAgentEvent(planned, {
    type: "agent.step.started",
    payload: {
      plan_id: "plan-1",
      step_id: "step-1",
      sequence: 1,
      title: "生成第一条分镜",
      status: "running",
      started_at: "2026-08-04T00:00:00Z",
    },
  });

  const paused = reduceVideoAgentEvent(started, {
    type: "agent.confirmation.requested",
    payload: { plan_id: "plan-1", step_id: "step-1" },
  });

  assert.equal(paused.plans["plan-1"].status, "awaiting_confirmation");
  assert.equal(paused.plans["plan-1"].steps["step-1"].status, "awaiting_confirmation");
});

test("progressed step event appends live phase log while keeping running status", () => {
  const planned = reduceVideoAgentEvent(createVideoAgentTimelineState(), {
    type: "agent.plan.created",
    payload: {
      plan_id: "plan-1",
      workspace_id: "workspace-1",
      status: "running",
      public_goal: "生成广告",
    },
  });
  const started = reduceVideoAgentEvent(planned, {
    type: "agent.step.started",
    payload: {
      plan_id: "plan-1",
      step_id: "step-2",
      sequence: 2,
      title: "生成广告脚本草稿",
      status: "running",
      started_at: "2026-08-04T00:00:00Z",
    },
  });
  const progressed = reduceVideoAgentEvent(started, {
    type: "agent.step.progressed",
    payload: {
      plan_id: "plan-1",
      step_id: "step-2",
      sequence: 2,
      title: "生成广告脚本草稿",
      status: "running",
      public_summary: "调用创意脚本 Skill（brief_generate）…",
      progress_phase: "invoke_skill",
      started_at: "2026-08-04T00:00:00Z",
    },
  });
  const waiting = reduceVideoAgentEvent(progressed, {
    type: "agent.step.progressed",
    payload: {
      plan_id: "plan-1",
      step_id: "step-2",
      sequence: 2,
      title: "生成广告脚本草稿",
      status: "running",
      public_summary: "已交给大模型生成脚本草稿，请稍候…",
      progress_phase: "await_model",
      started_at: "2026-08-04T00:00:00Z",
    },
  });

  assert.equal(waiting.plans["plan-1"].steps["step-2"].status, "running");
  assert.equal(
    waiting.plans["plan-1"].steps["step-2"].publicSummary,
    "已交给大模型生成脚本草稿，请稍候…",
  );
  assert.deepEqual(waiting.plans["plan-1"].steps["step-2"].progressLog, [
    "调用创意脚本 Skill（brief_generate）…",
    "已交给大模型生成脚本草稿，请稍候…",
  ]);
  assert.equal(waiting.plans["plan-1"].steps["step-2"].progressPhase, "await_model");
});
