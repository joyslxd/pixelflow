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
