import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const workspaceSource = fs.readFileSync(path.resolve(testDirectory, "../src/pages/WorkspacePage.tsx"), "utf8");

test("剪映草稿任务状态进入对话快照并按来源对话恢复", () => {
  assert.match(workspaceSource, /pendingJianyingDraftJob/);
  assert.match(workspaceSource, /pending_jianying_draft_job/);
  assert.match(workspaceSource, /jianyingDraftRecords/);
  assert.match(workspaceSource, /resumePendingJianyingDraftJob/);
  assert.match(workspaceSource, /pendingJob\.conversation_id/);
  assert.match(workspaceSource, /storyboard_version_id/);
});

test("恢复已有剪映草稿任务只查询状态而不启动新任务", () => {
  const resumeMatch = workspaceSource.match(
    /const resumePendingJianyingDraftJob[\s\S]*?(?=\n\s{2}const \w|\n\s{2}useEffect)/,
  );

  assert.ok(resumeMatch, "resumePendingJianyingDraftJob must exist");
  const resumeSource = resumeMatch[0];
  assert.match(resumeSource, /getJianyingDraftJob/);
  assert.doesNotMatch(resumeSource, /startJianyingDraftJob/);
});
