import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SUPERVISOR_EVENTS_TEST_MODULE;
assert.ok(moduleUrl, "SUPERVISOR_EVENTS_TEST_MODULE 必须指向编译后的 Supervisor 事件模块");

const {
  SupervisorEventStreamError,
  createSupervisorEventStreamClient,
} = await import(moduleUrl);

const encoder = new TextEncoder();

function makeEvent(sequence, cursor = `cursor-${sequence}`, overrides = {}) {
  return {
    schema_version: 1,
    event_id: `evt-${sequence}`,
    sequence,
    cursor,
    conversation_id: "conv-1",
    run_id: "run-1",
    occurred_at: "2026-07-24T09:30:00+08:00",
    type: "run.state_changed",
    payload: { status: "running" },
    ...overrides,
  };
}

function frame(event, { lineEnding = "\n", id = event.cursor } = {}) {
  return [
    `id: ${id}`,
    `event: ${event.type}`,
    `data: ${JSON.stringify(event)}`,
    "",
    "",
  ].join(lineEnding);
}

function streamResponse(chunks, init = {}) {
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  }), {
    status: 200,
    headers: { "Content-Type": "text/event-stream; charset=utf-8" },
    ...init,
  });
}

function pendingResponse(signal) {
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(signal.reason ?? new DOMException("请求已取消", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function makeWindow() {
  const listeners = new Map();
  const storage = { getItem: () => null };
  return {
    localStorage: storage,
    sessionStorage: storage,
    setTimeout,
    clearTimeout,
    addEventListener(type, listener) {
      const entries = listeners.get(type) ?? new Set();
      entries.add(listener);
      listeners.set(type, entries);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    dispatchEvent(event) {
      for (const listener of [...(listeners.get(event.type) ?? [])]) listener(event);
      return true;
    },
  };
}

function createSubscription(client, overrides = {}) {
  const events = [];
  const gaps = [];
  const errors = [];
  const subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: null,
    sequence: 0,
    onEvent: (event) => events.push(event),
    reloadSnapshot: async (gap) => {
      gaps.push(gap);
      return { cursor: `snapshot-${gap.receivedSequence}`, sequence: gap.receivedSequence };
    },
    onError: (error) => errors.push(error),
    ...overrides,
  });
  return { subscription, events, gaps, errors };
}

test("事件流使用 /agent2 路径、透传鉴权并按 cursor 断点续传", async () => {
  const calls = [];
  let subscription;
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "token-1",
    reconnectDelayMs: 0,
    fetchImpl: async (input, init) => {
      calls.push({ input: String(input), init });
      return streamResponse([
        ": heartbeat\r\n\r\n",
        frame(makeEvent(8, "cursor/8", { conversation_id: "conv /1" }), { lineEnding: "\r\n" }),
      ]);
    },
  });

  const received = [];
  subscription = client.subscribe({
    conversationId: "conv /1",
    cursor: "cursor/7",
    sequence: 7,
    onEvent: (event) => {
      received.push(event);
      subscription.close();
    },
    reloadSnapshot: async () => assert.fail("连续序列不应加载快照"),
    onError: (error) => assert.fail(`不应失败：${error.message}`),
  });

  await subscription.done;
  assert.deepEqual(received.map((event) => event.sequence), [8]);
  assert.equal(calls[0].input, "/agent2/conversations/conv%20%2F1/agent-events?cursor=cursor%2F7");
  assert.equal(calls[0].init.headers.Authorization, "Bearer token-1");
  assert.equal(calls[0].init.headers.Accept, "text/event-stream");
  assert.equal(calls[0].init.signal.aborted, true);
});

test("断线后使用最新事件 cursor 重连且不重复投递", async () => {
  const urls = [];
  let requestCount = 0;
  let subscription;
  const first = makeEvent(1, "cursor-1");
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async (input, init) => {
      urls.push(String(input));
      requestCount += 1;
      if (requestCount === 1) return streamResponse([frame(first)]);
      if (requestCount === 2) {
        return streamResponse([frame(first), frame(makeEvent(2, "cursor-2"))]);
      }
      return pendingResponse(init.signal);
    },
  });

  const received = [];
  subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: null,
    sequence: 0,
    onEvent: (event) => {
      received.push(event.sequence);
      if (event.sequence === 2) subscription.close();
    },
    reloadSnapshot: async () => assert.fail("重复事件不应被识别为序列缺口"),
    onError: (error) => assert.fail(`不应失败：${error.message}`),
  });

  await subscription.done;
  assert.deepEqual(received, [1, 2]);
  assert.equal(urls[0], "/agent2/conversations/conv-1/agent-events");
  assert.equal(urls[1], "/agent2/conversations/conv-1/agent-events?cursor=cursor-1");
});

test("默认鉴权会等待 content-app 在 iframe 启动后延迟注入", async () => {
  const originalWindow = globalThis.window;
  const fakeWindow = makeWindow();
  globalThis.window = fakeWindow;
  let authorization;
  let subscription;
  try {
    const client = createSupervisorEventStreamClient({
      reconnectDelayMs: 0,
      fetchImpl: async (_input, init) => {
        authorization = init.headers.Authorization;
        return streamResponse([frame(makeEvent(1))]);
      },
    });
    subscription = client.subscribe({
      conversationId: "conv-1",
      cursor: null,
      sequence: 0,
      onEvent: () => subscription.close(),
      reloadSnapshot: async () => assert.fail("不应加载快照"),
      onError: (error) => assert.fail(`不应失败：${error.message}`),
    });

    setTimeout(() => {
      fakeWindow.__CONTENT_APP_AUTHORIZATION__ = "late-token";
      fakeWindow.dispatchEvent({ type: "contentAppAuthorizationReady" });
    }, 5);
    await subscription.done;
    assert.equal(authorization, "Bearer late-token");
  } finally {
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
  }
});

test("瞬时网络断线使用原 cursor 自动重连且不泄露异常文本", async () => {
  const urls = [];
  let requestCount = 0;
  let subscription;
  const errors = [];
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    maxReconnectAttempts: 1,
    fetchImpl: async (input) => {
      urls.push(String(input));
      requestCount += 1;
      if (requestCount === 1) throw new Error("Authorization: Bearer secret-token");
      return streamResponse([frame(makeEvent(3, "cursor-3"))]);
    },
  });
  subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: "cursor-2",
    sequence: 2,
    onEvent: () => subscription.close(),
    reloadSnapshot: async () => assert.fail("瞬时断线不应加载快照"),
    onError: (error) => errors.push(error),
  });

  await subscription.done;
  assert.equal(requestCount, 2);
  assert.deepEqual(urls, [
    "/agent2/conversations/conv-1/agent-events?cursor=cursor-2",
    "/agent2/conversations/conv-1/agent-events?cursor=cursor-2",
  ]);
  assert.deepEqual(errors, []);
});

test("SSE frame 中途断线时丢弃残片，从最后 cursor 重连并接收补发", async () => {
  const urls = [];
  let requestCount = 0;
  let subscription;
  const event = makeEvent(3, "cursor-3");
  const incompleteFrame = `data: ${JSON.stringify(event).slice(0, -12)}`;
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async (input) => {
      urls.push(String(input));
      requestCount += 1;
      if (requestCount === 1) return streamResponse([incompleteFrame]);
      return streamResponse([frame(event)]);
    },
  });
  const received = [];
  const errors = [];
  subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: "cursor-2",
    sequence: 2,
    onEvent: (receivedEvent) => {
      received.push(receivedEvent.sequence);
      subscription.close();
    },
    reloadSnapshot: async () => assert.fail("残片断线不应被误判为序列缺口"),
    onError: (error) => errors.push(error),
  });

  await subscription.done;
  assert.equal(requestCount, 2);
  assert.deepEqual(received, [3]);
  assert.deepEqual(errors, []);
  assert.deepEqual(urls, [
    "/agent2/conversations/conv-1/agent-events?cursor=cursor-2",
    "/agent2/conversations/conv-1/agent-events?cursor=cursor-2",
  ]);
});

test("序列缺口时不投递越级事件，加载 Snapshot 后从新 cursor 续传", async () => {
  const urls = [];
  let requestCount = 0;
  let subscription;
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async (input, init) => {
      urls.push(String(input));
      requestCount += 1;
      if (requestCount === 1) {
        return streamResponse([frame(makeEvent(1)), frame(makeEvent(3))]);
      }
      if (requestCount === 2) return streamResponse([frame(makeEvent(4))]);
      return pendingResponse(init.signal);
    },
  });

  const received = [];
  const gaps = [];
  subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: null,
    sequence: 0,
    onEvent: (event) => {
      received.push(event.sequence);
      if (event.sequence === 4) subscription.close();
    },
    reloadSnapshot: async (gap) => {
      gaps.push(gap);
      return { cursor: "snapshot/cursor-3", sequence: 3 };
    },
    onError: (error) => assert.fail(`不应失败：${error.message}`),
  });

  await subscription.done;
  assert.deepEqual(received, [1, 4]);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].expectedSequence, 2);
  assert.equal(gaps[0].receivedSequence, 3);
  assert.equal(gaps[0].cursor, "cursor-1");
  assert.equal(urls[1], "/agent2/conversations/conv-1/agent-events?cursor=snapshot%2Fcursor-3");
});

test("Snapshot 恢复永久失败时只返回一次固定安全错误", async () => {
  let fetchCount = 0;
  let snapshotCount = 0;
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    maxReconnectAttempts: 2,
    fetchImpl: async () => {
      fetchCount += 1;
      return streamResponse([frame(makeEvent(1)), frame(makeEvent(3))]);
    },
  });
  const errors = [];
  const subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: null,
    sequence: 0,
    onEvent: () => undefined,
    reloadSnapshot: async () => {
      snapshotCount += 1;
      throw new Error("Authorization: Bearer secret-token");
    },
    onError: (error) => errors.push(error),
  });

  await subscription.done;
  assert.equal(fetchCount, 1);
  assert.equal(snapshotCount, 1);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].message, "Supervisor Snapshot 恢复失败");
  assert.doesNotMatch(errors[0].message, /secret|Authorization/u);
});

test("Snapshot 恢复期间取消订阅会静默终止", async () => {
  let markSnapshotStarted;
  const snapshotStarted = new Promise((resolve) => {
    markSnapshotStarted = resolve;
  });
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async () => streamResponse([frame(makeEvent(1)), frame(makeEvent(3))]),
  });
  const errors = [];
  const subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: null,
    sequence: 0,
    onEvent: () => undefined,
    reloadSnapshot: async (_gap, signal) => {
      markSnapshotStarted();
      return pendingResponse(signal);
    },
    onError: (error) => errors.push(error),
  });

  await snapshotStarted;
  subscription.close();
  await subscription.done;
  assert.deepEqual(errors, []);
});

test("乱序旧事件按 sequence 幂等丢弃，不回退 cursor", async () => {
  let subscription;
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async () => streamResponse([
      frame(makeEvent(5, "cursor-5")),
      frame(makeEvent(4, "cursor-4", { event_id: "evt-old" })),
      frame(makeEvent(6, "cursor-6")),
    ]),
  });
  const received = [];
  subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: "cursor-4",
    sequence: 4,
    onEvent: (event) => {
      received.push(event.sequence);
      if (event.sequence === 6) subscription.close();
    },
    reloadSnapshot: async () => assert.fail("旧事件不应触发快照恢复"),
    onError: (error) => assert.fail(`不应失败：${error.message}`),
  });

  await subscription.done;
  assert.deepEqual(received, [5, 6]);
});

test("同一网络分块可承载多个合法小事件，不按分块总长度误报过大", async () => {
  const firstFrame = frame(makeEvent(1));
  const secondFrame = frame(makeEvent(2));
  const perFrameLimit = Math.max(firstFrame.length, secondFrame.length) + 1;
  assert.ok(firstFrame.length + secondFrame.length > perFrameLimit);
  let subscription;
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    maxFrameCharacters: perFrameLimit,
    fetchImpl: async () => streamResponse([firstFrame + secondFrame]),
  });
  const received = [];
  subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: null,
    sequence: 0,
    onEvent: (event) => {
      received.push(event.sequence);
      if (event.sequence === 2) subscription.close();
    },
    reloadSnapshot: async () => assert.fail("连续事件不应加载快照"),
    onError: (error) => assert.fail(`不应失败：${error.message}`),
  });

  await subscription.done;
  assert.deepEqual(received, [1, 2]);
});

test("事件消费回调失败时立即终止，不重复投递同一事件", async () => {
  let fetchCount = 0;
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    maxReconnectAttempts: 2,
    fetchImpl: async () => {
      fetchCount += 1;
      return streamResponse([frame(makeEvent(1, "cursor-secret"))]);
    },
  });
  const errors = [];
  const subscription = client.subscribe({
    conversationId: "conv-1",
    cursor: null,
    sequence: 0,
    onEvent: () => {
      throw new Error("Authorization: Bearer secret-token");
    },
    reloadSnapshot: async () => assert.fail("回调异常不应加载快照"),
    onError: (error) => errors.push(error),
  });

  await subscription.done;
  assert.equal(fetchCount, 1);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].message, "Supervisor 事件处理失败");
  assert.doesNotMatch(errors[0].message, /secret|Authorization/u);
});

test("跨对话事件立即终止订阅且错误不暴露原始响应", async () => {
  const sensitiveEvent = makeEvent(1, "cursor-secret", {
    conversation_id: "conv-other",
    payload: { Authorization: "Bearer secret-token" },
  });
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async () => streamResponse([frame(sensitiveEvent)]),
  });
  const { subscription, events, errors } = createSubscription(client);

  await subscription.done;
  assert.deepEqual(events, []);
  assert.equal(errors.length, 1);
  assert.ok(errors[0] instanceof SupervisorEventStreamError);
  assert.equal(errors[0].message, "Supervisor 事件流返回了其他对话的事件");
  assert.doesNotMatch(errors[0].message, /secret|Authorization/u);
});

test("致命协议错误会取消底层 reader，不留下未消费长连接", async () => {
  let cancelCount = 0;
  const invalidEvent = makeEvent(1, "cursor-1", { conversation_id: "conv-other" });
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    fetchImpl: async () => new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(frame(invalidEvent)));
      },
      cancel() {
        cancelCount += 1;
      },
    }), {
      headers: { "Content-Type": "text/event-stream" },
    }),
  });
  const { subscription, errors } = createSubscription(client);

  await subscription.done;
  assert.equal(errors.length, 1);
  assert.equal(cancelCount, 1);
});

test("非 SSE 响应和非法事件只返回固定安全错误", async (t) => {
  await t.test("不读取非成功响应正文", async () => {
    let bodyRead = false;
    const client = createSupervisorEventStreamClient({
      getAuthorization: () => "Bearer token-1",
      fetchImpl: async () => ({
        ok: false,
        status: 500,
        headers: new Headers(),
        get body() {
          bodyRead = true;
          throw new Error("Authorization: Bearer secret-token");
        },
      }),
    });
    const { subscription, errors } = createSubscription(client);
    await subscription.done;
    assert.equal(bodyRead, false);
    assert.equal(errors[0].message, "Supervisor 事件流连接失败（HTTP 500）");
    assert.doesNotMatch(errors[0].message, /secret|Authorization/u);
  });

  await t.test("拒绝非 text/event-stream 成功响应", async () => {
    const client = createSupervisorEventStreamClient({
      getAuthorization: () => "Bearer token-1",
      fetchImpl: async () => new Response("<html>secret-token</html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      }),
    });
    const { subscription, errors } = createSubscription(client);
    await subscription.done;
    assert.equal(errors[0].message, "Supervisor 事件流返回了无效响应");
    assert.doesNotMatch(errors[0].message, /secret/u);
  });

  await t.test("拒绝不符合 wire 合同的 JSON", async () => {
    const client = createSupervisorEventStreamClient({
      getAuthorization: () => "Bearer token-1",
      fetchImpl: async () => streamResponse(["data: {\"Authorization\":\"Bearer secret-token\"}\n\n"]),
    });
    const { subscription, errors } = createSubscription(client);
    await subscription.done;
    assert.equal(errors[0].message, "Supervisor 事件流返回了无效事件");
    assert.doesNotMatch(errors[0].message, /secret|Authorization/u);
  });
});

test("取消订阅会中断 fetch 且不重连、不报错", async () => {
  let fetchCount = 0;
  let markFetchStarted;
  const fetchStarted = new Promise((resolve) => {
    markFetchStarted = resolve;
  });
  const client = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async (_input, init) => {
      fetchCount += 1;
      markFetchStarted();
      return pendingResponse(init.signal);
    },
  });
  const { subscription, errors } = createSubscription(client);
  await fetchStarted;
  subscription.close();
  await subscription.done;
  assert.equal(fetchCount, 1);
  assert.deepEqual(errors, []);
});
