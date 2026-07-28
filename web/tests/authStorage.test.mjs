import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.AUTH_STORAGE_TEST_MODULE;
assert.ok(moduleUrl, "AUTH_STORAGE_TEST_MODULE must point to the compiled authStorage module");

const {
  AUTHORIZATION_STORAGE_KEY,
  clearSavedAuthorization,
  getAuthorizationFromSources,
  normalizeAuthorization,
  saveAuthorization,
  isTrustedContentAppOrigin,
  setupContentAppAuthorizationListener,
} = await import(moduleUrl);

function makeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(key, String(value));
    },
    removeItem(key) {
      data.delete(key);
    },
    dump() {
      return Object.fromEntries(data.entries());
    },
  };
}

test("normalizeAuthorization accepts raw JWT and existing Bearer value", () => {
  assert.equal(normalizeAuthorization("abc.def.ghi"), "Bearer abc.def.ghi");
  assert.equal(normalizeAuthorization("Bearer abc.def.ghi"), "Bearer abc.def.ghi");
  assert.equal(normalizeAuthorization("  bearer abc.def.ghi  "), "bearer abc.def.ghi");
  assert.equal(normalizeAuthorization(""), "");
});

test("saveAuthorization stores normalized value in the canonical Authorization key", () => {
  const localStorage = makeStorage();

  const saved = saveAuthorization("abc.def.ghi", localStorage);

  assert.equal(saved, "Bearer abc.def.ghi");
  assert.deepEqual(localStorage.dump(), {
    [AUTHORIZATION_STORAGE_KEY]: "Bearer abc.def.ghi",
  });
});

test("getAuthorizationFromSources prefers injected value, then localStorage, then sessionStorage", () => {
  const localStorage = makeStorage({ token: "local-token" });
  const sessionStorage = makeStorage({ Authorization: "session-token" });

  assert.equal(
    getAuthorizationFromSources({ injected: "injected-token", localStorage, sessionStorage }),
    "Bearer injected-token",
  );
  assert.equal(
    getAuthorizationFromSources({ localStorage, sessionStorage }),
    "Bearer local-token",
  );
  assert.equal(
    getAuthorizationFromSources({ localStorage: makeStorage(), sessionStorage }),
    "Bearer session-token",
  );
});

test("clearSavedAuthorization removes all supported local testing keys", () => {
  const localStorage = makeStorage({
    Authorization: "a",
    authorization: "b",
    token: "c",
  });

  clearSavedAuthorization(localStorage);

  assert.deepEqual(localStorage.dump(), {});
});

test("isTrustedContentAppOrigin accepts local, test, and production content-app origins", () => {
  assert.equal(isTrustedContentAppOrigin("http://localhost:5174"), true);
  assert.equal(isTrustedContentAppOrigin("https://test-video.borgrise.com"), true);
  assert.equal(isTrustedContentAppOrigin("https://video.borgrise.com"), true);
  assert.equal(isTrustedContentAppOrigin("https://example.com"), false);
});

test("可信 content-app 用户消息会保留 Supervisor 目标定位元数据", () => {
  const originalWindow = globalThis.window;
  const originalCustomEvent = globalThis.CustomEvent;
  const listeners = new Map();
  const dispatched = [];
  globalThis.window = {
    location: { origin: "http://localhost:5174" },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
    dispatchEvent(event) {
      dispatched.push(event);
      return true;
    },
  };
  globalThis.CustomEvent = class {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  };

  try {
    const cleanup = setupContentAppAuthorizationListener();
    listeners.get("message")({
      origin: "http://localhost:5174",
      data: {
        type: "AGENT_USER_MESSAGE",
        content: "同意这个方案",
        materials: [{ artifact_ref: "artifact:video-plan:wf_001:v1:hash" }],
        reply_to_message_id: "msg_plan_001",
        artifact_refs: ["artifact:video-plan:wf_001:v1:hash"],
        interrupt_id: "interrupt_plan_001",
      },
    });

    assert.deepEqual(window.__CONTENT_APP_USER_MESSAGE__, {
      content: "同意这个方案",
      materials: [{ artifact_ref: "artifact:video-plan:wf_001:v1:hash" }],
      reply_to_message_id: "msg_plan_001",
      artifact_refs: ["artifact:video-plan:wf_001:v1:hash"],
      interrupt_id: "interrupt_plan_001",
    });
    assert.deepEqual(dispatched[0].detail, window.__CONTENT_APP_USER_MESSAGE__);
    cleanup();
    assert.equal(listeners.has("message"), false);
  } finally {
    globalThis.window = originalWindow;
    globalThis.CustomEvent = originalCustomEvent;
  }
});
