import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const moduleUrl = process.env.AGENT_RUNTIME_REDUCER_TEST_MODULE;
assert.ok(moduleUrl, "AGENT_RUNTIME_REDUCER_TEST_MODULE 必须指向编译后的 Runtime 状态模块");
const fixturePath = process.env.AGENT_RUNTIME_SNAPSHOT_FIXTURE;
assert.ok(fixturePath, "AGENT_RUNTIME_SNAPSHOT_FIXTURE 必须指向共享 harness-snapshot-v1.json");

const {
  applyPublicEvent,
  hydrateSnapshot,
  initialAgentWorkspaceState,
  isRecoveryRequired,
  isTerminalSnapshot,
  normalizeEventType,
  projectVisible,
  shouldReloadSnapshot,
} = await import(moduleUrl);

const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));

test("同一 fixture hydrate 与逐条 apply 产生等价用户可见结果", () => {
  const hydrated = hydrateSnapshot(fixture.snapshot);
  assert.deepEqual(projectVisible(hydrated), fixture.visible);

  let state = hydrateSnapshot({
    ...fixture.snapshot,
    status: "accepted",
    last_sequence: 0,
    last_cursor: "",
    events: [],
    messages: fixture.snapshot.messages.filter((message) => message.role === "user"),
  });
  for (const event of fixture.snapshot.events) {
    const [next, result] = applyPublicEvent(state, event);
    assert.equal(result, "applied", event.type);
    state = next;
  }
  assert.deepEqual(projectVisible(state), fixture.visible);
});

test("Sidecar 短名在公开边界规范化为 AgentEventType", () => {
  assert.equal(normalizeEventType("tool.completed"), "agent.tool.completed");
  assert.equal(normalizeEventType("response.completed"), "agent.response.completed");
  assert.equal(normalizeEventType("agent.tool.completed"), "agent.tool.completed");
});

test("公开执行摘要按阶段换行，不拼接为不可读文本", () => {
  let state = hydrateSnapshot({
    ...fixture.snapshot,
    status: "accepted",
    last_sequence: 0,
    last_cursor: "",
    events: [],
    messages: [],
  });
  const first = {
    ...fixture.snapshot.events[0],
    sequence: 1,
    type: "agent.thinking.delta",
    payload: { delta: "正在分析请求。" },
  };
  const second = {
    ...first,
    sequence: 2,
    payload: { delta: "正在调用工具：inspect_video_workspace" },
  };
  [state] = applyPublicEvent(state, first);
  [state] = applyPublicEvent(state, second);
  assert.equal(projectVisible(state).thinkingPreview, "正在分析请求。\n正在调用工具：inspect_video_workspace");
});

test("乱序事件判定为 gap，并要求回读 Snapshot", () => {
  const state = hydrateSnapshot({
    ...fixture.snapshot,
    last_sequence: 1,
    last_cursor: "cursor-1",
    events: [fixture.snapshot.events[0]],
    messages: [fixture.snapshot.messages[0]],
  });
  const skipped = fixture.snapshot.events[2];
  const [next, result] = applyPublicEvent(state, skipped);
  assert.equal(result, "gap");
  assert.equal(next.snapshot.last_sequence, 1);
  assert.equal(shouldReloadSnapshot(skipped, result), true);
});

test("重复事件忽略，Tool 完成事件要求回读 Snapshot", () => {
  const hydrated = hydrateSnapshot(fixture.snapshot);
  const duplicate = fixture.snapshot.events[1];
  const [, ignored] = applyPublicEvent(hydrated, duplicate);
  assert.equal(ignored, "ignored");
  assert.equal(shouldReloadSnapshot(duplicate, "applied"), true);
  assert.equal(shouldReloadSnapshot(fixture.snapshot.events[0], "applied"), false);
});

test("空状态忽略不属于当前 Run 的事件", () => {
  const [, result] = applyPublicEvent(initialAgentWorkspaceState, fixture.snapshot.events[0]);
  assert.equal(result, "ignored");
});

test("确认中断可由公开事件恢复，并在关闭事件后移除", () => {
  let state = hydrateSnapshot({
    ...fixture.snapshot,
    events: [],
    last_sequence: 0,
  });
  const opened = {
    ...fixture.snapshot.events[0],
    sequence: 1,
    type: "agent.confirmation.requested",
    payload: {
      confirmation_id: "hint_123",
      title: "生成视频",
      cost_summary: "确认后会开始生成。",
    },
  };
  [state] = applyPublicEvent(state, opened);
  assert.deepEqual(state.interrupts, [{
    interrupt_id: "hint_123",
    kind: "awaiting_confirmation",
    title: "生成视频",
    description: "确认后会开始生成。",
    status: "open",
  }]);
  const closed = {
    ...opened,
    sequence: 2,
    type: "interrupt.closed",
    payload: { interrupt_id: "hint_123" },
  };
  [state] = applyPublicEvent(state, closed);
  assert.deepEqual(state.interrupts, []);
});

test("Sidecar 确认挂起可从 run.state_changed 恢复中断", () => {
  let state = hydrateSnapshot({ ...fixture.snapshot, events: [], last_sequence: 0 });
  const suspended = {
    ...fixture.snapshot.events[0],
    sequence: 1,
    type: "run.state_changed",
    payload: { status: "suspended_confirmation", interrupt_id: "hint_run" },
  };
  [state] = applyPublicEvent(state, suspended);
  assert.equal(state.interrupts[0]?.interrupt_id, "hint_run");
  assert.equal(state.interrupts[0]?.kind, "awaiting_confirmation");
  assert.equal(isTerminalSnapshot(state.snapshot), true);
});

test("输出上限导致无公开回复时标记为可继续恢复", () => {
  const recoverySnapshot = {
    ...fixture.snapshot,
    status: "failed",
    events: [{
      ...fixture.snapshot.events[0],
      sequence: 1,
      type: "run.state_changed",
      payload: { status: "failed", code: "harness_run_recovery_required" },
    }],
    last_sequence: 1,
  };
  assert.equal(isRecoveryRequired(recoverySnapshot), true);
  assert.equal(isRecoveryRequired({ ...recoverySnapshot, status: "completed" }), false);
});

test("通用中断事件保留表单和授权的稳定身份", () => {
  let state = hydrateSnapshot({ ...fixture.snapshot, events: [], last_sequence: 0 });
  const form = {
    ...fixture.snapshot.events[0],
    sequence: 1,
    type: "interrupt.opened",
    payload: { interrupt_id: "hint_form", kind: "form", title: "补充需求", public_summary: "请补充交付要求。" },
  };
  [state] = applyPublicEvent(state, form);
  const authorization = {
    ...form,
    sequence: 2,
    payload: { interrupt_id: "hint_authorization", kind: "authorization_required", public_summary: "需要重新授权。" },
  };
  [state] = applyPublicEvent(state, authorization);
  assert.deepEqual(state.interrupts.map((item) => [item.interrupt_id, item.kind]), [
    ["hint_form", "form"],
    ["hint_authorization", "authorization_required"],
  ]);
});
