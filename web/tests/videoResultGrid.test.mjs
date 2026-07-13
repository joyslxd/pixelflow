import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const cardSource = readFileSync(new URL("../src/components/canvas/VideoResultCard.tsx", import.meta.url), "utf8");
const previewSource = readFileSync(new URL("../src/components/canvas/VideoPreviewPanel.tsx", import.meta.url), "utf8");
const canvasSource = readFileSync(new URL("../src/components/canvas/CanvasPanel.tsx", import.meta.url), "utf8");
const messageBubbleSource = readFileSync(new URL("../src/components/chat/MessageBubble.tsx", import.meta.url), "utf8");
const workspaceSource = readFileSync(new URL("../src/pages/WorkspacePage.tsx", import.meta.url), "utf8");

test("video result cards preview full video and expose sound plus download controls", () => {
  assert.match(cardSource, /<video[\s\S]*muted=\{muted\}[\s\S]*playsInline[\s\S]*preload="auto"/);
  assert.doesNotMatch(cardSource, /<video[\s\S]*\bcontrols\b/);
  assert.doesNotMatch(cardSource, /\bloop\b/);
  assert.match(cardSource, /onPointerEnter=\{playPreview\}/);
  assert.match(cardSource, /aria-label=\{muted \? "开启声音" : "关闭声音"\}/);
  assert.match(cardSource, /download=\{videoDownloadName\(result\)\}/);
  assert.match(cardSource, /formatVideoDuration\(duration\)/);
  assert.match(cardSource, /text-\[14px\]/);
  assert.match(cardSource, /h-7 w-7/);
});

test("canvas video preview preloads without leaking object URLs", () => {
  assert.match(previewSource, /new AbortController\(\)/);
  assert.match(previewSource, /fetch\(sourceUrl, \{ cache: "force-cache", signal: controller\.signal \}\)/);
  assert.match(previewSource, /URL\.createObjectURL\(blob\)/);
  assert.match(previewSource, /controller\.abort\(\)/);
  assert.match(previewSource, /URL\.revokeObjectURL\(objectUrl\)/);
  assert.match(previewSource, /hasPlaybackStartedRef\.current/);
  assert.match(previewSource, /src=\{playbackUrl\}/);
});

test("video results separate final and scene videos in chat and open canvas preview directly", () => {
  assert.match(messageBubbleSource, /mergedVideoResult/);
  assert.match(messageBubbleSource, /sceneVideoResults/);
  assert.match(messageBubbleSource, /onOpenVideoResult/);
  assert.doesNotMatch(messageBubbleSource, /合并视频：\{msg\.artifact\.mergedVideo\.merged_video_url\}/);
  assert.match(canvasSource, /VideoPreviewPanel/);
  assert.match(canvasSource, /selectedVideo/);
  assert.match(canvasSource, /onClose\?\.\(\)/);
  assert.doesNotMatch(canvasSource, /VideoResultGrid/);
  assert.match(workspaceSource, /handleOpenVideoResult/);
});

test("canvas video preview matches full-player control requirements", () => {
  assert.match(previewSource, /返回/);
  assert.match(previewSource, /下载/);
  assert.match(previewSource, /type="range"/);
  assert.match(previewSource, /togglePlaying/);
  assert.match(previewSource, /toggleMuted/);
  assert.doesNotMatch(previewSource, /<video[\s\S]*\bcontrols\b/);
});
