import assert from "node:assert/strict";
import test from "node:test";

const projectionModuleUrl = process.env.SUPERVISOR_WORKSPACE_PROJECTION_TEST_MODULE;
const reducerModuleUrl = process.env.SUPERVISOR_REDUCER_TEST_MODULE;
const taskBoardModuleUrl = process.env.WORKFLOW_TASK_BOARD_TEST_MODULE;
assert.ok(projectionModuleUrl, "SUPERVISOR_WORKSPACE_PROJECTION_TEST_MODULE 必须指向编译后的工作区投影模块");
assert.ok(reducerModuleUrl, "SUPERVISOR_REDUCER_TEST_MODULE 必须指向编译后的 Reducer 模块");
assert.ok(taskBoardModuleUrl, "WORKFLOW_TASK_BOARD_TEST_MODULE 必须指向编译后的任务看板模块");

const {
  mergeSupervisorMessagesWithPending,
  projectSupervisorSnapshot,
  projectSupervisorWorkflowProgress,
  selectSupervisorArtifactMessage,
} = await import(projectionModuleUrl);
const {
  createSupervisorRuntimeState,
  supervisorRuntimeReducer,
} = await import(reducerModuleUrl);
const { deriveWorkflowTaskBoard } = await import(taskBoardModuleUrl);

function workflow(overrides = {}) {
  return {
    workflow_id: "wf-video-1",
    conversation_id: "conv-1",
    kind: "video",
    status: "running",
    current_stage: "generate_scene_assets",
    stage_version: 3,
    creation_contract_snapshot: {
      source_message_id: "client-user-1",
      flow_kind: "standard",
    },
    pending_external_job: null,
    latest_artifact_refs: ["artifact:scene-assets-1"],
    context_version: 4,
    created_at: "2026-07-28T10:00:00Z",
    updated_at: "2026-07-28T10:03:00Z",
    ...overrides,
  };
}

function message(overrides = {}) {
  return {
    message_id: "stored-user-1",
    conversation_id: "conv-1",
    user_id: "user-1",
    role: "user",
    content: "生成商品视频",
    payload: {
      client_message_id: "client-user-1",
      materials: [{ type: "image", url: "https://example.com/product.png" }],
    },
    created_at: "2026-07-28T10:00:00Z",
    ...overrides,
  };
}

function snapshot(overrides = {}) {
  return {
    conversationId: "conv-1",
    run: { runId: "turn-1", status: "waiting_user", updatedAt: "2026-07-28T10:04:00Z" },
    compression: {
      status: "idle",
      progressPercent: 100,
      queuedInputCount: 0,
      lastOutcome: "completed",
      updatedAt: "2026-07-28T10:04:00Z",
    },
    inputQueue: [],
    messages: [message(), {
      message_id: "assistant-1",
      conversation_id: "conv-1",
      user_id: "user-1",
      role: "assistant",
      run_id: "wf-video-1",
      content: "场景素材已生成",
      payload: {
        workflow_id: "wf-video-1",
        artifact_ref: "artifact:scene-assets-1",
        artifact: {
          type: "video_scene_packages",
          title: "视频场景包",
          description: "请审核场景素材",
          actionLabel: "审核",
          videoScenePackages: { ok: true },
        },
      },
      created_at: "2026-07-28T10:03:00Z",
    }],
    workflows: [workflow()],
    interrupt: {
      interrupt_id: "interrupt-review-1",
      conversation_id: "conv-1",
      kind: "scene_package_review",
    },
    resume: { cursor: "cursor-4", sequence: 4 },
    context_version: 4,
    ...overrides,
  };
}

function event(sequence, type, payload, overrides = {}) {
  return {
    schema_version: 1,
    event_id: `evt-${sequence}`,
    sequence,
    cursor: `cursor-${sequence}`,
    conversation_id: "conv-1",
    run_id: "turn-1",
    occurred_at: `2026-07-28T10:04:${String(sequence).padStart(2, "0")}Z`,
    type,
    payload,
    ...overrides,
  };
}

test("Snapshot 原子恢复消息、artifact、工作流和当前 interrupt", () => {
  const projection = projectSupervisorSnapshot(snapshot(), "conv-1");
  const state = supervisorRuntimeReducer(createSupervisorRuntimeState("conv-1"), {
    type: "snapshot.hydrated",
    snapshot: projection,
  });

  assert.equal(state.messages.length, 2);
  assert.deepEqual(state.messages[0], {
    id: "client-user-1",
    conversationId: "conv-1",
    role: "user",
    content: "生成商品视频",
    time: "2026-07-28T10:00:00Z",
    materials: [{ type: "image", url: "https://example.com/product.png" }],
  });
  assert.equal(state.messages[1].artifact.type, "video_scene_packages");
  assert.equal(state.workflows[0].workflow_id, "wf-video-1");
  assert.equal(state.interrupt.interruptId, "interrupt-review-1");
});

test("Snapshot 原子恢复 VideoAgent workspace revision、计划和步骤", () => {
  const projection = projectSupervisorSnapshot(snapshot({
    videoAgent: {
      workspace: {
        workspace_id: "workspace-video-1",
        conversation_id: "conv-1",
        revision: 5,
        payload: {
          scenes: [{
            scene_id: "scene-1",
            scene_index: 1,
            title: "第五版商品镜头",
          }],
          assets: [],
        },
      },
      plan: {
        plan_id: "plan-video-1",
        workspace_id: "workspace-video-1",
        status: "awaiting_confirmation",
        public_goal: "修改商品镜头",
      },
      steps: [{
        step_id: "step-video-1",
        plan_id: "plan-video-1",
        sequence: 1,
        title: "更新第一条分镜",
        status: "awaiting_confirmation",
        public_summary: null,
        artifact_refs: [],
        started_at: null,
        completed_at: null,
        duration_ms: null,
      }],
      confirmation: {
        confirmation_id: "video_confirmation_1",
        plan_id: "plan-video-1",
        step_id: "step-video-1",
        title: "更新第一条分镜",
        cost_summary: "将生成1个镜头的新视频版本，执行后可能产生模型调用费用。",
        affected_scene_ids: ["scene-1"],
        submittable: false,
        unavailable_reason: "确认执行入口尚未开放。",
      },
    },
  }), "conv-1");

  assert.equal(projection.videoAgentWorkspace.current.revision, 5);
  assert.equal(projection.videoAgentWorkspace.current.scenes[0].title, "第五版商品镜头");
  assert.equal(projection.videoAgentPlan.planId, "plan-video-1");
  assert.equal(projection.videoAgentPlan.steps["step-video-1"].status, "awaiting_confirmation");
  assert.equal(projection.videoAgentConfirmation.confirmationId, "video_confirmation_1");
  assert.deepEqual(projection.videoAgentConfirmation.affectedSceneIds, ["scene-1"]);
  assert.equal(projection.videoAgentConfirmation.submittable, false);
});

test("双视频 Workflow 按权威 run 和 artifact 身份选择当前卡片", () => {
  const first = message({
    message_id: "assistant-wf-1",
    role: "assistant",
    run_id: "wf-video-1",
    payload: {
      workflow_id: "wf-video-1",
      artifact_ref: "artifact:video-plan:wf-video-1:v1",
      artifact: {
        type: "plan",
        title: "Workflow 1 方案",
        description: "旧时间但属于当前目标",
        actionLabel: "审核",
      },
    },
    created_at: "2026-07-28T10:03:00Z",
  });
  const second = message({
    message_id: "assistant-wf-2",
    role: "assistant",
    run_id: "wf-video-2",
    payload: {
      workflow_id: "wf-video-2",
      artifact_ref: "artifact:video-plan:wf-video-2:v2",
      artifact: {
        type: "plan",
        title: "Workflow 2 方案",
        description: "更新时间更新但不能串卡",
        actionLabel: "审核",
      },
    },
    created_at: "2026-07-28T10:05:00Z",
  });
  const projected = projectSupervisorSnapshot(snapshot({
    messages: [first, second],
    workflows: [
      workflow({
        workflow_id: "wf-video-1",
        current_stage: "plan_review",
        latest_artifact_refs: ["artifact:video-plan:wf-video-1:v1"],
      }),
      workflow({
        workflow_id: "wf-video-2",
        current_stage: "plan_review",
        latest_artifact_refs: ["artifact:video-plan:wf-video-2:v2"],
      }),
    ],
  }), "conv-1");

  assert.equal(selectSupervisorArtifactMessage(projected.messages, {
    workflowId: "wf-video-1",
    artifactRef: "artifact:video-plan:wf-video-1:v1",
    allowedTypes: ["plan"],
  })?.id, "assistant-wf-1");
  assert.equal(selectSupervisorArtifactMessage(projected.messages, {
    workflowId: "wf-video-2",
    artifactRef: "artifact:video-plan:wf-video-2:v2",
    allowedTypes: ["plan"],
  })?.id, "assistant-wf-2");
  assert.equal(selectSupervisorArtifactMessage(projected.messages, {
    workflowId: "wf-video-1",
    artifactRef: "artifact:video-plan:wf-video-2:v2",
    allowedTypes: ["plan"],
  }), null);
});

test("助手卡片拒绝 run、workflow 与 artifact 身份不一致", () => {
  assert.throws(
    () => projectSupervisorSnapshot(snapshot({
      messages: [message({
        role: "assistant",
        run_id: "wf-video-1",
        payload: {
          workflow_id: "wf-video-2",
          artifact_ref: "artifact:video-plan:wf-video-2:v1",
          artifact: {
            type: "plan",
            title: "非法串卡",
            description: "非法串卡",
            actionLabel: "审核",
          },
        },
      })],
    }), "conv-1"),
    /Supervisor 工作区投影状态不合法/,
  );
});

test("Snapshot 投影保留尚未入库的当前会话 pending 用户消息", () => {
  const authoritative = projectSupervisorSnapshot(snapshot(), "conv-1").messages;
  const merged = mergeSupervisorMessagesWithPending(authoritative, [{
    id: "client-pending-1",
    conversationId: "conv-1",
    content: "压缩期间继续排队的输入",
    materials: [{ type: "image", url: "https://example.com/pending.png" }],
  }, {
    id: "client-other-conversation",
    conversationId: "conv-2",
    content: "不能串入当前会话",
  }], "conv-1");

  assert.deepEqual(merged.map((item) => item.id), [
    "client-user-1",
    "assistant-1",
    "client-pending-1",
  ]);
  assert.equal(merged[2].role, "user");
  assert.equal(merged[2].content, "压缩期间继续排队的输入");

  const serverAcknowledged = mergeSupervisorMessagesWithPending(
    [...authoritative, {
      id: "client-pending-1",
      conversationId: "conv-1",
      role: "user",
      content: "服务端权威内容",
      time: "2026-07-28T10:05:00Z",
    }],
    [{
      id: "client-pending-1",
      conversationId: "conv-1",
      content: "本地旧内容",
    }],
    "conv-1",
  );
  assert.equal(serverAcknowledged.filter((item) => item.id === "client-pending-1").length, 1);
  assert.equal(serverAcknowledged.at(-1).content, "服务端权威内容");
});

test("message.upserted 按稳定消息 ID 原位更新并保持 artifact 同事件提交", () => {
  const projection = projectSupervisorSnapshot(snapshot({ resume: { cursor: "cursor-1", sequence: 1 } }), "conv-1");
  let state = supervisorRuntimeReducer(createSupervisorRuntimeState("conv-1"), {
    type: "snapshot.hydrated",
    snapshot: projection,
  });
  state = supervisorRuntimeReducer(state, {
    type: "event.received",
    event: event(2, "message.upserted", message({
      content: "生成商品视频，突出轻量卖点",
      payload: {
        client_message_id: "client-user-1",
        artifact: {
          type: "brief",
          title: "需求摘要",
          description: "已确认",
          actionLabel: "查看",
        },
      },
    })),
  });

  assert.equal(state.messages.length, 2);
  assert.equal(state.messages[0].content, "生成商品视频，突出轻量卖点");
  assert.equal(state.messages[0].artifact.type, "brief");
  assert.deepEqual(state.resume, { cursor: "cursor-2", sequence: 2 });
});

test("workflow.progressed 幂等更新权威工作流并驱动既有任务看板进度", () => {
  const projection = projectSupervisorSnapshot(snapshot({ resume: { cursor: "cursor-1", sequence: 1 } }), "conv-1");
  let state = supervisorRuntimeReducer(createSupervisorRuntimeState("conv-1"), {
    type: "snapshot.hydrated",
    snapshot: projection,
  });
  state = supervisorRuntimeReducer(state, {
    type: "event.received",
    event: event(2, "workflow.progressed", workflow({
      current_stage: "generate_scene_videos",
      stage_version: 4,
      updated_at: "2026-07-28T10:05:00Z",
    })),
  });

  assert.equal(state.workflows.length, 1);
  assert.equal(state.workflows[0].stage_version, 4);
  assert.deepEqual(projectSupervisorWorkflowProgress(state.workflows), {
    version: 1,
    intent: "video",
    flow_kind: "standard",
    source_message_id: "client-user-1",
    last_phase: "video_generation_running",
    scene_package_stage: null,
    updated_at: "2026-07-28T10:05:00Z",
  });
  const board = deriveWorkflowTaskBoard({
    progress: projectSupervisorWorkflowProgress(state.workflows),
    messages: state.messages,
  });
  assert.equal(board.intent, "video");
  assert.equal(board.currentStep.id, "generation");
  assert.equal(board.currentStep.status, "processing");
});

test("旧版本 workflow 事件只推进 cursor，不回退当前阶段", () => {
  const projection = projectSupervisorSnapshot(snapshot({ resume: { cursor: "cursor-1", sequence: 1 } }), "conv-1");
  let state = supervisorRuntimeReducer(createSupervisorRuntimeState("conv-1"), {
    type: "snapshot.hydrated",
    snapshot: projection,
  });
  state = supervisorRuntimeReducer(state, {
    type: "event.received",
    event: event(2, "workflow.progressed", workflow({
      current_stage: "intake",
      stage_version: 2,
    })),
  });
  assert.equal(state.workflows[0].current_stage, "generate_scene_assets");
  assert.deepEqual(state.resume, { cursor: "cursor-2", sequence: 2 });
});

test("interrupt 打开和关闭事件可恢复 M12.4 的单路响应目标", () => {
  const projection = projectSupervisorSnapshot(snapshot({ interrupt: null, resume: { cursor: "cursor-1", sequence: 1 } }), "conv-1");
  let state = supervisorRuntimeReducer(createSupervisorRuntimeState("conv-1"), {
    type: "snapshot.hydrated",
    snapshot: projection,
  });
  state = supervisorRuntimeReducer(state, {
    type: "event.received",
    event: event(2, "interrupt.opened", {
      interrupt: {
        interrupt_id: "interrupt-plan-1",
        conversation_id: "conv-1",
        kind: "plan_review",
      },
    }),
  });
  assert.equal(state.interrupt.interruptId, "interrupt-plan-1");
  state = supervisorRuntimeReducer(state, {
    type: "event.received",
    event: event(3, "interrupt.closed", { interrupt_id: "interrupt-plan-1" }),
  });
  assert.equal(state.interrupt, null);
});

test("sequence gap 不应用越级消息，非法事件只返回固定安全错误", () => {
  const projection = projectSupervisorSnapshot(snapshot({ resume: { cursor: "cursor-1", sequence: 1 } }), "conv-1");
  let state = supervisorRuntimeReducer(createSupervisorRuntimeState("conv-1"), {
    type: "snapshot.hydrated",
    snapshot: projection,
  });
  const hydratedMessages = state.messages;
  state = supervisorRuntimeReducer(state, {
    type: "event.received",
    event: event(3, "message.upserted", message({ content: "不能越级进入 UI" })),
  });
  assert.strictEqual(state.messages, hydratedMessages);
  assert.deepEqual(state.resume, { cursor: "cursor-1", sequence: 1 });
  assert.deepEqual(state.connection, {
    status: "reconnecting",
    error: "Supervisor 事件序列需要恢复",
  });

  state = supervisorRuntimeReducer(state, {
    type: "event.received",
    event: event(2, "message.upserted", message({ role: "token=secret-value" })),
  });
  assert.deepEqual(state.connection, {
    status: "fatal",
    error: "Supervisor 事件状态不合法",
  });
  assert.equal(JSON.stringify(state).includes("secret-value"), false);
  assert.deepEqual(state.resume, { cursor: "cursor-1", sequence: 1 });
});

test("跨对话和非法工作区数据固定失败关闭且不泄漏原值", () => {
  const secret = "token=secret-value";
  for (const invalid of [
    snapshot({ messages: [message({ conversation_id: "conv-2" })] }),
    snapshot({ workflows: [workflow({ conversation_id: "conv-2" })] }),
    snapshot({ workflows: [workflow({ context_version: 0 })] }),
    snapshot({
      messages: [message({
        payload: {
          artifact: {
            type: secret,
            title: "非法",
            description: "非法",
            actionLabel: "非法",
          },
        },
      })],
    }),
  ]) {
    assert.throws(
      () => projectSupervisorSnapshot(invalid, "conv-1"),
      (error) => error instanceof Error
        && error.message === "Supervisor 工作区投影状态不合法"
        && !error.message.includes(secret),
    );
  }
});

test("video_analysis 工作流不生成图片、视频或 PPT 任务看板投影", () => {
  assert.equal(projectSupervisorWorkflowProgress([
    workflow({ kind: "video_analysis", current_stage: "video_analysis_running" }),
  ]), null);
});

test("多工作流历史只投影更新时间最新的当前目标", () => {
  const progress = projectSupervisorWorkflowProgress([
    workflow({ updated_at: "2026-07-28T10:03:00Z" }),
    workflow({
      workflow_id: "wf-image-2",
      kind: "image",
      current_stage: "image_generation_running",
      creation_contract_snapshot: { source_message_id: "client-user-2" },
      updated_at: "2026-07-28T10:06:00Z",
    }),
  ]);
  assert.equal(progress.intent, "image");
  assert.equal(progress.source_message_id, "client-user-2");
});
