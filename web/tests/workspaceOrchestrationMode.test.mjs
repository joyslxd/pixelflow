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
});
