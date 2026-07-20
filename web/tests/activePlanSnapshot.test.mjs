import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const moduleUrl = process.env.ACTIVE_PLAN_SNAPSHOT_TEST_MODULE
  || pathToFileURL(path.join(os.tmpdir(), "pixelflow-active-plan-snapshot-test", "activePlanSnapshot.js")).href;

const { activePlanSnapshotForConversation } = await import(moduleUrl);

function planMessage({ id, conversationId, version, history, contract, durations, blueprints = [], manifest = { characters: [], scenes: [], props: [] }, restoredFromVersion = null }) {
  return {
    id,
    conversationId,
    role: "assistant",
    content: `plan.md v${version}`,
    time: "10:00",
    artifact: {
      type: "plan",
      title: "plan.md 创作方案",
      description: "测试 Plan",
      actionLabel: "审核",
      selectedDirection: { id: "direction-1", title: "雨天通勤", summary: "突出防泼水" },
      plan: {
        output_type: "video",
        plan_markdown: `# plan.md v${version}`,
        template_path: "plan_video.md",
        consistency_issues: [],
        review_timeout_sec: null,
        plan_version: version,
        plan_history: history,
        creation_contract: contract,
        scene_durations_sec: durations,
        scene_blueprints: blueprints,
        asset_manifest: manifest,
        llm_used: true,
        model_name: "deepseek-v4-pro",
        error: null,
        restored_from_version: restoredFromVersion,
      },
    },
  };
}

test("回退后的最后一条 Plan artifact 派生完整 active Plan 自动保存快照", () => {
  const history = [
    { version: 1, plan_markdown: "# plan.md v1", scene_durations_sec: [10, 10] },
    { version: 2, plan_markdown: "# plan.md v2", scene_durations_sec: [5, 15] },
  ];
  const messages = [
    planMessage({
      id: "plan-v2",
      conversationId: "conversation-a",
      version: 2,
      history,
      contract: { video_duration_sec: 20, video_model: "seedance-2.0" },
      durations: [5, 15],
    }),
    planMessage({
      id: "other-plan",
      conversationId: "conversation-b",
      version: 9,
      history: [{ version: 9, plan_markdown: "# other" }],
      contract: { video_duration_sec: 4 },
      durations: [4],
    }),
    planMessage({
      id: "plan-rollback-v1",
      conversationId: "conversation-a",
      version: 1,
      history,
      contract: { video_duration_sec: 20, video_model: "seedance-1.5-pro" },
      durations: [10, 10],
      blueprints: [{ scene_id: "scene-1", duration_sec: 10 }, { scene_id: "scene-2", duration_sec: 10 }],
      manifest: { characters: [{ asset_id: "character-host", name: "讲解者" }], scenes: [], props: [] },
      restoredFromVersion: 1,
    }),
  ];

  const snapshot = activePlanSnapshotForConversation(messages, "conversation-a");

  assert.deepEqual(snapshot, {
    selected_direction: { id: "direction-1", title: "雨天通勤", summary: "突出防泼水" },
    plan_markdown: "# plan.md v1",
    plan_version: 1,
    plan_history: history,
    creation_contract: { video_duration_sec: 20, video_model: "seedance-1.5-pro" },
    scene_durations_sec: [10, 10],
    scene_blueprints: [{ scene_id: "scene-1", duration_sec: 10 }, { scene_id: "scene-2", duration_sec: 10 }],
    asset_manifest: { characters: [{ asset_id: "character-host", name: "讲解者" }], scenes: [], props: [] },
    restored_from_version: 1,
  });
});

test("active Plan 快照经 JSON 自动保存与恢复后仍保持当前版本且不引用原 artifact", () => {
  const message = planMessage({
    id: "plan-rollback-v1",
    conversationId: "conversation-a",
    version: 1,
    history: [
      { version: 1, plan_markdown: "# plan.md v1", scene_durations_sec: [] },
      { version: 2, plan_markdown: "# plan.md v2", scene_durations_sec: [] },
    ],
    contract: { intent: "image", image_model: "gpt-image-2" },
    durations: [],
    restoredFromVersion: 1,
  });

  const autosaveInput = {
    taskId: "task-1",
    ...activePlanSnapshotForConversation([message], "conversation-a"),
  };
  const resumedContext = JSON.parse(JSON.stringify(autosaveInput));

  assert.equal(resumedContext.plan_version, 1);
  assert.equal(resumedContext.plan_markdown, "# plan.md v1");
  assert.deepEqual(resumedContext.plan_history.map((entry) => entry.version), [1, 2]);
  assert.deepEqual(resumedContext.scene_durations_sec, []);
  autosaveInput.creation_contract.image_model = "changed-locally";
  assert.equal(message.artifact.plan.creation_contract.image_model, "gpt-image-2");
});

test("没有当前对话 Plan artifact 时不生成会覆盖既有 context 的空字段", () => {
  const snapshot = activePlanSnapshotForConversation(
    [
      planMessage({
        id: "other-plan",
        conversationId: "conversation-b",
        version: 1,
        history: [{ version: 1, plan_markdown: "# other" }],
        contract: {},
        durations: [],
      }),
    ],
    "conversation-a",
  );

  assert.deepEqual(snapshot, {});
});

test("尚未落库的乐观 Plan 卡片不能抢先成为自动保存的 active Plan", () => {
  const history = [
    { version: 1, plan_markdown: "# plan.md v1", scene_durations_sec: [10, 10] },
    { version: 2, plan_markdown: "# plan.md v2", scene_durations_sec: [5, 15] },
  ];
  const persistedV2 = planMessage({
    id: "persisted-v2",
    conversationId: "conversation-a",
    version: 2,
    history,
    contract: { video_duration_sec: 20 },
    durations: [5, 15],
  });
  const optimisticRollbackV1 = planMessage({
    id: "optimistic-v1",
    conversationId: "conversation-a",
    version: 1,
    history,
    contract: { video_duration_sec: 20 },
    durations: [10, 10],
    restoredFromVersion: 1,
  });

  const snapshot = activePlanSnapshotForConversation(
    [persistedV2, optimisticRollbackV1],
    "conversation-a",
    new Set(["optimistic-v1"]),
  );

  assert.equal(snapshot.plan_version, 2);
  assert.equal(snapshot.plan_markdown, "# plan.md v2");
});
