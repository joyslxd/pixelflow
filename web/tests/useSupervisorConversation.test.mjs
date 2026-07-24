import assert from "node:assert/strict";
import test from "node:test";
import React, { StrictMode, act } from "react";
import { createRoot } from "react-dom/client";

const moduleUrl = process.env.SUPERVISOR_HOOK_TEST_MODULE;
assert.ok(moduleUrl, "SUPERVISOR_HOOK_TEST_MODULE 必须指向编译后的 Supervisor hook 模块");
const eventsModuleUrl = process.env.SUPERVISOR_EVENTS_TEST_MODULE;
assert.ok(eventsModuleUrl, "SUPERVISOR_EVENTS_TEST_MODULE 必须指向编译后的 Supervisor 事件模块");

const {
  createSupervisorConversationController,
  useSupervisorConversation,
} = await import(moduleUrl);
const { createSupervisorEventStreamClient } = await import(eventsModuleUrl);
const encoder = new TextEncoder();

function projection(conversationId, sequence = 0) {
  return {
    conversationId,
    run: { runId: null, status: "idle", updatedAt: null },
    compression: {
      status: "idle",
      progressPercent: null,
      queuedInputCount: 0,
      lastOutcome: null,
      updatedAt: null,
    },
    inputQueue: [],
    resume: {
      cursor: sequence === 0 ? null : `cursor-${sequence}`,
      sequence,
    },
  };
}

function event(conversationId, sequence, overrides = {}) {
  return {
    schema_version: 1,
    event_id: `evt-${conversationId}-${sequence}`,
    sequence,
    cursor: `cursor-${sequence}`,
    conversation_id: conversationId,
    run_id: "run-1",
    occurred_at: "2026-07-24T14:45:00+08:00",
    type: "run.state_changed",
    payload: { status: "processing" },
    ...overrides,
  };
}

function createEventStreamFake() {
  const subscriptions = [];
  return {
    subscriptions,
    client: {
      subscribe(options) {
        const record = {
          options,
          closed: false,
        };
        subscriptions.push(record);
        return {
          close() {
            record.closed = true;
          },
          done: new Promise(() => undefined),
        };
      },
    },
  };
}

function createApiFake(getSnapshot) {
  return {
    getSnapshot,
    startTurn: async () => ({ status: "accepted" }),
    respondToInterrupt: async () => ({ status: "accepted" }),
    getRunStatus: async () => ({ status: "processing" }),
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function streamResponse(events) {
  const body = events
    .map((item) => `data: ${JSON.stringify(item)}\n\n`)
    .join("");
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  }), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

class TestNode extends EventTarget {
  constructor(nodeType, nodeName, ownerDocument) {
    super();
    this.nodeType = nodeType;
    this.nodeName = nodeName;
    this.tagName = nodeType === 1 ? nodeName : undefined;
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.childNodes = [];
    this.style = {};
    this.namespaceURI = "http://www.w3.org/1999/xhtml";
    this.value = "";
  }

  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  insertBefore(child, before) {
    child.parentNode = this;
    const index = this.childNodes.indexOf(before);
    if (index < 0) this.childNodes.push(child);
    else this.childNodes.splice(index, 0, child);
    return child;
  }

  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    return child;
  }

  setAttribute(name, value) {
    this[name] = String(value);
  }

  removeAttribute(name) {
    delete this[name];
  }

  get firstChild() {
    return this.childNodes[0] ?? null;
  }

  get lastChild() {
    return this.childNodes.at(-1) ?? null;
  }

  get textContent() {
    return this.nodeValue ?? "";
  }

  set textContent(value) {
    this.nodeValue = String(value);
    this.childNodes = [];
  }
}

class TestDocument extends EventTarget {
  constructor() {
    super();
    this.nodeType = 9;
    this.nodeName = "#document";
    this.documentElement = new TestNode(1, "HTML", this);
    this.body = new TestNode(1, "BODY", this);
    this.defaultView = globalThis;
    this.activeElement = this.body;
  }

  createElement(name) {
    return new TestNode(1, name.toUpperCase(), this);
  }

  createElementNS(_namespace, name) {
    return this.createElement(name);
  }

  createTextNode(value) {
    const node = new TestNode(3, "#text", this);
    node.nodeValue = value;
    return node;
  }
}

function installTestDom() {
  const previous = {
    document: globalThis.document,
    window: globalThis.window,
    HTMLElement: globalThis.HTMLElement,
    HTMLIFrameElement: globalThis.HTMLIFrameElement,
    actEnvironment: globalThis.IS_REACT_ACT_ENVIRONMENT,
  };
  const document = new TestDocument();
  globalThis.document = document;
  globalThis.window = globalThis;
  globalThis.HTMLElement = TestNode;
  globalThis.HTMLIFrameElement = class extends TestNode {};
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  return {
    container: new TestNode(1, "DIV", document),
    restore() {
      globalThis.document = previous.document;
      globalThis.window = previous.window;
      globalThis.HTMLElement = previous.HTMLElement;
      globalThis.HTMLIFrameElement = previous.HTMLIFrameElement;
      globalThis.IS_REACT_ACT_ENVIRONMENT = previous.actEnvironment;
    },
  };
}

test("控制器先恢复当前对话 Snapshot，再从恢复点订阅事件", async () => {
  const snapshotCalls = [];
  const stream = createEventStreamFake();
  const controller = createSupervisorConversationController({
    conversationId: "conv-1",
    api: createApiFake(async (conversationId, options) => {
      snapshotCalls.push({ conversationId, signal: options.signal });
      return projection(conversationId, 4);
    }),
    eventStream: stream.client,
  });
  const states = [];
  const unsubscribe = controller.subscribe(() => states.push(controller.getState()));

  await controller.start();

  assert.equal(snapshotCalls.length, 1);
  assert.equal(snapshotCalls[0].conversationId, "conv-1");
  assert.equal(snapshotCalls[0].signal.aborted, false);
  assert.equal(stream.subscriptions.length, 1);
  assert.equal(stream.subscriptions[0].options.conversationId, "conv-1");
  assert.equal(stream.subscriptions[0].options.cursor, "cursor-4");
  assert.equal(stream.subscriptions[0].options.sequence, 4);
  assert.equal(controller.getState().connection.status, "connected");

  stream.subscriptions[0].options.onEvent(event("conv-1", 5));
  assert.equal(controller.getState().run.status, "running");
  assert.equal(controller.getState().resume.sequence, 5);
  assert.ok(states.length >= 3);

  unsubscribe();
  controller.dispose();
});

test("切换对话后取消旧 Snapshot，并拒绝忽略 AbortSignal 的迟到结果", async () => {
  const oldSnapshot = deferred();
  let oldSignal;
  const oldStream = createEventStreamFake();
  const oldController = createSupervisorConversationController({
    conversationId: "conv-a",
    api: createApiFake((_conversationId, options) => {
      oldSignal = options.signal;
      return oldSnapshot.promise;
    }),
    eventStream: oldStream.client,
  });
  const oldStart = oldController.start();
  oldController.dispose();

  const newStream = createEventStreamFake();
  const newController = createSupervisorConversationController({
    conversationId: "conv-b",
    api: createApiFake(async (conversationId) => projection(conversationId, 2)),
    eventStream: newStream.client,
  });
  await newController.start();
  oldSnapshot.resolve(projection("conv-a", 99));
  await oldStart;

  assert.equal(oldSignal.aborted, true);
  assert.equal(oldStream.subscriptions.length, 0);
  assert.equal(oldController.getState().resume.sequence, 0);
  assert.equal(newController.getState().conversationId, "conv-b");
  assert.equal(newController.getState().resume.sequence, 2);

  newController.dispose();
});

test("卸载后关闭事件订阅并拒绝旧对话迟到事件", async () => {
  const stream = createEventStreamFake();
  const controller = createSupervisorConversationController({
    conversationId: "conv-a",
    api: createApiFake(async (conversationId) => projection(conversationId)),
    eventStream: stream.client,
  });
  await controller.start();
  const stateBeforeDispose = controller.getState();

  controller.dispose();
  stream.subscriptions[0].options.onEvent(event("conv-a", 1));

  assert.equal(stream.subscriptions[0].closed, true);
  assert.strictEqual(controller.getState(), stateBeforeDispose);
});

test("sequence gap 通过当前会话 Snapshot 原地恢复后返回新恢复点", async () => {
  let snapshotCount = 0;
  const stream = createEventStreamFake();
  const controller = createSupervisorConversationController({
    conversationId: "conv-1",
    api: createApiFake(async (conversationId) => {
      snapshotCount += 1;
      return projection(conversationId, snapshotCount === 1 ? 1 : 8);
    }),
    eventStream: stream.client,
  });
  await controller.start();

  const resume = await stream.subscriptions[0].options.reloadSnapshot({
    expectedSequence: 2,
    receivedSequence: 8,
    cursor: "cursor-1",
  }, new AbortController().signal);

  assert.deepEqual(resume, { cursor: "cursor-8", sequence: 8 });
  assert.equal(controller.getState().resume.sequence, 8);
  assert.equal(snapshotCount, 2);

  controller.dispose();
});

test("Turn 提交使用会话生命周期信号，卸载后不把取消误记为失败", async () => {
  const turn = deferred();
  let turnSignal;
  const api = createApiFake(async (conversationId) => projection(conversationId));
  api.startTurn = (_conversationId, _request, options) => {
    turnSignal = options.signal;
    return turn.promise;
  };
  const controller = createSupervisorConversationController({
    conversationId: "conv-1",
    api,
    eventStream: createEventStreamFake().client,
  });
  await controller.start();

  const pending = controller.startTurn({
    client_input_id: "input-1",
    content: "继续",
    materials: [],
    reply_to_message_id: null,
    artifact_refs: [],
    expected_context_version: 1,
  });
  assert.equal(controller.getState().inputQueue[0].status, "sending");

  controller.dispose();
  turn.reject(turnSignal.reason ?? new DOMException("请求已取消", "AbortError"));
  await assert.rejects(pending, { name: "AbortError" });

  assert.equal(turnSignal.aborted, true);
  assert.equal(controller.getState().inputQueue[0].status, "sending");
});

test("语义非法事件让 SSE 与 reducer 一起失败关闭且不越过恢复点", async () => {
  let fetchCount = 0;
  let subscription;
  const streamClient = createSupervisorEventStreamClient({
    getAuthorization: () => "Bearer token-1",
    reconnectDelayMs: 0,
    fetchImpl: async () => {
      fetchCount += 1;
      if (fetchCount > 1) throw new Error("后续连接不应发生");
      return streamResponse([
        event("conv-1", 1, { payload: { status: "token=secret-value" } }),
        event("conv-1", 2),
      ]);
    },
  });
  const controller = createSupervisorConversationController({
    conversationId: "conv-1",
    api: createApiFake(async (conversationId) => projection(conversationId)),
    eventStream: {
      subscribe(options) {
        subscription = streamClient.subscribe(options);
        return subscription;
      },
    },
  });

  await controller.start();
  await subscription.done;

  assert.equal(fetchCount, 1);
  assert.deepEqual(controller.getState().resume, { cursor: null, sequence: 0 });
  assert.equal(controller.getState().connection.status, "fatal");
  assert.equal(JSON.stringify(controller.getState()).includes("secret-value"), false);

  controller.dispose();
});

test("旧对话迟到的 Turn、interrupt 和 status 结果统一按取消结束", async () => {
  const lateTurn = deferred();
  const lateInterrupt = deferred();
  const lateStatus = deferred();
  const api = createApiFake(async (conversationId) => projection(conversationId));
  api.startTurn = () => lateTurn.promise;
  api.respondToInterrupt = () => lateInterrupt.promise;
  api.getRunStatus = () => lateStatus.promise;
  const controller = createSupervisorConversationController({
    conversationId: "conv-old",
    api,
    eventStream: createEventStreamFake().client,
  });
  await controller.start();

  const requests = [
    controller.startTurn({
      client_input_id: "input-late",
      content: "继续",
      materials: [],
      reply_to_message_id: null,
      artifact_refs: [],
      expected_context_version: 1,
    }),
    controller.respondToInterrupt("interrupt-1", { decision: "continue" }),
    controller.getRunStatus("run-1"),
  ];
  controller.dispose();
  lateTurn.resolve({ status: "accepted" });
  lateInterrupt.reject(new Error("Authorization: Bearer secret-token"));
  lateStatus.resolve({ status: "completed" });

  for (const request of requests) {
    await assert.rejects(request, (error) => {
      assert.equal(error.name, "AbortError");
      assert.equal(error.message, "请求已取消");
      assert.doesNotMatch(error.message, /secret|Authorization/u);
      return true;
    });
  }
});

test("effect 清理重放后可在同一控制器上重新建立当前对话订阅", async () => {
  let snapshotCount = 0;
  const stream = createEventStreamFake();
  const controller = createSupervisorConversationController({
    conversationId: "conv-1",
    api: createApiFake(async (conversationId) => {
      snapshotCount += 1;
      return projection(conversationId, snapshotCount);
    }),
    eventStream: stream.client,
  });

  await controller.start();
  controller.dispose();
  await controller.start();

  assert.equal(snapshotCount, 2);
  assert.equal(stream.subscriptions.length, 2);
  assert.equal(stream.subscriptions[0].closed, true);
  assert.equal(stream.subscriptions[1].closed, false);
  assert.equal(controller.getState().connection.status, "connected");
  assert.equal(controller.getState().resume.sequence, 2);

  controller.dispose();
});

test("StrictMode 挂载、切换和卸载隔离旧 Snapshot、事件与错误", async () => {
  const dom = installTestDom();
  const stream = createEventStreamFake();
  const delayedSnapshot = deferred();
  let delayRefresh = false;
  const api = createApiFake(async (conversationId) => {
    if (conversationId === "conv-a" && delayRefresh) return delayedSnapshot.promise;
    return projection(conversationId, conversationId === "conv-a" ? 1 : 4);
  });
  const rendered = [];

  function Harness({ conversationId }) {
    const result = useSupervisorConversation(conversationId, {
      api,
      eventStream: stream.client,
    });
    rendered.push(result);
    return null;
  }

  const root = createRoot(dom.container);
  try {
    await act(async () => {
      root.render(React.createElement(
        StrictMode,
        null,
        React.createElement(Harness, { conversationId: "conv-a" }),
      ));
    });
    const activeA = stream.subscriptions.findLast(
      (item) => item.options.conversationId === "conv-a" && !item.closed,
    );
    assert.ok(activeA);
    const resultA = rendered.at(-1);
    assert.equal(resultA.state.conversationId, "conv-a");

    delayRefresh = true;
    const oldRefresh = resultA.refreshSnapshot();
    await act(async () => {
      root.render(React.createElement(
        StrictMode,
        null,
        React.createElement(Harness, { conversationId: "conv-b" }),
      ));
    });
    const activeB = stream.subscriptions.findLast(
      (item) => item.options.conversationId === "conv-b" && !item.closed,
    );
    assert.ok(activeB);
    assert.equal(activeA.closed, true);
    const beforeLateSignals = rendered.at(-1).state;

    delayedSnapshot.resolve(projection("conv-a", 99));
    await assert.rejects(oldRefresh, { name: "AbortError", message: "请求已取消" });
    activeA.options.onEvent(event("conv-a", 2));
    activeA.options.onError(new Error("Authorization: Bearer secret-token"));
    assert.strictEqual(rendered.at(-1).state, beforeLateSignals);
    assert.equal(rendered.at(-1).state.conversationId, "conv-b");
    assert.equal(rendered.at(-1).state.resume.sequence, 4);

    await act(async () => root.unmount());
    assert.equal(activeB.closed, true);
  } finally {
    dom.restore();
  }
});

test("当前请求返回其他对话 Snapshot 时失败关闭且不启动事件订阅", async () => {
  const stream = createEventStreamFake();
  const controller = createSupervisorConversationController({
    conversationId: "conv-1",
    api: createApiFake(async () => projection("conv-other", 7)),
    eventStream: stream.client,
  });

  await controller.start();

  assert.equal(stream.subscriptions.length, 0);
  assert.deepEqual(controller.getState().connection, {
    status: "fatal",
    error: "Supervisor 连接无法恢复",
  });
  assert.deepEqual(controller.getState().resume, { cursor: null, sequence: 0 });

  controller.dispose();
});
