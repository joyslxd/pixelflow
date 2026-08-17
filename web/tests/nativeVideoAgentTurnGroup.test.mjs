import assert from "node:assert/strict";
import test from "node:test";
import { pathToFileURL } from "node:url";

const modulePath = process.env.NATIVE_VIDEO_AGENT_STATE_TEST_MODULE;
if (!modulePath) throw new Error("缺少 NATIVE_VIDEO_AGENT_STATE_TEST_MODULE");

const {
  createEmptyNativeVideoAgentUiState,
  hydrateNativeVideoAgentUiState,
  reduceNativeVideoAgentEvent,
  nativeTurnSectionPresence,
  selectNativeAgentTurns,
  turnOffersScriptPreview,
  turnOffersScenePackageStoryboard,
} = await import(modulePath.startsWith("file:") ? modulePath : pathToFileURL(modulePath).href);

function event(sequence, type, payload) {
  return {
    schema_version: 1,
    event_id: `evt-${sequence}`,
    sequence,
    cursor: `cursor-${sequence}`,
    conversation_id: "conv-1",
    run_id: "turn-1",
    occurred_at: "2026-08-12T12:00:00Z",
    type,
    payload: { turn_id: "turn-1", ...payload },
  };
}

test("native Turn reducer 忽略旧 sequence 并保持展示顺序", () => {
  let state = createEmptyNativeVideoAgentUiState("conv-1");
  state = reduceNativeVideoAgentEvent(state, event(1, "agent.reasoning_summary.delta", {
    delta: "先看脚本",
  }));
  state = reduceNativeVideoAgentEvent(state, event(1, "agent.reasoning_summary.delta", {
    delta: "应被忽略",
  }));
  state = reduceNativeVideoAgentEvent(state, event(2, "agent.reasoning_summary.completed", {
    summary: "先看脚本再生成",
  }));
  state = reduceNativeVideoAgentEvent(state, event(3, "agent.tool.started", {
    tool_name: "inspect_video_workspace",
    tool_call_id: "call-1",
    title: "检查工作区",
  }));
  state = reduceNativeVideoAgentEvent(state, event(4, "agent.tool.completed", {
    tool_name: "inspect_video_workspace",
    tool_call_id: "call-1",
    public_summary: "工作区正常",
  }));
  state = reduceNativeVideoAgentEvent(state, event(5, "agent.response.completed", {
    text: "已检查完毕",
  }));

  const turns = selectNativeAgentTurns(state);
  assert.equal(turns.length, 1);
  const turn = turns[0];
  assert.equal(turn.reasoningText, "先看脚本再生成");
  assert.equal(turn.tools[0].publicSummary, "工作区正常");
  assert.equal(turn.responseText, "已检查完毕");
  assert.deepEqual(nativeTurnSectionPresence(turn), {
    hasReasoning: true,
    hasPlan: false,
    hasActivity: true,
    hasResponse: true,
  });
});

test("活动先于思考到达时仍先渲染思考占位", async () => {
  const source = await import("node:fs").then((fs) =>
    fs.readFileSync(
      new URL("../src/features/native-video-agent/chat/AgentTurnGroup.tsx", import.meta.url),
      "utf8",
    )
  );
  assert.match(source, /showReasoningPlaceholder/);
  assert.match(source, /正在分析你的输入/);
  assert.match(
    source,
    /hasReasoning[\s\S]*showReasoningPlaceholder[\s\S]*hasPlan \|\| sections\.hasActivity/,
    "Thought placeholder must render before Activity",
  );
});

test("脚本就绪结论气泡提供在右侧查看脚本入口", async () => {
  let state = createEmptyNativeVideoAgentUiState("conv-1");
  state = reduceNativeVideoAgentEvent(state, event(1, "agent.tool.completed", {
    tool_name: "apply_production_fields",
    tool_call_id: "call-fields",
    public_summary: "已补全生产字段",
  }));
  state = reduceNativeVideoAgentEvent(state, event(2, "agent.response.completed", {
    text: "脚本已就绪，请点击对话中的「在右侧查看脚本」预览并在底部确认方案。",
  }));
  const turn = selectNativeAgentTurns(state)[0];
  assert.equal(turnOffersScriptPreview(turn), true);
  assert.equal(turnOffersScenePackageStoryboard(turn), false);

  const inspectOnly = createEmptyNativeVideoAgentUiState("conv-2");
  let other = reduceNativeVideoAgentEvent(inspectOnly, {
    ...event(1, "agent.response.completed", { text: "已检查完毕" }),
    conversation_id: "conv-2",
    run_id: "turn-2",
    payload: { turn_id: "turn-2", text: "已检查完毕" },
  });
  assert.equal(turnOffersScriptPreview(selectNativeAgentTurns(other)[0]), false);

  let prepareState = createEmptyNativeVideoAgentUiState("conv-prepare");
  prepareState = reduceNativeVideoAgentEvent(prepareState, {
    ...event(1, "agent.tool.completed", {
      tool_name: "prepare_scene_packages",
      tool_call_id: "call-prepare",
      public_summary: "已生成分镜资产包",
    }),
    conversation_id: "conv-prepare",
    run_id: "turn-prepare",
    payload: {
      turn_id: "turn-prepare",
      tool_name: "prepare_scene_packages",
      tool_call_id: "call-prepare",
      public_summary: "已生成分镜资产包",
    },
  });
  prepareState = reduceNativeVideoAgentEvent(prepareState, {
    ...event(2, "agent.response.completed", {
      text: "视频场景包已就绪（14 个分镜）。请打开下方卡片查看。",
    }),
    conversation_id: "conv-prepare",
    run_id: "turn-prepare",
    payload: {
      turn_id: "turn-prepare",
      text: "视频场景包已就绪（14 个分镜）。请打开下方卡片查看。",
    },
  });
  const prepareTurn = selectNativeAgentTurns(prepareState)[0];
  assert.equal(turnOffersScenePackageStoryboard(prepareTurn), true);
  assert.equal(turnOffersScriptPreview(prepareTurn), false);

  let patchState = createEmptyNativeVideoAgentUiState("conv-patch");
  patchState = reduceNativeVideoAgentEvent(patchState, {
    ...event(1, "agent.tool.completed", {
      tool_name: "patch_scene",
      tool_call_id: "call-patch",
      public_summary: "镜头 scene-4 已更新并标记为待重新生成",
    }),
    conversation_id: "conv-patch",
    run_id: "turn-patch",
    payload: {
      turn_id: "turn-patch",
      tool_name: "patch_scene",
      tool_call_id: "call-patch",
      public_summary: "镜头 scene-4 已更新并标记为待重新生成",
    },
  });
  patchState = reduceNativeVideoAgentEvent(patchState, {
    ...event(2, "agent.response.completed", {
      text: "已更新分镜 scene-4，并标记为待重新生成。可继续改其他镜头。",
    }),
    conversation_id: "conv-patch",
    run_id: "turn-patch",
    payload: {
      turn_id: "turn-patch",
      text: "已更新分镜 scene-4，并标记为待重新生成。可继续改其他镜头。",
    },
  });
  const patchTurn = selectNativeAgentTurns(patchState)[0];
  assert.equal(turnOffersScenePackageStoryboard(patchTurn), true);
  assert.equal(turnOffersScriptPreview(patchTurn), false);

  const source = await import("node:fs").then((fs) =>
    fs.readFileSync(
      new URL("../src/features/native-video-agent/chat/AgentTurnGroup.tsx", import.meta.url),
      "utf8",
    )
  );
  assert.match(source, /data-script-preview-cta/);
  assert.match(source, /在右侧查看脚本/);
  assert.match(source, /onOpenScriptPreview/);
  assert.match(source, /data-scene-package-cta/);
  assert.match(source, /查看分镜/);
  assert.match(source, /onOpenScenePackageStoryboard/);
});

test("native Turn reducer 跨对话事件不写入", () => {
  let state = createEmptyNativeVideoAgentUiState("conv-1");
  state = reduceNativeVideoAgentEvent(state, {
    ...event(1, "agent.response.completed", { text: "错对话" }),
    conversation_id: "conv-other",
  });
  assert.equal(selectNativeAgentTurns(state).length, 0);
});

test("snapshot hydrate 保留已有 Turn 与 tool，并用思考历史补正文", () => {
  let state = createEmptyNativeVideoAgentUiState("conv-1");
  state = reduceNativeVideoAgentEvent(state, event(1, "agent.reasoning_summary.completed", {
    summary: "本地思考",
  }));
  state = reduceNativeVideoAgentEvent(state, event(2, "agent.tool.completed", {
    tool_name: "import_script",
    tool_call_id: "call-import",
    public_summary: "已导入脚本",
  }));
  state = reduceNativeVideoAgentEvent(state, event(3, "agent.response.completed", {
    text: "本地回答更长一些，请保留",
  }));

  const hydrated = hydrateNativeVideoAgentUiState(state, "conv-1", [
    {
      turnId: "turn-1",
      text: "短",
      answer: "短回答",
      status: "completed",
      startedAt: "2026-08-13T00:00:00Z",
    },
    {
      turnId: "turn-2",
      text: "历史思考",
      answer: "历史回答",
      status: "completed",
      startedAt: "2026-08-13T00:01:00Z",
    },
  ]);

  const turns = selectNativeAgentTurns(hydrated);
  assert.equal(turns.length, 2);
  assert.equal(turns[0].reasoningText, "本地思考");
  assert.equal(turns[0].responseText, "本地回答更长一些，请保留");
  assert.equal(turns[0].tools.length, 1);
  assert.equal(turns[0].tools[0].publicSummary, "已导入脚本");
  assert.equal(turns[1].reasoningText, "历史思考");
  assert.equal(turns[1].responseText, "历史回答");
});

test("plan steps 最多保留 3 步", () => {
  let state = createEmptyNativeVideoAgentUiState("conv-1");
  state = reduceNativeVideoAgentEvent(state, event(1, "agent.plan.created", {
    plan_id: "plan-1",
    steps: [
      { step_id: "s1", sequence: 1, title: "一步", status: "pending", tool_name: "a" },
      { step_id: "s2", sequence: 2, title: "二步", status: "pending", tool_name: "b" },
      { step_id: "s3", sequence: 3, title: "三步", status: "pending", tool_name: "c" },
      { step_id: "s4", sequence: 4, title: "四步", status: "pending", tool_name: "d" },
    ],
  }));
  const turn = selectNativeAgentTurns(state)[0];
  assert.equal(turn.planSteps.length, 3);
  assert.equal(turn.planSteps[2].stepId, "s3");
});
