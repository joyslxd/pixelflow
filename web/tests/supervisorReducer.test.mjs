import assert from "node:assert/strict";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

const modulePath = process.env.SUPERVISOR_REDUCER_TEST_MODULE;
if (!modulePath) throw new Error("缺少 SUPERVISOR_REDUCER_TEST_MODULE");

const {
  createSupervisorRuntimeState,
  supervisorRuntimeReducer,
} = await import(modulePath.startsWith("file:") ? modulePath : pathToFileURL(modulePath).href);

function event(sequence, type, payload, overrides = {}) {
  return {
    schema_version: 1,
    event_id: `evt-${sequence}`,
    sequence,
    cursor: `cursor-${sequence}`,
    conversation_id: "conv-1",
    run_id: "run-1",
    occurred_at: `2026-07-24T02:00:${String(sequence).padStart(2, "0")}Z`,
    type,
    payload,
    ...overrides,
  };
}

function receive(state, nextEvent) {
  return supervisorRuntimeReducer(state, { type: "event.received", event: nextEvent });
}

test("初始四维状态保持空闲且拒绝空对话标识", () => {
  assert.deepEqual(createSupervisorRuntimeState("conv-1"), {
    conversationId: "conv-1",
    connection: { status: "idle", error: null },
    run: { runId: null, status: "idle", updatedAt: null },
    compression: {
      status: "idle",
      progressPercent: null,
      queuedInputCount: 0,
      lastOutcome: null,
      updatedAt: null,
    },
    inputQueue: [],
    messages: [],
    workflows: [],
    interrupt: null,
    videoAgentWorkspace: { conversationId: "conv-1", current: null },
    videoAgentPlan: null,
    videoAgentPlans: {},
    videoAgentPlanOrder: [],
    videoAgentConfirmation: null,
    videoAgentQuota: null,
    agentThinkingHistory: [],
    agentThinking: null,
    resume: { cursor: null, sequence: 0 },
  });
  assert.throws(
    () => createSupervisorRuntimeState("  "),
    /对话 ID 不能为空/u,
  );
});

test("connection 状态机只接受表内转换并支持 fatal 后显式重连", () => {
  const transitions = [
    ["connecting", "connecting"],
    ["connected", "connected"],
    ["reconnecting", "reconnecting"],
    ["connected", "connected"],
    ["fatal", "fatal"],
    ["connecting", "connecting"],
    ["connected", "connected"],
    ["idle", "idle"],
  ];
  let state = createSupervisorRuntimeState("conv-1");
  for (const [requested, expected] of transitions) {
    state = supervisorRuntimeReducer(state, {
      type: "connection.state_changed",
      status: requested,
    });
    assert.equal(state.connection.status, expected);
  }

  const initial = createSupervisorRuntimeState("conv-1");
  assert.strictEqual(
    supervisorRuntimeReducer(initial, {
      type: "connection.state_changed",
      status: "connected",
    }),
    initial,
  );
  const fatal = supervisorRuntimeReducer(
    supervisorRuntimeReducer(initial, {
      type: "connection.state_changed",
      status: "connecting",
    }),
    { type: "connection.state_changed", status: "fatal" },
  );
  assert.deepEqual(fatal.connection, {
    status: "fatal",
    error: "Supervisor 连接无法恢复",
  });
});

test("run 状态机映射服务端状态且同一终态不会被回退", () => {
  const rows = [
    ["accepted", "running"],
    ["processing", "running"],
    ["waiting_user", "waiting_user"],
    ["paused", "paused"],
    ["processing", "running"],
    ["completed", "completed"],
  ];
  let state = createSupervisorRuntimeState("conv-1");
  let sequence = 1;
  for (const [wireStatus, expected] of rows) {
    state = receive(state, event(sequence, "run.state_changed", { status: wireStatus }));
    assert.equal(state.run.status, expected);
    sequence += 1;
  }

  state = receive(state, event(sequence, "run.state_changed", { status: "processing" }));
  assert.equal(state.run.status, "completed");
  assert.equal(state.resume.sequence, sequence);

  state = receive(state, event(sequence + 1, "run.state_changed", { status: "processing" }, {
    run_id: "run-2",
  }));
  assert.deepEqual(state.run, {
    runId: "run-2",
    status: "running",
    updatedAt: `2026-07-24T02:00:${String(sequence + 1).padStart(2, "0")}Z`,
  });
});

test("compression 状态机覆盖开始、单调进度、完成、失败和重试", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = receive(state, event(1, "context.compression_started", {
    queued_input_count: 2,
  }));
  assert.deepEqual(state.compression, {
    status: "compacting",
    progressPercent: null,
    queuedInputCount: 2,
    lastOutcome: null,
    updatedAt: "2026-07-24T02:00:01Z",
  });

  state = receive(state, event(2, "context.compression_progressed", {
    progress_percent: 40,
    queued_input_count: 3,
  }));
  state = receive(state, event(3, "context.compression_progressed", {
    progress_percent: 20,
    queued_input_count: 3,
  }));
  assert.equal(state.compression.progressPercent, 40);
  assert.equal(state.compression.queuedInputCount, 3);

  state = receive(state, event(4, "context.compression_completed", {
    queued_input_count: 0,
  }));
  assert.deepEqual(state.compression, {
    status: "idle",
    progressPercent: 100,
    queuedInputCount: 0,
    lastOutcome: "completed",
    updatedAt: "2026-07-24T02:00:04Z",
  });

  state = receive(state, event(5, "context.compression_progressed", {
    progress_percent: 80,
  }));
  assert.equal(state.compression.progressPercent, 100);
  assert.equal(state.compression.status, "idle");

  state = receive(state, event(6, "context.compression_started", {
    queued_input_count: 1,
  }));
  state = receive(state, event(7, "context.compression_failed", {
    queued_input_count: 1,
  }));
  assert.deepEqual(state.compression, {
    status: "blocked",
    progressPercent: null,
    queuedInputCount: 0,
    lastOutcome: "failed",
    updatedAt: "2026-07-24T02:00:07Z",
  });
  state = receive(state, event(8, "context.compression_started", {
    queued_input_count: 1,
  }));
  assert.equal(state.compression.status, "compacting");
  assert.equal(state.compression.lastOutcome, null);
});

test("compression 状态机兼容 M04 真实事件并在终态清理旧排队计数", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = supervisorRuntimeReducer(state, {
    type: "connection.state_changed",
    status: "connecting",
  });
  state = supervisorRuntimeReducer(state, {
    type: "connection.state_changed",
    status: "connected",
  });
  state = supervisorRuntimeReducer(state, {
    type: "snapshot.hydrated",
    snapshot: {
      conversationId: "conv-1",
      run: {
        runId: "run-1",
        status: "running",
        updatedAt: "2026-07-24T02:00:09Z",
      },
      compression: {
        status: "compacting",
        progressPercent: 60,
        queuedInputCount: 2,
        lastOutcome: null,
        updatedAt: "2026-07-24T02:00:09Z",
      },
      inputQueue: [
        {
          clientInputId: "input-1",
          turnId: "turn-1",
          status: "queued",
          queuePosition: 1,
          updatedAt: "2026-07-24T02:00:09Z",
        },
      ],
      resume: { cursor: "cursor-9", sequence: 9 },
    },
  });

  state = receive(state, event(10, "context.compression_progressed", {
    status: "running",
    action: "summarize_old_messages",
    step: 1,
  }));
  assert.equal(state.connection.status, "connected");
  assert.equal(state.compression.progressPercent, 60);
  assert.deepEqual(state.resume, { cursor: "cursor-10", sequence: 10 });

  state = receive(state, event(11, "context.compression_completed", {
    status: "completed",
    message: "上下文整理完成",
  }));
  assert.equal(state.compression.status, "idle");
  assert.equal(state.compression.queuedInputCount, 0);
  assert.deepEqual(state.resume, { cursor: "cursor-11", sequence: 11 });

  state = receive(state, event(12, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-1",
    status: "processing",
  }));
  assert.equal(state.inputQueue[0].status, "processing");

  state = receive(state, event(13, "context.compression_started", {
    status: "running",
    message: "正在整理上下文",
  }));
  assert.equal(state.compression.progressPercent, null);
  state = receive(state, event(14, "context.compression_progressed", {
    status: "running",
    action: "trim_tool_results",
    step: 1,
  }));
  assert.equal(state.compression.progressPercent, null);
  state = receive(state, event(15, "context.compression_failed", {
    status: "retry_required",
    reason_code: "hard_gate_compaction_failed",
    message: "稍后重试",
  }));
  assert.equal(state.connection.status, "connected");
  assert.deepEqual(state.compression, {
    status: "blocked",
    progressPercent: null,
    queuedInputCount: 0,
    lastOutcome: "failed",
    updatedAt: "2026-07-24T02:00:15Z",
  });
  assert.deepEqual(state.resume, { cursor: "cursor-15", sequence: 15 });
});

test("compression 事件显式携带非法百分比时保持失败关闭", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = receive(state, event(1, "context.compression_started", {
    status: "running",
  }));
  state = receive(state, event(2, "context.compression_progressed", {
    status: "running",
    action: "summarize_old_messages",
    step: 1,
    progress_percent: 101,
  }));
  assert.equal(state.connection.status, "fatal");
  assert.deepEqual(state.resume, { cursor: "cursor-1", sequence: 1 });
});

test("input queue 按 client_input_id 幂等更新且服务端接管后不被本地失败回退", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = supervisorRuntimeReducer(state, {
    type: "input.sending",
    clientInputId: "input-1",
  });
  state = receive(state, event(1, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-1",
    status: "accepted",
  }));
  state = receive(state, event(2, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-1",
    status: "queued",
    queue_position: 2,
  }));
  state = receive(state, event(3, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-1",
    status: "processing",
  }));
  state = receive(state, event(4, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-1",
    status: "completed",
  }));
  state = supervisorRuntimeReducer(state, {
    type: "input.submit_failed",
    clientInputId: "input-1",
  });
  state = supervisorRuntimeReducer(state, {
    type: "input.sending",
    clientInputId: "input-1",
  });
  assert.deepEqual(state.inputQueue, [{
    clientInputId: "input-1",
    turnId: "turn-1",
    status: "accepted",
    queuePosition: null,
    updatedAt: "2026-07-24T02:00:04Z",
  }]);

  state = supervisorRuntimeReducer(state, {
    type: "input.sending",
    clientInputId: "input-2",
  });
  state = supervisorRuntimeReducer(state, {
    type: "input.submit_failed",
    clientInputId: "input-2",
  });
  assert.equal(state.inputQueue[1].status, "failed");
  state = receive(state, event(5, "input.state_changed", {
    client_input_id: "input-2",
    turn_id: "turn-2",
    status: "queued",
    queue_position: 1,
  }));
  assert.equal(state.inputQueue[1].status, "queued");
  assert.equal(state.inputQueue[1].queuePosition, 1);
});

test("重复、乱序和跨对话事件不改变状态，sequence gap 保留恢复点并等待 Snapshot", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = receive(state, event(1, "run.state_changed", { status: "processing" }));
  const applied = state;
  assert.strictEqual(
    receive(state, event(1, "run.state_changed", { status: "failed" })),
    applied,
  );
  assert.strictEqual(
    receive(state, event(2, "run.state_changed", { status: "failed" }, {
      conversation_id: "conv-2",
    })),
    applied,
  );

  state = receive(state, event(3, "run.state_changed", { status: "failed" }));
  assert.equal(state.run.status, "running");
  assert.deepEqual(state.resume, { cursor: "cursor-1", sequence: 1 });
  assert.deepEqual(state.connection, {
    status: "reconnecting",
    error: "Supervisor 事件序列需要恢复",
  });

  state = receive(state, event(2, "message.upserted", {
    message: {
      message_id: "msg-1",
      conversation_id: "conv-1",
      role: "assistant",
      content: "已恢复消息",
      payload: {},
      created_at: "2026-07-24T02:00:02Z",
    },
  }));
  assert.equal(state.run.status, "running");
  assert.deepEqual(state.resume, { cursor: "cursor-2", sequence: 2 });
});

test("语义非法的四维事件进入固定 fatal 状态且不暴露 payload 内容", () => {
  const initial = createSupervisorRuntimeState("conv-1");
  const state = receive(initial, event(1, "input.state_changed", {
    client_input_id: "input-1",
    status: "token=secret-value",
  }));
  assert.deepEqual(state.connection, {
    status: "fatal",
    error: "Supervisor 事件状态不合法",
  });
  assert.equal(JSON.stringify(state).includes("secret-value"), false);
  assert.deepEqual(state.resume, { cursor: null, sequence: 0 });

  const invalidPosition = receive(createSupervisorRuntimeState("conv-1"), event(1, "input.state_changed", {
    client_input_id: "input-1",
    status: "queued",
    queue_position: 0,
  }));
  assert.equal(invalidPosition.connection.status, "fatal");
});

test("Snapshot 投影只前进当前对话并保留本地连接状态", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = supervisorRuntimeReducer(state, {
    type: "connection.state_changed",
    status: "connecting",
  });
  state = supervisorRuntimeReducer(state, {
    type: "connection.state_changed",
    status: "connected",
  });
  const snapshot = {
    conversationId: "conv-1",
    run: { runId: "run-9", status: "waiting_user", updatedAt: "2026-07-24T02:01:00Z" },
    compression: {
      status: "compacting",
      progressPercent: 60,
      queuedInputCount: 1,
      lastOutcome: null,
      updatedAt: "2026-07-24T02:01:00Z",
    },
    inputQueue: [{
      clientInputId: "input-9",
      turnId: "turn-9",
      status: "queued",
      queuePosition: 1,
      updatedAt: "2026-07-24T02:01:00Z",
    }],
    resume: { cursor: "cursor-9", sequence: 9 },
  };
  state = supervisorRuntimeReducer(state, { type: "snapshot.hydrated", snapshot });
  assert.equal(state.connection.status, "connected");
  assert.equal(state.run.runId, "run-9");
  assert.equal(state.compression.progressPercent, 60);
  assert.equal(state.inputQueue[0].clientInputId, "input-9");
  assert.deepEqual(state.resume, { cursor: "cursor-9", sequence: 9 });

  const applied = state;
  assert.strictEqual(
    supervisorRuntimeReducer(state, {
      type: "snapshot.hydrated",
      snapshot: { ...snapshot, resume: { cursor: "cursor-8", sequence: 8 } },
    }),
    applied,
  );
  assert.strictEqual(
    supervisorRuntimeReducer(state, {
      type: "snapshot.hydrated",
      snapshot: { ...snapshot, conversationId: "conv-2" },
    }),
    applied,
  );

  assert.strictEqual(
    supervisorRuntimeReducer(state, {
      type: "snapshot.hydrated",
      snapshot: { conversationId: "conv-2" },
    }),
    applied,
  );

  assert.doesNotThrow(() => {
    state = supervisorRuntimeReducer(state, {
      type: "snapshot.hydrated",
      snapshot: { conversationId: "conv-1" },
    });
  });
  assert.deepEqual(state.connection, {
    status: "fatal",
    error: "Supervisor Snapshot 状态不合法",
  });
});

test("Snapshot 拒绝四维交叉矛盾和重复 turn 绑定", () => {
  const initial = createSupervisorRuntimeState("conv-1");
  const base = {
    conversationId: "conv-1",
    run: { runId: null, status: "idle", updatedAt: null },
    compression: {
      status: "idle",
      progressPercent: null,
      queuedInputCount: 0,
      lastOutcome: null,
      updatedAt: null,
    },
    inputQueue: [],
    resume: { cursor: "cursor-1", sequence: 1 },
  };
  const invalidSnapshots = [
    {
      ...base,
      run: { runId: null, status: "running", updatedAt: "2026-07-24T02:01:00Z" },
    },
    {
      ...base,
      compression: {
        status: "compacting",
        progressPercent: 50,
        queuedInputCount: 0,
        lastOutcome: "completed",
        updatedAt: "2026-07-24T02:01:00Z",
      },
    },
    {
      ...base,
      inputQueue: [
        {
          clientInputId: "input-1",
          turnId: "turn-1",
          status: "queued",
          queuePosition: 1,
          updatedAt: "2026-07-24T02:01:00Z",
        },
        {
          clientInputId: "input-2",
          turnId: "turn-1",
          status: "processing",
          queuePosition: null,
          updatedAt: "2026-07-24T02:01:01Z",
        },
      ],
    },
  ];
  for (const snapshot of invalidSnapshots) {
    const state = supervisorRuntimeReducer(initial, {
      type: "snapshot.hydrated",
      snapshot,
    });
    assert.deepEqual(state.connection, {
      status: "fatal",
      error: "Supervisor Snapshot 状态不合法",
    });
  }
});

test("input queue 固定 client_input_id 与 turn_id 一一绑定", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = receive(state, event(1, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-1",
    status: "accepted",
  }));
  const bound = state;

  state = receive(state, event(2, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-2",
    status: "queued",
  }));
  assert.deepEqual(state.inputQueue, bound.inputQueue);
  assert.deepEqual(state.resume, bound.resume);
  assert.equal(state.connection.status, "fatal");

  const missingTurn = receive(createSupervisorRuntimeState("conv-1"), event(1, "input.state_changed", {
    client_input_id: "input-1",
    status: "queued",
  }));
  assert.equal(missingTurn.connection.status, "fatal");
  assert.deepEqual(missingTurn.resume, { cursor: null, sequence: 0 });

  let duplicateTurn = receive(createSupervisorRuntimeState("conv-1"), event(1, "input.state_changed", {
    client_input_id: "input-1",
    turn_id: "turn-1",
    status: "accepted",
  }));
  duplicateTurn = receive(duplicateTurn, event(2, "input.state_changed", {
    client_input_id: "input-2",
    turn_id: "turn-1",
    status: "queued",
  }));
  assert.equal(duplicateTurn.connection.status, "fatal");
  assert.equal(duplicateTurn.inputQueue.length, 1);
  assert.deepEqual(duplicateTurn.resume, { cursor: "cursor-1", sequence: 1 });
});


test("thinking completed 写入 agentThinkingHistory 供刷新回显", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = receive(state, event(1, "agent.thinking.started", {
    turn_id: "turn-1",
    title: "正在判断…",
    subtitle: "AI 编剧思考中…",
    started_at: "2026-08-11T13:00:00Z",
  }));
  state = receive(state, event(2, "agent.thinking.delta", {
    turn_id: "turn-1",
    delta: "先看脚本",
    channel: "reasoning",
  }));
  state = receive(state, event(3, "agent.thinking.completed", {
    turn_id: "turn-1",
  }));
  assert.equal(state.agentThinking?.status, "completed");
  assert.equal(state.agentThinkingHistory.length, 1);
  assert.equal(state.agentThinkingHistory[0].turnId, "turn-1");
  assert.equal(state.agentThinkingHistory[0].text, "先看脚本");
});

test("conversation reset 原子清空上一对话四维状态", () => {
  let state = createSupervisorRuntimeState("conv-1");
  state = supervisorRuntimeReducer(state, {
    type: "input.sending",
    clientInputId: "input-1",
  });
  state = receive(state, event(1, "context.compression_started", {
    queued_input_count: 1,
  }));
  state = supervisorRuntimeReducer(state, {
    type: "conversation.reset",
    conversationId: "conv-2",
  });
  assert.deepEqual(state, createSupervisorRuntimeState("conv-2"));
});
