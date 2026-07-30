import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const workspaceSource = fs.readFileSync(path.resolve("src/pages/WorkspacePage.tsx"), "utf8");
const genParamsDialogSource = fs.readFileSync(path.resolve("src/components/composer/GenParamsDialog.tsx"), "utf8");
const chatPanelSource = fs.readFileSync(path.resolve("src/components/chat/ChatPanel.tsx"), "utf8");
const messageBubbleSource = fs.readFileSync(path.resolve("src/components/chat/MessageBubble.tsx"), "utf8");
const apiSource = fs.readFileSync(path.resolve("src/lib/api.ts"), "utf8");
const viteConfigSource = fs.readFileSync(path.resolve("vite.config.ts"), "utf8");
const videoRequirementConfigSource = fs.readFileSync(path.resolve("src/lib/videoRequirementConfig.ts"), "utf8");
const activePlanSnapshotPath = path.resolve("src/lib/activePlanSnapshot.ts");
const activePlanSnapshotSource = fs.existsSync(activePlanSnapshotPath) ? fs.readFileSync(activePlanSnapshotPath, "utf8") : "";
const planRevisionDialogPath = path.resolve("src/components/composer/PlanRevisionDialog.tsx");
const planRevisionDialogSource = fs.existsSync(planRevisionDialogPath) ? fs.readFileSync(planRevisionDialogPath, "utf8") : "";
const sceneAssetReplacementPickerSource = fs.readFileSync(path.resolve("src/components/canvas/SceneAssetReplacementPicker.tsx"), "utf8");
const storyboardPanelSource = fs.readFileSync(path.resolve("src/components/canvas/StoryboardPanel.tsx"), "utf8");

test("plan cards do not expose backend consistency diagnostics to users", () => {
  assert.match(apiSource, /consistency_issues/, "the API contract must retain internal plan diagnostics");
  assert.doesNotMatch(
    messageBubbleSource,
    /consistency_issues\.join/,
    "plan cards must not render internal consistency diagnostics",
  );
});

test("点击 Agent 修改后隐藏当前 Plan 编辑入口并支持恢复", () => {
  assert.match(messageBubbleSource, /!hidePlanEdit[\s\S]*onEditPlan/);
  assert.match(chatPanelSource, /hidePlanEdit=\{isSupersededArtifact \|\| m\.id === agentRevisionSourceMessageId\}/);
  assert.match(workspaceSource, /setAgentRevisionSourceMessageId\(msg\.id\)/);
  assert.match(workspaceSource, /planRevisionArtifactRef\.current\?\.sourceMessageId[\s\S]*restoredPlanRevisionChoice\?\.sourceMessageId/);
  assert.match(workspaceSource, /handleCancelPlanRevisionMode[\s\S]*setAgentRevisionSourceMessageId\(""\)/);
});

test("scene asset replacement keeps temporary local upload and adds persistent asset-library upload", () => {
  assert.match(sceneAssetReplacementPickerSource, /const uploadLocalImage = async/);
  assert.match(sceneAssetReplacementPickerSource, /source: "local_upload"/);
  assert.match(sceneAssetReplacementPickerSource, /const uploadImageAsset = async/);
  assert.match(sceneAssetReplacementPickerSource, /onProgress: \(percent\) => setAssetUploadProgress\(percent\)/);
  assert.match(sceneAssetReplacementPickerSource, /api\.createContentImageAsset/);
  assert.match(sceneAssetReplacementPickerSource, /上传到资产库/);
  assert.doesNotMatch(sceneAssetReplacementPickerSource, /naturalWidth|naturalHeight/);
});

test("storyboard global asset rows expose manual addition through the shared picker", () => {
  assert.match(storyboardPanelSource, /onAddGlobalAsset/);
  assert.match(storyboardPanelSource, /operation="add"/);
  assert.match(storyboardPanelSource, /添加素材/);
  assert.match(sceneAssetReplacementPickerSource, /operation\?: "add" \| "replace"/);
  assert.match(sceneAssetReplacementPickerSource, /adding \? "添加素材" : "替换素材"/);
  assert.match(workspaceSource, /addGlobalSceneAssetReference/);
  assert.match(workspaceSource, /scene_global_asset_added/);
});

test("manual global asset addition persists in place without clearing generated or dirty scene state", () => {
  const start = workspaceSource.indexOf("const handleAddGlobalAsset = (");
  const end = workspaceSource.indexOf("const handleRemoveReferencedMaterial", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const handler = workspaceSource.slice(start, end);
  assert.match(handler, /const updatedArtifact: ChatArtifact = \{\s*\.\.\.artifact,/);
  assert.match(handler, /updateVideoScenePackageArtifactInMessage/);
  assert.match(handler, /api\.updateConversationMessage/);
  assert.match(handler, /persistScenePackageSnapshot/);
  assert.doesNotMatch(handler, /videoScenePackageEditedSceneIds:\s*\[\]/);
});

test("分镜保存必须更新权威消息后再关闭画布", () => {
  const start = workspaceSource.indexOf("const handleSaveVideoScenePackage = async");
  const end = workspaceSource.indexOf("const handleGenerateVideoFromScenePackages = async", start);
  assert.notEqual(start, -1, "Workspace 必须提供分镜显式保存处理器");
  assert.notEqual(end, -1, "视频生成处理器必须位于分镜保存处理器之后");
  const handler = workspaceSource.slice(start, end);
  assert.match(handler, /messagesRef\.current\.find/, "保存时必须读取最新消息，不能使用画布旧闭包");
  assert.match(handler, /await api\.updateConversationMessage/, "保存时必须先更新权威消息");
  assert.match(handler, /persistScenePackageSnapshot/, "保存后必须同步会话快照");
  assert.match(handler, /setCanvasOpen\(false\)/, "权威消息更新成功后才能关闭画布");
  assert.match(storyboardPanelSource, /onSave\?: \(\) => void \| Promise<void>/, "分镜画布必须暴露独立保存动作");
  assert.match(storyboardPanelSource, /onClick=\{onSave\}/, "保存按钮不能继续复用仅关闭画布的动作");
  assert.match(workspaceSource, /onSave=\{legacyArtifactActionsEnabled[\s\S]*handleSaveVideoScenePackage/, "Workspace 必须把权威保存处理器接到画布");
});

test("删除分镜素材必须持久化更新后的权威消息", () => {
  const start = workspaceSource.indexOf("const handleDeleteReferencedGlobalAsset = async");
  const end = workspaceSource.indexOf("const showImageEditOptions = async", start);
  assert.notEqual(start, -1, "Workspace 必须提供分镜素材删除处理器");
  assert.notEqual(end, -1, "图片编辑参数处理器必须位于删除处理器之后");
  const handler = workspaceSource.slice(start, end);
  assert.match(handler, /const updatedArtifact: ChatArtifact = \{[\s\S]*videoScenePackages: updatedPackages/, "删除必须以当前消息构造完整更新 artifact");
  assert.match(handler, /await api\.updateConversationMessage/, "删除必须更新权威消息，避免 Snapshot 用旧素材覆盖");
  assert.ok(
    handler.indexOf("await api.updateConversationMessage") < handler.indexOf("updateVideoScenePackageArtifactInMessage"),
    "删除必须先持久化成功，再更新画布并提示成功",
  );
});

test("刷新恢复不得用旧 Conversation context 覆盖权威分镜消息", () => {
  const start = workspaceSource.indexOf("function restoreLatestVideoScenePackagesFromContext");
  const end = workspaceSource.indexOf("function markLatestPptFileDoneFromContext", start);
  assert.notEqual(start, -1, "Workspace 必须提供历史场景包恢复函数");
  assert.notEqual(end, -1, "PPT 恢复函数必须位于场景包恢复函数之后");
  const restoreSource = workspaceSource.slice(start, end);
  assert.doesNotMatch(
    restoreSource,
    /global_assets:\s*\(globalAssets\s*\|\|/,
    "已有消息时不得让 context.global_assets 抢占权威 Artifact",
  );
  assert.doesNotMatch(
    restoreSource,
    /scene_packages:\s*\(Array\.isArray\(scenePackages\)/,
    "已有消息时不得让 context.scene_packages 抢占权威 Artifact",
  );
  assert.match(
    restoreSource,
    /if \(latestIndex < 0\)[\s\S]*restoredVideoScenePackages/,
    "只有没有物化场景包消息时才能用 context 补建",
  );
});

test("分镜画布缺少动作 Handler 时必须禁用按钮", () => {
  assert.match(storyboardPanelSource, /disabled=\{!onSave\}/, "没有保存 Handler 时保存按钮必须禁用");
  assert.match(
    storyboardPanelSource,
    /disabled=\{sceneAssetQuotaPaused \? !onRetrySceneAssets : !onGenerateVideo\}/,
    "没有生成或恢复 Handler 时主动作必须禁用",
  );
});

test("需求表单折叠按钮的读屏标签必须反映下一步动作", () => {
  assert.match(
    genParamsDialogSource,
    /aria-label=\{collapsed \? "展开表单" : "折叠表单"\}/,
    "折叠后按钮必须提示可展开，展开时按钮必须提示可折叠",
  );
});

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

function handleSelectDirectionSource() {
  const start = workspaceSource.indexOf("const handleSelectDirection = async");
  const end = workspaceSource.indexOf("const handleApprovePlan = async", start);
  assert.notEqual(start, -1, "direction selection handler must exist");
  assert.notEqual(end, -1, "Plan approval handler must follow direction selection");
  return workspaceSource.slice(start, end);
}

function handleConfirmPlanRevisionModeSource() {
  const start = workspaceSource.indexOf("const handleConfirmPlanRevisionMode = async");
  const end = workspaceSource.indexOf("const handleCancelPlanRevisionMode", start);
  assert.notEqual(start, -1, "Plan revision mode handler must exist");
  assert.notEqual(end, -1, "Plan revision cancel handler must follow revision mode handler");
  return workspaceSource.slice(start, end);
}

function resumePendingMessageJobSource() {
  const start = workspaceSource.indexOf("const resumePendingMessageJob = async");
  const end = workspaceSource.indexOf("const resumePendingPlanJob = async", start);
  assert.notEqual(start, -1, "pending message resume helper must exist");
  assert.notEqual(end, -1, "pending Plan resume helper must follow pending message resume");
  return workspaceSource.slice(start, end);
}

function resumePendingPlanJobSource() {
  const start = workspaceSource.indexOf("const resumePendingPlanJob = async");
  const end = workspaceSource.indexOf("const scenePackageContext", start);
  assert.notEqual(start, -1, "pending Plan resume helper must exist");
  assert.notEqual(end, -1, "scene package helper must follow pending Plan resume");
  return workspaceSource.slice(start, end);
}

function assertRecoverablePlanJobOrder(source, startCall, label) {
  const startIndex = source.indexOf(startCall);
  const persistIndex = source.indexOf("await persistPendingPlanJob", startIndex);
  const resumeIndex = source.indexOf("await resumePendingPlanJob", persistIndex);
  assert.notEqual(startIndex, -1, `${label} must start a recoverable Plan job`);
  assert.notEqual(persistIndex, -1, `${label} must persist the Plan job before polling`);
  assert.notEqual(resumeIndex, -1, `${label} must enter the shared Plan resume path`);
  assert.ok(startIndex < persistIndex, `${label} must start the Plan job before persisting its handle`);
  assert.ok(persistIndex < resumeIndex, `${label} must persist the Plan job before resuming it`);
}

function handleRetrySceneAssetsSource() {
  const start = workspaceSource.indexOf("const handleRetrySceneAssets = async");
  const end = workspaceSource.indexOf("const handleRevisePlan =", start);
  assert.notEqual(start, -1, "handleRetrySceneAssets must exist");
  assert.notEqual(end, -1, "handleRevisePlan must follow scene asset retry");
  return workspaceSource.slice(start, end);
}

function applyConversationSource() {
  const start = workspaceSource.indexOf("const applyConversation = async");
  const end = workspaceSource.indexOf("const taskId = snapshot.taskId", start);
  assert.notEqual(start, -1, "applyConversation must exist");
  assert.notEqual(end, -1, "task reconciliation must follow restored conversation actions");
  return workspaceSource.slice(start, end);
}

function handleGenerateVideoFromScenePackagesSource() {
  const start = workspaceSource.indexOf("const handleGenerateVideoFromScenePackages = async");
  const end = workspaceSource.indexOf("const handleRetryVideoMerge", start);
  assert.notEqual(start, -1, "handleGenerateVideoFromScenePackages must exist");
  assert.notEqual(end, -1, "handleRetryVideoMerge must follow scene video generation");
  return workspaceSource.slice(start, end);
}

function startAndResumeVideoMergeJobSource() {
  const start = workspaceSource.indexOf("const startAndResumeVideoMergeJob = async");
  const end = workspaceSource.indexOf("const handleCompletedSceneGenerationJob = async", start);
  assert.notEqual(start, -1, "startAndResumeVideoMergeJob must exist");
  assert.notEqual(end, -1, "handleCompletedSceneGenerationJob must follow video merge helper");
  return workspaceSource.slice(start, end);
}

function handleRegenerateVideoWithRevisionSource() {
  const start = workspaceSource.indexOf("async function handleRegenerateVideoWithRevision");
  const end = workspaceSource.indexOf("const handleApprove = async", start);
  assert.notEqual(start, -1, "handleRegenerateVideoWithRevision must exist");
  assert.notEqual(end, -1, "handleApprove must follow video regeneration");
  return workspaceSource.slice(start, end);
}

function handleCompletedScenePackageJobSource() {
  const start = workspaceSource.indexOf("const handleCompletedScenePackageJob = async");
  const end = workspaceSource.indexOf("const handleCompletedSceneAssetJob = async", start);
  assert.notEqual(start, -1, "handleCompletedScenePackageJob must exist");
  assert.notEqual(end, -1, "handleCompletedSceneAssetJob must follow scene package job completion");
  return workspaceSource.slice(start, end);
}

function resumePendingScenePackageJobSource() {
  const start = workspaceSource.indexOf("const resumePendingScenePackageJob = async");
  const end = workspaceSource.indexOf("const pushReviewArtifact =", start);
  assert.notEqual(start, -1, "resumePendingScenePackageJob must exist");
  assert.notEqual(end, -1, "pushReviewArtifact must follow scene package resume");
  return workspaceSource.slice(start, end);
}

function resumePendingImageJobSource() {
  const start = workspaceSource.indexOf("const resumePendingImageJob = async");
  const end = workspaceSource.indexOf("const resumePendingScenePackageJob = async", start);
  assert.notEqual(start, -1, "resumePendingImageJob must exist");
  assert.notEqual(end, -1, "resumePendingScenePackageJob must follow image job resume");
  return workspaceSource.slice(start, end);
}

function handleCompletedIntakeJobSource() {
  const start = workspaceSource.indexOf("const handleCompletedIntakeJob = async");
  const end = workspaceSource.indexOf("const resumePendingIntakeJob = async", start);
  assert.notEqual(start, -1, "handleCompletedIntakeJob must exist");
  assert.notEqual(end, -1, "resumePendingIntakeJob must follow intake completion");
  return workspaceSource.slice(start, end);
}

function handleEditReferencedGlobalAssetSource() {
  const start = workspaceSource.indexOf("const handleEditReferencedGlobalAsset = async");
  const end = workspaceSource.indexOf("const handleDeleteReferencedGlobalAsset = async", start);
  assert.notEqual(start, -1, "handleEditReferencedGlobalAsset must exist");
  assert.notEqual(end, -1, "handleDeleteReferencedGlobalAsset must follow global asset edit");
  return workspaceSource.slice(start, end);
}

function pushSceneGlobalAssetEditOptionsSource() {
  const start = workspaceSource.indexOf("const pushSceneGlobalAssetEditOptions = async");
  const end = workspaceSource.indexOf("const handleEditReferencedGlobalAsset = async", start);
  assert.notEqual(start, -1, "pushSceneGlobalAssetEditOptions must exist");
  assert.notEqual(end, -1, "handleEditReferencedGlobalAsset must follow global asset edit options");
  return workspaceSource.slice(start, end);
}

function executeSceneGlobalAssetEditSource() {
  const start = workspaceSource.indexOf("const executeSceneGlobalAssetEdit = async");
  const end = workspaceSource.indexOf("const handleDeleteReferencedGlobalAsset = async", start);
  assert.notEqual(start, -1, "executeSceneGlobalAssetEdit must exist");
  assert.notEqual(end, -1, "handleDeleteReferencedGlobalAsset must follow global asset edit execution");
  return workspaceSource.slice(start, end);
}

function findStoryboardMessageForGlobalAssetSource() {
  const start = workspaceSource.indexOf("const findStoryboardMessageForGlobalAsset = (");
  const end = workspaceSource.indexOf("const handleUpdateVideoScenePackage", start);
  assert.notEqual(start, -1, "findStoryboardMessageForGlobalAsset must exist");
  assert.notEqual(end, -1, "handleUpdateVideoScenePackage must follow storyboard lookup");
  return workspaceSource.slice(start, end);
}

function handleCompletedImageAssetEditJobSource() {
  const start = workspaceSource.indexOf("const handleCompletedImageAssetEditJob = async");
  const end = workspaceSource.indexOf("const handleCompletedSceneAssetJob = async", start);
  assert.notEqual(start, -1, "handleCompletedImageAssetEditJob must exist");
  assert.notEqual(end, -1, "handleCompletedSceneAssetJob must follow image asset edit completion");
  return workspaceSource.slice(start, end);
}

function handleAcceptImageResultSource() {
  const start = workspaceSource.indexOf("async function handleAcceptImageResult");
  const end = workspaceSource.indexOf("function handleReviseImageResult", start);
  assert.notEqual(start, -1, "handleAcceptImageResult must exist");
  assert.notEqual(end, -1, "handleReviseImageResult must follow image result acceptance");
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

test("video requirement form collects and persists the complete creation contract", () => {
  assert.match(apiSource, /listVideoGenerateModelConfigs/, "api client must load video model configs");
  assert.equal(apiSource.includes("/api/modelParamConfig/listByCategory/video_generate"), true, "video model configs must use video_generate");
  assert.match(genParamsDialogSource, /视频总时长/, "video form must show total duration");
  assert.match(genParamsDialogSource, /视频画幅/, "video form must show video ratio");
  assert.match(genParamsDialogSource, /视频模型/, "video form must show video model");
  assert.match(genParamsDialogSource, /图片模型/, "video form must show scene image model");
  assert.match(genParamsDialogSource, /视频用途/, "video form must show video usage");
  assert.match(genParamsDialogSource, /视觉风格/, "video form must show visual style");
  assert.match(genParamsDialogSource, /\["30", "60", "90", "180", "自定义"\]/, "duration presets must include custom");
  assert.match(genParamsDialogSource, /min=\{4\}/, "custom duration must enforce the lower boundary");
  assert.match(genParamsDialogSource, /max=\{300\}/, "custom duration must enforce the upper boundary");
  assert.match(genParamsDialogSource, /image_model_capabilities/, "selected image model capabilities must be submitted");
  assert.match(genParamsDialogSource, /video_model_capabilities/, "selected video model generation capabilities must be submitted");
  assert.equal(genParamsDialogSource.includes('label="图片比例"'), false, "video form must not ask users for scene image ratio");
  assert.equal(genParamsDialogSource.includes('label="图片清晰度"'), false, "video form must not ask users for scene image quality");
  assert.match(workspaceSource, /video_duration_sec:\s*form\.video_duration_sec/, "Workspace must persist confirmed video duration");
  assert.match(workspaceSource, /video_model:\s*form\.video_model/, "Workspace must persist confirmed video model");
  assert.match(workspaceSource, /video_model_capabilities:\s*form\.video_model_capabilities/, "Workspace must persist video model capabilities");
  assert.match(workspaceSource, /image_model:\s*form\.image_model/, "Workspace must persist confirmed image model");
  assert.match(workspaceSource, /image_model_capabilities:\s*form\.image_model_capabilities/, "Workspace must persist image model capabilities");
  assert.match(videoRequirementConfigSource, /filterSeedanceConfigs/, "video model filtering must remain centralized");
});

test("plan revision defaults to modifying the current creative and only regenerates directions on explicit choice", () => {
  const revisionSource = handleConfirmPlanRevisionModeSource();

  assert.match(planRevisionDialogSource, /extend_current/, "revision dialog must expose current-creative modification");
  assert.match(planRevisionDialogSource, /regenerate_directions/, "revision dialog must expose creative regeneration");
  assert.match(planRevisionDialogSource, /useState<PlanRevisionMode>\("extend_current"\)/, "current-creative modification must be the default");
  assert.match(planRevisionDialogSource, /在当前创意基础上扩展\/修改/, "dialog must explain current creative modification");
  assert.match(planRevisionDialogSource, /放弃当前创意，重新生成新创意/, "dialog must explain creative regeneration");
  assert.match(planRevisionDialogSource, /max-h-48/, "long revision feedback must have a bounded preview height");
  assert.match(planRevisionDialogSource, /overflow-y-auto/, "long revision feedback must scroll inside the dialog");
  assert.match(planRevisionDialogSource, /max-h-\[calc\(100dvh-2\.5rem\)\]/, "revision dialog must stay within the viewport");
  assertRecoverablePlanJobOrder(revisionSource, "api.startPlanRevisionJob", "extend-current mode");
  assert.match(revisionSource, /kind:\s*"plan_revision"/, "the recoverable job must retain its revision kind");
  assert.equal(revisionSource.includes("api.revisePlanMarkdown"), false, "the handler must not fall back to synchronous Plan revision");
  assert.match(revisionSource, /mode === "regenerate_directions"[\s\S]*startDirectionJob/, "only regenerate mode may call the directions job");
  assert.match(apiSource, /planning\/plan\/revise/, "api client must expose Plan revision");
  assert.match(apiSource, /planning\/plan\/restore/, "api client must expose Plan restore");
  assert.match(messageBubbleSource, /plan\.plan_version/, "Plan cards must display their version");
  assert.match(messageBubbleSource, /onRollbackPlan/, "Plan cards with history must expose rollback");
});

test("plan rollback activates history directly and persists conversation context", () => {
  const start = workspaceSource.indexOf("const handleRollbackPlan = async");
  const end = workspaceSource.indexOf("const handle", start + 30);
  assert.notEqual(start, -1, "Plan rollback handler must exist");
  assert.notEqual(end, -1, "the next handler must follow Plan rollback");
  const rollbackSource = workspaceSource.slice(start, end);

  assert.equal(
    rollbackSource.includes("并保留为新版本"),
    false,
    "rollback must not claim that it creates another version",
  );
  assert.equal(rollbackSource.includes("api.updateConversation"), false, "rollback context must only be written after the recoverable job completes");
  assert.match(rollbackSource, /type:\s*"plan_save"/, "rollback must persist a recoverable Plan continuation");
  const snapshotIndex = rollbackSource.indexOf("const rollbackSnapshot = makeSnapshot(targetConversationId)");
  const restoreIndex = rollbackSource.indexOf("api.restorePlanMarkdown");
  const messagePersistIndex = rollbackSource.indexOf("await persistPlanArtifactForConversation");
  const successIndex = rollbackSource.indexOf("已回退到 plan.md");
  assert.notEqual(snapshotIndex, -1, "rollback must freeze the conversation snapshot");
  assert.ok(snapshotIndex < restoreIndex, "rollback must freeze the snapshot before calling restore");
  assert.match(rollbackSource, /context:\s*rollbackSnapshot/, "completion context must use the frozen snapshot");
  assert.notEqual(messagePersistIndex, -1, "rollback must await strict Plan message persistence");
  assert.notEqual(successIndex, -1, "rollback success wording must travel with the continuation");
  assert.ok(messagePersistIndex < successIndex, "success wording must be registered as part of strict persistence");
});

test("strict Plan message persistence starts and resumes a recoverable pending job", () => {
  const removeStart = workspaceSource.indexOf("const removeOptimisticMessage =");
  const strictStart = workspaceSource.indexOf("const persistPlanArtifactForConversation = async");
  const strictEnd = workspaceSource.indexOf("const startConversationMessageJobForConversation", strictStart);
  assert.notEqual(removeStart, -1, "optimistic message removal helper must exist");
  assert.notEqual(strictStart, -1, "strict Plan persistence helper must exist");
  assert.notEqual(strictEnd, -1, "the next message helper must follow strict Plan persistence");

  const removeSource = workspaceSource.slice(removeStart, strictStart);
  const strictSource = workspaceSource.slice(strictStart, strictEnd);
  assert.match(removeSource, /items\.filter/, "removal must delete the optimistic message from current messages");
  assert.match(removeSource, /messagesRef\.current = nextItems/, "removal must keep the messages ref in sync");
  assert.match(strictSource, /pendingPlanMessagePersistenceIdsRef\.current\.add/, "strict persistence must exclude the optimistic card from autosave");
  assert.match(strictSource, /startConversationMessageJobForConversation/, "strict persistence must start through the shared pending job helper");
  assert.match(strictSource, /await resumePendingMessageJob/, "strict persistence must resume the persisted job");
  assert.match(
    strictSource,
    /pendingMessageJob\?\.source_message_id === message\.id[\s\S]*schedulePendingMessageJobResume\(pendingMessageJob, 0\)/,
    "start success with pending-context uncertainty must immediately resume the same in-memory job",
  );
  assert.equal(
    strictSource.includes("appendMessageForConversation("),
    false,
    "strict Plan persistence must not reuse the error-swallowing append helper",
  );
});

test("current-creative Plan revision persists the returned message before authoritative context", () => {
  const revisionSource = handleConfirmPlanRevisionModeSource();
  const planResumeSource = resumePendingPlanJobSource();
  const messageResumeSource = resumePendingMessageJobSource();

  assert.equal(revisionSource.includes("pushPlanArtifact("), false, "revision must not use the failure-swallowing Plan helper");
  assertRecoverablePlanJobOrder(revisionSource, "api.startPlanRevisionJob", "revision");
  assert.equal(revisionSource.includes("api.updateConversation"), false, "message rejection must prevent any revision context write");
  assert.match(
    revisionSource,
    /await persistPendingPlanJob\([\s\S]*pendingPlanRevisionChoice:\s*null,[\s\S]*pending_plan_revision_choice:\s*null/,
    "persisting the pending job must clear the completed revision-mode choice so refresh cannot reopen it",
  );
  assert.match(planResumeSource, /await persistPlanArtifactForConversation\(/, "the shared resume path must await strict Plan message persistence");
  assert.match(planResumeSource, /createPlanArtifactMessage\(/, "the shared resume path must persist the returned revision Plan artifact");
  assert.match(planResumeSource, /type:\s*"plan_save"/, "revision must use a recoverable Plan continuation");
  assert.match(planResumeSource, /pendingPlanRevisionChoice:\s*null/, "the final authoritative Plan context must keep the revision choice cleared");

  const savedIndex = messageResumeSource.indexOf("const step = await resumePlanMessageJobStep");
  const savedMessageIndex = messageResumeSource.indexOf("const savedMessage = messageFromResponse(step.result");
  const authoritativeContextIndex = messageResumeSource.indexOf(
    "authoritativeContext = planContextFromSavedMessage(savedMessage, planContinuation.context)",
  );
  const contextWriteIndex = messageResumeSource.indexOf("await updateConversationWithProgress", authoritativeContextIndex);
  assert.notEqual(savedIndex, -1, "the strict message resume step must exist");
  assert.notEqual(savedMessageIndex, -1, "the strict path must deserialize the server-saved message result");
  assert.notEqual(authoritativeContextIndex, -1, "completion must derive context from the server-saved Plan artifact");
  assert.notEqual(contextWriteIndex, -1, "the authoritative Plan context must eventually be written");
  assert.ok(savedIndex < savedMessageIndex, "the server message job must complete before its result is deserialized");
  assert.ok(savedMessageIndex < authoritativeContextIndex, "the saved server message must be the source of authoritative context");
  assert.ok(authoritativeContextIndex < contextWriteIndex, "authoritative context must be derived before it is written");
});

test("initial v1 Plan uses the recoverable strict message job before writing context", () => {
  const source = handleSelectDirectionSource();
  const planResumeSource = resumePendingPlanJobSource();

  assert.equal(source.includes("pushPlanArtifact("), false, "initial v1 must not use failure-swallowing Plan persistence");
  assertRecoverablePlanJobOrder(source, "api.startPlanMarkdownJob", "initial v1");
  assert.match(source, /kind:\s*"plan_generation"/, "the recoverable job must retain its generation kind");
  assert.equal(source.includes("api.updateConversation("), false, "initial handler must not write Plan context outside job completion");
  assert.match(planResumeSource, /await persistPlanArtifactForConversation\(/, "the shared resume path must await recoverable Plan message persistence");
  assert.match(planResumeSource, /type:\s*"plan_save"/, "initial v1 must persist a plan_save continuation");
  assert.match(planResumeSource, /flowDraft:\s*null/, "initial Plan completion must clear the direction draft");
});

test("Plan job 轮询不再用前端时长误判，临时失败继续恢复原任务", () => {
  const planResumeSource = resumePendingPlanJobSource();

  assert.equal(apiSource.includes("PLAN_JOB_TIMEOUT_MS"), false, "Plan job 终态只能由后端权威状态决定");
  assert.match(planResumeSource, /classifyPlanJobResume/, "恢复路径必须按纯合同分类");
  assert.match(planResumeSource, /planJobResumeDelayMs/, "临时失败必须有限退避后恢复同一 job");
  assert.match(
    planResumeSource,
    /Plan 查询暂时中断，正在使用原任务继续恢复/,
    "用户只接收一次可恢复提示，不能误报任务失败",
  );
});

test("recoverable Plan message jobs retain unknown results and resume with server artifact authority", () => {
  const helperStart = workspaceSource.indexOf("const persistPlanArtifactForConversation = async");
  const helperEnd = workspaceSource.indexOf("const startConversationMessageJobForConversation", helperStart);
  const resumeStart = workspaceSource.indexOf("const resumePendingMessageJob = async");
  const resumeEnd = workspaceSource.indexOf("const scenePackageContext", resumeStart);
  assert.notEqual(helperStart, -1, "strict Plan helper must exist");
  assert.notEqual(helperEnd, -1, "generic message start helper must follow strict Plan helper");
  assert.notEqual(resumeStart, -1, "pending message resume helper must exist");
  assert.notEqual(resumeEnd, -1, "scene package helper must follow message resume helper");
  const helperSource = workspaceSource.slice(helperStart, helperEnd);
  const resumeSource = workspaceSource.slice(resumeStart, resumeEnd);

  assert.match(helperSource, /startConversationMessageJobForConversation/, "Plan helper must reuse the existing pending message job start path");
  assert.match(helperSource, /await resumePendingMessageJob/, "Plan helper must resume the persisted job instead of blind polling");
  assert.equal(helperSource.includes("persistChatMessage("), false, "Plan helper must not use non-recoverable start+poll persistence");
  assert.match(resumeSource, /resumePlanMessageJobStep/, "resume must use the behavior-tested recoverable Plan job step");
  assert.match(resumeSource, /planContextFromSavedMessage/, "completion context must derive from the server-saved artifact");
  assert.match(resumeSource, /restart:\s*\(request(?::\s*ConversationMessageJobRequest)?\)/, "404 recovery must reuse the step-provided original request and client_message_id");
  assert.match(resumeSource, /persistPendingMessageJob\(/, "unknown/restarted jobs must remain persisted");
  assert.match(resumeSource, /const failPendingPlanMessage = async/, "malformed completed Plan results must use one explicit failure cleanup path");
  assert.match(
    resumeSource,
    /planContextFromSavedMessage[\s\S]*catch \(protocolError\)[\s\S]*failPendingPlanMessage\(protocolError\)/,
    "a completed message without a valid Plan artifact must fail explicitly instead of retrying forever",
  );
  assert.match(
    resumeSource,
    /pendingMessageJobRef\.current = restartedPendingMessageJob[\s\S]*schedulePendingMessageJobResume\(restartedPendingMessageJob\)/,
    "a failed 404 replacement-job context write must retain and safely reschedule the same replacement job",
  );
  assert.match(resumeSource, /continue_after_save\?\.type === "handle_send"/, "ordinary user-message continuation must remain supported");
});

test("Plan message recovery schedules replacement jobs non-recursively with generation checks and backoff", () => {
  const schedulerStart = workspaceSource.indexOf("const schedulePendingMessageJobResume =");
  const schedulerEnd = workspaceSource.indexOf("const persistPlanArtifactForConversation", schedulerStart);
  const resumeStart = workspaceSource.indexOf("const resumePendingMessageJob = async");
  const resumeEnd = workspaceSource.indexOf("const scenePackageContext", resumeStart);
  assert.notEqual(schedulerStart, -1, "a shared non-recursive Plan message scheduler must exist");
  assert.notEqual(schedulerEnd, -1, "strict Plan persistence must follow the scheduler");
  const schedulerSource = workspaceSource.slice(schedulerStart, schedulerEnd);
  const resumeSource = workspaceSource.slice(resumeStart, resumeEnd);

  assert.match(schedulerSource, /planMessageResumeDelayMs/, "replacement retries must use bounded backoff");
  assert.match(schedulerSource, /isSameMessageJobGeneration/, "stale scheduled jobs must not resume after the pending generation changes");
  assert.match(schedulerSource, /resumePendingMessageJob\(current\)\.catch/, "scheduled recovery must absorb unexpected promise rejection");
  assert.equal(
    /await\s+resumePendingMessageJob\(step\.pending\)/.test(resumeSource),
    false,
    "404 replacement must not recursively await the next recovery generation",
  );
  assert.match(resumeSource, /restart_count:\s*\(pendingMessageJob\.restart_count \|\| 0\) \+ 1/, "each 404 generation must advance backoff state");
  assert.match(resumeSource, /schedulePendingMessageJobResume\(restartedPendingMessageJob\)/, "replacement jobs must be scheduled after the current call exits");
  assert.match(
    resumeSource,
    /if \(!shouldContinuePolling\(\)\) return;[\s\S]*retryPendingMessageJob[\s\S]*schedulePendingMessageJobResume\(retryPendingMessageJob\)/,
    "network errors and 408 responses must reschedule the same job without spinning while hidden",
  );
  assert.match(
    resumeSource,
    /catch \(contextError\)[\s\S]*contextSyncPendingMessageJob[\s\S]*schedulePendingMessageJobResume\(contextSyncPendingMessageJob\)/,
    "a saved Plan whose context update failed must automatically retry context synchronization",
  );
});

test("ordinary sends cannot overwrite a pending Plan message recovery handle", () => {
  const sendStart = workspaceSource.indexOf("const handleSend = async");
  const sendEnd = workspaceSource.indexOf("const sceneGlobalAssetReference", sendStart);
  const startJobStart = workspaceSource.indexOf("const startConversationMessageJobForConversation = async");
  const startJobEnd = workspaceSource.indexOf("const pushAssistant", startJobStart);
  const sendSource = workspaceSource.slice(sendStart, sendEnd);
  const startJobSource = workspaceSource.slice(startJobStart, startJobEnd);

  assert.match(sendSource, /isPendingPlanSaveForConversation/, "send entry must detect an existing Plan save job");
  assert.match(sendSource, /schedulePendingMessageJobResume\(pendingPlanMessageJob, 0\)/, "send entry must prioritize the original Plan job");
  assert.ok(
    sendSource.indexOf("isPendingPlanSaveForConversation") < sendSource.indexOf("startConversationMessageJobForConversation"),
    "the Plan guard must run before a new recoverable user message job starts",
  );
  assert.match(startJobSource, /continuation\?\.type !== "plan_save"/, "the shared message starter must defensively reject non-Plan overwrites");
  assert.match(startJobSource, /isPendingPlanSaveForConversation/, "the defensive guard must be scoped to the same conversation");
});

test("restoring a pending plan_save keeps its optimistic Plan out of autosave authority", () => {
  const applyStart = workspaceSource.indexOf("const applySnapshot =");
  const applyEnd = workspaceSource.indexOf("const makeSnapshot", applyStart);
  assert.notEqual(applyStart, -1, "applySnapshot must exist");
  assert.notEqual(applyEnd, -1, "makeSnapshot must follow applySnapshot");
  const applySource = workspaceSource.slice(applyStart, applyEnd);

  assert.match(applySource, /continue_after_save\?\.type === "plan_save"/, "restore must recognize pending Plan message jobs");
  assert.match(applySource, /pendingPlanMessagePersistenceIdsRef\.current = new Set/, "restore must rebuild the optimistic Plan exclusion set");
  assert.match(applySource, /source_message_id/, "the restored Plan client message id must remain excluded until completion");
});

test("active Plan autosave reuses makeSnapshot instead of a drifting field list", () => {
  const makeStart = workspaceSource.indexOf("const makeSnapshot =");
  const makeEnd = workspaceSource.indexOf("const resetWorkspace", makeStart);
  const autosaveStart = workspaceSource.indexOf("useEffect(() => {", workspaceSource.indexOf("const restoreConversation = async"));
  const titleStart = workspaceSource.indexOf("const titleFromPrompt", autosaveStart);
  assert.notEqual(makeStart, -1, "makeSnapshot must exist");
  assert.notEqual(makeEnd, -1, "resetWorkspace must follow makeSnapshot");
  assert.notEqual(autosaveStart, -1, "400ms autosave effect must exist");
  assert.notEqual(titleStart, -1, "title helper must follow autosave effect");
  const makeSource = workspaceSource.slice(makeStart, makeEnd);
  const autosaveSource = workspaceSource.slice(autosaveStart, titleStart);

  assert.match(
    makeSource,
    /activePlanSnapshotForConversation\(\s*messagesRef\.current,\s*snapshotConversationId,\s*pendingPlanMessagePersistenceIdsRef\.current,?\s*\)/,
    "makeSnapshot must derive active Plan while excluding messages that have not persisted",
  );
  assert.match(autosaveSource, /const snapshot(?:: WorkspaceSnapshot)? = makeSnapshot\(currentConversationId\)/, "autosave must reuse makeSnapshot");
  assert.match(autosaveSource, /\[.*messages.*\]/s, "Plan message changes must schedule autosave");
  for (const key of [
    "selected_direction",
    "plan_markdown",
    "plan_version",
    "plan_history",
    "creation_contract",
    "scene_durations_sec",
    "restored_from_version",
  ]) {
    assert.match(activePlanSnapshotSource, new RegExp(key), `makeSnapshot active Plan contract must include ${key}`);
  }
  assert.match(workspaceSource, /pendingPlanMessagePersistenceIdsRef/, "optimistic Plan cards must be tracked until message persistence completes");
  assert.match(makeSource, /pendingPlanMessagePersistenceIdsRef\.current/, "autosave must exclude Plan cards that are not persisted yet");
});

test("new conversation route replacement does not clear the first intake progress message", () => {
  const ensureStart = workspaceSource.indexOf("const ensureConversation = async");
  const ensureEnd = workspaceSource.indexOf("const normalizeSendInput", ensureStart);
  const restoreStart = workspaceSource.indexOf("const restoreConversation = async");
  const restoreEnd = workspaceSource.indexOf("void restoreConversation();", restoreStart);
  assert.notEqual(ensureStart, -1, "ensureConversation must exist");
  assert.notEqual(ensureEnd, -1, "normalizeSendInput must follow ensureConversation");
  assert.notEqual(restoreStart, -1, "conversation restore effect must exist");
  assert.notEqual(restoreEnd, -1, "snapshot save effect must follow conversation restore");
  const ensureSource = workspaceSource.slice(ensureStart, ensureEnd);
  const restoreSource = workspaceSource.slice(restoreStart, restoreEnd);
  assert.match(workspaceSource, /skipRouteRestoreConversationRef/, "Workspace must track route restores started by the current send");
  assert.match(ensureSource, /skipRouteRestoreConversationRef\.current = created\.conversation_id/, "newly created conversations should skip the immediate route restore");
  assert.match(restoreSource, /if \(skipRouteRestoreConversationRef\.current === conversationId\)/, "restore effect must ignore the immediate replacement navigation");
  assert.ok(
    restoreSource.indexOf("skipRouteRestoreConversationRef.current === conversationId") < restoreSource.indexOf("api.resumeConversation"),
    "skip check must happen before loading a stale server snapshot",
  );
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
  assert.equal(source.includes("api.startPrepareScenePackagesJob"), true, "video approval must start a recoverable scene package job");
  assert.equal(source.includes("api.prepareVideoScenePackages"), false, "video approval must not wait on the legacy synchronous scene package API");
  assert.equal(source.includes("api.generateSceneAssets"), false, "video approval must not wait on the legacy synchronous scene asset API");
});

test("image form values preserve requested multi-image count", () => {
  const valuesStart = workspaceSource.indexOf("function valuesFromForm");
  const valuesEnd = workspaceSource.indexOf("function formatSceneIndexesForMessage", valuesStart);
  assert.notEqual(valuesStart, -1, "valuesFromForm must exist");
  assert.notEqual(valuesEnd, -1, "formatSceneIndexesForMessage must follow valuesFromForm");
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

test("video product category is editable text initialized from intake industry type", () => {
  assert.equal(genParamsDialogSource.includes("const VIDEO_CATEGORIES"), false, "video product category must not use fixed radio choices");
  assert.match(genParamsDialogSource, /product_category:\s*textValue\(values,\s*"product_category"\)/, "video product category must accept free text");
  assert.match(genParamsDialogSource, /label="产品品类"[\s\S]*<input[\s\S]*value=\{video\.product_category\}/, "video product category must render as an input");
  assert.match(workspaceSource, /function initialValuesFromIntake\(intake: IntakeIntentResponse\)/, "Workspace must adapt intake values before opening the dialog");
  assert.match(workspaceSource, /function displayIndustryType\(value: string\)/, "Workspace must normalize generic industry labels for display");
  assert.match(workspaceSource, /return "其他品类"/, "generic industry values must display as other category");
  assert.match(workspaceSource, /values\.product_category = displayIndustryType\(industryType\)/, "video product category must default to display-safe intake_context.industry_type");
  assert.match(workspaceSource, /setPendingFormValues\(initialValuesFromIntake\(intake\)\)/, "dialog must receive adapted intake initial values");
});

test("ppt intent opens a ppt requirement form instead of image video planning", () => {
  assert.match(genParamsDialogSource, /export type CreationIntent = "video" \| "image" \| "ppt"/, "CreationIntent must include ppt");
  assert.match(genParamsDialogSource, /PPT生成需求收集/, "GenParamsDialog must render a PPT form");
  assert.match(genParamsDialogSource, /ppt_topic/, "PPT form must include ppt_topic");
  assert.match(genParamsDialogSource, /ppt_style/, "PPT form must include ppt_style");
  assert.match(genParamsDialogSource, /attachments/, "PPT form must include attachments");
  assert.match(workspaceSource, /if \(intake\.intent === "ppt"\)/, "handleSend must branch PPT intent before image/video creation flow");
  assert.match(workspaceSource, /api\.createPptSummaryJob/, "PPT form confirmation must start SmartPPT summary job");
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

test("ppt form limits each uploaded attachment to 20MB and all attachments to 100MB", () => {
  assert.match(
    genParamsDialogSource,
    /const PPT_MAX_ATTACHMENT_SIZE_BYTES = 20 \* 1024 \* 1024/,
    "PPT attachment size limit must be 20MB",
  );
  assert.match(
    genParamsDialogSource,
    /file\.size > PPT_MAX_ATTACHMENT_SIZE_BYTES/,
    "PPT attachments must be rejected before upload when the selected file is oversized",
  );
  assert.match(
    genParamsDialogSource,
    /uploaded\.size > PPT_MAX_ATTACHMENT_SIZE_BYTES/,
    "PPT attachments must also be checked against the uploaded size returned by content-app",
  );
  assert.match(
    genParamsDialogSource,
    /const PPT_MAX_TOTAL_ATTACHMENT_SIZE_BYTES = 100 \* 1024 \* 1024/,
    "PPT total attachment size limit must be 100MB",
  );
  assert.match(
    genParamsDialogSource,
    /ppt\.attachments\.reduce\(\(sum, attachment\) => sum \+ attachmentSize\(attachment\), 0\)/,
    "PPT total attachment validation must include files already in the form",
  );
  assert.match(
    genParamsDialogSource,
    /totalSize \+ file\.size > PPT_MAX_TOTAL_ATTACHMENT_SIZE_BYTES/,
    "PPT attachments must be rejected before upload when their cumulative size is oversized",
  );
  assert.match(genParamsDialogSource, /文件大小不能超过/, "PPT form must explain the attachment size validation error");
  assert.match(genParamsDialogSource, /总大小不能超过/, "PPT form must explain the total attachment size validation error");
  assert.match(genParamsDialogSource, /className="flex flex-col gap-1"/, "PPT upload limits must be displayed on a separate line");
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

test("恢复快照必须用响应中的对话 ID 绑定工作流进度", () => {
  const applyStart = workspaceSource.indexOf("const applySnapshot = ");
  const applyEnd = workspaceSource.indexOf("const makeSnapshot", applyStart);
  const conversationStart = workspaceSource.indexOf("const applyConversation = async");
  const conversationEnd = workspaceSource.indexOf("const resumeVisiblePendingJobs", conversationStart);
  assert.notEqual(applyStart, -1, "applySnapshot 必须存在");
  assert.notEqual(applyEnd, -1, "makeSnapshot 必须位于 applySnapshot 之后");
  assert.notEqual(conversationStart, -1, "applyConversation 必须存在");
  assert.notEqual(conversationEnd, -1, "待恢复任务处理器必须位于 applyConversation 之后");
  const applySource = workspaceSource.slice(applyStart, applyEnd);
  const conversationSource = workspaceSource.slice(conversationStart, conversationEnd);
  assert.match(
    applySource,
    /const applySnapshot = \(snapshot: Partial<WorkspaceSnapshot>, targetConversationId: string\)/,
    "快照应用必须显式接收目标对话 ID，不能依赖渲染时序中的可变 ref",
  );
  assert.match(
    applySource,
    /workflowProgressConversationIdRef\.current = targetConversationId/,
    "恢复的任务看板必须绑定权威对话 ID",
  );
  assert.match(
    conversationSource,
    /applySnapshot\(\{[\s\S]*\}, detail\.conversation\.conversation_id\)/,
    "恢复入口必须把服务端响应中的对话 ID 传给快照应用",
  );
});

test("image edit intake bypasses the normal directions and plan flow", () => {
  const source = handleSendSource();
  const intakeCompletionSource = handleCompletedIntakeJobSource();
  assert.match(workspaceSource, /pendingImageEditRequestRef/, "Workspace must store an image-edit request while waiting for upload");
  assert.match(workspaceSource, /function looksLikeImageEditPrompt/, "Workspace must detect natural image-edit prompts");
  assert.match(workspaceSource, /function isImageEditIntake/, "Workspace must detect image-edit intake metadata");
  assert.match(workspaceSource, /const executeDirectImageEdit = async/, "Workspace must have a direct image-edit executor");
  assert.match(source, /if \(pendingImageEditRequestRef\.current\?\.conversationId === activeConversation\)/, "next user upload must resume pending image edit");
  assert.match(source, /looksLikeImageEditPrompt\(text\)[\s\S]*pendingImageEditRequestRef\.current = null/, "a fresh image-edit prompt must reset stale pending image-edit state");
  assert.match(intakeCompletionSource, /if \(intake\.intent === "image" && isImageEditIntake\(intake, text\)\)/, "image-edit intake must branch before the generic image form");
  assert.match(intakeCompletionSource, /请上传需要编辑的图片/, "missing source image should prompt the user to upload");
  assert.match(intakeCompletionSource, /showImageEditOptions/, "image-edit branch must show model options before generation");
  assert.equal(/if \(intake\.intent === "image" && isImageEditIntake\(intake, text\)\)[\s\S]*executeDirectImageEdit/.test(intakeCompletionSource), false, "image-edit intake must not generate before model confirmation");
});

test("image edit options load content-app model configs and submit selected model params", () => {
  const optionsStart = workspaceSource.indexOf("const showImageEditOptions = async");
  const optionsEnd = workspaceSource.indexOf("const executeDirectImageEdit = async", optionsStart);
  const executeStart = workspaceSource.indexOf("const executeDirectImageEdit = async");
  const executeEnd = workspaceSource.indexOf("const pushDirectionsArtifact", executeStart);
  assert.notEqual(optionsStart, -1, "Workspace must have an image-edit model options step");
  assert.notEqual(optionsEnd, -1, "executeDirectImageEdit must follow showImageEditOptions");
  assert.notEqual(executeStart, -1, "executeDirectImageEdit must exist");
  assert.notEqual(executeEnd, -1, "pushDirectionsArtifact must follow executeDirectImageEdit");
  const optionsSource = workspaceSource.slice(optionsStart, optionsEnd);
  const executeSource = workspaceSource.slice(executeStart, executeEnd);
  assert.match(apiSource, /listImageGenerateModelConfigs/, "api client must expose content-app model config lookup");
  assert.equal(apiSource.includes("/api/modelParamConfig/listByCategory/image_generate"), true, "model config lookup must call content-app image_generate category");
  assert.match(viteConfigSource, /\^\/api\(\/\|\$\)/, "Vite dev server must proxy all content-app /api calls");
  assert.match(optionsSource, /api\.listImageGenerateModelConfigs/, "image-edit options step must load live model configs");
  assert.match(optionsSource, /type:\s*"image_edit_options"/, "image-edit options must be rendered as a chat artifact");
  assert.match(workspaceSource, /modelType:\s*"gpt-image-2"/, "image-edit options should keep gpt-image-2 as the request model by default");
  assert.match(executeSource, /request\.selection/, "direct image edit must use the confirmed model selection");
  assert.match(executeSource, /image_model/, "confirmed model must be written into image form values");
  assert.match(executeSource, /image_quality/, "confirmed quality must be written into image form values");
  assert.match(workspaceSource, /const AUTO_CONFIRM_TIMEOUT_MS = 60_000/, "auto-confirm timeout must be 60 seconds");
  assert.match(workspaceSource, /window\.setTimeout\([\s\S]*handleAcceptImageResult\(imageResultMessage, true\)[\s\S]*AUTO_CONFIRM_TIMEOUT_MS/, "successful direct image edit must auto-accept after 60 seconds");
  assert.match(messageBubbleSource, /imageEditModelConfigs/, "MessageBubble must render image-edit model options");
  assert.match(messageBubbleSource, /model === "gpt-image-2" \? "image-2"/, "MessageBubble must show image-2 as the gpt-image-2 display label");
  assert.match(messageBubbleSource, /onConfirmImageEditOptions/, "MessageBubble must submit selected image-edit options");
  assert.match(messageBubbleSource, /max-h-\[420px\][\s\S]*object-contain/, "image result previews must preserve the generated image aspect ratio");
  assert.match(messageBubbleSource, /清晰度/, "image-edit options card must show quality choices");
  assert.match(messageBubbleSource, /尺寸/, "image-edit options card must show ratio choices");
  assert.match(messageBubbleSource, /imageEditUnsupportedReason/, "image-edit options must detect unsupported requested params");
  assert.match(messageBubbleSource, /当前模型不支持需求尺寸/, "unsupported requested ratio should show a clear message");
  assert.match(messageBubbleSource, /当前模型不支持需求清晰度/, "unsupported requested quality should show a clear message");
  assert.match(messageBubbleSource, /你也可以重新选择当前模型支持的参数后继续提交/, "unsupported requested params should guide the user to choose supported values");
  assert.match(messageBubbleSource, /imageEditSubmitDisabled/, "image-edit submit should only disable when no usable option exists");
  assert.equal(messageBubbleSource.includes("disabled={Boolean(imageEditUnsupportedReason)}"), false, "unsupported requested params must not block submit after choosing supported values");
});

test("video results require explicit user confirmation", () => {
  assert.doesNotMatch(workspaceSource, /handleAcceptVideoResult\([^)]*,\s*true\)/, "video result cards must not auto-accept after a timeout");
  assert.doesNotMatch(workspaceSource, /timeoutReviewMessage\("video"/, "video flow must not emit timeout auto-finish messages");
  assert.match(messageBubbleSource, /无意见，结束/, "video result cards must still expose manual accept");
  assert.match(messageBubbleSource, /提出修改意见/, "video result cards must still expose manual revision");
});

test("视频结束必须先持久化权威产物再保存完成快照", () => {
  const start = workspaceSource.indexOf("async function handleAcceptVideoResult");
  const end = workspaceSource.indexOf("function handleReviseVideoResult", start);
  assert.notEqual(start, -1, "Workspace 必须提供视频结束处理器");
  assert.notEqual(end, -1, "视频修改处理器必须位于结束处理器之后");
  const handler = workspaceSource.slice(start, end);
  assert.match(handler, /videoAccepted:\s*true/, "结束动作必须把确认标记写入视频 artifact");
  assert.match(handler, /await api\.updateConversationMessage/, "结束动作必须更新权威视频消息");
  assert.match(handler, /await updateConversationWithProgress/, "结束动作必须等待完成快照落库");
  assert.ok(
    handler.indexOf("await api.updateConversationMessage") < handler.indexOf("await updateConversationWithProgress"),
    "必须先保存权威产物，再保存 video_accepted 快照",
  );
  assert.ok(
    handler.indexOf("await updateConversationWithProgress") < handler.indexOf("已确认视频无修改意见，流程结束。"),
    "只有权威产物和完成快照都落库后才能提示成功",
  );
});

test("confirmed image edit options survive conversation restore", () => {
  const optionsStart = workspaceSource.indexOf("const showImageEditOptions = async");
  const executeStart = workspaceSource.indexOf("const executeDirectImageEdit = async");
  const confirmStart = workspaceSource.indexOf("const handleConfirmImageEditOptions = async");
  const confirmEnd = workspaceSource.indexOf("const pushDirectionsArtifact", confirmStart);
  const applyStart = workspaceSource.indexOf("const applyConversation = async");
  const applyEnd = workspaceSource.indexOf("const taskId = snapshot.taskId", applyStart);
  assert.notEqual(optionsStart, -1, "showImageEditOptions must exist");
  assert.notEqual(executeStart, -1, "executeDirectImageEdit must exist");
  assert.notEqual(confirmStart, -1, "handleConfirmImageEditOptions must exist");
  assert.notEqual(confirmEnd, -1, "pushDirectionsArtifact must follow image edit confirmation");
  assert.notEqual(applyStart, -1, "applyConversation must exist");
  assert.notEqual(applyEnd, -1, "task reconciliation must follow restored messages");
  const confirmSource = workspaceSource.slice(confirmStart, confirmEnd);
  const applySource = workspaceSource.slice(applyStart, applyEnd);

  assert.match(workspaceSource, /imageEditConfirmedSelectionsRef/, "Workspace must keep confirmed image-edit selections by message id");
  assert.match(workspaceSource, /imageEditConfirmedSelections\?: Record<string, ImageEditModelSelection>/, "conversation snapshots must persist confirmed image-edit selections");
  assert.match(confirmSource, /recordImageEditConfirmedSelection\(msg\.id,\s*targetConversationId,\s*selection\)/, "confirming image-edit options must record the selected model ratio and quality before generation");
  assert.match(applySource, /applyImageEditConfirmedSelectionsToMessages/, "restored messages must be patched with confirmed selections from context");
  assert.match(messageBubbleSource, /imageEditConfirmedSelection/, "MessageBubble must read the confirmed selection from restored artifacts");
  assert.match(messageBubbleSource, /confirmedImageEditSelection\?\.model/, "confirmed model should initialize the options card after restore");
  assert.match(messageBubbleSource, /confirmedImageEditSelection\?\.ratio/, "confirmed ratio should initialize the options card after restore");
  assert.match(messageBubbleSource, /confirmedImageEditSelection\?\.size/, "confirmed quality should initialize the options card after restore");
});

test("failed direct image edit can reopen model options instead of blindly retrying stale params", () => {
  const retryStart = workspaceSource.indexOf("const handleRetryImageResult = async");
  const retryEnd = workspaceSource.indexOf("async function handleAcceptImageResult", retryStart);
  assert.notEqual(retryStart, -1, "handleRetryImageResult must exist");
  assert.notEqual(retryEnd, -1, "handleAcceptImageResult must follow retry handler");
  const retrySource = workspaceSource.slice(retryStart, retryEnd);
  assert.match(retrySource, /imagePrepare\.method === "image_edit"/, "image edit failures need a dedicated retry branch");
  assert.match(retrySource, /showImageEditOptions/, "image edit retry should reopen the model and quality options card");
  assert.match(retrySource, /imageEditRequest/, "retry should rebuild the image edit request from the failed result artifact");
  assert.match(retrySource, /releaseArtifactAction\(processedKey\)/, "reopening options should release the failed result action so the user can submit again");
});

test("全局素材编辑失败后重新打开专用参数卡并保留可恢复请求", () => {
  const retryStart = workspaceSource.indexOf("const handleRetryImageResult = async");
  const retryEnd = workspaceSource.indexOf("async function handleAcceptImageResult", retryStart);
  const retrySource = workspaceSource.slice(retryStart, retryEnd);
  const completionSource = handleCompletedImageAssetEditJobSource();
  const sceneRetryIndex = retrySource.indexOf("sceneGlobalAssetReferenceFromMaterials");
  const imagePrepareIndex = retrySource.indexOf("const imagePrepare = artifact.imagePrepare");
  assert.notEqual(sceneRetryIndex, -1, "全局素材重试必须从持久化素材中恢复引用");
  assert.notEqual(imagePrepareIndex, -1, "普通图片重试必须保留 imagePrepare 分支");
  assert.ok(sceneRetryIndex < imagePrepareIndex, "全局素材重试必须先于普通图片 imagePrepare 门禁执行");
  assert.match(retrySource, /pushSceneGlobalAssetEditOptions/, "全局素材重试必须重新打开专用模型参数卡");
  assert.match(completionSource, /const retryRequest: PendingImageEditRequest/, "全局素材任务失败后必须构造可恢复编辑请求");
  assert.match(completionSource, /imageEditRequest:\s*retryRequest/, "失败结果卡必须持久化可恢复编辑请求");
  assert.match(completionSource, /\.\.\.uploadedReferenceMaterials\(failedRequest\.materials \|\| \[\]\)/, "融合重试必须保留用户上传的参考图");
});

test("ppt image pages stream partial status into the existing artifact card", () => {
  assert.match(apiSource, /type PptJobStatusCallback/, "PPT job polling must expose status callbacks");
  assert.match(apiSource, /startPptImagesJob:[\s\S]*onStatus\?: PptJobStatusCallback/, "PPT image job must accept a status callback");
  assert.match(workspaceSource, /pendingPptImagesFromContentJson/, "Workspace must show PPT page placeholders before images finish");
  assert.match(workspaceSource, /updatePptImagesArtifactInMessage/, "Workspace must update the existing PPT image card");
  assert.match(workspaceSource, /api\.createPptImagesJob/, "Workspace must start the PPT image job");
  assert.match(workspaceSource, /partialImages\?\.pages[\s\S]*updatePptImagesArtifactInMessage/, "PPT image polling must stream page status into the card");
});

test("closing the requirement dialog cancels and terminates the pending flow", () => {
  assert.match(workspaceSource, /const handleCancelParamsDialog = \(\) =>/, "Workspace must have an explicit dialog-cancel handler");
  assert.match(workspaceSource, /pendingDialogContextRef\.current = null/, "cancel must clear pending dialog context");
  assert.match(workspaceSource, /已取消当前需求表单，流程已终止/, "cancel should write a visible terminal message");
  assert.match(workspaceSource, /last_phase:\s*"form_cancelled"/, "cancel should persist a cancelled phase");
  assert.match(workspaceSource, /onCancel=\{handleCancelParamsDialog\}/, "GenParamsDialog X must call the flow-cancel handler");
});

test("交互策略分别控制输入框、产物动作和运行时忙碌态", () => {
  assert.match(workspaceSource, /interactionPolicy\.composer\.disabled/, "composer must use the composer policy");
  assert.match(workspaceSource, /interactionPolicy\.artifact\.actionsDisabled/, "artifact actions must use the artifact policy");
  assert.match(workspaceSource, /interactionPolicy\.runtime\.busy/, "runtime busy must remain separately observable");
  assert.match(chatPanelSource, /latestActionableMessageId/, "ChatPanel must identify the latest actionable artifact");
  assert.match(chatPanelSource, /isLatestActionableQualityReview/, "ChatPanel must keep the latest QC result card actionable after analysis");
  assert.match(chatPanelSource, /hasRecoverableArtifactAction/, "ChatPanel must identify failed recoverable artifact cards");
  assert.match(chatPanelSource, /actionsDisabled=\{Boolean\(artifactActionsDisabled\) \|\| \(!isLatestActionableQualityReview &&[\s\S]*!keepRecoverableActions/, "ChatPanel must disable actions while the artifact policy is locked or on older artifacts except the current QC result and recoverable failure cards");
  assert.match(messageBubbleSource, /actionsDisabled\?: boolean/, "MessageBubble must accept disabled action state");
  assert.match(messageBubbleSource, /onClickCapture=\{blockDisabledAction\}/, "MessageBubble must intercept disabled button clicks");
});

test("active assistant progress messages render a loading indicator", () => {
  assert.match(chatPanelSource, /latestProgressMessageId/, "ChatPanel must identify the latest assistant progress message");
  assert.match(chatPanelSource, /function isProgressMessage/, "ChatPanel must detect assistant progress messages from content");
  assert.match(chatPanelSource, /latestAssistantMessage && isProgressMessage\(latestAssistantMessage\)/, "ChatPanel must clear loading once a newer assistant result message appears");
  assert.match(chatPanelSource, /showProgressLoading=\{m\.id === latestProgressMessageId\}/, "ChatPanel must show loading on the latest progress message");
  assert.match(messageBubbleSource, /showProgressLoading\?: boolean/, "MessageBubble must accept loading state");
  assert.match(messageBubbleSource, /function progressDescription/, "MessageBubble must derive per-stage progress descriptions");
  assert.match(messageBubbleSource, /role="status"/, "loading indicator must be exposed as status");
  assert.match(messageBubbleSource, /showProgressLoading[\s\S]*msg\.time/, "loading indicator should render above the message time");
  assert.match(messageBubbleSource, /animate-spin/, "loading indicator should spin");
  assert.match(messageBubbleSource, /conic-gradient/, "loading indicator should use a gradient ring");
  assert.doesNotMatch(workspaceSource, /已默认采用推荐方向[\s\S]*正在生成 plan\.md/, "directions must not auto-select a recommended option");
  assert.match(messageBubbleSource, /重新生成创意方向/, "directions card should expose manual regeneration instead of auto-selection");
  assert.match(chatPanelSource, /采集 Agent 判断这是\(\?:图片\|视频\)生成需求/, "image and video intake confirmation should keep loading active before the next assistant result");
  assert.match(messageBubbleSource, /采集 Agent 判断这是\(\?:图片\|视频\)生成需求[\s\S]*计划文件生成中[\s\S]*采集 Agent\|理解\|表单/, "image and video intake confirmation loading should show plan file generation before generic intake text");
  assert.match(messageBubbleSource, /可编辑视频资产\|可编辑场景包\|场景包[\s\S]*可编辑视频资产生成中\.\.\.[\s\S]*plan\\\.md\|计划文件\|创作方案/, "scene package progress should be labeled before generic plan.md progress");
  assert.match(messageBubbleSource, /计划文件/, "plan.md progress should be labeled as plan file generation");
});

test("completed hidden conversation jobs can clear busy without requiring a refresh", () => {
  assert.match(workspaceSource, /const isCurrentConversation = \(targetConversationId: string\) =>/, "Workspace must distinguish active conversation from page visibility");
  assert.match(
    workspaceSource,
    /if \(!isCurrentConversation\(targetConversationId\)\) return;[\s\S]*if \(value && !pageVisibleRef\.current\) return;[\s\S]*setBusy\(value\);/,
    "busy=false must still clear for the active conversation even if the page was hidden during completion",
  );
});

test("ppt page regenerate updates the same card and hides regenerate while a page is running", () => {
  const start = workspaceSource.indexOf("const handleRegeneratePptImage = async");
  const end = workspaceSource.indexOf("const handleGeneratePptFile = async", start);
  assert.notEqual(start, -1, "handleRegeneratePptImage must exist");
  assert.notEqual(end, -1, "handleGeneratePptFile must follow handleRegeneratePptImage");
  const source = workspaceSource.slice(start, end);
  assert.match(source, /updatePptImagesArtifactInMessage\(msg\.id,\s*targetConversationId,\s*runningImages,\s*artifact\)/, "regenerate must switch the target page to running inside the same card");
  assert.match(source, /image_message_id:\s*msg\.id/, "regenerate job must remember the target card id");
  assert.match(workspaceSource, /handleCompletedPptImageRegenerationJob[\s\S]*updatePptImagesArtifactInMessage\(imageMessageId,\s*targetConversationId,\s*nextImages,\s*sourceArtifact\)/, "regenerate result must update the same card");
  assert.equal(source.includes("pushPptImagesArtifact"), false, "regenerate must not append a new PPT image grid");
  assert.match(messageBubbleSource, /page\.status !== "running"/, "running PPT pages must hide the regenerate button");
  assert.match(messageBubbleSource, /loadingDots/, "PPT loading text should use animated dots");
});

test("image plan approval continues through image generation instead of stopping at prepare", () => {
  const source = handleApprovePlanSource();
  const imageBranchStart = source.indexOf('if (artifact.intent === "image")');
  const imageBranchEnd = source.indexOf("const formValues = artifact.formValues", imageBranchStart);
  assert.notEqual(imageBranchStart, -1, "image branch must exist in handleApprovePlan");
  assert.notEqual(imageBranchEnd, -1, "image branch must return before video flow");
  const imageBranch = source.slice(imageBranchStart, imageBranchEnd);
  assert.equal(imageBranch.includes("api.prepareImageGeneration"), true, "image branch must still choose the image endpoint through prepare");
  assert.equal(imageBranch.includes("api.startImageGenerationJob(request)"), true, "image branch must start an image generation job after prepare");
  assert.equal(imageBranch.includes("await persistPendingImageJob(pendingImageJob"), true, "image branch must persist the image job before polling");
  assert.equal(imageBranch.includes("await resumePendingImageJob(pendingImageJob, processedKey)"), true, "image branch must resume polling the persisted image job");
  assert.equal(imageBranch.includes("api.generateImage"), false, "image branch must not synchronously wait on image generation");
});

test("failed image video and analysis stages expose retry paths", () => {
  const source = handleApprovePlanSource();
  const imageJobSource = workspaceSource.slice(
    workspaceSource.indexOf("const handleCompletedImageGenerationJob = async"),
    workspaceSource.indexOf("const handleCompletedImageAssetEditJob = async"),
  );
  const scenePackageJobSource = handleCompletedScenePackageJobSource();
  assert.match(source, /if \(!imagePrepare\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "image prepare failure must release the plan action");
  assert.match(imageJobSource, /if \(!imageResult\.ok\) releaseArtifactAction\(processedKey\)/, "image generation failure must let the previous stage retry");
  assert.match(scenePackageJobSource, /if \(!videoScenePackages\.ok \|\| quotaPaused\) releaseArtifactAction\(processedKey\)/, "scene package job failure must release the plan action");
  assert.match(workspaceSource, /if \(!generatedSceneVideos\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "scene video failure must let the scene package card retry");
  assert.match(workspaceSource, /if \(!mergedVideo\.ok\)[\s\S]*releaseArtifactAction\(processedKey\)/, "merge failure must release the generating scene package action");
  assert.match(messageBubbleSource, /videoGenerationFailed/, "video generation failure card must render a retry affordance");
  assert.match(chatPanelSource, /artifact\.mergedVideo && !artifact\.mergedVideo\.ok && Boolean\(artifact\.generatedSceneVideos\?\.scene_videos\.length\)/, "failed merge cards must remain clickable for retry");
  assert.match(messageBubbleSource, /onRetryVideoAnalysis/, "video analysis failure card must render a retry affordance");
});

test("image jobs persist their id before polling so conversations can recover", () => {
  assert.match(apiSource, /startImageGenerationJob:/, "API client must expose a start-only image generation job call");
  assert.match(apiSource, /getImageGenerationJob:/, "API client must expose a query-only image generation job call");
  assert.match(apiSource, /pollImageGenerationJob,/, "API client must expose polling for existing image generation jobs");
  assert.match(apiSource, /startImageAssetEditJob:/, "API client must expose a start-only image asset edit job call");
  assert.match(apiSource, /getImageAssetEditJob:/, "API client must expose a query-only image asset edit job call");
  assert.match(apiSource, /pollImageAssetEditJob,/, "API client must expose polling for existing image asset edit jobs");
  assert.match(workspaceSource, /pendingImageJob\?: PendingImageJob \| null/, "WorkspaceSnapshot must store pending image jobs");
  assert.match(workspaceSource, /pending_image_job\?: PendingImageJob \| null/, "WorkspaceSnapshot must restore snake_case pending image jobs");
  assert.match(workspaceSource, /pendingImageJobRef\.current\?\.conversation_id === snapshotConversationId/, "conversation snapshots must keep the active pending image job");

  const source = handleApprovePlanSource();
  const imageBranchStart = source.indexOf('if (artifact.intent === "image")');
  const imageBranchEnd = source.indexOf("const formValues = artifact.formValues", imageBranchStart);
  const imageBranch = source.slice(imageBranchStart, imageBranchEnd);
  const startIndex = imageBranch.indexOf("api.startImageGenerationJob(request)");
  const persistIndex = imageBranch.indexOf("await persistPendingImageJob(pendingImageJob");
  const pollIndex = imageBranch.indexOf("await resumePendingImageJob(pendingImageJob, processedKey)");
  assert.notEqual(startIndex, -1, "image generation must start a backend job explicitly");
  assert.notEqual(persistIndex, -1, "image generation must persist the job id");
  assert.notEqual(pollIndex, -1, "image generation must poll the persisted job");
  assert.ok(startIndex < persistIndex && persistIndex < pollIndex, "image job id must be persisted before polling starts");
  assert.match(imageBranch, /kind:\s*"image_generation"/, "first-time image generation must record its pending job kind");
});

test("restored conversations resume existing image jobs without starting duplicates", () => {
  const applySource = applyConversationSource();
  assert.match(applySource, /snapshot\.pendingImageJob \|\| snapshot\.pending_image_job/, "restore must read pending image job metadata");
  assert.match(applySource, /resumePendingImageJob\(pendingImageJob\)/, "restore must resume polling an existing image job");
  assert.equal(applySource.includes("startImageGenerationJob"), false, "restore must not start a duplicate image generation job");
  assert.equal(applySource.includes("startImageAssetEditJob"), false, "restore must not start a duplicate image asset edit job");
  assert.equal(applySource.includes("pushAssistant"), false, "restore must not add a separate resume-polling progress message");
});

test("scene global asset image editing uses recoverable image edit jobs", () => {
  const source = handleEditReferencedGlobalAssetSource();
  const optionsSource = pushSceneGlobalAssetEditOptionsSource();
  const executeSource = executeSceneGlobalAssetEditSource();
  const lookupSource = findStoryboardMessageForGlobalAssetSource();
  const completionSource = handleCompletedImageAssetEditJobSource();
  const acceptSource = handleAcceptImageResultSource();
  const startIndex = executeSource.indexOf("api.startImageAssetEditJob(jobRequest)");
  const fusionStartIndex = executeSource.indexOf("api.startImageAssetFusionJob(jobRequest)");
  const persistIndex = executeSource.indexOf("await persistPendingImageJob(pendingImageJob");
  const pollIndex = executeSource.indexOf("await resumePendingImageJob(pendingImageJob)");
  assert.match(source, /pushSceneGlobalAssetEditOptions/, "referenced global asset edits must open the model options step first");
  assert.match(optionsSource, /api\.listImageGenerateModelConfigs/, "global asset edit options must load live content-app model configs");
  assert.match(optionsSource, /type:\s*"image_edit_options"/, "global asset edit must render the shared image edit options card");
  assert.match(optionsSource, /gpt-image-2/, "global asset edit options should default to gpt-image-2");
  assert.match(optionsSource, /sceneGlobalAssetEditRatio/, "global asset edit options must infer the source asset ratio");
  assert.notEqual(startIndex, -1, "confirmed global asset edit must start an image edit job explicitly");
  assert.notEqual(fusionStartIndex, -1, "confirmed global asset fusion must start a separate fusion job when uploaded images exist");
  assert.notEqual(persistIndex, -1, "global asset edit must persist the job id");
  assert.notEqual(pollIndex, -1, "global asset edit must poll the persisted job");
  assert.ok(startIndex < persistIndex && fusionStartIndex < persistIndex && persistIndex < pollIndex, "global asset job id must be persisted before polling starts");
  assert.match(executeSource, /kind:\s*shouldFuseAsset \? "scene_global_asset_fusion" : "scene_global_asset_edit"/, "uploaded image materials must route to the fusion pending job kind");
  assert.match(executeSource, /job_api:\s*shouldFuseAsset \? "fuse_asset" : "edit_asset"/, "uploaded image materials must route to the fusion job API");
  assert.match(executeSource, /materials:\s*uploadedReferences/, "global asset jobs must pass uploaded image materials to the backend");
  assert.match(executeSource, /model:\s*request\.selection\?\.model/, "global asset jobs must pass the confirmed model");
  assert.match(executeSource, /ratio:\s*request\.selection\?\.ratio/, "global asset jobs must pass the confirmed ratio");
  assert.match(executeSource, /size:\s*request\.selection\?\.size/, "global asset jobs must pass the confirmed quality");
  assert.equal(executeSource.includes("api.editImageAsset"), false, "global asset edit must not synchronously wait on image editing");
  assert.match(lookupSource, /const currentMessages = messagesRef\.current/, "global asset lookup must use the latest restored message ref");
  assert.match(lookupSource, /preferredMessageIds/, "global asset lookup must prefer persisted source message ids");
  assert.match(completionSource, /pendingImageJob\.artifact\?\.videoScenePackages/, "completion must fall back to the persisted job artifact");
  assert.match(completionSource, /sceneGlobalAssetEditReview/, "completion must create a review payload instead of replacing immediately");
  assert.match(completionSource, /scene_global_asset_edit_review/, "completion must persist the pending review payload");
  assert.doesNotMatch(completionSource, /syncGlobalSceneAssetEditAcrossConversation/, "completion must not replace scene package assets before user confirmation");
  assert.match(acceptSource, /sceneGlobalAssetEditReview[\s\S]*syncGlobalSceneAssetEditAcrossConversation/, "accepting the review must sync edited images back into scene package cards");
  assert.match(acceptSource, /pushArtifact\("素材已替换，已推送更新后的场景包[\s\S]*type:\s*"video_scene_packages"/, "accepting the review must push a fresh scene package card after replacement");
  assert.match(acceptSource, /setSelectedStoryboardMessageId\(updatedScenePackageMessage\.id\)/, "the storyboard panel should follow the newly pushed scene package card");
  assert.match(completionSource, /baseVideoScenePackages/, "completion must be able to patch the fallback scene package snapshot");
});

test("scene video jobs persist their id before polling so conversations can recover", () => {
  assert.match(apiSource, /startSceneVideosJob:/, "API client must expose a start-only scene video job call");
  assert.match(apiSource, /getSceneVideosJob:/, "API client must expose a query-only scene video job call");
  assert.match(apiSource, /pollSceneVideoJob,/, "API client must expose polling for existing scene video jobs");
  assert.match(workspaceSource, /pendingVideoJob\?: PendingVideoJob \| null/, "WorkspaceSnapshot must store pending video jobs");
  assert.match(workspaceSource, /pending_video_job\?: PendingVideoJob \| null/, "WorkspaceSnapshot must restore legacy snake_case pending video jobs");
  assert.match(workspaceSource, /pendingVideoJobRef\.current\?\.conversation_id === snapshotConversationId/, "conversation snapshots must keep the active pending video job");

  const source = handleGenerateVideoFromScenePackagesSource();
  const startIndex = source.indexOf("api.startSceneVideosJob(request)");
  const persistIndex = source.indexOf("await persistPendingVideoJob(pendingVideoJob");
  const pollIndex = source.indexOf("await resumePendingVideoJob(pendingVideoJob, processedKey)");
  assert.notEqual(startIndex, -1, "scene video generation must start a backend job explicitly");
  assert.notEqual(persistIndex, -1, "scene video generation must persist the job id");
  assert.notEqual(pollIndex, -1, "scene video generation must poll the persisted job");
  assert.ok(startIndex < persistIndex && persistIndex < pollIndex, "job id must be persisted before polling starts");
  assert.match(source, /kind:\s*"scene_generation"/, "first-time scene generation must record its pending job kind");
  assert.equal(source.includes("api.generateSceneVideos"), false, "WorkspacePage must not use the start+poll convenience wrapper for scene jobs");
});

test("video merge uses start and polling instead of a long synchronous request", () => {
  assert.match(apiSource, /startMergeSceneVideosJob:/, "API client must expose a start-only video merge job call");
  assert.match(apiSource, /getMergeSceneVideosJob:/, "API client must expose a query-only video merge job call");
  assert.match(apiSource, /pollMergeSceneVideoJob,/, "API client must expose polling for existing video merge jobs");
  assert.match(apiSource, /const started = await api\.startMergeSceneVideosJob\(body\)/, "mergeSceneVideos must start a backend merge job first");
  assert.match(apiSource, /return pollMergeSceneVideoJob\(started\.job_id\)/, "mergeSceneVideos must poll the merge job result");
  assert.doesNotMatch(apiSource, /mergeSceneVideos:[\s\S]*?req<MergeSceneVideosResponse>\(`\$\{FLOW_BASE\}\/video\/merge`/, "mergeSceneVideos must not wait on synchronous /video/merge");
});

test("video merge jobs are persisted before polling so conversations can recover", () => {
  assert.match(workspaceSource, /type PendingVideoJobKind =[\s\S]*"video_merge"/, "pending video jobs must include video_merge");
  assert.match(workspaceSource, /api\.getMergeSceneVideosJob\(pendingVideoJob\.job_id\)/, "resumePendingVideoJob must query existing merge jobs");
  assert.match(workspaceSource, /api\.pollMergeSceneVideoJob\(pendingVideoJob\.job_id,\s*shouldContinuePolling\)/, "resumePendingVideoJob must poll existing merge jobs");
  assert.match(workspaceSource, /handleCompletedVideoMergeJob\(pendingVideoJob,\s*mergedVideo,\s*processedKey\)/, "merge job completion must use the shared video result handler");

  const source = startAndResumeVideoMergeJobSource();
  const startIndex = source.indexOf("api.startMergeSceneVideosJob(request)");
  const persistIndex = source.indexOf("await persistPendingVideoJob(pendingVideoJob");
  const pollIndex = source.indexOf("await resumePendingVideoJob(pendingVideoJob, processedKey)");
  assert.notEqual(startIndex, -1, "scene completion must start a backend merge job explicitly");
  assert.notEqual(persistIndex, -1, "scene completion must persist the merge job id");
  assert.notEqual(pollIndex, -1, "scene completion must poll the persisted merge job");
  assert.ok(startIndex < persistIndex && persistIndex < pollIndex, "merge job id must be persisted before polling starts");
});

test("scene package jobs persist their id before polling so conversations can recover", () => {
  assert.match(apiSource, /startPrepareScenePackagesJob:/, "API client must expose a start-only scene package job call");
  assert.match(apiSource, /getPrepareScenePackagesJob:/, "API client must expose a query-only scene package job call");
  assert.match(apiSource, /pollPrepareScenePackagesJob,/, "API client must expose polling for existing scene package jobs");
  assert.match(apiSource, /startSceneAssetsJob:/, "API client must expose a start-only scene asset job call");
  assert.match(apiSource, /getSceneAssetsJob:/, "API client must expose a query-only scene asset job call");
  assert.match(apiSource, /pollSceneAssetsJob,/, "API client must expose polling for existing scene asset jobs");
  assert.match(workspaceSource, /pendingScenePackageJob\?: PendingScenePackageJob \| null/, "WorkspaceSnapshot must store pending scene package jobs");
  assert.match(workspaceSource, /pending_scene_package_job\?: PendingScenePackageJob \| null/, "WorkspaceSnapshot must restore legacy snake_case pending scene package jobs");
  assert.match(workspaceSource, /pendingScenePackageJobRef\.current\?\.conversation_id === snapshotConversationId/, "conversation snapshots must keep the active pending scene package job");

  const source = handleApprovePlanSource();
  const startIndex = source.indexOf("api.startPrepareScenePackagesJob(request)");
  const persistIndex = source.indexOf("await persistPendingScenePackageJob(pendingScenePackageJob");
  const pollIndex = source.indexOf("await resumePendingScenePackageJob(pendingScenePackageJob, processedKey)");
  assert.notEqual(startIndex, -1, "video approval must start a backend scene package job explicitly");
  assert.notEqual(persistIndex, -1, "video approval must persist the job id");
  assert.notEqual(pollIndex, -1, "video approval must poll the persisted job");
  assert.ok(startIndex < persistIndex && persistIndex < pollIndex, "scene package job id must be persisted before polling starts");
  assert.match(source, /kind:\s*"scene_package_generation"/, "scene package generation must record its pending job kind");
});

test("scene reference image retry only submits failed asset targets", () => {
  const source = handleRetrySceneAssetsSource();
  assert.match(source, /sceneAssetRetryTargets\(artifact\.sceneAssetFailures\)/, "retry must derive stable targets from failed assets");
  assert.match(source, /target_assets:\s*targetAssets/, "retry request must carry only failed asset targets");
  assert.match(workspaceSource, /mergeSceneAssetRetryFailures\(/, "retry completion must preserve failures not completed by this retry");
  assert.match(apiSource, /target_assets\?: SceneAssetRetryTarget\[\]/, "scene asset API DTO must expose the target whitelist");
});

test("video plan contract drives scene package assets videos and recoverable jobs", () => {
  const approveSource = handleApprovePlanSource();
  const sceneRequestStart = workspaceSource.indexOf("const sceneVideoRequestFromPackages = (");
  const sceneRequestEnd = workspaceSource.indexOf("const handleCompletedSceneGenerationJob", sceneRequestStart);
  const sceneRequestSource = workspaceSource.slice(sceneRequestStart, sceneRequestEnd);

  assert.match(approveSource, /const creationContract = artifact\.plan\.creation_contract/, "video approval must use the final Plan contract");
  assert.match(approveSource, /target_duration_ms:\s*creationContract\.video_duration_sec \* 1000/, "scene timeline must use confirmed duration");
  assert.match(approveSource, /creation_contract:\s*creationContract/, "scene package request must carry the final contract");
  assert.doesNotMatch(approveSource, /inferTargetDurationMs\(/, "video approval must not infer duration again after Plan approval");
  assert.match(sceneRequestSource, /videoScenePackages\.creation_contract/, "scene video request must read the persisted final contract");
  assert.match(sceneRequestSource, /ratio:\s*creationContract\.video_ratio/, "scene videos must use the confirmed ratio");
  assert.match(sceneRequestSource, /size:\s*creationContract\.video_size/, "scene videos must use the confirmed size");
  assert.match(sceneRequestSource, /model:\s*creationContract\.video_model/, "scene videos must use the confirmed model");
  assert.match(sceneRequestSource, /sound:\s*creationContract\.video_sound/, "scene videos must use the confirmed sound setting");
  assert.match(workspaceSource, /creation_contract:\s*videoScenePackages\.creation_contract/, "conversation context must persist the contract with scene packages");
});

test("restored conversations resume existing video jobs without starting duplicates", () => {
  const applySource = applyConversationSource();
  assert.match(applySource, /snapshot\.pendingVideoJob \|\| snapshot\.pending_video_job/, "restore must read pending video job metadata");
  assert.match(applySource, /resumePendingVideoJob\(pendingVideoJob\)/, "restore must resume polling an existing job");
  assert.equal(applySource.includes("startSceneVideosJob"), false, "restore must not start a duplicate scene video job");
  assert.equal(applySource.includes("pushAssistant"), false, "restore must not add a separate resume-polling progress message");
});

test("restored conversations resume existing scene package jobs without starting duplicates", () => {
  const applySource = applyConversationSource();
  const resumeSource = resumePendingScenePackageJobSource();
  assert.match(applySource, /snapshot\.pendingScenePackageJob \|\| snapshot\.pending_scene_package_job/, "restore must read pending scene package job metadata");
  assert.match(applySource, /resumePendingScenePackageJob\(pendingScenePackageJob\)/, "restore must resume polling an existing scene package job");
  assert.equal(applySource.includes("startPrepareScenePackagesJob"), false, "restore must not start a duplicate scene package job");
  assert.equal(applySource.includes("startSceneAssetsJob"), false, "restore must not start a duplicate scene asset job");
  assert.equal(resumeSource.includes("已恢复上次场景包生成任务"), false, "restore polling should not append duplicate progress messages");
  assert.match(resumeSource, /const shouldContinuePolling = \(\) => isVisibleConversation\(pendingScenePackageJob\.conversation_id\)/, "scene package polling must stop when the conversation is no longer visible");
  assert.match(resumeSource, /pausedForHiddenConversation[\s\S]*releaseArtifactAction\(processedKey\)/, "stopping hidden conversation polling must release the local action lock without clearing the pending job");
});

test("video revision regeneration also uses recoverable scene video jobs", () => {
  const source = handleRegenerateVideoWithRevisionSource();
  const startIndex = source.indexOf("api.startSceneVideosJob(request)");
  const persistIndex = source.indexOf("await persistPendingVideoJob(pendingVideoJob");
  const pollIndex = source.indexOf("await resumePendingVideoJob(pendingVideoJob, processedKey)");
  assert.notEqual(startIndex, -1, "video revision must start a backend scene job explicitly");
  assert.notEqual(persistIndex, -1, "video revision must persist the job id");
  assert.notEqual(pollIndex, -1, "video revision must poll the persisted job");
  assert.ok(startIndex < persistIndex && persistIndex < pollIndex, "revision job id must be persisted before polling starts");
  assert.match(source, /kind:\s*"scene_regeneration"/, "video revision must record its pending job kind");
  assert.match(source, /affected_scene_ids:\s*Array\.from\(affectedSceneIds\)/, "video revision pending job must preserve affected scene ids");
  assert.match(source, /scenePackagesWithRevisionContract/, "video revision must rewrite affected scene packages before regeneration");
  assert.match(source, /sceneVideoRequestFromPackages\(nextVideoScenePackages,\s*affectedSceneIds\)/, "video revision request must use the rewritten scene package contract");
  assert.equal(source.includes("api.generateSceneVideos"), false, "video revision must not use the start+poll convenience wrapper");
});

test("scene package storyboard edits after final video regenerate only dirty scene videos before re-merge", () => {
  const source = handleGenerateVideoFromScenePackagesSource();
  assert.match(source, /videoScenePackageEditedSceneIds/, "final storyboard edits must track dirty scene ids");
  assert.match(source, /new Set\(artifact\.videoScenePackageEditedSceneIds/, "dirty scene ids should drive the regeneration subset");
  assert.match(workspaceSource, /messagesRef\.current = nextItems[\s\S]*return nextItems/, "storyboard edits must update messagesRef before generation reads the artifact");
  assert.match(source, /const latestMessage =[\s\S]*messagesRef\.current\.find[\s\S]*message\.id === msg\.id[\s\S]*const artifact = latestMessage\.artifact/, "scene generation must reload the latest scene package artifact by message id");
  assert.match(source, /canReuseUneditedSceneVideos\(videoScenePackages,\s*artifact\.generatedSceneVideos,\s*dirtySceneIds\)/, "dirty-scene regeneration must be based on reusable scene videos instead of only mergedVideo.ok");
  assert.doesNotMatch(source, /const isFinalStoryboardRegeneration = Boolean\(artifact\.mergedVideo\?\.ok/, "dirty-scene regeneration must not require an already merged final video");
  assert.match(source, /kind:\s*"scene_regeneration"/, "confirmed final storyboard edits must use recoverable scene regeneration jobs");
  assert.match(source, /affected_scene_ids:\s*Array\.from\(dirtySceneIds\)/, "pending regeneration jobs must persist the dirty scene ids");
  assert.match(source, /sceneVideoRequestFromPackages\(videoScenePackages,\s*dirtySceneIds\)/, "request builder must receive only dirty scenes for final storyboard edits");
  assert.match(source, /merged_video:\s*artifact\.mergedVideo/, "pending regeneration context must retain the previous merged video when present");
  assert.doesNotMatch(source, /artifact\.type === "video_result"/, "dirty-scene regeneration must work from the original scene package card, not only video_result cards");
  assert.match(workspaceSource, /sceneVideoForPackageScene\(scene,\s*regenerated\.scene_videos\)[\s\S]*sceneVideoForPackageScene\(scene,\s*previousGeneratedSceneVideos\.scene_videos\)/, "regeneration completion must reuse unchanged scene videos with scene_index fallback");
});

test("video QC revisions use scene-package-ready baseline instead of user-edited result packages", () => {
  const completedScenePackageSource = handleCompletedScenePackageJobSource();
  const generateSource = handleGenerateVideoFromScenePackagesSource();
  const revisionSource = handleRegenerateVideoWithRevisionSource();

  assert.match(completedScenePackageSource, /originalVideoScenePackages:\s*videoScenePackages/, "scene package ready card must freeze the original scene contract");
  assert.match(generateSource, /const originalVideoScenePackages = artifact\.originalVideoScenePackages \|\| latestOriginalVideoScenePackagesForConversation/, "scene video job must recover the frozen baseline before persisting");
  assert.match(generateSource, /artifact:\s*\{\s*\.\.\.artifact,\s*originalVideoScenePackages/, "scene video job must carry the frozen scene package baseline forward");
  assert.doesNotMatch(generateSource, /originalVideoScenePackages:\s*artifact\.originalVideoScenePackages\s*\|\|\s*videoScenePackages/, "video result must not freeze a possibly user-edited package as the original baseline");
  assert.match(revisionSource, /const originalVideoScenePackages = artifact\.originalVideoScenePackages \|\| latestOriginalVideoScenePackagesForConversation/, "QC revision must recover the frozen baseline");
  assert.match(revisionSource, /originalVideoScenePackages\.scene_packages as ScenePackageRecord\[\]/, "QC revision must restore affected scenes from the frozen baseline");
});

test("failed scene video retries only resubmit failed scenes and reuse successful scene videos", () => {
  const source = handleGenerateVideoFromScenePackagesSource();
  assert.match(source, /failedSceneIdsFromGeneratedSceneVideos/, "failed scene ids must be extracted from generatedSceneVideos.failed_scenes");
  assert.match(source, /kind:\s*"scene_failed_retry"/, "retrying failed scene videos must use a distinct recoverable job kind");
  assert.match(source, /sceneVideoRequestFromPackages\(videoScenePackages,\s*retrySceneIds\)/, "failed scene retry must only submit the failed scene ids");
  assert.match(workspaceSource, /sceneVideoForPackageScene\(scene,\s*retried\.scene_videos\)[\s\S]*sceneVideoForPackageScene\(scene,\s*previousGeneratedSceneVideos\.scene_videos\)/, "retry completion must reuse previously successful scene videos with scene_index fallback");
});

test("scene generation completion updates the original scene package card with videos", () => {
  assert.match(workspaceSource, /updateOriginalScenePackageMessageWithVideoResult|syncScenePackageMessageVideoResult/, "workspace must update the original scene package message after videos are generated");
  assert.match(workspaceSource, /source_message_id:[\s\S]*pendingVideoJob\.source_message_id/, "pending video jobs must keep the original scene package message id");
  assert.match(workspaceSource, /generatedSceneVideos[\s\S]*mergedVideo[\s\S]*videoScenePackageEditedSceneIds:\s*\[\]/, "the original scene package card must receive generated scene videos and merged video");
  assert.match(workspaceSource, /currentMessage\?\.artifact \|\| savedMessage\.artifact/, "conversation message save responses must not overwrite locally enriched artifacts");
  assert.match(workspaceSource, /messagesRef\.current = nextItems[\s\S]*return nextItems/, "scene package video-result sync must update the message ref used by later snapshots");
});

test("final storyboard edits persist and restore the latest scene package context", () => {
  assert.match(workspaceSource, /video_scene_package_edited_scene_ids/, "dirty scene ids must be persisted in conversation context");
  assert.match(workspaceSource, /latestScenePackageSnapshotForConversation/, "snapshots must preserve latest scene package restore fields");
  assert.match(workspaceSource, /latestVideoResultArtifactForConversation/, "restoring after refresh must recover scene videos from the persisted video_result card");
  assert.match(
    workspaceSource,
    /latestVideoResultArtifact\?\.generatedSceneVideos \|\|[\s\S]*message\.artifact\.generatedSceneVideos \|\|[\s\S]*contextGeneratedSceneVideos/,
    "存在权威消息时必须优先用视频结果或原场景包消息恢复分镜视频",
  );
  assert.match(
    workspaceSource,
    /global_assets:\s*videoScenePackages\.global_assets,[\s\S]*scene_packages:\s*videoScenePackages\.scene_packages/,
    "存在权威场景包消息时不得用旧 context 覆盖素材和分镜",
  );
  assert.match(workspaceSource, /messagesRef\.current = snapshot\.messages/, "restored messages must update the ref used by snapshot persistence");
  assert.match(workspaceSource, /generated_scene_videos:\s*artifact\.generatedSceneVideos\?\.scene_videos/, "snapshot must include generated scene videos from the latest scene package card");
  assert.match(workspaceSource, /merged_video:\s*artifact\.mergedVideo/, "snapshot must include merged video from the latest scene package card");
  assert.match(workspaceSource, /\.\.\.scenePackageSnapshot/, "all conversation updates based on makeSnapshot must keep scene video restore fields");
  assert.match(workspaceSource, /message\.artifact\?\.type === "video_scene_packages" && Boolean\(message\.artifact\.videoScenePackages\)/, "restored context must target the latest original scene package card");
  assert.match(workspaceSource, /generated_scene_videos[\s\S]*merged_video/, "restored scene package context must include generated scene videos and merged video");
  assert.match(workspaceSource, /api\s*\.\s*updateConversation\(targetConversationId,[\s\S]*global_assets:[\s\S]*scene_packages:[\s\S]*video_scene_package_edited_scene_ids/, "scene package edits must update conversation context");
});
