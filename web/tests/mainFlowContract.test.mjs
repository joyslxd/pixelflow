import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const workspaceSource = fs.readFileSync(path.resolve("src/pages/WorkspacePage.tsx"), "utf8");
const genParamsDialogSource = fs.readFileSync(path.resolve("src/components/composer/GenParamsDialog.tsx"), "utf8");
const chatPanelSource = fs.readFileSync(path.resolve("src/components/chat/ChatPanel.tsx"), "utf8");
const messageBubbleSource = fs.readFileSync(path.resolve("src/components/chat/MessageBubble.tsx"), "utf8");
const apiSource = fs.readFileSync(path.resolve("src/lib/api.ts"), "utf8");

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

test("persisted chat messages keep the optimistic client id for action dedupe", () => {
  const persistStart = workspaceSource.indexOf("const persistChatMessage = async");
  const persistEnd = workspaceSource.indexOf("const appendMessageForConversation", persistStart);
  const responseStart = workspaceSource.indexOf("function messageFromResponse");
  const responseEnd = workspaceSource.indexOf("export function WorkspacePage", responseStart);
  assert.notEqual(persistStart, -1, "persistChatMessage must exist");
  assert.notEqual(persistEnd, -1, "appendMessageForConversation must follow persistChatMessage");
  assert.notEqual(responseStart, -1, "messageFromResponse must exist");
  assert.notEqual(responseEnd, -1, "WorkspacePage must follow messageFromResponse");
  const persistSource = workspaceSource.slice(persistStart, persistEnd);
  const responseSource = workspaceSource.slice(responseStart, responseEnd);
  assert.match(persistSource, /client_message_id:\s*message\.id/, "persisted payload must include the frontend client message id");
  assert.match(persistSource, /id:\s*message\.id/, "saved optimistic message must keep the same id used by pending timers");
  assert.match(responseSource, /client_message_id/, "restored history must prefer the persisted client message id");
});

test("artifact action dedupe is scoped by conversation id", () => {
  assert.match(workspaceSource, /function processedArtifactKey/, "WorkspacePage must build a stable artifact action key");
  assert.match(workspaceSource, /conversationId \|\| "local"/, "artifact action key must include the owning conversation id");
  assert.match(workspaceSource, /beginArtifactAction\(msg,\s*targetConversationId\)/, "artifact actions must be guarded after resolving message conversation");
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

test("ppt intent opens a ppt requirement form instead of image video planning", () => {
  assert.match(genParamsDialogSource, /export type CreationIntent = "video" \| "image" \| "ppt"/, "CreationIntent must include ppt");
  assert.match(genParamsDialogSource, /PPT生成需求收集/, "GenParamsDialog must render a PPT form");
  assert.match(genParamsDialogSource, /ppt_topic/, "PPT form must include ppt_topic");
  assert.match(genParamsDialogSource, /ppt_style/, "PPT form must include ppt_style");
  assert.match(genParamsDialogSource, /attachments/, "PPT form must include attachments");
  assert.match(workspaceSource, /if \(intake\.intent === "ppt"\)/, "handleSend must branch PPT intent before image/video creation flow");
  assert.match(workspaceSource, /api\.startPptSummaryJob/, "PPT form confirmation must start SmartPPT summary job");
});

test("ppt form supports custom style and does not expose free-form as a fixed option", () => {
  const match = genParamsDialogSource.match(/const PPT_STYLES = \[(.*?)\];/s);
  assert.ok(match, "PPT_STYLES must be declared");
  assert.equal(match[1].includes("自定义"), true);
  assert.equal(match[1].includes("自由发挥"), false);
  assert.match(genParamsDialogSource, /pptStyleMode/, "PPT dialog must track selected style mode separately from submitted style");
  assert.match(genParamsDialogSource, /pptCustomStyle/, "PPT dialog must expose a custom style input state");
  assert.match(genParamsDialogSource, /placeholder="输入自定义 PPT 风格"/, "custom style input should guide the user");
});

test("restoring or creating a conversation clears stale pending dialog attachments", () => {
  const applyStart = workspaceSource.indexOf("const applySnapshot = ");
  const applyEnd = workspaceSource.indexOf("const makeSnapshot", applyStart);
  const resetStart = workspaceSource.indexOf("const resetWorkspace = ");
  const resetEnd = workspaceSource.indexOf("const applyConversation", resetStart);
  assert.notEqual(applyStart, -1, "applySnapshot must exist");
  assert.notEqual(applyEnd, -1, "makeSnapshot must follow applySnapshot");
  assert.notEqual(resetStart, -1, "resetWorkspace must exist");
  assert.notEqual(resetEnd, -1, "applyConversation must follow resetWorkspace");
  const applySource = workspaceSource.slice(applyStart, applyEnd);
  const resetSource = workspaceSource.slice(resetStart, resetEnd);
  assert.match(applySource, /setPendingMaterials\(Array\.isArray\(snapshot\.pendingMaterials\) \? snapshot\.pendingMaterials : \[\]\)/, "restore must replace missing pending materials with an empty list");
  assert.match(applySource, /setPendingFormValues\(\{\}\)/, "restore must clear stale pending form values");
  assert.match(applySource, /pendingDialogContextRef\.current = null/, "restore must clear stale pending dialog context");
  assert.match(resetSource, /setPendingFormValues\(\{\}\)/, "new conversation reset must clear pending form values");
});

test("image edit intake bypasses the normal directions and plan flow", () => {
  const source = handleSendSource();
  assert.match(workspaceSource, /pendingImageEditRequestRef/, "Workspace must store an image-edit request while waiting for upload");
  assert.match(workspaceSource, /function isImageEditIntake/, "Workspace must detect image-edit intake metadata");
  assert.match(workspaceSource, /const executeDirectImageEdit = async/, "Workspace must have a direct image-edit executor");
  assert.match(source, /if \(pendingImageEditRequestRef\.current\?\.conversationId === activeConversation\)/, "next user upload must resume pending image edit");
  assert.match(source, /if \(intake\.intent === "image" && isImageEditIntake\(intake, text\)\)/, "image-edit intake must branch before the generic image form");
  assert.match(source, /请上传需要编辑的图片/, "missing source image should prompt the user to upload");
  assert.match(source, /executeDirectImageEdit/, "image-edit branch must call the direct executor");
});

test("ppt image pages stream partial status into the existing artifact card", () => {
  assert.match(apiSource, /type PptJobStatusCallback/, "PPT job polling must expose status callbacks");
  assert.match(apiSource, /startPptImagesJob:[\s\S]*onStatus\?: PptJobStatusCallback/, "PPT image job must accept a status callback");
  assert.match(workspaceSource, /pendingPptImagesFromContentJson/, "Workspace must show PPT page placeholders before images finish");
  assert.match(workspaceSource, /updatePptImagesArtifactInMessage/, "Workspace must update the existing PPT image card");
  assert.match(workspaceSource, /api\.startPptImagesJob\([\s\S]*partialImages\?\.pages/, "PPT image polling must stream page status into the card");
});

test("closing the requirement dialog cancels and terminates the pending flow", () => {
  assert.match(workspaceSource, /const handleCancelParamsDialog = \(\) =>/, "Workspace must have an explicit dialog-cancel handler");
  assert.match(workspaceSource, /pendingDialogContextRef\.current = null/, "cancel must clear pending dialog context");
  assert.match(workspaceSource, /已取消当前需求表单，流程已终止/, "cancel should write a visible terminal message");
  assert.match(workspaceSource, /last_phase:\s*"form_cancelled"/, "cancel should persist a cancelled phase");
  assert.match(workspaceSource, /onCancel=\{handleCancelParamsDialog\}/, "GenParamsDialog X must call the flow-cancel handler");
});

test("only the latest artifact card can trigger actions while idle and all actions are blocked while busy", () => {
  assert.match(workspaceSource, /busy=\{busy \|\| dialogOpen\}/, "open dialogs must keep the chat in busy mode");
  assert.match(chatPanelSource, /latestActionableMessageId/, "ChatPanel must identify the latest actionable artifact");
  assert.match(chatPanelSource, /actionsDisabled=\{Boolean\(busy\) \|\|/, "ChatPanel must disable actions while busy or on older artifacts");
  assert.match(messageBubbleSource, /actionsDisabled\?: boolean/, "MessageBubble must accept disabled action state");
  assert.match(messageBubbleSource, /onClickCapture=\{blockDisabledAction\}/, "MessageBubble must intercept disabled button clicks");
});

test("ppt page regenerate updates the same card and hides regenerate while a page is running", () => {
  const start = workspaceSource.indexOf("const handleRegeneratePptImage = async");
  const end = workspaceSource.indexOf("const handleGeneratePptFile = async", start);
  assert.notEqual(start, -1, "handleRegeneratePptImage must exist");
  assert.notEqual(end, -1, "handleGeneratePptFile must follow handleRegeneratePptImage");
  const source = workspaceSource.slice(start, end);
  assert.match(source, /updatePptImagesArtifactInMessage\(msg\.id,\s*targetConversationId,\s*runningImages\)/, "regenerate must switch the target page to running inside the same card");
  assert.match(source, /updatePptImagesArtifactInMessage\(msg\.id,\s*targetConversationId,\s*nextImages\)/, "regenerate result must update the same card");
  assert.equal(source.includes("pushPptImagesArtifact"), false, "regenerate must not append a new PPT image grid");
  assert.match(messageBubbleSource, /page\.status !== "running"/, "running PPT pages must hide the regenerate button");
  assert.match(messageBubbleSource, /loadingDots/, "PPT loading text should use animated dots");
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

test("failed image video and analysis stages expose retry paths", () => {
  const source = handleApprovePlanSource();
  assert.match(source, /if \(!imagePrepare\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "image prepare failure must release the plan action");
  assert.match(source, /if \(!imageResult\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "image generation failure must let the previous stage retry");
  assert.match(source, /if \(!scenePackagesForReview\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "scene package failure must release the plan action");
  assert.match(workspaceSource, /if \(!generatedSceneVideos\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "scene video failure must let the scene package card retry");
  assert.match(workspaceSource, /if \(!mergedVideo\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "merge failure must release the generating scene package action");
  assert.match(messageBubbleSource, /videoGenerationFailed/, "video generation failure card must render a retry affordance");
  assert.match(messageBubbleSource, /onRetryVideoAnalysis/, "video analysis failure card must render a retry affordance");
});
