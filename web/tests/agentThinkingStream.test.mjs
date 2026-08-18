import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";

const reducerModuleUrl = process.env.SUPERVISOR_REDUCER_TEST_MODULE;

if (reducerModuleUrl) {
  const {
    createSupervisorRuntimeState,
    supervisorRuntimeReducer,
  } = await import(reducerModuleUrl);

  test("thinking events accumulate stream text then complete", () => {
    let state = createSupervisorRuntimeState("conv-1");
    state = supervisorRuntimeReducer(state, {
      type: "event.received",
      event: {
        schema_version: 1,
        event_id: "e1",
        sequence: 1,
        cursor: "c1",
        conversation_id: "conv-1",
        run_id: "turn-1",
        occurred_at: "2026-08-11T00:00:00Z",
        type: "agent.thinking.started",
        payload: {
          turn_id: "turn-1",
          title: "正在分析素材，提炼电商属性并构思方向…",
          subtitle: "AI 编剧思考中…",
          started_at: "2026-08-11T00:00:00Z",
        },
      },
    });
    assert.equal(state.agentThinking?.status, "streaming");
    assert.equal(state.agentThinking?.text, "");
    assert.equal(state.agentThinking?.answer, "");

    state = supervisorRuntimeReducer(state, {
      type: "event.received",
      event: {
        schema_version: 1,
        event_id: "e2",
        sequence: 2,
        cursor: "c2",
        conversation_id: "conv-1",
        run_id: "turn-1",
        occurred_at: "2026-08-11T00:00:01Z",
        type: "agent.thinking.delta",
        payload: { turn_id: "turn-1", delta: "先看素材", channel: "reasoning", index: 1 },
      },
    });
    state = supervisorRuntimeReducer(state, {
      type: "event.received",
      event: {
        schema_version: 1,
        event_id: "e3",
        sequence: 3,
        cursor: "c3",
        conversation_id: "conv-1",
        run_id: "turn-1",
        occurred_at: "2026-08-11T00:00:02Z",
        type: "agent.thinking.delta",
        payload: { turn_id: "turn-1", delta: "再定方向", channel: "reasoning", index: 2 },
      },
    });
    state = supervisorRuntimeReducer(state, {
      type: "event.received",
      event: {
        schema_version: 1,
        event_id: "e3b",
        sequence: 4,
        cursor: "c3b",
        conversation_id: "conv-1",
        run_id: "turn-1",
        occurred_at: "2026-08-11T00:00:02.5Z",
        type: "agent.thinking.delta",
        payload: { turn_id: "turn-1", delta: "结论给用户", channel: "answer", index: 3 },
      },
    });
    assert.equal(state.agentThinking?.text, "先看素材再定方向");
    assert.equal(state.agentThinking?.answer, "结论给用户");

    state = supervisorRuntimeReducer(state, {
      type: "event.received",
      event: {
        schema_version: 1,
        event_id: "e4",
        sequence: 5,
        cursor: "c4",
        conversation_id: "conv-1",
        run_id: "turn-1",
        occurred_at: "2026-08-11T00:00:03Z",
        type: "agent.thinking.completed",
        payload: { turn_id: "turn-1" },
      },
    });
    assert.equal(state.agentThinking?.status, "completed");
    assert.equal(state.agentThinking?.answer, "结论给用户");
  });
}

test("thinking stream uses flat Thought-for style without card shell", async () => {
  const fs = await import("node:fs");
  const source = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentThinkingStream.tsx"),
    "utf8",
  );
  assert.match(source, /Thought for/, "header must follow Thought for Xs pattern");
  assert.match(source, /思考中…/, "empty live header uses 思考中… once");
  assert.doesNotMatch(source, />Thinking</, "must not stack a second Thinking footer");
  assert.doesNotMatch(source, /Thinking…/, "must not show empty-body Thinking… placeholder");
  assert.doesNotMatch(source, /statusLine/, "title/subtitle must not render as extra status lines");
  assert.doesNotMatch(source, /rounded-2xl border/, "must not use bordered card shell");
  assert.doesNotMatch(source, /bg-\[#1c2128\]/, "thinking must not keep dark shell");
  assert.doesNotMatch(source, /bg-\[#fbfcfd\]/, "thinking must not keep old plan card background");
  assert.match(source, /nextThinkingRevealStep/, "thinking must typewriter-reveal SSE deltas");
  assert.match(source, /requestAnimationFrame\(loop\)/, "typewriter must use persistent rAF loop");
});

test("thinking reveal step stays small for short lag", async () => {
  const fs = await import("node:fs");
  const source = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentThinkingStream.tsx"),
    "utf8",
  );
  assert.match(source, /if \(lag > 80\) return 3/);
  assert.match(source, /return 1/);
  assert.match(source, /禁止 `visibleText \|\| thinking\.text`|visibleText;/);
});

test("thinking delta event stream must not artificially delay SSE", async () => {
  const fs = await import("node:fs");
  const source = fs.readFileSync(
    path.resolve("src/lib/supervisor/events.ts"),
    "utf8",
  );
  assert.doesNotMatch(
    source,
    /agent\.thinking\.delta[\s\S]{0,240}setTimeout\(resolve/,
    "thinking.delta must not block SSE consumer with setTimeout",
  );
});

test("completed thinking collapses by default and keeps conclusion", async () => {
  const fs = await import("node:fs");
  const source = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentThinkingStream.tsx"),
    "utf8",
  );
  assert.match(source, /thinkingConclusionPreview/);
  assert.match(source, /thoughtForLabel/);
  assert.match(source, /ChevronRight/);
  assert.match(source, /catchingUp/);
  assert.match(source, /等打字机追平再折叠/);
  assert.match(source, /onRevealStateChange/);
});

test("workspace prefers live thinking over archived for current turn", async () => {
  const fs = await import("node:fs");
  const source = fs.readFileSync(
    path.resolve("src/features/legacy-workspace/LegacyWorkspace.tsx"),
    "utf8",
  );
  assert.match(source, /agentThinkingHistory/);
  assert.match(source, /setAgentThinkingHistory/);
  assert.match(source, /thinking-answer:/, "completed answer must become a chat bubble");
  assert.match(source, /thinkingAnswerNoticeInFlightRef/);
  assert.match(source, /waiting_for_input/, "waiting plans must not duplicate answer bubbles");
  assert.match(
    source,
    /orchestrationModeRef\.current === "video_agent_v2"\) return;/,
    "video_agent_v2 must not persist thinking-answer bubbles (AgentTurnGroup owns response)",
  );
  assert.match(
    source,
    /orchestrationMode === "video_agent_v2"[\s\S]*AgentTurnGroup/,
    "video_agent_v2 must render AgentTurnGroup in agentActivityBlocks",
  );
  assert.match(
    source,
    /orchestrationMode === "video_agent_v2"[\s\S]*resolveThinkingAfterMessageId\(turn\.turnId/,
    "native Turn groups must anchor after the triggering user message",
  );
  assert.doesNotMatch(
    source,
    /orchestrationMode === "video_agent_v2"[\s\S]{0,200}AgentThinkingStream/,
    "video_agent_v2 must not stack legacy AgentThinkingStream",
  );
  assert.match(
    source,
    /orchestrationMode === "video_agent_v2"[\s\S]*stepCount === 0[\s\S]*return false/,
    "video_agent_v2 must hide empty observation plan timeline shells",
  );
  assert.match(
    source,
    /afterMessageId: historyAnchor\?\.afterMessageId/,
    "native Turn anchor must prefer Snapshot thinkingHistory.afterMessageId after refresh",
  );
  assert.match(
    source,
    /硬刷新后 native tool 事件丢失/,
    "asset package progress must restore from Workspace after hard refresh",
  );
  assert.match(source, /thinkingTurnAnchorsRef/, "thinking must pin to turn user message");
  assert.match(source, /当前 Turn 优先展示 live/);
  assert.match(source, /holdActivePlanForThinking/);
  assert.match(source, /思考流打字机未结束前不展示本轮 Plan/);
  assert.match(source, /resolveThinkingAfterMessageId/);
  assert.match(source, /Snapshot 恢复的思考历史/);
  assert.doesNotMatch(
    source,
    /thinking\.status === "completed"\s*\n\s*&& supervisorRuntime\.state\.videoAgentConfirmation/,
    "must not hide completed thinking when confirmation appears",
  );
});
