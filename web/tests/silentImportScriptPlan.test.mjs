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
  assert.match(stages, /SCRIPT_VERSION_PREVIEW_LINK_RE/);
  assert.match(stages, /export function splitScriptVersionPreviewParts/);
  assert.ok(stages.includes("已(?:更新|导入)脚本版本"));
  assert.ok(stages.includes("在右侧查看脚本"));
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
  // 未编辑确认不得把 script.content 原文塞回拆解 episode
  assert.match(source, /dirty \? draft\.trim\(\) : ""/);
});

test("confirm script prefers structured stages over raw script content", () => {
  const workspace = fs.readFileSync(
    path.resolve("src/features/legacy-workspace/LegacyWorkspace.tsx"),
    "utf8",
  );
  assert.match(workspace, /preferStages/);
  assert.match(workspace, /scriptContent: preferStages \? "" :/);
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
  assert.match(workspace, /openScriptPreviewFromChat/);
  assert.match(workspace, /onOpenScriptPreview=\{openScriptPreviewFromChat\}/);
  assert.match(workspace, /setScriptPreviewOpen\(true\)/);
  assert.match(workspace, /scriptPreviewOpen && \(/);
  assert.match(workspace, /onOpenScriptPreview=\{nativeScriptPreviewOpener\}/);
  assert.match(workspace, /AgentTurnGroup/);
});

test("script preview panel stays closed until chat opens it", () => {
  const workspace = fs.readFileSync(
    path.resolve("src/features/legacy-workspace/LegacyWorkspace.tsx"),
    "utf8",
  );
  const panel = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentScriptPreviewPanel.tsx"),
    "utf8",
  );
  assert.match(workspace, /const \[scriptPreviewOpen, setScriptPreviewOpen\] = useState\(false\)/);
  assert.doesNotMatch(
    workspace,
    /工作区有脚本草稿时关掉画布，露出右侧脚本预览/,
  );
  assert.match(panel, /收起脚本预览/);
  assert.match(panel, /onClose\?/);
});

test("plan timeline shows 规划中 while steps are empty", () => {
  const source = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentPlanTimeline.tsx"),
    "utf8",
  );
  assert.match(source, /规划中/);
  assert.match(source, /规划中，正在生成执行步骤/);
});

test("plan timeline shows 等待补充 for waiting_for_input", () => {
  const timeline = fs.readFileSync(
    path.resolve("src/features/video-agent/AgentPlanTimeline.tsx"),
    "utf8",
  );
  const contracts = fs.readFileSync(
    path.resolve("src/features/video-agent/state/contracts.ts"),
    "utf8",
  );
  const reducer = fs.readFileSync(
    path.resolve("src/features/video-agent/state/reducer.ts"),
    "utf8",
  );
  assert.match(contracts, /waiting_for_input/);
  assert.match(reducer, /waiting_for_input/);
  assert.match(timeline, /等待补充/);
  assert.match(timeline, /等待你在对话框补充所需信息后再继续规划/);
  assert.doesNotMatch(
    timeline,
    /等待你补充所需信息后再继续规划\s*\{?\$?\{?plan\.publicGoal/,
    "waiting body must not repeat publicGoal",
  );
  assert.match(timeline, /plan\.status === "waiting_for_input"/);
});

test("supervisor reducer keeps thinking open across plan updates", () => {
  const source = fs.readFileSync(
    path.resolve("src/lib/supervisor/reducer.ts"),
    "utf8",
  );
  assert.match(source, /agent\.plan\.updated/);
  assert.match(source, /思考流先于 Plan/);
});

test("intake_draft script projects without artifact_ref; preview stays user-opened", () => {
  const workspace = fs.readFileSync(
    path.resolve("src/features/video-agent/state/workspace.ts"),
    "utf8",
  );
  const legacy = fs.readFileSync(
    path.resolve("src/features/legacy-workspace/LegacyWorkspace.tsx"),
    "utf8",
  );
  assert.match(
    workspace,
    /artifact:script:draft:v\$\{version/,
    "missing artifact_ref must synthesize draft ref",
  );
  assert.match(legacy, /scriptPreviewOpen/);
  assert.match(legacy, /waiting_for_input/);
});

test("asset package markdown merges script preview characters and outline", () => {
  const stages = fs.readFileSync(
    path.resolve("src/features/video-agent/scriptSkillStages.ts"),
    "utf8",
  );
  const prompts = fs.readFileSync(
    path.resolve("../backend/pixelflow/video_agent/prompts.py"),
    "utf8",
  );
  assert.match(stages, /export function buildAssetPackagePlanMarkdown/);
  assert.match(stages, /stageId === "outline"/);
  assert.match(stages, /分镜提示词|镜头列表|分镜大纲/);
  assert.match(prompts, /prepare_scene_packages/);
  assert.match(prompts, /script_pipeline\.characters\/outline|优先投影 script_pipeline/);
});
