import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiSource = readFileSync(path.join(webRoot, "src/lib/api.ts"), "utf8");
const workspaceSource = readFileSync(
  path.join(webRoot, "src/features/legacy-workspace/LegacyWorkspace.tsx"),
  "utf8",
);

const REMOVED_MESSAGE = "旧视频 Job API 已删除，请通过对话由 VideoAgent 继续";
const CONTINUE_TIP = "请在对话中说明需求，由 VideoAgent 继续生成";

test("api client stubs legacy video job methods and does not call /flows/video", () => {
  assert.match(apiSource, new RegExp(REMOVED_MESSAGE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.doesNotMatch(apiSource, /`\$\{FLOW_BASE\}\/video\//);
  assert.doesNotMatch(apiSource, /req<[^>]+>\(`\$\{FLOW_BASE\}\/video/);

  for (const method of [
    "startPrepareScenePackagesJob",
    "getPrepareScenePackagesJob",
    "generateSceneAssets",
    "startSceneAssetsJob",
    "getSceneAssetsJob",
    "startSceneVideosJob",
    "getSceneVideosJob",
    "startMergeSceneVideosJob",
    "getMergeSceneVideosJob",
    "reviewVideoQuality",
    "startVideoQualityReviewJob",
    "getJianyingDraftCapability",
    "startJianyingDraftJob",
    "getJianyingDraftJob",
    "analyzeStoryboards",
  ]) {
    assert.match(apiSource, new RegExp(`${method}:`), `${method} stub must remain exported`);
    assert.match(apiSource, new RegExp(`${method}[\\s\\S]*?throwLegacyVideoJobApiRemoved\\(\\)`), `${method} must throw`);
  }
});

test("LegacyWorkspace blocks resume and user-initiated legacy video job HTTP", () => {
  assert.match(workspaceSource, new RegExp(CONTINUE_TIP.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(workspaceSource, /LEGACY_VIDEO_JOB_HTTP_REMOVED/);
  assert.match(workspaceSource, /const resumePendingVideoJob = async[\s\S]*?if \(LEGACY_VIDEO_JOB_HTTP_REMOVED\) return;/);
  assert.match(workspaceSource, /const resumePendingScenePackageJob = async[\s\S]*?if \(LEGACY_VIDEO_JOB_HTTP_REMOVED\) return;/);
  assert.match(workspaceSource, /const resumePendingJianyingDraftJob = async[\s\S]*?if \(LEGACY_VIDEO_JOB_HTTP_REMOVED\) return;/);
  assert.doesNotMatch(workspaceSource, /void api\.getJianyingDraftCapability\(\)/);
});
