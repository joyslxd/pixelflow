import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SUPERVISOR_API_TEST_MODULE;
assert.ok(moduleUrl, "SUPERVISOR_API_TEST_MODULE 必须指向编译后的 Supervisor API 模块");

const {
  SupervisorApiError,
  createSupervisorApiTransport,
} = await import(moduleUrl);

function jsonResponse(value, init = {}) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function makeWindow() {
  const listeners = new Map();
  return {
    localStorage: {
      getItem() { return null; },
      setItem() {},
      removeItem() {},
    },
    sessionStorage: {
      getItem() { return null; },
      setItem() {},
      removeItem() {},
    },
    addEventListener(type, listener) {
      const values = listeners.get(type) || new Set();
      values.add(listener);
      listeners.set(type, values);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    dispatchEvent(event) {
      for (const listener of listeners.get(event.type) || []) listener(event);
      return true;
    },
    setTimeout,
    clearTimeout,
  };
}

test("六类 Supervisor 请求使用 /agent 路径、编码标识并透传请求体", async () => {
  const calls = [];
  const signal = new AbortController().signal;
  const transport = createSupervisorApiTransport({
    fetchImpl: async (url, init = {}) => {
      calls.push({ url: String(url), init });
      return jsonResponse({ ok: true, index: calls.length });
    },
    getAuthorization: () => "transport-token",
  });
  const turn = {
    client_input_id: "client-001",
    content: "继续",
    materials: [],
    reply_to_message_id: null,
    artifact_refs: [],
    expected_context_version: 12,
  };
  const interruptResponse = {
    client_response_id: "response-001",
    value: { action: "approve" },
  };
  const confirmationResponse = {
    step_id: "step/001",
    decision: "confirm",
  };

  await transport.getSnapshot("conv/001", { signal });
  await transport.startTurn("conv/001", turn, { signal });
  await transport.respondToInterrupt("conv/001", "interrupt/001", interruptResponse, { signal });
  await transport.respondToVideoAgentConfirmation(
    "conv/001",
    "confirmation/001",
    confirmationResponse,
    { signal },
  );
  await transport.respondToVideoAgentQuota(
    "conv/001",
    "quota/001",
    { decision: "resume" },
    { signal },
  );
  await transport.getRunStatus("conv/001", "run/001", { signal });

  assert.deepEqual(calls.map(call => call.url), [
    "/agent/conversations/conv%2F001/agent-snapshot",
    "/agent/conversations/conv%2F001/turns/start",
    "/agent/conversations/conv%2F001/interrupts/interrupt%2F001/responses",
    "/agent/conversations/conv%2F001/video-agent/confirmations/confirmation%2F001/responses",
    "/agent/conversations/conv%2F001/video-agent/quota/quota%2F001/responses",
    "/agent/conversations/conv%2F001/turns/jobs/run%2F001",
  ]);
  assert.deepEqual(calls.map(call => call.init.method), ["GET", "POST", "POST", "POST", "POST", "GET"]);
  for (const call of calls) {
    assert.equal(call.init.headers.Authorization, "Bearer transport-token");
    assert.equal(call.init.signal, signal);
  }
  assert.equal(calls[0].init.headers["Content-Type"], undefined);
  assert.equal(calls[1].init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[1].init.body), turn);
  assert.deepEqual(JSON.parse(calls[2].init.body), interruptResponse);
  assert.deepEqual(JSON.parse(calls[3].init.body), confirmationResponse);
  assert.deepEqual(JSON.parse(calls[4].init.body), { decision: "resume" });
});

test("请求开始前已取消时不读取鉴权也不发送网络请求", async () => {
  const controller = new AbortController();
  controller.abort();
  let authorizationReads = 0;
  let fetchCalls = 0;
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => {
      fetchCalls += 1;
      return jsonResponse({});
    },
    getAuthorization: () => {
      authorizationReads += 1;
      return "unused-token";
    },
  });

  await assert.rejects(
    transport.getSnapshot("conv-001", { signal: controller.signal }),
    error => error?.name === "AbortError",
  );
  assert.equal(authorizationReads, 0);
  assert.equal(fetchCalls, 0);
});

test("默认鉴权等待 content-app 在 iframe 启动后延迟注入", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const testWindow = makeWindow();
  globalThis.window = testWindow;
  let capturedAuthorization = "";
  globalThis.fetch = async (_url, init = {}) => {
    capturedAuthorization = init.headers.Authorization;
    return jsonResponse({ ok: true });
  };

  try {
    const request = createSupervisorApiTransport().getSnapshot("conv-001");
    setTimeout(() => {
      testWindow.__CONTENT_APP_AUTHORIZATION__ = "delayed-token";
      testWindow.dispatchEvent(new Event("contentAppAuthorizationReady"));
    }, 10);

    await request;
    assert.equal(capturedAuthorization, "Bearer delayed-token");
  } finally {
    globalThis.fetch = previousFetch;
    if (previousWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = previousWindow;
    }
  }
});

test("取消信号立即打断尚未完成的鉴权读取", async () => {
  const controller = new AbortController();
  let resolveAuthorization;
  let fetchCalls = 0;
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => {
      fetchCalls += 1;
      return jsonResponse({});
    },
    getAuthorization: () => new Promise(resolve => {
      resolveAuthorization = resolve;
    }),
  });

  const request = transport.getSnapshot("conv-001", { signal: controller.signal });
  await Promise.resolve();
  controller.abort();
  const timeoutMarker = Symbol("timeout");
  const outcome = await Promise.race([
    request.then(
      () => ({ resolved: true }),
      error => ({ error }),
    ),
    new Promise(resolve => setTimeout(() => resolve(timeoutMarker), 50)),
  ]);
  resolveAuthorization?.("late-token");

  assert.notEqual(outcome, timeoutMarker);
  assert.equal(outcome.error?.name, "AbortError");
  assert.equal(fetchCalls, 0);
});

test("请求进行中取消时由同一个 AbortSignal 终止 fetch", async () => {
  const controller = new AbortController();
  let capturedSignal;
  let markFetchStarted;
  const fetchStarted = new Promise(resolve => {
    markFetchStarted = resolve;
  });
  const transport = createSupervisorApiTransport({
    fetchImpl: (_url, init = {}) => new Promise((_resolve, reject) => {
      capturedSignal = init.signal;
      markFetchStarted();
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
    }),
    getAuthorization: async () => "Bearer async-token",
  });

  const request = transport.getRunStatus("conv-001", "run-001", { signal: controller.signal });
  await fetchStarted;
  controller.abort();

  await assert.rejects(request, error => error?.name === "AbortError");
  assert.equal(capturedSignal, controller.signal);
});

test("缺少 Authorization 时以 401 失败且不发送请求", async () => {
  let fetchCalls = 0;
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => {
      fetchCalls += 1;
      return jsonResponse({});
    },
    getAuthorization: () => "",
  });

  await assert.rejects(
    transport.getSnapshot("conv-001"),
    error => error instanceof SupervisorApiError
      && error.status === 401
      && /Authorization/u.test(error.message),
  );
  assert.equal(fetchCalls, 0);
});

test("非成功响应保留 HTTP 状态但不信任服务端响应正文", async () => {
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => jsonResponse(
      { detail: "上下文版本冲突" },
      { status: 409, statusText: "Conflict" },
    ),
    getAuthorization: () => "safe-token",
  });

  await assert.rejects(
    transport.startTurn("conv-001", {
      client_input_id: "client-001",
      content: "继续",
      materials: [],
      reply_to_message_id: null,
      artifact_refs: [],
      expected_context_version: 12,
    }),
    error => error instanceof SupervisorApiError
      && error.status === 409
      && error.message === "Supervisor API 请求失败（HTTP 409）",
  );
});

test("非成功响应不读取可能包含敏感信息或抛出 AbortError 的正文", async () => {
  let jsonReads = 0;
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => ({
      ok: false,
      status: 500,
      json: async () => {
        jsonReads += 1;
        throw new DOMException("请求已取消", "AbortError");
      },
    }),
    getAuthorization: () => "safe-token",
  });

  await assert.rejects(
    transport.getSnapshot("conv-001"),
    error => error instanceof SupervisorApiError
      && error.status === 500
      && error.message === "Supervisor API 请求失败（HTTP 500）",
  );
  assert.equal(jsonReads, 0);
});

test("服务端敏感错误和非 JSON 错误统一降级为安全文案", async () => {
  const unsafeResponses = [
    jsonResponse({ detail: "Authorization: Bearer private-token" }, { status: 500 }),
    jsonResponse({ message: "https://provider.example/result?token=private" }, { status: 500 }),
    jsonResponse({ error: "Traceback /Users/example/private.py secret=private" }, { status: 500 }),
    new Response("API key: private", { status: 500 }),
  ];

  for (const response of unsafeResponses) {
    const transport = createSupervisorApiTransport({
      fetchImpl: async () => response,
      getAuthorization: () => "safe-token",
    });
    await assert.rejects(
      transport.getSnapshot("conv-001"),
      error => error instanceof SupervisorApiError
        && error.status === 500
        && error.message === "Supervisor API 请求失败（HTTP 500）",
    );
  }
});

test("204 空响应按协议错误处理而不伪装成任意 JSON 类型", async () => {
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => new Response(null, { status: 204 }),
    getAuthorization: () => "safe-token",
  });

  await assert.rejects(
    transport.getSnapshot("conv-001"),
    error => error instanceof SupervisorApiError
      && error.status === 502
      && error.message === "Supervisor API 返回空响应",
  );
});

test("成功响应的非法 JSON 统一映射为安全协议错误", async () => {
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token at /Applications/private/config.json");
      },
    }),
    getAuthorization: () => "safe-token",
  });

  await assert.rejects(
    transport.getSnapshot("conv-001"),
    error => error instanceof SupervisorApiError
      && error.status === 502
      && error.message === "Supervisor API 返回无效 JSON 响应",
  );
});

test("成功响应读取期间的 AbortError 保持取消语义", async () => {
  const transport = createSupervisorApiTransport({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new DOMException("请求已取消", "AbortError");
      },
    }),
    getAuthorization: () => "safe-token",
  });

  await assert.rejects(
    transport.getSnapshot("conv-001"),
    error => error?.name === "AbortError",
  );
});
