import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.API_TEST_MODULE;
assert.ok(moduleUrl, "API_TEST_MODULE must point to the compiled api module");

const { api } = await import(moduleUrl);

function makeWindow() {
  const listeners = new Map();
  const storage = new Map();

  return {
    localStorage: {
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      setItem(key, value) {
        storage.set(key, String(value));
      },
      removeItem(key) {
        storage.delete(key);
      },
    },
    sessionStorage: {
      getItem() {
        return null;
      },
      setItem() {},
      removeItem() {},
    },
    addEventListener(type, listener) {
      const set = listeners.get(type) || new Set();
      set.add(listener);
      listeners.set(type, set);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    dispatchEvent(event) {
      for (const listener of listeners.get(event.type) || []) {
        listener(event);
      }
      return true;
    },
    setTimeout,
    clearTimeout,
  };
}

test("API requests wait briefly for content-app authorization injected after iframe startup", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const testWindow = makeWindow();
  globalThis.window = testWindow;

  let capturedAuthorization = "";
  globalThis.fetch = async (_url, init) => {
    capturedAuthorization = init?.headers?.Authorization || "";
    return {
      ok: true,
      status: 200,
      json: async () => ({ items: [], next_cursor: null }),
    };
  };

  try {
    const request = api.listConversations();
    setTimeout(() => {
      testWindow.__CONTENT_APP_AUTHORIZATION__ = "Bearer delayed-token";
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

test("uploadAttachment posts multipart file to content-app upload API with authorization", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const testWindow = makeWindow();
  testWindow.__CONTENT_APP_AUTHORIZATION__ = "Bearer upload-token";
  globalThis.window = testWindow;

  let capturedUrl = "";
  let capturedAuthorization = "";
  let capturedBody;
  let hasJsonContentType = false;
  globalThis.fetch = async (url, init) => {
    capturedUrl = String(url);
    capturedAuthorization = init?.headers?.Authorization || "";
    hasJsonContentType = Boolean(init?.headers?.["Content-Type"]);
    capturedBody = init?.body;
    return {
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        filename: "product.png",
        size: 1234,
        url: "https://x/product.png",
        path: "https://x/product.png",
      }),
    };
  };

  try {
    const file = new File(["fake"], "product.png", { type: "image/png" });
    const uploaded = await api.uploadAttachment(file);

    assert.equal(capturedUrl, "/api/upload");
    assert.equal(capturedAuthorization, "Bearer upload-token");
    assert.equal(hasJsonContentType, false);
    assert.ok(capturedBody instanceof FormData);
    assert.deepEqual(uploaded, {
      name: "product.png",
      filename: "product.png",
      size: 1234,
      type: "image",
      mimeType: "image/png",
      url: "https://x/product.png",
      path: "https://x/product.png",
    });
  } finally {
    globalThis.fetch = previousFetch;
    if (previousWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = previousWindow;
    }
  }
});
