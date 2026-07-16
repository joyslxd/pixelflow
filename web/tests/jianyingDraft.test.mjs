import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.JIANYING_DRAFT_TEST_MODULE;
assert.ok(moduleUrl, "JIANYING_DRAFT_TEST_MODULE must point to the compiled jianyingDraft module");

const {
  JianyingDraftStartGuard,
  draftButtonState,
  isJianyingDraftSucceededResultValid,
  patchJianyingDraftTargetConversation,
  patchJianyingDraftConversationContext,
  storyboardVersionId,
} = await import(moduleUrl);

function scene(sceneIndex, videoUrl = `https://cdn.example.com/${sceneIndex}.mp4`, taskId = `task-${sceneIndex}`) {
  return {
    scene_id: `scene-${sceneIndex}`,
    scene_index: sceneIndex,
    task_id: taskId,
    video_url: videoUrl,
  };
}

test("storyboard version is stable for the same ordered scene set", () => {
  const scenes = [scene(2, "https://cdn.example.com/b.mp4", "task-2"), scene(1, "https://cdn.example.com/a.mp4", "task-1")];

  assert.equal(storyboardVersionId(scenes), storyboardVersionId([...scenes].reverse()));
});

test("storyboard version matches the backend fixed FNV-1a 64 regression vector", () => {
  assert.equal(
    storyboardVersionId([
      scene(1, "https://cdn/1.mp4", "t1"),
      scene(2, "https://cdn/2.mp4", "t2"),
    ]),
    "storyboard-459f6271da98fbff",
  );
});

test("storyboard version changes after one scene is regenerated", () => {
  assert.notEqual(
    storyboardVersionId([scene(1, "https://cdn.example.com/a.mp4", "task-1")]),
    storyboardVersionId([scene(1, "https://cdn.example.com/a-v2.mp4", "task-2")]),
  );
});

test("storyboard version rejects the same scene data that the backend rejects", () => {
  assert.throws(() => storyboardVersionId([]), /scenes cannot be empty/);
  assert.throws(() => storyboardVersionId([scene(1), scene(1, "https://cdn.example.com/duplicate.mp4")]), /scene_index values must be unique/);
  assert.throws(() => storyboardVersionId([{ ...scene(1), scene_id: "" }]), /scene_id/);
  assert.throws(() => storyboardVersionId([{ ...scene(1), scene_index: 0 }]), /scene_index/);
  assert.throws(() => storyboardVersionId([{ ...scene(1), video_url: "blob:https://local/1" }]), /video_url/);
  assert.throws(() => storyboardVersionId([{ ...scene(1), video_url: "file:///tmp/1.mp4" }]), /video_url/);
});

test("button is disabled when provider is unavailable", () => {
  assert.deepEqual(
    draftButtonState({ providerAvailable: false, scenes: [scene(1)] }),
    { enabled: false, label: "生成剪映草稿", reason: "剪映草稿服务待接入" },
  );
});

test("button state prioritizes pending and invalid scene results", () => {
  assert.deepEqual(
    draftButtonState({ providerAvailable: true, pendingJob: { status: "running" }, scenes: [] }),
    { enabled: false, label: "剪映草稿生成中", reason: "剪映草稿正在生成中" },
  );
  assert.deepEqual(
    draftButtonState({ providerAvailable: true, scenes: [] }),
    { enabled: false, label: "生成剪映草稿", reason: "暂无可用视频分镜" },
  );
  assert.deepEqual(
    draftButtonState({ providerAvailable: true, failedSceneIds: ["scene-1"], scenes: [scene(1)] }),
    { enabled: false, label: "重新生成剪映草稿", reason: "存在生成失败的分镜" },
  );
  assert.deepEqual(
    draftButtonState({ providerAvailable: true, scenes: [{ ...scene(1), video_url: "" }] }),
    { enabled: false, label: "生成剪映草稿", reason: "存在缺少视频地址的分镜" },
  );
});

test("button offers download for a current draft and regeneration for an expired draft", () => {
  const now = new Date("2026-07-16T00:00:00.000Z");
  assert.deepEqual(
    draftButtonState({
      providerAvailable: true,
      scenes: [scene(1)],
      result: {
        status: "succeeded",
        download_url: "https://cdn.example.com/draft.zip",
        expire_at: "2026-07-16T01:00:00.000Z",
      },
      now,
    }),
    { enabled: true, label: "下载剪映草稿", reason: "剪映草稿已生成" },
  );
  assert.deepEqual(
    draftButtonState({
      providerAvailable: true,
      scenes: [scene(1)],
      result: {
        status: "succeeded",
        download_url: "https://cdn.example.com/draft.zip",
        expire_at: "2026-07-15T23:59:59.000Z",
      },
      now,
    }),
    { enabled: true, label: "重新生成剪映草稿", reason: "剪映草稿已过期，请重新生成" },
  );
});

test("succeeded result validity is conservative and permits only explicitly expired retries", () => {
  const now = new Date("2026-07-16T00:00:00.000Z");

  assert.equal(
    isJianyingDraftSucceededResultValid(
      { status: "succeeded", expire_at: "2026-07-16T01:00:00.000Z" },
      now,
    ),
    true,
  );
  assert.equal(
    isJianyingDraftSucceededResultValid(
      { status: "succeeded", expire_at: "2026-07-15T23:59:59.000Z" },
      now,
    ),
    false,
  );
  assert.equal(isJianyingDraftSucceededResultValid({ status: "succeeded", expire_at: null }, now), true);
  assert.equal(isJianyingDraftSucceededResultValid({ status: "succeeded", expire_at: "invalid" }, now), true);
  assert.equal(isJianyingDraftSucceededResultValid({ status: "failed", expire_at: null }, now), false);
});

test("start guard lets concurrent clicks start one job for the same conversation and storyboard", async () => {
  const guard = new JianyingDraftStartGuard();
  let starts = 0;
  let releaseCapability;
  const capability = new Promise((resolve) => {
    releaseCapability = resolve;
  });
  const start = async () => {
    if (!guard.tryAcquire("conversation-1", "storyboard-1")) return;
    try {
      await capability;
      starts += 1;
    } finally {
      guard.release("conversation-1", "storyboard-1");
    }
  };

  const first = start();
  const second = start();
  releaseCapability();
  await Promise.all([first, second]);

  assert.equal(starts, 1);
  assert.equal(guard.tryAcquire("conversation-1", "storyboard-1"), true);
});

test("target conversation patch keeps A context intact after the UI switches to B", () => {
  const contextA = {
    brand_name: "A 品牌",
    selected_direction: { title: "A 创意" },
    pendingJianyingDraftJob: { job_id: "old-a" },
    jianying_draft_records: {
      "storyboard-server": {
        status: "failed",
        job_id: "job-server",
        conversation_id: "conversation-a",
        storyboard_version_id: "storyboard-server",
      },
    },
    concurrent_server_field: "A 服务端最新值",
  };
  const contextB = {
    brand_name: "B 品牌",
    selected_direction: { title: "B 创意" },
  };
  const records = {
    "storyboard-a": {
      status: "succeeded",
      job_id: "job-a",
      provider_task_id: null,
      conversation_id: "conversation-a",
      storyboard_version_id: "storyboard-a",
      download_url: "https://cdn.example.com/a.zip",
      file_name: "a.zip",
      expire_at: null,
      message: "完成",
    },
  };

  const patchedA = patchJianyingDraftConversationContext(contextA, {
    pendingJianyingDraftJob: null,
    jianyingDraftRecords: records,
    jianyingDraftJobResumeError: "任务已过期",
  });

  assert.equal(patchedA.brand_name, "A 品牌");
  assert.deepEqual(patchedA.selected_direction, { title: "A 创意" });
  assert.equal(patchedA.pendingJianyingDraftJob, null);
  assert.equal(patchedA.pending_jianying_draft_job, null);
  assert.equal(patchedA.concurrent_server_field, "A 服务端最新值");
  assert.deepEqual(patchedA.jianyingDraftRecords, {
    "storyboard-server": contextA.jianying_draft_records["storyboard-server"],
    ...records,
  });
  assert.deepEqual(patchedA.jianying_draft_records, patchedA.jianyingDraftRecords);
  assert.equal(patchedA.jianying_draft_job_resume_error, "任务已过期");
  assert.deepEqual(contextB, {
    brand_name: "B 品牌",
    selected_direction: { title: "B 创意" },
  });
});

test("target-bound orchestration keeps writing A after an awaited patch switches the UI to B", async () => {
  let activeConversationId = "conversation-a";
  const expectedJobId = "job-a";
  let releasePatch;
  const patchDeferred = new Promise((resolve) => {
    releasePatch = resolve;
  });
  const patchedConversationIds = [];
  const syncedConversationIds = [];

  const pending = patchJianyingDraftTargetConversation({
    targetConversationId: "conversation-a",
    expectedJobId,
    isCurrentConversation: (conversationId) => activeConversationId === conversationId,
    syncCurrentConversation: () => syncedConversationIds.push(activeConversationId),
    patchTargetConversation: async (conversationId, patchExpectedJobId) => {
      patchedConversationIds.push([conversationId, patchExpectedJobId]);
      await patchDeferred;
      return { conversation_id: conversationId, expected_job_id: patchExpectedJobId };
    },
  });

  assert.deepEqual(syncedConversationIds, ["conversation-a"]);
  activeConversationId = "conversation-b";
  releasePatch();
  const result = await pending;

  assert.deepEqual(result, { conversation_id: "conversation-a", expected_job_id: "job-a" });
  assert.deepEqual(patchedConversationIds, [["conversation-a", "job-a"]]);
  assert.deepEqual(syncedConversationIds, ["conversation-a"]);
});
