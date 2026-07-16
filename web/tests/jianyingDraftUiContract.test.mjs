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
  assert.match(workspaceSource, /jianying_draft_records/);
  assert.match(workspaceSource, /resumePendingJianyingDraftJob/);
  assert.match(workspaceSource, /pendingJob\.conversation_id/);
  assert.match(workspaceSource, /storyboard_version_id/);
});

test("当前对话启动任务会立即写入 pending ref，并持久化两种 records 字段", () => {
  const persistMatch = workspaceSource.match(
    /const persistPendingJianyingDraftJob[\s\S]*?(?=\n\s{2}const \w|\n\s{2}useEffect)/,
  );

  assert.ok(persistMatch, "persistPendingJianyingDraftJob must exist");
  const persistSource = persistMatch[0];
  assert.match(persistSource, /isCurrentConversation\(targetConversationId\)/);
  assert.match(persistSource, /pendingJianyingDraftJobRef\.current = pendingJianyingDraftJob/);
  assert.match(persistSource, /pending_jianying_draft_job: pendingJianyingDraftJob/);
  assert.match(workspaceSource, /jianying_draft_records: records/);
  assert.match(workspaceSource, /snapshot\.jianyingDraftRecords \|\| snapshot\.jianying_draft_records/);
});

test("草稿启动 guard 在 capability 查询前建立，并在 finally 中释放", () => {
  const generateMatch = workspaceSource.match(
    /const handleGenerateJianyingDraft[\s\S]*?(?=\n\s{2}const \w|\n\s{2}async function|\n\s{2}function)/,
  );

  assert.ok(generateMatch, "handleGenerateJianyingDraft must exist");
  const generateSource = generateMatch[0];
  assert.match(generateSource, /jianyingDraftStartGuardRef\.current\.tryAcquire\(targetConversationId, storyboard_version_id\)/);
  assert.match(generateSource, /tryAcquire\(targetConversationId, storyboard_version_id\)[\s\S]*?await api\.getJianyingDraftCapability\(\)/);
  assert.match(generateSource, /finally[\s\S]*?jianyingDraftStartGuardRef\.current\.release\(targetConversationId, storyboard_version_id\)/);
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
