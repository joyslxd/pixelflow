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
});

test("storyboard detail panel enforces at-reference image limit and failure details", () => {
  assert.ok(existsSync(storyboardPanelPath), "StoryboardPanel must exist");
  assert.match(storyboardPanelSource, /MAX_REFERENCE_IMAGE_COUNT/);
  assert.match(storyboardPanelSource, /最多\s*9\s*张/);
  assert.match(storyboardPanelSource, /@/);
  assert.match(messageBubbleSource, /failed_scenes|失败场景/);
  assert.match(workspaceSource, /generatedSceneVideos\.failed_scenes|failed_scenes/);
});
