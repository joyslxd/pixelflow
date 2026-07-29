import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const moduleUrl = process.env.SUPERVISOR_TURN_SUBMISSION_TEST_MODULE;
const fixturePath = process.env.AGENT_RUNTIME_CONTRACT_FIXTURE;

if (!moduleUrl || !fixturePath) {
  throw new Error("缺少 Supervisor 目标定位测试模块或合同 fixture 路径");
}

const { buildSupervisorSubmission } = await import(moduleUrl);
const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
const workspaceSource = await readFile(new URL("../src/pages/WorkspacePage.tsx", import.meta.url), "utf8");

test("目标定位 fixture 的 reply 与 Artifact 引用完整进入 Turn", () => {
  const canonical = fixture.turn_start_request;
  const submission = buildSupervisorSubmission({
    conversationId: "conv_001",
    clientInputId: canonical.client_input_id,
    content: canonical.content,
    materials: canonical.materials,
    replyToMessageId: canonical.reply_to_message_id,
    artifactRefs: canonical.artifact_refs,
  }, canonical.expected_context_version);

  assert.deepEqual(submission, {
    kind: "turn",
    request: canonical,
  });
});

test("场景 mention 素材保留目标元数据并补齐消息与 Artifact 引用", () => {
  const material = {
    source: "scene_global_asset",
    asset_id: "character-host",
    storyboard_message_id: "msg_scene_001",
    artifact_ref: "artifact:video-scene-package:wf_video_001:plan-v1:hash",
    mention_ref: "artifact:video-scene-package:wf_video_001:plan-v1:hash",
    source_image_url: "https://example.com/host.png",
  };

  const submission = buildSupervisorSubmission({
    conversationId: "conv_001",
    clientInputId: "client-scene-001",
    content: "把 @讲解者 的服装改成蓝色",
    materials: [material],
    artifactRefs: [" artifact:image:3 ", "artifact:image:3", "", "不是 Artifact 引用"],
  }, 13);

  assert.equal(submission.kind, "turn");
  assert.deepEqual(submission.request, {
    client_input_id: "client-scene-001",
    content: "把 @讲解者 的服装改成蓝色",
    materials: [material],
    reply_to_message_id: "msg_scene_001",
    artifact_refs: [
      "artifact:image:3",
      "artifact:video-scene-package:wf_video_001:plan-v1:hash",
    ],
    expected_context_version: 13,
  });
});

test("interrupt ID 改走幂等响应且不会构造额外 Turn", () => {
  const input = {
    conversationId: "conv_001",
    clientInputId: "client-response-001",
    content: "同意方案",
    materials: [],
    replyToMessageId: "msg_plan_001",
    artifactRefs: ["artifact:video-plan:wf_video_001:v1:hash"],
    interruptId: " interrupt_plan_001 ",
  };

  const first = buildSupervisorSubmission(input, 14);
  const repeated = buildSupervisorSubmission(input, 99);

  assert.deepEqual(first, repeated);
  assert.deepEqual(first, {
    kind: "interrupt",
    interruptId: "interrupt_plan_001",
    request: {
      client_response_id: "client-response-001",
      value: {
        content: "同意方案",
        materials: [],
        reply_to_message_id: "msg_plan_001",
        artifact_refs: ["artifact:video-plan:wf_video_001:v1:hash"],
      },
    },
  });
});

test("无目标普通输入保持空目标且场景元数据不会跨会话提交", () => {
  const ordinary = buildSupervisorSubmission({
    conversationId: "conv_001",
    clientInputId: "client-ordinary-001",
    content: "帮我做一张商品图",
    materials: [],
  }, 1);

  assert.equal(ordinary.kind, "turn");
  assert.equal(ordinary.request.reply_to_message_id, null);
  assert.deepEqual(ordinary.request.artifact_refs, []);

  assert.throws(
    () => buildSupervisorSubmission({
      conversationId: "conv_002",
      clientInputId: "client-cross-conversation",
      content: "修改这个角色",
      materials: [{
        source: "scene_global_asset",
        asset_id: "character-host",
        conversation_id: "conv_001",
        storyboard_message_id: "msg_scene_001",
      }],
    }, 2),
    /目标元数据与当前会话不一致/,
  );
});

test("多个不同消息目标会失败关闭而不是猜测最近目标", () => {
  assert.throws(
    () => buildSupervisorSubmission({
      conversationId: "conv_001",
      clientInputId: "client-ambiguous-target",
      content: "修改这个",
      materials: [
        { storyboard_message_id: "msg_scene_001" },
        { reply_to_message_id: "msg_scene_002" },
      ],
    }, 3),
    /目标消息引用不唯一/,
  );
});

test("Workspace 普通输入只启动一次 Turn 并完整携带目标元数据", () => {
  const functionStart = workspaceSource.indexOf("const handleSupervisorTurn = async");
  const functionEnd = workspaceSource.indexOf("useEffect(() => {", functionStart);
  const functionSource = workspaceSource.slice(functionStart, functionEnd);

  assert.ok(functionStart >= 0 && functionEnd > functionStart);
  assert.match(
    functionSource,
    /buildSupervisorSubmission\(\{[\s\S]*replyToMessageId: pendingTurn\.replyToMessageId[\s\S]*artifactRefs: pendingTurn\.artifactRefs/,
  );
  assert.equal(
    functionSource.match(/supervisorRuntime\.startTurn\(request\)/g)?.length,
    1,
  );
});

test("Workspace 仅由 supervisor_v1 响应 interrupt，成功后持久化清除 pending", () => {
  const functionStart = workspaceSource.indexOf("const handleSupervisorTurn = async");
  const functionEnd = workspaceSource.indexOf("useEffect(() => {", functionStart);
  const functionSource = workspaceSource.slice(functionStart, functionEnd);

  assert.match(
    functionSource,
    /submission\.kind === "interrupt"[\s\S]*orchestrationModeRef\.current !== "supervisor_v1"[\s\S]*respondToInterrupt\(submission\.interruptId, submission\.request\)[\s\S]*persistPendingSupervisorTurns\([\s\S]*clientInputId !== pendingTurn\.clientInputId/,
  );
  assert.match(
    workspaceSource,
    /interruptId: ownership\.orchestrationMode === "supervisor_v1"[\s\S]*\? interruptId \?\? restoredInterruptId[\s\S]*: null/,
  );
  assert.match(workspaceSource, /const shouldRegisterRuntime = ownership\.orchestrationMode === "supervisor_v1"[\s\S]*ownership\.agentRuntimeMode === "assist"[\s\S]*ownership\.agentRuntimeMode === "shadow"/);
  assert.match(workspaceSource, /if \(shouldUseRecoverableIntakeEntry\(text, materials, activeConversation\)\)/);
});

test("Supervisor 只释放场景目标引用入口，不恢复旧供应商动作", () => {
  assert.match(
    workspaceSource,
    /onReferenceGlobalAsset=\{runtimePolicy\.supervisorEnabled \|\| legacyArtifactActionsEnabled\s*\? handleReferenceGlobalAsset/,
  );
  assert.match(
    workspaceSource,
    /onDeleteGlobalAsset=\{runtimePolicy\.supervisorEnabled \|\| legacyArtifactActionsEnabled\s*\? handleDeleteGlobalAsset/,
  );
  assert.match(
    workspaceSource,
    /onReplaceGlobalAsset=\{legacyArtifactActionsEnabled \? handleReplaceGlobalAsset : undefined\}/,
  );
  assert.match(
    workspaceSource,
    /onGenerateVideo=\{legacyArtifactActionsEnabled/,
  );
});
