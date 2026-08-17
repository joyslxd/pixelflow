import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const messageBubbleSource = readFileSync(
  fileURLToPath(new URL("../src/components/chat/MessageBubble.tsx", import.meta.url)),
  "utf8",
);
const workspaceSource = readFileSync(
  fileURLToPath(new URL("../src/features/legacy-workspace/LegacyWorkspace.tsx", import.meta.url)),
  "utf8",
);
const storyboardPanelPath = fileURLToPath(new URL("../src/components/canvas/StoryboardPanel.tsx", import.meta.url));
const storyboardPanelSource = existsSync(storyboardPanelPath) ? readFileSync(storyboardPanelPath, "utf8") : "";
const sceneMentionEditorPath = fileURLToPath(new URL("../src/components/canvas/SceneMentionEditor.tsx", import.meta.url));
const sceneMentionEditorSource = existsSync(sceneMentionEditorPath) ? readFileSync(sceneMentionEditorPath, "utf8") : "";

test("video scene package card hides duration editing from users", () => {
  assert.doesNotMatch(messageBubbleSource, /时长\(ms\)/);
  assert.doesNotMatch(messageBubbleSource, /value=\{scene\.duration_ms\}/);
});

test("video scene package chat card is a compact storyboard entry", () => {
  assert.match(messageBubbleSource, /查看分镜/);
  assert.match(messageBubbleSource, /storyboardPreviewAssets|previewAssets/);
  assert.match(messageBubbleSource, /确认并生成视频/);
  assert.doesNotMatch(messageBubbleSource, /全局固定资产/);
  assert.doesNotMatch(messageBubbleSource, /onUpdateVideoSceneAssetField/);
});

test("scene asset model options card echoes the confirmed model not the gpt default", () => {
  const start = messageBubbleSource.indexOf('msg.artifact?.type === "scene_asset_model_options"');
  const end = messageBubbleSource.indexOf('msg.artifact?.type === "video_scene_packages"', start);
  assert.notEqual(start, -1, "model options branch must exist");
  assert.notEqual(end, -1, "video scene packages branch must follow model options");
  const branch = messageBubbleSource.slice(start, end);
  assert.match(branch, /confirmedModel/);
  assert.match(branch, /creation_contract[\s\S]*image_model/);
  assert.match(branch, /sceneAssetModelConfirmed[\s\S]*preferred/);
});

test("storyboard detail panel edits global assets and scene-varying fields", () => {
  assert.ok(existsSync(storyboardPanelPath), "StoryboardPanel must render scene package details in the right canvas");
  assert.match(workspaceSource, /selectedStoryboardMessageId/);
  assert.match(workspaceSource, /VideoAgentStoryboardSurface/);
  assert.match(storyboardPanelSource, /出场角色/);
  assert.match(storyboardPanelSource, /场景/);
  assert.match(storyboardPanelSource, /道具/);
  assert.match(storyboardPanelSource, /视觉风格/);
  assert.match(storyboardPanelSource, /故事线/);
  assert.match(storyboardPanelSource, /镜头描述/);
  // 六字段「旁白（对白）」在镜头描述结构化编辑器内；底部独立旁白框已删除。
  assert.doesNotMatch(
    storyboardPanelSource,
    /updateScene\(\{\s*narration:/,
    "底部重复旁白字段已删除，旁白只在镜头描述六字段里编辑",
  );
  assert.doesNotMatch(storyboardPanelSource, />\s*时间范围\s*</);
  assert.doesNotMatch(storyboardPanelSource, />\s*地点标注\s*</);
  assert.doesNotMatch(storyboardPanelSource, />\s*角色标注\s*</);
  assert.doesNotMatch(storyboardPanelSource, />\s*景别\s*</);
  assert.match(storyboardPanelSource, /shotDescriptionText/);
  assert.match(storyboardPanelSource, /SceneMentionEditor/);
  assert.match(storyboardPanelSource, /ShotDescriptionStructuredEditor|点击字段直接编辑/);
  assert.match(storyboardPanelSource, /composeShotDescriptionFields/);
  assert.match(storyboardPanelSource, /generatingSceneIds/, "分镜面板须接收正在生成的 scene_id");
  assert.match(storyboardPanelSource, /SceneVideoGeneratingOverlay/, "生成中须盖灰蒙版转圈");
  assert.match(workspaceSource, /optimisticGeneratingSceneIds/, "单镜重生点按后须乐观蒙版");
  assert.match(
    workspaceSource,
    /polling[\s\S]*即使仍有旧成片也要蒙版|即使仍有旧成片也要蒙版/,
    "重生保留旧 video_url 时仍须蒙版",
  );
  assert.match(storyboardPanelSource, /previewExpanded/, "镜头预览须支持放大覆盖全屏");
  assert.match(storyboardPanelSource, /放大镜头预览/, "镜头预览标题须可点击放大");
  assert.match(storyboardPanelSource, /返回分镜编辑/, "放大预览须提供返回");
  assert.match(storyboardPanelSource, /mergedVideoUrl/, "资产包须接收合并成片 URL");
  assert.match(storyboardPanelSource, /查看合并后的视频/, "有成片时底部须提供查看入口");
  assert.match(storyboardPanelSource, /合并成片预览/, "查看入口须打开成片预览");
  assert.match(workspaceSource, /mergedVideoUrl=\{/, "工作台须把 Workspace 成片 URL 传入资产包");
  assert.doesNotMatch(storyboardPanelSource, /编辑并 @ 参考图素材/);
  assert.doesNotMatch(storyboardPanelSource, /shotDescriptionEditorOpen/);
  assert.doesNotMatch(storyboardPanelSource, />\s*参考素材\s*</);
});

test("storyboard detail panel enforces at-reference image limit and failure details", () => {
  assert.ok(existsSync(storyboardPanelPath), "StoryboardPanel must exist");
  assert.ok(existsSync(sceneMentionEditorPath), "SceneMentionEditor must own inline @ references");
  assert.match(sceneMentionEditorSource, /MAX_REFERENCE_IMAGE_COUNT/);
  assert.match(sceneMentionEditorSource, /最多\s*9\s*张/);
  assert.match(sceneMentionEditorSource, /选择素材进行关联/);
  assert.match(sceneMentionEditorSource, /@/);
  assert.match(sceneMentionEditorSource, /contentEditable/);
  assert.match(sceneMentionEditorSource, /suppressContentEditableWarning/);
  assert.match(sceneMentionEditorSource, /data-mention-id/);
  assert.match(sceneMentionEditorSource, /data-mention-image-url/);
  assert.match(sceneMentionEditorSource, /group-hover:block/);
  assert.match(sceneMentionEditorSource, /MENTION_CANDIDATE_GROUPS/);
  assert.match(sceneMentionEditorSource, /createPortal/);
  assert.match(sceneMentionEditorSource, /data-scene-mention-menu/);
  assert.match(sceneMentionEditorSource, /className="fixed z-\[100\]/);
  assert.doesNotMatch(sceneMentionEditorSource, /className="absolute z-30/);
  assert.doesNotMatch(sceneMentionEditorSource, /filteredCandidates\.slice/);
  assert.doesNotMatch(sceneMentionEditorSource, /<textarea/);
  assert.match(messageBubbleSource, /failed_scenes|失败场景/);
  assert.match(messageBubbleSource, /查看失败原因/);
  assert.match(messageBubbleSource, /sceneAssetFailureDetails/);
  assert.match(workspaceSource, /generatedSceneVideos\.failed_scenes|failed_scenes/);
});

function videoResultBranchSource() {
  const start = messageBubbleSource.indexOf('msg.artifact?.type === "video_result" && msg.artifact.mergedVideo');
  const end = messageBubbleSource.indexOf(') : msg.artifact?.type === "video_result" ? null', start);
  assert.notEqual(start, -1, "merged video_result branch must exist");
  assert.notEqual(end, -1, "non-merged video_result must be suppressed");
  return messageBubbleSource.slice(start, end);
}

test("original scene package button reopens storyboard with generated scene video previews", () => {
  assert.doesNotMatch(videoResultBranchSource(), /查看分镜/);
  assert.doesNotMatch(videoResultBranchSource(), /分镜视频预览/);
  assert.match(videoResultBranchSource(), /mergedVideo/, "对话 video_result 仅保留合并成片卡");
  assert.match(storyboardPanelSource, /generatedSceneVideos/);
  assert.match(storyboardPanelSource, /sceneVideoForScene/);
  assert.match(storyboardPanelSource, /video\.scene_id === scene\.scene_id/);
  assert.match(storyboardPanelSource, /Number\(video\.scene_index\) === Number\(scene\.scene_index\)/);
  assert.match(storyboardPanelSource, /<video[\s\S]*controls[\s\S]*preload="metadata"/);
  assert.match(storyboardPanelSource, /dirtySceneIds|已修改/);
  assert.match(storyboardPanelSource, /generatingIdSet\.has\(selectedScene\.scene_id\)/);
  assert.match(workspaceSource, /updateOriginalScenePackageMessageWithVideoResult|syncScenePackageMessageVideoResult/);
  assert.match(workspaceSource, /videoScenePackageEditedSceneIds/);
});

test("Supervisor 场景包和分镜操作提交结构化 modify continue regenerate retry", () => {
  assert.match(workspaceSource, /scene_packages/);
  assert.match(workspaceSource, /global_assets/);
  assert.match(workspaceSource, /scene_patches/);
  assert.match(workspaceSource, /action:\s*"modify_workflow"/);
  assert.match(workspaceSource, /action:\s*"regenerate_stage"/);
  assert.match(workspaceSource, /action:\s*"retry_failed"/);
  assert.match(workspaceSource, /action:\s*"continue_workflow"/);
  assert.match(storyboardPanelSource, /onUpdateVideoScenePackage/);
  assert.match(storyboardPanelSource, /onGenerateVideo/);
  assert.match(storyboardPanelSource, /onRetrySceneAssets/);
  assert.match(storyboardPanelSource, /onSave/);
});

test("Supervisor 分镜编辑只在显式保存时提交一次当前草稿", () => {
  assert.match(
    workspaceSource,
    /deferSceneUpdates=\{\s*orchestrationMode === "video_agent_v2" \|\| Boolean\(supervisorVideoArtifact\)\s*\}/,
  );
  assert.match(workspaceSource, /deferSceneUpdates\n/);
  assert.match(storyboardPanelSource, /sceneDraftPatches/);
  assert.match(storyboardPanelSource, /saveStoryboardDraft/);
  assert.match(storyboardPanelSource, /await onUpdateVideoScenePackage/);
  assert.match(storyboardPanelSource, /请先保存当前分镜/);
});

test("Supervisor 最终视频下载携带当前成品 URL 更新交付状态", () => {
  assert.match(workspaceSource, /delivery_download_url/);
  assert.match(workspaceSource, /action:\s*"continue_workflow"/);
  assert.doesNotMatch(workspaceSource, /action:\s*"answer_only"[\s\S]{0,200}delivery_download_url/);
});
