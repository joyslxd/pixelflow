import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const adapterModuleUrl = process.env.SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE;
const workspaceSource = readFileSync(new URL("../src/pages/WorkspacePage.tsx", import.meta.url), "utf8");

if (!adapterModuleUrl) {
  throw new Error("缺少 SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE");
}

const {
  createConversationWriteSequencer,
  resolveWorkspaceOrchestrationMode,
  resolveWorkspaceAgentRuntimeMode,
  resolveWorkspaceRuntimePolicy,
  resolveWorkspaceInteractionPolicy,
  resolveAssistHandoffAction,
} = await import(adapterModuleUrl);

test("同一会话的 pending Turn 写入串行读取最新状态", async () => {
  const sequencer = createConversationWriteSequencer();
  const stored = [];
  let releaseFirst;
  let markFirstStarted;
  const firstStarted = new Promise((resolve) => {
    markFirstStarted = resolve;
  });
  const firstGate = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  const persist = (clientInputId) => sequencer.run("conv-m12", async () => {
    const next = [...stored, clientInputId];
    if (clientInputId === "turn-1") {
      markFirstStarted();
      await firstGate;
    }
    stored.splice(0, stored.length, ...next);
  });

  const first = persist("turn-1");
  await firstStarted;
  const second = persist("turn-2");
  await Promise.resolve();
  assert.deepEqual(stored, []);

  releaseFirst();
  await Promise.all([first, second]);
  assert.deepEqual(stored, ["turn-1", "turn-2"]);
});

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

test("R1 assist 只从服务端保留命名空间解析且不改变业务归属", () => {
  const detail = {
    conversation: conversation({
      orchestration_mode: "frontend_v2",
      orchestration_version: 1,
      context: {
        __agent_runtime: {
          mode: "assist",
          enabled_intents: [],
          context_compaction_enabled: true,
          context_version: 0,
        },
      },
    }),
    messages: [],
  };

  assert.equal(resolveWorkspaceOrchestrationMode(detail), "frontend_v2");
  assert.equal(resolveWorkspaceAgentRuntimeMode(detail), "assist");
  assert.equal(resolveWorkspaceAgentRuntimeMode({
    conversation: conversation({ context: { agent_runtime_mode: "assist" } }),
    messages: [],
  }), "off");
});

test("运行时策略保证 Supervisor 与旧 runner、旧动作互斥", () => {
  assert.deepEqual(resolveWorkspaceRuntimePolicy("supervisor_v1", "conv-m12", "off"), {
    supervisorEnabled: true,
    legacyRunnerEnabled: false,
    legacyArtifactActionsEnabled: false,
  });
  assert.deepEqual(resolveWorkspaceRuntimePolicy("frontend_v2", "conv-m12", "off"), {
    supervisorEnabled: false,
    legacyRunnerEnabled: true,
    legacyArtifactActionsEnabled: true,
  });
  assert.equal(resolveWorkspaceRuntimePolicy("supervisor_v1", "").supervisorEnabled, false);
});

test("R1 assist 同时挂载统一会话基础设施与旧业务 runner", () => {
  assert.deepEqual(resolveWorkspaceRuntimePolicy("frontend_v2", "conv-m12", "assist"), {
    supervisorEnabled: true,
    legacyRunnerEnabled: true,
    legacyArtifactActionsEnabled: true,
  });
});

test("R1 assist 只在服务端 Turn 可执行后接力旧流程", () => {
  const base = {
    registrationStatus: "registered",
    continueLegacy: true,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  };
  assert.equal(resolveAssistHandoffAction({
    ...base,
    registrationStatus: "pending",
  }), "register");
  assert.equal(resolveAssistHandoffAction(base), "wait");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: "queued",
  }), "wait");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: "processing",
  }), "continue_legacy");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: "accepted",
    continueLegacy: false,
  }), "acknowledge");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: "failed",
  }), "failed");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: "processing",
    dialogOpen: true,
  }), "wait");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: "processing",
    pendingPlanRevision: true,
  }), "wait");
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
  assert.match(workspaceSource, /agentRuntimeMode:\s*createdAgentRuntimeMode/);
  assert.match(workspaceSource, /ownership\.orchestrationMode\s*===\s*"supervisor_v1"/);
  assert.match(workspaceSource, /supervisorRuntime\.state\.connection\.status\s*!==\s*"connected"/);
  assert.match(workspaceSource, /supervisorRuntime\.contextVersion/);
  assert.match(workspaceSource, /await\s+supervisorRuntime\.refreshSnapshot\(\)/);
  assert.match(workspaceSource, /supervisorRuntime\.getContextVersion\(\)/);
  assert.doesNotMatch(workspaceSource, /expected_context_version:\s*1[,\n]/);
  assert.match(workspaceSource, /supervisorRuntime\.startTurn/);
});

test("WorkspacePage 的 assist Turn 使用同一 UUID 幂等键并在注册后续跑旧流程", () => {
  assert.match(workspaceSource, /crypto\.randomUUID\(\)/);
  assert.match(workspaceSource, /resolveWorkspaceAgentRuntimeMode\(created\)/);
  assert.match(workspaceSource, /ownership\.agentRuntimeMode\s*===\s*"assist"/);
  assert.match(workspaceSource, /skipRuntimeRegistration/);
  assert.match(workspaceSource, /clientInputId:\s*pendingTurn\.clientInputId/);
});

test("assist pending Turn 先持久化恢复上下文再向注册 effect 暴露", () => {
  const functionStart = workspaceSource.indexOf("const persistPendingSupervisorTurns");
  const functionEnd = workspaceSource.indexOf("const persistPendingMessageJob", functionStart);
  const functionSource = workspaceSource.slice(functionStart, functionEnd);
  const persistenceIndex = functionSource.indexOf("await updateConversationWithProgress");
  const refExposureIndex = functionSource.indexOf("pendingSupervisorTurnsRef.current = normalized");
  const stateExposureIndex = functionSource.indexOf("setPendingSupervisorTurns(normalized)");

  assert.ok(functionStart >= 0 && functionEnd > functionStart);
  assert.ok(persistenceIndex >= 0);
  assert.ok(refExposureIndex > persistenceIndex);
  assert.ok(stateExposureIndex > persistenceIndex);
  assert.match(functionSource, /pendingSupervisorTurnWritesRef\.current\.run/);
  assert.match(functionSource, /const normalized = updater\(\[\.\.\.current\]\)/);
});

test("assist 排队 Turn 在恢复上下文落库后立即显示且按稳定 UUID 去重", () => {
  const functionStart = workspaceSource.indexOf("const handleSend = async");
  const functionEnd = workspaceSource.indexOf("const sceneGlobalAssetReference", functionStart);
  const functionSource = workspaceSource.slice(functionStart, functionEnd);
  const persistenceIndex = functionSource.indexOf("await persistPendingSupervisorTurns");
  const visibleIndex = functionSource.indexOf("ensurePendingSupervisorTurnVisible(pendingTurn)");

  assert.ok(functionStart >= 0 && functionEnd > functionStart);
  assert.ok(persistenceIndex >= 0);
  assert.ok(visibleIndex > persistenceIndex);
  assert.match(workspaceSource, /item\.id\s*===\s*pendingTurn\.clientInputId/);
  assert.match(workspaceSource, /if\s*\(alreadyVisible\)\s*return/);
  assert.match(workspaceSource, /if\s*\(serverInput\)\s*ensurePendingSupervisorTurnVisible\(pendingTurn\)/);
  assert.match(workspaceSource, /ensurePendingSupervisorTurnVisible\(registeredTurn\)/);
  assert.match(
    workspaceSource,
    /persistPendingSupervisorTurns\(\s*\(current\)\s*=>\s*\(\s*current\.some/,
  );
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

test("Supervisor 权威投影替换历史消息并复用既有任务看板", () => {
  assert.match(workspaceSource, /supervisorRuntime\.state\.messages/);
  assert.match(workspaceSource, /mergeSupervisorMessagesWithPending\(/);
  assert.match(workspaceSource, /projectSupervisorWorkflowProgress\(supervisorRuntime\.state\.workflows\)/);
  assert.match(workspaceSource, /messagesRef\.current\s*=\s*projectedMessages/);
  assert.match(
    workspaceSource,
    /supervisorRuntime\.state\.conversationId\s*!==\s*currentConversationId/,
  );
  assert.match(workspaceSource, /runtimePolicy\.legacyRunnerEnabled\s*\|\|\s*runtimePolicy\.supervisorEnabled/);
});

test("Supervisor 普通输入自动使用 Snapshot 恢复的当前 interrupt", () => {
  assert.match(
    workspaceSource,
    /const restoredInterruptId = ownership\.orchestrationMode === "supervisor_v1"[\s\S]*supervisorRuntime\.state\.conversationId === activeConversation/,
  );
  assert.match(
    workspaceSource,
    /interruptId: ownership\.orchestrationMode === "supervisor_v1"[\s\S]*\? interruptId \?\? restoredInterruptId[\s\S]*: null/,
  );
});
