import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const adapterModuleUrl = process.env.SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE;
const workspaceSource = readFileSync(new URL("../src/features/legacy-workspace/LegacyWorkspace.tsx", import.meta.url), "utf8");

function extractFunctionBody(source, functionName) {
  const declarationPatterns = [
    `function ${functionName}`,
    `const ${functionName} =`,
  ];
  const declarationIndex = declarationPatterns
    .map((pattern) => source.indexOf(pattern))
    .find((index) => index >= 0);
  assert.notEqual(declarationIndex, undefined, `${functionName} 必须存在`);
  const openBrace = source.indexOf("{", declarationIndex);
  assert.notEqual(openBrace, -1, `${functionName} 必须包含函数体`);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let index = openBrace; index < source.length; index += 1) {
    const character = source[index];
    const next = source[index + 1];
    if (lineComment) {
      if (character === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (character === "*" && next === "/") {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === quote) {
        quote = null;
      }
      continue;
    }
    if (character === "/" && next === "/") {
      lineComment = true;
      index += 1;
      continue;
    }
    if (character === "/" && next === "*") {
      blockComment = true;
      index += 1;
      continue;
    }
    if (character === '"' || character === "'" || character === "`") {
      quote = character;
      continue;
    }
    if (character === "{") depth += 1;
    if (character === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(openBrace + 1, index);
    }
  }
  assert.fail(`${functionName} 的函数体花括号不配对`);
}

if (!adapterModuleUrl) {
  throw new Error("缺少 SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE");
}

const {
  createConversationWriteSequencer,
  resolveWorkspaceOrchestrationMode,
  resolveWorkspaceAgentRuntimeMode,
  resolveWorkspacePrimaryExecutionReady,
  resolveWorkspaceRuntimePolicy,
  resolveWorkspaceInteractionPolicy,
  resolveAssistHandoffAction,
  resolveUnavailableSupervisorRecovery,
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

test("live Handler 就绪只能由服务端保留命名空间声明", () => {
  assert.equal(resolveWorkspacePrimaryExecutionReady({
    conversation: conversation({
      context: {
        __agent_runtime: {
          mode: "primary",
          enabled_intents: ["video"],
          context_compaction_enabled: true,
          context_version: 0,
          primary_execution_ready: true,
        },
      },
    }),
    messages: [],
  }), true);
  assert.equal(resolveWorkspacePrimaryExecutionReady({
    conversation: conversation({
      context: {
        primary_execution_ready: true,
        __agent_runtime: {
          mode: "primary",
          enabled_intents: ["video"],
          context_compaction_enabled: true,
          context_version: 0,
        },
      },
    }),
    messages: [],
  }), false);
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

test("R1 assist 不得用空 Supervisor Workflow 覆盖 v2 任务看板", () => {
  const effectStart = workspaceSource.indexOf("const projectedMessages = mergeSupervisorMessagesWithPending");
  const effectEnd = workspaceSource.indexOf("void api.getJianyingDraftCapability", effectStart);
  assert.notEqual(effectStart, -1, "Workspace 必须投影统一会话消息");
  assert.notEqual(effectEnd, -1, "剪映能力加载必须位于统一会话投影之后");
  const effectSource = workspaceSource.slice(effectStart, effectEnd);
  assert.match(
    effectSource,
    /if \(orchestrationModeRef\.current === "supervisor_v1"\) \{[\s\S]*projectSupervisorWorkflowProgress/,
    "只有 supervisor_v1 业务归属才能投影 Supervisor Workflow",
  );
});

test("R2 primary 先挂载统一会话层，业务归属等待服务端首个 Turn 冻结", () => {
  assert.deepEqual(resolveWorkspaceRuntimePolicy("frontend_v2", "conv-m13-r2", "primary"), {
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

test("supervisor_v1 accepted Turn 不得由 assist 接力确认", () => {
  assert.equal(resolveAssistHandoffAction({
    orchestrationMode: "supervisor_v1",
    primaryExecutionReady: true,
    registrationStatus: "registered",
    serverInputStatus: "accepted",
    serverRunStatus: "running",
    continueLegacy: false,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  }), "wait");
});

test("supervisor_v1 按权威失败或完成状态清理 pending", () => {
  const base = {
    orchestrationMode: "supervisor_v1",
    primaryExecutionReady: true,
    registrationStatus: "registered",
    continueLegacy: false,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  };
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: "failed",
    serverRunStatus: "failed",
  }), "failed");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: undefined,
    serverRunStatus: "completed",
  }), "acknowledge");
  assert.equal(resolveAssistHandoffAction({
    ...base,
    serverInputStatus: undefined,
    serverRunStatus: "running",
  }), "wait");
  assert.match(
    workspaceSource,
    /handoffAction === "failed"[\s\S]*appendPersistedSupervisorNotice[\s\S]*failedSupervisorNoticeId[\s\S]*persistPendingSupervisorTurns/,
    "终态失败提示必须先按稳定 ID 持久化，再清理 pending",
  );
});

test("历史 supervisor_v1 缺少 live Handler 就绪证据时停止自动重试", () => {
  assert.equal(resolveAssistHandoffAction({
    orchestrationMode: "supervisor_v1",
    primaryExecutionReady: false,
    registrationStatus: "registered",
    serverInputStatus: "accepted",
    serverRunStatus: "running",
    continueLegacy: false,
    legacyBusy: false,
    dialogOpen: false,
    pendingPlanRevision: false,
  }), "unavailable");
  assert.match(workspaceSource, /该历史会话由未接线的 R2 候选创建，已停止自动重试/);
});

test("历史未就绪 Supervisor 按会话幂等收敛孤儿 inputQueue", () => {
  const base = {
    orchestrationMode: "supervisor_v1",
    primaryExecutionReady: false,
    connectionStatus: "connected",
    markerVersion: 0,
    noticePersisted: false,
  };

  assert.equal(resolveUnavailableSupervisorRecovery({
    ...base,
    pendingCount: 0,
    hasActiveInput: true,
  }), "persist_notice");
  assert.equal(resolveUnavailableSupervisorRecovery({
    ...base,
    pendingCount: 3,
    hasActiveInput: true,
  }), "persist_notice");
  assert.equal(resolveUnavailableSupervisorRecovery({
    ...base,
    pendingCount: 0,
    hasActiveInput: true,
    noticePersisted: true,
  }), "finalize");
  assert.equal(resolveUnavailableSupervisorRecovery({
    ...base,
    pendingCount: 0,
    hasActiveInput: true,
    markerVersion: 1,
    noticePersisted: true,
  }), "none");
  assert.equal(resolveUnavailableSupervisorRecovery({
    ...base,
    pendingCount: 0,
    hasActiveInput: false,
  }), "none");
});

test("未就绪 Supervisor 不得从服务端 inputQueue 反复重建本地 pending", () => {
  const recoveryStart = workspaceSource.indexOf("刷新或压缩完成后，服务端 Turn");
  const recoveryEnd = workspaceSource.indexOf("const handleVisibilityResume", recoveryStart);
  const recoverySource = workspaceSource.slice(recoveryStart, recoveryEnd);

  assert.match(
    recoverySource,
    /orchestrationModeRef\.current === "supervisor_v1"[\s\S]*!primaryExecutionReadyRef\.current[\s\S]*return/,
  );
  assert.match(workspaceSource, /agent-runtime-unavailable:\$\{conversationId\}:v1/);
  assert.match(
    workspaceSource,
    /await appendPersistedSupervisorNotice[\s\S]*unavailableSupervisorNoticeVersionsRef\.current\.set[\s\S]*await persistPendingSupervisorTurns/,
    "必须先幂等保存说明，再写会话级 marker 并一次清空全部 pending",
  );
  assert.match(
    workspaceSource,
    /enabled: runtimePolicy\.supervisorEnabled && !primaryExecutionUnavailable/,
    "历史未就绪会话不得继续投影永久 running Notice",
  );
  assert.match(
    workspaceSource,
    /const primaryExecutionUnavailable = orchestrationResolved[\s\S]*orchestrationMode === "supervisor_v1"/,
    "切换对话时必须先完成服务端归属解析，不能沿用上一会话状态执行恢复",
  );
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

test("WorkspacePage 创建空壳后使用首个 Turn 返回的服务端权威归属", () => {
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
  const turnSource = extractFunctionBody(workspaceSource, "handleSupervisorTurn");
  assert.match(turnSource, /started\.orchestrationMode/);
  assert.match(turnSource, /setResolvedOrchestrationMode\(started\.orchestrationMode\)/);
  assert.match(
    turnSource,
    /primaryExecutionReadyRef\.current\s*=\s*started\.orchestrationMode\s*===\s*"supervisor_v1"/,
  );
  assert.match(turnSource, /started\.routeIntent\s*===\s*"unknown"/);
  assert.match(turnSource, /appendPersistedSupervisorNotice/);
  assert.match(
    workspaceSource,
    /continueLegacy:\s*registered\.routeIntent\s*===\s*"unknown"[\s\S]{0,120}\?\s*false/,
  );
  assert.match(
    workspaceSource,
    /resolveAssistHandoffAction\(\{[\s\S]*orchestrationMode:\s*orchestrationModeRef\.current/,
  );
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

test("Supervisor 视频控件只走结构化提交入口", () => {
  const supervisorBranch = extractFunctionBody(workspaceSource, "renderSupervisorVideoArtifact");
  assert.match(supervisorBranch, /submitSupervisorAction/);
  assert.match(supervisorBranch, /buildSupervisorWorkflowAction/);
  for (const legacyName of [
    "handleSelectDirection",
    "handleApprovePlan",
    "handleGenerateVideoFromScenePackages",
    "handleRetryVideoMerge",
    "handleAcceptVideoResult",
    "handleGenerateJianyingDraft",
  ]) {
    assert.doesNotMatch(supervisorBranch, new RegExp(`\\b${legacyName}\\b`));
  }
});

test("Supervisor 动作持久化原 explicitAction 与唯一 clientInputId", () => {
  const submitSource = extractFunctionBody(workspaceSource, "submitSupervisorAction");
  assert.equal((submitSource.match(/crypto\.randomUUID\(\)/g) || []).length, 1);
  assert.match(submitSource, /explicitAction/);
  assert.match(submitSource, /persistPendingSupervisorTurns/);
  assert.match(submitSource, /clientInputId/);
  const turnSource = extractFunctionBody(workspaceSource, "handleSupervisorTurn");
  assert.match(turnSource, /explicitAction:\s*pendingTurn\.explicitAction/);
  assert.match(workspaceSource, /explicitAction:\s*ExplicitActionSignal \| null/);
});

test("已注册 Supervisor 结构化动作只轮询原 run", () => {
  assert.match(
    workspaceSource,
    /pendingTurn\.explicitAction[\s\S]*pendingTurn\.registrationStatus === "registered"[\s\S]*pendingTurn\.runId[\s\S]*supervisorRuntime\.getRunStatus\(pendingTurn\.runId\)/,
  );
  assert.doesNotMatch(
    workspaceSource,
    /pendingTurn\.explicitAction[\s\S]{0,500}handleSupervisorTurn\(pendingTurn/,
  );
});

test("Supervisor 视频界面只按权威 interrupt 纯恢复", () => {
  const restoreSource = extractFunctionBody(workspaceSource, "restoreSupervisorVideoUi");
  for (const uiKind of [
    "video_intake_form",
    "video_direction_review",
    "video_plan_review",
    "video_scene_package_review",
    "video_result_review",
  ]) {
    assert.match(restoreSource, new RegExp(`case ["']${uiKind}["']`));
  }
  for (const forbidden of ["submitSupervisorAction", "handleSupervisor", "api.", "setTimeout", "startTurn"]) {
    assert.doesNotMatch(restoreSource, new RegExp(forbidden.replace(".", "\\.")));
  }
  assert.match(workspaceSource, /restoreSupervisorVideoUi\(supervisorRuntime\.state\.interrupt\?\.payload/);
  assert.match(workspaceSource, /supervisorRuntime\.state\.workflows\.find/);
  assert.match(workspaceSource, /workflow\.workflow_id === restoredSupervisorUi\.workflowId/);
  assert.match(workspaceSource, /workflow\.current_stage === restoredSupervisorUi\.stage/);
});

test("Supervisor 授权中断恢复原结构化动作且不保存凭据", () => {
  const restoreSource = extractFunctionBody(workspaceSource, "restoreSupervisorVideoUi");
  assert.match(restoreSource, /authorization_required/);
  assert.match(restoreSource, /parseExplicitAction\(payload\.authorization_action\)/);
  assert.match(
    workspaceSource,
    /restoredSupervisorUi\?\.kind === "authorization_required"[\s\S]*submitSupervisorAction[\s\S]*authorizationAction/,
  );
  assert.doesNotMatch(restoreSource, /token|authorization_header|credential/iu);
});

test("Supervisor 当前卡片按 Workflow 与 artifact 权威身份选择", () => {
  assert.match(workspaceSource, /selectSupervisorArtifactMessage/);
  assert.match(workspaceSource, /workflowId:\s*activeSupervisorVideoTarget\.workflow\.workflow_id/);
  assert.match(workspaceSource, /artifactRef:\s*activeSupervisorVideoTarget\.artifactRef/);
});

test("Supervisor 新增全局素材复用统一分组 ID 与名称唯一化", () => {
  const supervisorBranch = extractFunctionBody(workspaceSource, "renderSupervisorVideoArtifact");
  assert.match(supervisorBranch, /addGlobalSceneAssetReference/);
  assert.match(supervisorBranch, /added\.added_asset\.asset_id/);
  assert.match(supervisorBranch, /added\.added_asset\.name/);
  assert.doesNotMatch(supervisorBranch, /`manual-\$\{rawId\}`/);
});
