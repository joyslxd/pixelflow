import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("agent timeline renders persisted step status and duration without hidden reasoning", () => {
  const source = readFileSync(
    new URL("../src/features/video-agent/AgentPlanTimeline.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /durationMs/);
  assert.match(source, /正在执行/);
  assert.match(source, /等待确认/);
  assert.doesNotMatch(source, /toolArguments|chainOfThought|reasoning/);
});
