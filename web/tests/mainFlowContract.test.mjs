import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const workspaceSource = fs.readFileSync(path.resolve("src/pages/WorkspacePage.tsx"), "utf8");
const genParamsDialogSource = fs.readFileSync(path.resolve("src/components/composer/GenParamsDialog.tsx"), "utf8");

function handleSendSource() {
  const start = workspaceSource.indexOf("const handleSend = async");
  const end = workspaceSource.indexOf("async function onEvent", start);
  assert.notEqual(start, -1, "handleSend must exist");
  assert.notEqual(end, -1, "onEvent must follow handleSend");
  return workspaceSource.slice(start, end);
}

function handleApprovePlanSource() {
  const start = workspaceSource.indexOf("const handleApprovePlan = async");
  const end = workspaceSource.indexOf("const handleRevisePlan =", start);
  assert.notEqual(start, -1, "handleApprovePlan must exist");
  assert.notEqual(end, -1, "handleRevisePlan must follow handleApprovePlan");
  return workspaceSource.slice(start, end);
}

test("new conversation stores the user message before agent replies", () => {
  const source = handleSendSource();
  const appendIndex = source.indexOf("await appendMessageForConversation(message, activeConversation)");
  const firstAgentIndex = source.indexOf('pushAssistant("正在调用采集 Agent 识别意图');
  assert.notEqual(appendIndex, -1, "handleSend must await user message persistence");
  assert.notEqual(firstAgentIndex, -1, "handleSend must still call the intake agent");
  assert.ok(appendIndex < firstAgentIndex, "user message persistence must happen before the first agent reply");
});

test("video plan approval always enters scene package and merge main flow", () => {
  const source = handleApprovePlanSource();
  assert.equal(source.includes("api.generateDirectVideo"), false, "video approval must not bypass scene packages with direct video generation");
  assert.equal(source.includes("inferDirectVideoRequest"), false, "video approval must not infer direct-video shortcuts");
  assert.equal(source.includes("api.prepareVideoScenePackages"), true, "video approval must prepare editable scene packages first");
  assert.equal(source.includes("api.generateSceneAssets"), true, "video approval must generate scene reference assets before scene video generation");
});

test("image form values preserve requested multi-image count", () => {
  const valuesStart = workspaceSource.indexOf("function valuesFromForm");
  const valuesEnd = workspaceSource.indexOf("function revisedScenePrompt", valuesStart);
  assert.notEqual(valuesStart, -1, "valuesFromForm must exist");
  assert.notEqual(valuesEnd, -1, "revisedScenePrompt must follow valuesFromForm");
  const source = workspaceSource.slice(valuesStart, valuesEnd);
  assert.match(source, /image_count:\s*form\.image_count/);
});

test("image requirement form only exposes approved image size choices", () => {
  const match = genParamsDialogSource.match(/const IMAGE_SIZES = \[(.*?)\];/s);
  assert.ok(match, "IMAGE_SIZES must be declared");
  assert.equal(match[1].includes("1:1"), true);
  assert.equal(match[1].includes("16:9"), true);
  assert.equal(match[1].includes("9:16"), true);
  assert.equal(match[1].includes("自动适配"), true);
  assert.equal(match[1].includes("3:4"), false);
  assert.equal(match[1].includes("4:5"), false);
});

test("image plan approval continues through image generation instead of stopping at prepare", () => {
  const source = handleApprovePlanSource();
  const imageBranchStart = source.indexOf('if (artifact.intent === "image")');
  const imageBranchEnd = source.indexOf("\n    setBusyForConversation(targetConversationId, true);\n    const formValues", imageBranchStart);
  assert.notEqual(imageBranchStart, -1, "image branch must exist in handleApprovePlan");
  assert.notEqual(imageBranchEnd, -1, "image branch must return before video flow");
  const imageBranch = source.slice(imageBranchStart, imageBranchEnd);
  assert.equal(imageBranch.includes("api.prepareImageGeneration"), true, "image branch must still choose the image endpoint through prepare");
  assert.equal(imageBranch.includes("api.generateImage"), true, "image branch must invoke image generation after prepare");
  assert.equal(imageBranch.includes('type: "image_result"'), true, "image branch must return an image result artifact");
});
