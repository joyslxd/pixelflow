import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const moduleUrl = process.env.SUPERVISOR_RUNTIME_NOTICE_TEST_MODULE;
if (!moduleUrl) throw new Error("缺少 SUPERVISOR_RUNTIME_NOTICE_TEST_MODULE");

const { resolveSupervisorRuntimeNotice } = await import(moduleUrl);

const compression = (overrides = {}) => ({
  status: "idle",
  progressPercent: null,
  queuedInputCount: 0,
  lastOutcome: null,
  updatedAt: null,
  ...overrides,
});

const queuedInput = (overrides = {}) => ({
  clientInputId: "input-1",
  turnId: "turn-1",
  status: "queued",
  queuePosition: 1,
  updatedAt: "2026-07-25T03:00:00Z",
  ...overrides,
});

test("仅 supervisor_v1 投影压缩开始与进度提示", () => {
  const input = {
    enabled: true,
    runStatus: "running",
    runUpdatedAt: "2026-07-25T03:00:00Z",
    compression: compression({
      status: "compacting",
      progressPercent: null,
      queuedInputCount: 0,
    }),
    inputQueue: [],
  };
  assert.equal(resolveSupervisorRuntimeNotice({ ...input, enabled: false }), null);
  assert.deepEqual(resolveSupervisorRuntimeNotice(input), {
    kind: "compression",
    tone: "working",
    title: "对话内容较长，正在整理上下文，当前任务和已生成内容不会丢失。",
    detail: "你仍可继续发送，新输入会安全排队。",
    progressPercent: null,
    queueBadge: null,
  });

  assert.deepEqual(resolveSupervisorRuntimeNotice({
    ...input,
    compression: compression({
      status: "compacting",
      progressPercent: 40,
      queuedInputCount: 3,
    }),
    inputQueue: [queuedInput({ queuePosition: 2 })],
  }), {
    kind: "compression",
    tone: "working",
    title: "对话内容较长，正在整理上下文，当前任务和已生成内容不会丢失。",
    detail: "整理进度 40%。你仍可继续发送，新输入会安全排队。",
    progressPercent: 40,
    queueBadge: "已排队 1 条 · 第 2 位",
  });
});

test("压缩完成只在继续运行时提示，失败提示保持可恢复语义", () => {
  const completed = {
    enabled: true,
    runStatus: "running",
    runUpdatedAt: "2026-07-25T03:00:00Z",
    compression: compression({
      progressPercent: 100,
      lastOutcome: "completed",
      updatedAt: "2026-07-25T03:00:01Z",
    }),
    inputQueue: [],
  };
  assert.deepEqual(resolveSupervisorRuntimeNotice(completed), {
    kind: "compression",
    tone: "success",
    title: "上下文整理完成，正在继续处理刚才的请求。",
    detail: null,
    progressPercent: 100,
    queueBadge: null,
  });
  assert.equal(resolveSupervisorRuntimeNotice({ ...completed, runStatus: "completed" }), null);

  assert.deepEqual(resolveSupervisorRuntimeNotice({
    ...completed,
    runStatus: "paused",
    compression: compression({
      status: "blocked",
      progressPercent: 72,
      queuedInputCount: 1,
      lastOutcome: "failed",
    }),
    inputQueue: [queuedInput()],
  }), {
    kind: "compression",
    tone: "warning",
    title: "上下文整理暂时未完成，你的输入已保留，系统将继续重试。",
    detail: null,
    progressPercent: 72,
    queueBadge: "已排队 1 条 · 第 1 位",
  });
});

test("新 Run 不会重复展示旧的上下文整理完成提示", () => {
  assert.equal(resolveSupervisorRuntimeNotice({
    enabled: true,
    runStatus: "running",
    runUpdatedAt: "2026-07-25T03:10:00Z",
    compression: compression({
      progressPercent: 100,
      lastOutcome: "completed",
      updatedAt: "2026-07-25T03:00:01Z",
    }),
    inputQueue: [],
  }), null);
});

test("没有压缩提示时仍按服务端队列显示排队 badge", () => {
  assert.deepEqual(resolveSupervisorRuntimeNotice({
    enabled: true,
    runStatus: "running",
    runUpdatedAt: "2026-07-25T03:00:00Z",
    compression: compression(),
    inputQueue: [
      queuedInput({ clientInputId: "sending", status: "sending", queuePosition: null }),
      queuedInput({ clientInputId: "queued", queuePosition: 2 }),
      queuedInput({ clientInputId: "processing", status: "processing", queuePosition: null }),
    ],
  }), {
    kind: "queue",
    tone: "queued",
    title: "输入已排队，系统会按顺序处理。",
    detail: null,
    progressPercent: null,
    queueBadge: "已排队 1 条 · 第 2 位",
  });
});

test("排队 badge 仅以 inputQueue 为权威来源，不沿用压缩快照旧计数", () => {
  assert.equal(resolveSupervisorRuntimeNotice({
    enabled: true,
    runStatus: "completed",
    runUpdatedAt: "2026-07-25T03:00:00Z",
    compression: compression({
      queuedInputCount: 4,
      lastOutcome: "completed",
      progressPercent: 100,
    }),
    inputQueue: [queuedInput({ status: "processing", queuePosition: null })],
  }), null);
});

test("Notice 位于 Composer 上方并提供状态与进度可访问语义", () => {
  const componentSource = readFileSync(
    new URL("../src/components/chat/ConversationRuntimeNotice.tsx", import.meta.url),
    "utf8",
  );
  const chatPanelSource = readFileSync(
    new URL("../src/components/chat/ChatPanel.tsx", import.meta.url),
    "utf8",
  );
  const workspaceSource = readFileSync(
    new URL("../src/pages/WorkspacePage.tsx", import.meta.url),
    "utf8",
  );

  assert.match(componentSource, /aria-live="polite"/);
  assert.match(componentSource, /role="progressbar"/);
  assert.match(componentSource, /aria-valuenow=\{notice\.progressPercent\}/);
  assert.match(chatPanelSource, /<ConversationRuntimeNotice notice=\{runtimeNotice\}/);
  assert.ok(
    chatPanelSource.indexOf("<ConversationRuntimeNotice") < chatPanelSource.indexOf("<Composer"),
    "运行时提示必须位于输入框上方",
  );
  assert.match(workspaceSource, /resolveSupervisorRuntimeNotice/);
  assert.match(workspaceSource, /runtimeNotice=\{runtimeNotice\}/);
});
