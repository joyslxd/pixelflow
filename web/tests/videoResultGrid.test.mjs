import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const sourcePath = new URL("../src/components/canvas/VideoResultGrid.tsx", import.meta.url);
const source = readFileSync(sourcePath, "utf8");

test("video result cards do not overlay custom controls on top of native video controls", () => {
  assert.match(source, /<video[\s\S]*\bcontrols\b/);
  assert.doesNotMatch(source, /absolute bottom-1\.5 right-1\.5/);
  assert.doesNotMatch(source, /aria-label="播放"/);
});
