import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import fs from "node:fs";

test("V2 no longer hides import or production-fields plans from timeline", () => {
  const workspace = fs.readFileSync(
    path.resolve("src/features/legacy-workspace/LegacyWorkspace.tsx"),
    "utf8",
  );
  const stages = fs.readFileSync(
    path.resolve("src/features/video-agent/scriptSkillStages.ts"),
    "utf8",
  );
  assert.doesNotMatch(workspace, /isSilentImportScriptPlan\(plan\)/);
  assert.doesNotMatch(workspace, /isSilentProductionFieldsPlan\(plan\)/);
  assert.doesNotMatch(workspace, /import-result:\$\{planId\}/);
  assert.doesNotMatch(workspace, /fields-result:\$\{planId\}/);
  assert.match(stages, /export function isSilentImportScriptPlan/);
  assert.match(stages, /return false/);
});

test("script version preview link helpers remain available", () => {
  const stages = fs.readFileSync(
    path.resolve("src/features/video-agent/scriptSkillStages.ts"),
    "utf8",
  );
  assert.match(stages, /export function splitScriptVersionPreviewParts/);
  assert.match(stages, /已(?:更新|导入)脚本版本/);
});

test("script preview panel keeps edit and confirm at draft footer", () => {
  const source = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentScriptPreviewPanel.tsx"),
    "utf8",
  );
  assert.match(source, /脚本草稿已就绪/);
  assert.match(source, /confirming \? "确认中…" : "确认"/);
  assert.match(source, /PencilLine/);
  assert.match(source, /border-t border-slate-100 pt-3/);
});

test("message bubble opens script preview from version phrase", () => {
  const bubble = fs.readFileSync(
    path.resolve("src/components/chat/MessageBubble.tsx"),
    "utf8",
  );
  const workspace = fs.readFileSync(
    path.resolve("src/features/legacy-workspace/LegacyWorkspace.tsx"),
    "utf8",
  );
  assert.match(bubble, /splitScriptVersionPreviewParts/);
  assert.match(bubble, /onOpenScriptPreview/);
  assert.match(workspace, /onOpenScriptPreview=\{\(\) => \{/);
});

test("plan timeline shows 规划中 while steps are empty", () => {
  const source = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentPlanTimeline.tsx"),
    "utf8",
  );
  assert.match(source, /规划中/);
  assert.match(source, /规划中，正在生成执行步骤/);
});

test("supervisor reducer keeps thinking open across plan updates", () => {
  const source = fs.readFileSync(
    path.resolve("src/lib/supervisor/reducer.ts"),
    "utf8",
  );
  assert.match(source, /agent\.plan\.updated/);
  assert.match(source, /思考流先于 Plan/);
});
