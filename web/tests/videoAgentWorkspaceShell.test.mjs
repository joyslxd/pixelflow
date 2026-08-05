import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("workspace page is a thin V2 route shell and legacy implementation is isolated", () => {
  const page = read("../src/pages/WorkspacePage.tsx");
  const legacy = read("../src/features/legacy-workspace/LegacyWorkspace.tsx");

  assert.match(page, /VideoAgentWorkspace/);
  assert.ok(page.split("\n").length <= 200);
  assert.match(legacy, /LegacyWorkspace/);
});
