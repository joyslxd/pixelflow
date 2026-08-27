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
