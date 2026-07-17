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

test("uploadAttachment reports real upload progress through XMLHttpRequest when onProgress is provided", async () => {
  const previousWindow = globalThis.window;
  const previousXhr = globalThis.XMLHttpRequest;
  const testWindow = makeWindow();
  testWindow.__CONTENT_APP_AUTHORIZATION__ = "Bearer progress-token";
  globalThis.window = testWindow;

  let capturedMethod = "";
  let capturedUrl = "";
  let capturedAuthorization = "";
  let capturedBody;
  class FakeXMLHttpRequest {
    upload = {};
    status = 200;
    statusText = "OK";
    responseText = JSON.stringify({
      success: true,
      filename: "asset.webp",
      size: 2048,
      url: "https://x/asset.webp",
    });

    open(method, url) {
      capturedMethod = method;
      capturedUrl = url;
    }

    setRequestHeader(key, value) {
      if (key === "Authorization") capturedAuthorization = value;
    }

    send(body) {
      capturedBody = body;
      this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 4 });
      this.onload?.();
    }
  }
  globalThis.XMLHttpRequest = FakeXMLHttpRequest;

  try {
    const progress = [];
    const file = new File(["fake"], "asset.webp", { type: "image/webp" });
    const uploaded = await api.uploadAttachment(file, { onProgress: percent => progress.push(percent) });

    assert.equal(capturedMethod, "POST");
    assert.equal(capturedUrl, "/api/upload");
    assert.equal(capturedAuthorization, "Bearer progress-token");
    assert.ok(capturedBody instanceof FormData);
    assert.deepEqual(progress, [25, 100]);
    assert.equal(uploaded.url, "https://x/asset.webp");
  } finally {
    if (previousXhr === undefined) {
      delete globalThis.XMLHttpRequest;
    } else {
      globalThis.XMLHttpRequest = previousXhr;
    }
    if (previousWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = previousWindow;
    }
  }
});

test("content asset clients list projects and create an uploaded image asset", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const testWindow = makeWindow();
  testWindow.__CONTENT_APP_AUTHORIZATION__ = "Bearer asset-token";
  globalThis.window = testWindow;

  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if (String(url) === "/api/projects") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ success: true, projects: [{ id: "105" }] }),
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        data: {
          id: 9342,
          assetType: "image",
          assetSource: "upload",
          projectId: "105",
          name: "asset.png",
          refrenceUrl: "https://x/asset.png",
        },
      }),
    };
  };

  try {
    const projects = await api.listContentProjects();
    const created = await api.createContentImageAsset({
      projectId: projects[0].id,
      name: "asset.png",
      refrenceUrl: "https://x/asset.png",
    });

    assert.equal(projects[0].id, "105");
    assert.equal(created.id, 9342);
    assert.equal(calls[0].url, "/api/projects");
    assert.equal(calls[0].init.headers.Authorization, "Bearer asset-token");
    assert.equal(calls[1].url, "/api/asset/create");
    assert.deepEqual(JSON.parse(calls[1].init.body), {
      assetType: "image",
      assetSource: "upload",
      projectId: "105",
      name: "asset.png",
      refrenceUrl: "https://x/asset.png",
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
