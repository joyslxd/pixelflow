import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const messageBubbleSource = readFileSync(
  fileURLToPath(new URL("../src/components/chat/MessageBubble.tsx", import.meta.url)),
  "utf8",
);
const workspaceSource = readFileSync(
  fileURLToPath(new URL("../src/pages/WorkspacePage.tsx", import.meta.url)),
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
  assert.doesNotMatch(messageBubbleSource, /全局固定资产/);
  assert.doesNotMatch(messageBubbleSource, /onUpdateVideoSceneAssetField/);
});

test("storyboard detail panel edits global assets and scene-varying fields", () => {
  assert.ok(existsSync(storyboardPanelPath), "StoryboardPanel must render scene package details in the right canvas");
  assert.match(workspaceSource, /selectedStoryboardMessageId/);
  assert.match(workspaceSource, /StoryboardPanel/);
  assert.match(storyboardPanelSource, /出场角色/);
  assert.match(storyboardPanelSource, /场景/);
  assert.match(storyboardPanelSource, /道具/);
  assert.match(storyboardPanelSource, /视觉风格/);
  assert.match(storyboardPanelSource, /故事线/);
  assert.match(storyboardPanelSource, /镜头描述/);
  assert.match(storyboardPanelSource, /旁白/);
  assert.doesNotMatch(storyboardPanelSource, />\s*时间范围\s*</);
  assert.doesNotMatch(storyboardPanelSource, />\s*地点标注\s*</);
  assert.doesNotMatch(storyboardPanelSource, />\s*角色标注\s*</);
  assert.doesNotMatch(storyboardPanelSource, />\s*景别\s*</);
  assert.match(storyboardPanelSource, /shotDescriptionText/);
  assert.match(storyboardPanelSource, /SceneMentionEditor/);
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
  assert.match(workspaceSource, /generatedSceneVideos\.failed_scenes|failed_scenes/);
});

function videoResultBranchSource() {
  const start = messageBubbleSource.indexOf('msg.artifact?.type === "video_result"');
  const end = messageBubbleSource.indexOf(") : msg.artifact ?", start);
  assert.notEqual(start, -1, "video result branch must exist");
  assert.notEqual(end, -1, "generic artifact branch must follow video result branch");
  return messageBubbleSource.slice(start, end);
}

test("original scene package button reopens storyboard with generated scene video previews", () => {
  assert.doesNotMatch(videoResultBranchSource(), /查看分镜/);
  assert.match(storyboardPanelSource, /generatedSceneVideos/);
  assert.match(storyboardPanelSource, /sceneVideoForScene/);
  assert.match(storyboardPanelSource, /video\.scene_id === scene\.scene_id/);
  assert.match(storyboardPanelSource, /Number\(video\.scene_index\) === Number\(scene\.scene_index\)/);
  assert.match(storyboardPanelSource, /<video[\s\S]*controls[\s\S]*preload="metadata"/);
  assert.match(storyboardPanelSource, /dirtySceneIds|已修改/);
  assert.match(workspaceSource, /updateOriginalScenePackageMessageWithVideoResult|syncScenePackageMessageVideoResult/);
  assert.match(workspaceSource, /videoScenePackageEditedSceneIds/);
});
