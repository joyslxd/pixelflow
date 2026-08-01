import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SUPERVISOR_ACTIONS_TEST_MODULE;

if (!moduleUrl) {
  throw new Error("缺少 Supervisor 结构化动作测试模块路径");
}

const {
  SupervisorActionValidationError,
  buildSupervisorWorkflowAction,
} = await import(moduleUrl);

const actionCases = [
  {
    name: "answer_only",
    input: { action: "answer_only" },
    expected: {
      action: "answer_only",
      intent: null,
      workflow_id: null,
      stage: null,
      artifact_ref: null,
      patch: {},
    },
  },
  {
    name: "continue_workflow",
    input: {
      action: "continue_workflow",
      intent: "video",
      workflowId: " wf-continue ",
      stage: " plan_review ",
      artifactRef: "artifact:video-plan:wf-continue:v1",
      patch: { approved: true },
    },
    expected: {
      action: "continue_workflow",
      intent: "video",
      workflow_id: "wf-continue",
      stage: "plan_review",
      artifact_ref: "artifact:video-plan:wf-continue:v1",
      patch: { approved: true },
    },
  },
  {
    name: "modify_workflow",
    input: {
      action: "modify_workflow",
      intent: "video",
      workflowId: "wf-modify",
      stage: "scene_package_review",
      patch: { instruction: "调整第二镜" },
    },
    expected: {
      action: "modify_workflow",
      intent: "video",
      workflow_id: "wf-modify",
      stage: "scene_package_review",
      artifact_ref: null,
      patch: { instruction: "调整第二镜" },
    },
  },
  {
    name: "regenerate_stage",
    input: {
      action: "regenerate_stage",
      intent: "video",
      workflowId: "wf-regenerate",
      stage: "generate_scene_assets",
    },
    expected: {
      action: "regenerate_stage",
      intent: "video",
      workflow_id: "wf-regenerate",
      stage: "generate_scene_assets",
      artifact_ref: null,
      patch: {},
    },
  },
  {
    name: "retry_failed",
    input: {
      action: "retry_failed",
      intent: "video",
      workflowId: "wf-retry",
      stage: "generate_scene_videos",
      patch: { failed_scene_ids: ["scene-2"] },
    },
    expected: {
      action: "retry_failed",
      intent: "video",
      workflow_id: "wf-retry",
      stage: "generate_scene_videos",
      artifact_ref: null,
      patch: { failed_scene_ids: ["scene-2"] },
    },
  },
  {
    name: "start_workflow",
    input: {
      action: "start_workflow",
      intent: "video",
      patch: { requirement: { duration_sec: 30 } },
    },
    expected: {
      action: "start_workflow",
      intent: "video",
      workflow_id: null,
      stage: null,
      artifact_ref: null,
      patch: { requirement: { duration_sec: 30 } },
    },
  },
  {
    name: "switch_workflow",
    input: {
      action: "switch_workflow",
      intent: "video",
      workflowId: "wf-switch",
    },
    expected: {
      action: "switch_workflow",
      intent: "video",
      workflow_id: "wf-switch",
      stage: null,
      artifact_ref: null,
      patch: {},
    },
  },
  {
    name: "cancel_workflow",
    input: {
      action: "cancel_workflow",
      intent: "video",
      workflowId: "wf-cancel",
    },
    expected: {
      action: "cancel_workflow",
      intent: "video",
      workflow_id: "wf-cancel",
      stage: null,
      artifact_ref: null,
      patch: {},
    },
  },
  {
    name: "clarify",
    input: { action: "clarify", intent: null },
    expected: {
      action: "clarify",
      intent: null,
      workflow_id: null,
      stage: null,
      artifact_ref: null,
      patch: {},
    },
  },
];

for (const actionCase of actionCases) {
  test(`九动作 builder 生成唯一结构：${actionCase.name}`, () => {
    const result = buildSupervisorWorkflowAction(actionCase.input);

    assert.deepEqual(result, actionCase.expected);
    assert.deepEqual(Object.keys(result), [
      "action",
      "intent",
      "workflow_id",
      "stage",
      "artifact_ref",
      "patch",
    ]);
  });
}

function assertReasonCode(input, reasonCode) {
  assert.throws(
    () => buildSupervisorWorkflowAction(input),
    (error) => {
      assert.equal(error instanceof SupervisorActionValidationError, true);
      assert.equal(error.name, "SupervisorActionValidationError");
      assert.equal(error.message, reasonCode);
      assert.equal(error.reasonCode, reasonCode);
      return true;
    },
  );
}

test("start_workflow 只接受 video intent 且禁止现有 Workflow 目标", () => {
  assertReasonCode(
    { action: "start_workflow", intent: null },
    "start_workflow_target_invalid",
  );
  assertReasonCode(
    { action: "start_workflow", intent: "video", workflowId: "wf-existing" },
    "start_workflow_target_invalid",
  );
});

test("六个现有 Workflow 动作逐一拒绝缺失或空白 Workflow ID", () => {
  for (const action of [
    "continue_workflow",
    "modify_workflow",
    "regenerate_stage",
    "retry_failed",
    "switch_workflow",
    "cancel_workflow",
  ]) {
    assertReasonCode(
      { action, intent: "video", workflowId: "   " },
      "workflow_id_required",
    );
  }
});

test("answer_only 与 clarify 拒绝非空 patch", () => {
  for (const action of ["answer_only", "clarify"]) {
    assertReasonCode(
      { action, patch: { guessed_target: "wf-nearest" } },
      "read_only_action_patch_forbidden",
    );
  }
});

test("Artifact 引用去空白并只接受 artifact 非空格式", () => {
  const normalized = buildSupervisorWorkflowAction({
    action: "continue_workflow",
    workflowId: "wf-1",
    artifactRef: "  artifact:video-plan:wf-1:v1  ",
  });
  const empty = buildSupervisorWorkflowAction({
    action: "continue_workflow",
    workflowId: "wf-1",
    artifactRef: "   ",
  });

  assert.equal(normalized.artifact_ref, "artifact:video-plan:wf-1:v1");
  assert.equal(empty.artifact_ref, null);
  for (const artifactRef of ["video-plan:wf-1", "artifact:", "artifact:has space"]) {
    assertReasonCode(
      { action: "continue_workflow", workflowId: "wf-1", artifactRef },
      "artifact_ref_invalid",
    );
  }
});

test("Workflow ID 与 stage 只做首尾规范化且空 stage 变为 null", () => {
  const result = buildSupervisorWorkflowAction({
    action: "modify_workflow",
    workflowId: "  wf-1  ",
    stage: "   ",
  });

  assert.equal(result.workflow_id, "wf-1");
  assert.equal(result.stage, null);
});

test("patch 拒绝循环和稀疏数组", () => {
  const cyclic = {};
  cyclic.self = cyclic;
  const sparse = [];
  sparse.length = 1;

  for (const patch of [cyclic, { items: sparse }]) {
    assertReasonCode(
      { action: "continue_workflow", workflowId: "wf-1", patch },
      "patch_invalid_json",
    );
  }
});

test("patch 拒绝非有限数字和 JSON 不支持的值", () => {
  for (const invalid of [
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    () => "不可序列化",
    Symbol("不可序列化"),
    1n,
    undefined,
  ]) {
    assertReasonCode(
      { action: "continue_workflow", workflowId: "wf-1", patch: { invalid } },
      "patch_invalid_json",
    );
  }
});

test("patch 拒绝数组根与非普通对象原型", () => {
  class CustomPatch {
    constructor() {
      this.approved = true;
    }
  }

  for (const patch of [[], new Date("2026-08-02T00:00:00Z"), new CustomPatch()]) {
    assertReasonCode(
      { action: "continue_workflow", workflowId: "wf-1", patch },
      "patch_invalid_json",
    );
  }
});

test("patch 代理陷阱异常也只暴露固定 reason code", () => {
  const patch = new Proxy({}, {
    ownKeys() {
      throw new Error("不应暴露的输入异常");
    },
  });

  assertReasonCode(
    { action: "continue_workflow", workflowId: "wf-1", patch },
    "patch_invalid_json",
  );
});

test("patch 递归深拷贝且修改原输入不改变动作输出", () => {
  const patch = {
    approved: true,
    nested: { scene_ids: ["scene-1", "scene-2"] },
  };
  const result = buildSupervisorWorkflowAction({
    action: "continue_workflow",
    workflowId: "wf-1",
    patch,
  });

  assert.notEqual(result.patch, patch);
  assert.notEqual(result.patch.nested, patch.nested);
  assert.notEqual(result.patch.nested.scene_ids, patch.nested.scene_ids);
  patch.approved = false;
  patch.nested.scene_ids[0] = "scene-mutated";
  assert.deepEqual(result.patch, {
    approved: true,
    nested: { scene_ids: ["scene-1", "scene-2"] },
  });
});

test("非法 action 只暴露固定 reason code 而不回显输入", () => {
  const privateAction = "provider_token_from_user";

  assert.throws(
    () => buildSupervisorWorkflowAction({ action: privateAction }),
    (error) => {
      assert.equal(error instanceof SupervisorActionValidationError, true);
      assert.equal(error.message, "action_invalid");
      assert.equal(error.reasonCode, "action_invalid");
      assert.equal(error.message.includes(privateAction), false);
      return true;
    },
  );
});
