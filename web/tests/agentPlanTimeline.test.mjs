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
  assert.match(source, /setInterval/);
  assert.match(source, /1_000/);
  assert.match(source, /执行方案/);
  assert.match(source, /查看本步新增/);
  assert.match(source, /changeHints|changeSummary|scriptStages/);
  assert.doesNotMatch(source, /在右侧查看结果/);
  assert.match(source, /progressLog/);
  assert.match(source, /已交给大模型|正在执行该步骤|liveStatus|brief_generate|调用创意脚本/);
  assert.doesNotMatch(source, /toolArguments|chainOfThought|reasoning/);
  assert.doesNotMatch(source, /md:grid-cols-2|xl:grid-cols-3/);
});
