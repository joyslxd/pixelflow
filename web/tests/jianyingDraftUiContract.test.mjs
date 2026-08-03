import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const workspaceSource = fs.readFileSync(path.resolve(testDirectory, "../src/pages/WorkspacePage.tsx"), "utf8");
const apiSource = fs.readFileSync(path.resolve(testDirectory, "../src/lib/api.ts"), "utf8");
const jianyingDraftSource = fs.readFileSync(path.resolve(testDirectory, "../src/lib/jianyingDraft.ts"), "utf8");
const chatPanelSource = fs.readFileSync(path.resolve(testDirectory, "../src/components/chat/ChatPanel.tsx"), "utf8");
const messageBubbleSource = fs.readFileSync(path.resolve(testDirectory, "../src/components/chat/MessageBubble.tsx"), "utf8");

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
  const targetPatchMatch = workspaceSource.match(
    /const patchJianyingDraftConversationContextForTarget[\s\S]*?(?=\n\s{2}const \w|\n\s{2}useEffect)/,
  );

  assert.ok(persistMatch, "persistPendingJianyingDraftJob must exist");
  assert.ok(targetPatchMatch, "target-local context patch helper must exist");
  const persistSource = persistMatch[0];
  const targetPatchSource = targetPatchMatch[0];
  assert.match(persistSource, /patchJianyingDraftConversationContextForTarget/);
  assert.match(
    persistSource,
    /conversationIdRef\.current === targetConversationId[\s\S]*?pendingJianyingDraftJobRef\.current = pendingJianyingDraftJob/,
  );
  assert.match(
    targetPatchSource,
    /await patchJianyingDraftTargetConversation\([\s\S]*?isCurrentConversation: \(conversationId\) => conversationIdRef\.current === conversationId/,
  );
  assert.match(targetPatchSource, /setJianyingDraftRecordsForConversation\(\s*targetConversationId/);
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

test("草稿生成 handler 仅跳过仍有效的成功记录，过期成功允许重新启动", () => {
  const generateMatch = workspaceSource.match(
    /const handleGenerateJianyingDraft[\s\S]*?(?=\n\s{2}const \w|\n\s{2}async function|\n\s{2}function)/,
  );

  assert.ok(generateMatch, "handleGenerateJianyingDraft must exist");
  const generateSource = generateMatch[0];
  assert.match(generateSource, /const existingRecord = jianyingDraftRecordsForConversation\(targetConversationId\)\[storyboard_version_id\]/);
  assert.match(generateSource, /if \(isJianyingDraftSucceededResultValid\(existingRecord\)\) return/);
  assert.doesNotMatch(generateSource, /\?\.status === "succeeded"\) return/);
});

test("跨会话持久化使用后端原子 PATCH，不再执行 GET 加全量 PUT", () => {
  const targetPatchMatch = workspaceSource.match(
    /const patchJianyingDraftConversationContextForTarget[\s\S]*?(?=\n\s{2}const \w|\n\s{2}useEffect)/,
  );
  const persistMatch = workspaceSource.match(
    /const persistPendingJianyingDraftJob[\s\S]*?(?=\n\s{2}const \w|\n\s{2}useEffect)/,
  );

  assert.ok(targetPatchMatch, "target-local context patch helper must exist");
  assert.ok(persistMatch, "persistPendingJianyingDraftJob must exist");
  const targetPatchSource = targetPatchMatch[0];
  const persistSource = persistMatch[0];
  assert.match(targetPatchSource, /api\.patchJianyingDraftConversationContext\(/);
  assert.match(targetPatchSource, /patchJianyingDraftTargetConversation\(/);
  assert.match(targetPatchSource, /expectedJobId/);
  assert.match(targetPatchSource, /expected_job_id: expectedJobId/);
  assert.doesNotMatch(targetPatchSource, /api\.getConversation\(/);
  assert.doesNotMatch(targetPatchSource, /api\.updateConversation\(/);
  assert.doesNotMatch(targetPatchSource, /makeSnapshot\(targetConversationId\)/);
  assert.match(persistSource, /patchJianyingDraftConversationContextForTarget/);
  assert.doesNotMatch(persistSource, /makeSnapshot\(targetConversationId\)/);
  assert.match(apiSource, /patchJianyingDraftConversationContext/);
  assert.match(apiSource, /expected_job_id: string/);
  assert.match(apiSource, /\/conversations\/\$\{encodeURIComponent\(conversationId\)\}\/jianying-draft-context/);
  assert.match(apiSource, /method: "PATCH"/);
});

test("终态与过期写入携带原 pending job 条件，启动写入携带新 job 条件", () => {
  assert.match(
    workspaceSource,
    /persistPendingJianyingDraftJob\([\s\S]*?`jianying_draft_\$\{boundResult\.status\}`,[\s\S]*?pendingJob\.job_id/,
  );
  assert.match(
    workspaceSource,
    /persistPendingJianyingDraftJob\(\s*null,\s*targetConversationId,\s*"jianying_draft_job_expired",[\s\S]*?pendingJob\.job_id/,
  );
  assert.match(
    workspaceSource,
    /persistPendingJianyingDraftJob\(\s*pendingJianyingDraftJob,\s*targetConversationId,\s*"jianying_draft_running",[\s\S]*?pendingJianyingDraftJob\.job_id/,
  );
});

test("过期任务保留恢复错误，capability 后只使用捕获的目标对话", () => {
  const expiredMatch = workspaceSource.match(
    /const clearExpiredJianyingDraftJob[\s\S]*?(?=\n\s{2}const \w|\n\s{2}useEffect)/,
  );
  const generateMatch = workspaceSource.match(
    /const handleGenerateJianyingDraft[\s\S]*?(?=\n\s{2}const \w|\n\s{2}async function|\n\s{2}function)/,
  );

  assert.ok(expiredMatch, "clearExpiredJianyingDraftJob must exist");
  assert.ok(generateMatch, "handleGenerateJianyingDraft must exist");
  assert.match(
    expiredMatch[0],
    /persistPendingJianyingDraftJob\(\s*null,\s*targetConversationId,\s*"jianying_draft_job_expired",\s*pendingJob\.job_id,\s*\{\},\s*message/,
  );
  const afterCapability = generateMatch[0].slice(generateMatch[0].indexOf("await api.getJianyingDraftCapability()"));
  assert.doesNotMatch(afterCapability, /conversationIdRef\.current/);
  assert.match(afterCapability, /conversation_id: targetConversationId/);
  assert.match(afterCapability, /persistPendingJianyingDraftJob\(\s*pendingJianyingDraftJob,\s*targetConversationId/);
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

test("最终视频卡片透传剪映草稿 handler，并保持原视频结果作为可操作消息", () => {
  assert.match(chatPanelSource, /onGenerateJianyingDraft/);
  assert.match(chatPanelSource, /onDownloadJianyingDraft/);
  assert.match(messageBubbleSource, /onGenerateJianyingDraft/);
  assert.match(messageBubbleSource, /onDownloadJianyingDraft/);
  assert.match(chatPanelSource, /message\.artifact\?\.type !== "jianying_draft"/);
  assert.match(chatPanelSource, /latestActionableMessageId/);
});

test("最终视频提供三按钮、历史入口与运行锁定", () => {
  const videoResultBranch = messageBubbleSource.match(
    /msg\.artifact\?\.type === "video_result"[\s\S]*?(?=\n        \) : msg\.artifact \?)/,
  );
  assert.ok(videoResultBranch, "video result branch must exist");
  const source = videoResultBranch[0];
  assert.match(source, /sm:grid-cols-3/);
  assert.match(source, /无意见，结束/);
  assert.match(source, /生成剪映草稿/);
  assert.match(source, /提出修改意见/);
  assert.match(source, /videoAccepted/);
  assert.match(source, /草稿生成中/);
  assert.match(source, /disabled=/);
  assert.match(source, /剪映草稿服务待接入/);
  assert.match(source, /title=\{.*剪映草稿服务待接入/);
});

test("剪映草稿结果卡提供下载、失败重试且不用伪下载", () => {
  assert.match(messageBubbleSource, /msg\.artifact\?\.type === "jianying_draft"/);
  assert.match(messageBubbleSource, /剪映草稿已生成/);
  assert.match(messageBubbleSource, /FileArchive/);
  assert.match(messageBubbleSource, /LoaderCircle/);
  assert.match(messageBubbleSource, /下载剪映草稿/);
  assert.match(messageBubbleSource, /重新生成剪映草稿/);
  assert.match(messageBubbleSource, /href=\{jianyingDraftDownloadUrl\}/);
  assert.doesNotMatch(messageBubbleSource, /URL\.createObjectURL/);
});

test("历史无下载地址的成功草稿卡按失败处理并允许重试", () => {
  assert.match(messageBubbleSource, /isJianyingDraftResultRetryable\(jianyingDraftResult\)/);
  assert.match(messageBubbleSource, /jianyingDraftSucceeded \? "剪映草稿已生成" : "剪映草稿生成失败"/);
  assert.match(messageBubbleSource, /\) : jianyingDraftRetryable \? \(/);
  assert.match(messageBubbleSource, /剪映草稿生成失败，请重新生成。/);
});

test("失败草稿结果卡绕过旧消息锁定且只受忙碌或服务状态限制", () => {
  assert.match(chatPanelSource, /artifact\.type === "jianying_draft"/);
  assert.match(chatPanelSource, /isJianyingDraftResultRetryable\(artifact\.jianyingDraft\)/);
  assert.match(chatPanelSource, /const keepRecoverableActions = hasRecoverableArtifactAction\(m\)/);
  assert.match(
    chatPanelSource,
    /actionsDisabled=\{Boolean\(artifactActionsDisabled\) \|\| \(!isLatestActionableQualityReview && isSupersededArtifact && !keepScenePackageActions && !keepRecoverableActions\)\}/,
  );
  assert.match(
    messageBubbleSource,
    /disabled=\{actionsDisabled \|\| jianyingDraftRunning \|\| jianyingDraftUnavailable\}/,
  );
});

test("失败重试、not_configured 终态和 job 级消息幂等均有明确合同", () => {
  const completeMatch = workspaceSource.match(
    /const completeJianyingDraftJob[\s\S]*?(?=\n\s{2}const clearExpiredJianyingDraftJob)/,
  );
  const resumeMatch = workspaceSource.match(
    /const resumePendingJianyingDraftJob[\s\S]*?(?=\n\s{2}const resumePendingImageJob)/,
  );
  const generateMatch = workspaceSource.match(/const handleGenerateJianyingDraft[\s\S]*?\n  const handleGenerateVideoFromScenePackages/);
  assert.ok(completeMatch && resumeMatch && generateMatch);
  assert.match(completeMatch[0], /pendingJob\.job_id/);
  assert.match(completeMatch[0], /existingRecord\.job_id !== boundResult\.job_id/);
  assert.match(completeMatch[0], /isJianyingDraftSucceededResultValid\(result\)/);
  assert.match(completeMatch[0], /status: "failed"/);
  assert.match(completeMatch[0], /剪映草稿生成失败，请重新生成。/);
  assert.match(resumeMatch[0], /result\.status === "not_configured"/);
  assert.match(generateMatch[0], /retry_failed/);
  assert.match(jianyingDraftSource, /retry_failed\?: boolean/);
});

test("从视频结果卡重试失败草稿会显式传 retry_failed，错误展示不会拼接响应正文", () => {
  const resumeMatch = workspaceSource.match(
    /const resumePendingJianyingDraftJob[\s\S]*?(?=\n\s{2}const resumePendingImageJob)/,
  );
  const generateMatch = workspaceSource.match(/const handleGenerateJianyingDraft[\s\S]*?\n  const handleGenerateVideoFromScenePackages/);
  assert.ok(resumeMatch && generateMatch);
  assert.match(generateMatch[0], /const retry_failed = existingRecord\?\.status === "failed" \|\| existingRecord\?\.status === "timeout"/);
  assert.match(generateMatch[0], /retry_failed,/);
  assert.match(generateMatch[0], /jianyingDraftPublicErrorMessage\("capability"\)/);
  assert.match(generateMatch[0], /jianyingDraftPublicErrorMessage\("start"\)/);
  assert.doesNotMatch(generateMatch[0], /err\.message|String\(err\)|started\.message/);
  assert.match(resumeMatch[0], /jianyingDraftPublicErrorMessage\("poll"\)/);
  assert.doesNotMatch(resumeMatch[0], /继续查询剪映草稿任务失败:\$\{message\}|err\.message|String\(err\)/);
});

test("Supervisor 剪映生成重试下载均提交当前 workflow 的结构化动作", () => {
  assert.match(workspaceSource, /jianying_action:\s*"start"/);
  assert.match(workspaceSource, /jianying_action:\s*"download"/);
  assert.match(workspaceSource, /storyboard_version_id/);
  assert.match(workspaceSource, /action:\s*"retry_failed"/);
  assert.match(workspaceSource, /action:\s*"continue_workflow"/);
  assert.match(chatPanelSource, /onGenerateJianyingDraft/);
  assert.match(messageBubbleSource, /onDownloadJianyingDraft/);
});
