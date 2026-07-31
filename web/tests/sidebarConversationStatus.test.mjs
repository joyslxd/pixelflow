import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const sidebarSource = fs.readFileSync(
  path.resolve("src/components/layout/Sidebar.tsx"),
  "utf8",
);
const workspaceSource = fs.readFileSync(
  path.resolve("src/pages/WorkspacePage.tsx"),
  "utf8",
);

test("最近对话使用中文阶段标签且不直接展示后端状态值", () => {
  assert.match(sidebarSource, /plan_manual_edit_running: "正在发布编辑"/);
  assert.match(sidebarSource, /conversationPhaseLabel\(t\.last_phase\)/);
  assert.doesNotMatch(sidebarSource, /t\.last_phase === "idle" \? "新" : t\.last_phase/);
});

test("会话阶段持久化成功后通知最近对话刷新", () => {
  const start = workspaceSource.indexOf("const updateConversationWithProgress = async");
  const end = workspaceSource.indexOf("const setBusyForConversation", start);
  assert.notEqual(start, -1, "会话阶段持久化方法必须为异步方法");
  assert.notEqual(end, -1, "会话繁忙状态方法必须位于阶段持久化之后");
  const source = workspaceSource.slice(start, end);
  assert.ok(
    source.indexOf("await api.updateConversation") < source.indexOf("pixelflow-conversations-updated"),
    "只有后端阶段写入成功后才能刷新最近对话",
  );
});
