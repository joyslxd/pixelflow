import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const adapterModuleUrl = process.env.SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE;
const workspaceSource = readFileSync(new URL("../src/pages/WorkspacePage.tsx", import.meta.url), "utf8");

if (!adapterModuleUrl) {
  throw new Error("缺少 SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE");
}

const {
  resolveWorkspaceOrchestrationMode,
  resolveWorkspaceRuntimePolicy,
  resolveWorkspaceInteractionPolicy,
} = await import(adapterModuleUrl);

function conversation(overrides = {}) {
  return {
    conversation_id: "conv-m12",
    title: "测试对话",
    last_phase: "idle",
    context: {},
    ...overrides,
  };
}

test("缺少服务端编排归属时保持旧 frontend_v2", () => {
  assert.equal(resolveWorkspaceOrchestrationMode({ conversation: conversation(), messages: [] }), "frontend_v2");
});

test("服务端明确 supervisor_v1 时启用新运行时", () => {
  assert.equal(
    resolveWorkspaceOrchestrationMode({
      conversation: conversation({ orchestration_mode: "supervisor_v1", orchestration_version: 1 }),
      messages: [],
    }),
    "supervisor_v1",
  );
});

test("普通 context 不能伪造服务端编排归属", () => {
  assert.equal(
    resolveWorkspaceOrchestrationMode({
      conversation: conversation({
        context: {
          orchestration_mode: "supervisor_v1",
          orchestration_version: 1,
        },
      }),
      messages: [],
    }),
    "frontend_v2",
  );
});

test("服务端编排版本缺失或非法时失败关闭到旧运行时", () => {
  assert.equal(
    resolveWorkspaceOrchestrationMode({
      conversation: conversation({ orchestration_mode: "supervisor_v1" }),
      messages: [],
    }),
    "frontend_v2",
  );
  assert.equal(
    resolveWorkspaceOrchestrationMode({
      conversation: conversation({ orchestration_mode: "supervisor_v1", orchestration_version: 2 }),
      messages: [],
    }),
    "frontend_v2",
  );
});

test("Supervisor 会话的非法 context 形状失败关闭", () => {
  assert.equal(
    resolveWorkspaceOrchestrationMode({
      conversation: conversation({
        orchestration_mode: "supervisor_v1",
        orchestration_version: 1,
        context: "不可解析的上下文",
      }),
      messages: [],
    }),
    "frontend_v2",
  );
});

test("旧 pending job 未排空时强制回到 frontend_v2", () => {
  assert.equal(
    resolveWorkspaceOrchestrationMode({
      conversation: conversation({
        orchestration_mode: "supervisor_v1",
        orchestration_version: 1,
        context: {
          pendingVideoJob: {
            job_id: "job-1",
            conversation_id: "conv-m12",
          },
        },
      }),
      messages: [],
    }),
    "frontend_v2",
  );
});

test("运行时策略保证 Supervisor 与旧 runner、旧动作互斥", () => {
  assert.deepEqual(resolveWorkspaceRuntimePolicy("supervisor_v1", "conv-m12"), {
    supervisorEnabled: true,
    legacyRunnerEnabled: false,
    legacyArtifactActionsEnabled: false,
  });
  assert.deepEqual(resolveWorkspaceRuntimePolicy("frontend_v2", "conv-m12"), {
    supervisorEnabled: false,
    legacyRunnerEnabled: true,
    legacyArtifactActionsEnabled: true,
  });
  assert.equal(resolveWorkspaceRuntimePolicy("supervisor_v1", "").supervisorEnabled, false);
});

test("旧运行时的 composer 与 artifact 动作共享业务 busy 闸门", () => {
  assert.deepEqual(resolveWorkspaceInteractionPolicy({
    mode: "frontend_v2",
    conversationId: "conv-m12",
    orchestrationResolved: true,
    legacyBusy: true,
    dialogOpen: false,
    pendingPlanRevision: false,
  }), {
    composer: { disabled: true, canQueue: false },
    artifact: { actionsDisabled: true },
    runtime: { busy: true, mode: "frontend_v2" },
  });
});

test("Supervisor 运行中允许输入排队但不打开旧产物动作", () => {
  assert.deepEqual(resolveWorkspaceInteractionPolicy({
    mode: "supervisor_v1",
    conversationId: "conv-m12",
    orchestrationResolved: true,
    legacyBusy: true,
    dialogOpen: true,
    pendingPlanRevision: true,
    supervisorConnection: "connected",
    supervisorRun: "running",
    supervisorCompression: "compacting",
    pendingSupervisorTurns: 1,
  }), {
    composer: { disabled: false, canQueue: true },
    artifact: { actionsDisabled: false },
    runtime: { busy: true, mode: "supervisor_v1" },
  });
});

test("归属未决或 Supervisor 连接致命失败时 fail-closed 禁止输入", () => {
  assert.equal(resolveWorkspaceInteractionPolicy({
    mode: "frontend_v2",
    conversationId: "conv-m12",
    orchestrationResolved: false,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  }).composer.disabled, true);
  assert.equal(resolveWorkspaceInteractionPolicy({
    mode: "supervisor_v1",
    conversationId: "conv-m12",
    orchestrationResolved: true,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
    supervisorConnection: "fatal",
  }).composer.disabled, true);
});

test("全新 frontend_v2 页面在尚未创建会话时仍允许首条输入", () => {
  assert.deepEqual(resolveWorkspaceInteractionPolicy({
    mode: "frontend_v2",
    conversationId: "",
    orchestrationResolved: true,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  }).composer, {
    disabled: false,
    canQueue: false,
  });
});

test("WorkspacePage 使用创建响应的权威归属并等待 Snapshot 后提交首个 Turn", () => {
  assert.match(workspaceSource, /useSupervisorConversation/);
  assert.match(workspaceSource, /resolveWorkspaceOrchestrationMode\(created\)/);
  assert.match(workspaceSource, /return\s*\{\s*conversationId:\s*created\.conversation_id,\s*orchestrationMode:\s*createdMode\s*\}/);
  assert.match(workspaceSource, /ownership\.orchestrationMode\s*===\s*"supervisor_v1"/);
  assert.match(workspaceSource, /supervisorRuntime\.state\.connection\.status\s*!==\s*"connected"/);
  assert.match(workspaceSource, /supervisorRuntime\.contextVersion/);
  assert.match(workspaceSource, /await\s+supervisorRuntime\.refreshSnapshot\(\)/);
  assert.match(workspaceSource, /supervisorRuntime\.getContextVersion\(\)/);
  assert.doesNotMatch(workspaceSource, /expected_context_version:\s*1[,\n]/);
  assert.match(workspaceSource, /supervisorRuntime\.startTurn/);
});

test("WorkspacePage 恢复归属未决时排队输入且 Supervisor 禁用旧动作", () => {
  assert.match(workspaceSource, /if\s*\(restoringRef\.current\)\s*\{/);
  assert.match(workspaceSource, /deferredOwnershipInputsRef\.current\.push/);
  assert.match(workspaceSource, /if\s*\(resolvedMode\s*!==\s*"frontend_v2"\)\s*return/);
  assert.match(workspaceSource, /orchestrationModeRef\.current\s*!==\s*"frontend_v2"/);
  assert.match(workspaceSource, /legacyArtifactActionsEnabled\s*\?\s*handleSelectDirection\s*:\s*undefined/);
  assert.match(workspaceSource, /legacyArtifactActionsEnabled\s*\?\s*handleGenerateImage\s*:\s*undefined/);
  assert.match(workspaceSource, /legacyArtifactActionsEnabled\s*\?\s*handleGenerateVideoFromScenePackages\s*:\s*undefined/);
  assert.match(workspaceSource, /legacyArtifactActionsEnabled\s*\?\s*handleGeneratePptFile\s*:\s*undefined/);
  assert.match(workspaceSource, /legacyArtifactActionsEnabled\s*\?\s*handleGenerateJianyingDraft\s*:\s*undefined/);
  assert.match(workspaceSource, /resolveWorkspaceInteractionPolicy/);
  assert.match(workspaceSource, /composerDisabled=\{interactionPolicy\.composer\.disabled\}/);
  assert.match(workspaceSource, /artifactActionsDisabled=\{interactionPolicy\.artifact\.actionsDisabled\}/);
  assert.match(workspaceSource, /runtimeBusy=\{interactionPolicy\.runtime\.busy\}/);
});
