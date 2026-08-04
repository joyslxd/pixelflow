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
