import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const modulePath = process.env.SUPERVISOR_LEGACY_ADAPTER_TEST_MODULE;
const fixturePath = process.env.SUPERVISOR_LEGACY_ADAPTER_FIXTURE;
if (!modulePath || !fixturePath) {
  throw new Error("缺少 Supervisor legacy adapter 测试入口");
}

const {
  LEGACY_PENDING_FIELD_PAIRS,
  projectLegacyConversationSnapshot,
} = await import(modulePath);
const fixture = JSON.parse(await readFile(fixturePath, "utf8"));

function detail(context = {}, messages = []) {
  return {
    conversation: {
      conversation_id: fixture.conversation_id,
      title: "历史对话",
      current_task_id: "task-1",
      last_phase: "video_generating",
      context,
      created_at: "2026-07-24T07:00:00Z",
      updated_at: "2026-07-24T07:01:00Z",
    },
    messages,
  };
}

function persistedMessage(index, artifact) {
  return {
    message_id: `server-${index}`,
    conversation_id: fixture.conversation_id,
    role: "assistant",
    content: `产物 ${artifact.type}`,
    payload: {
      client_message_id: `client-${index}`,
      materials: [{ url: `https://example.com/${index}.png` }],
      artifact,
    },
    created_at: "2026-07-24T07:01:00Z",
  };
}

test("fixture 覆盖全部 legacy pending 双字段且 camel/snake 投影一致", () => {
  assert.deepEqual(
    LEGACY_PENDING_FIELD_PAIRS.map(({ camel, snake }) => ({ camel, snake })),
    fixture.pending_pairs.map(({ camel, snake }) => ({ camel, snake })),
  );

  for (const { camel, snake, value } of fixture.pending_pairs) {
    const camelProjection = projectLegacyConversationSnapshot(detail({ [camel]: value }));
    const snakeProjection = projectLegacyConversationSnapshot(detail({ [snake]: value }));
    assert.deepEqual(camelProjection.pending[camel], value, camel);
    assert.deepEqual(snakeProjection.pending[camel], value, snake);
    assert.notStrictEqual(camelProjection.pending[camel], value);
    assert.equal(camelProjection.hasPendingWork, true);
    assert.equal(snakeProjection.hasPendingWork, true);
  }
});

test("相同 pending 双字段只归一一次，冲突值与跨对话值 fail-closed", () => {
  const pair = fixture.pending_pairs[0];
  const projection = projectLegacyConversationSnapshot(detail({
    [pair.camel]: pair.value,
    [pair.snake]: structuredClone(pair.value),
  }));
  assert.deepEqual(projection.pending[pair.camel], pair.value);

  assert.throws(
    () => projectLegacyConversationSnapshot(detail({
      [pair.camel]: pair.value,
      [pair.snake]: { ...pair.value, job_id: "other-job" },
    })),
    /历史对话 pending 状态不一致/,
  );
  assert.throws(
    () => projectLegacyConversationSnapshot(detail({
      [pair.camel]: { ...pair.value, conversation_id: "other-conversation" },
    })),
    /历史对话 pending 状态归属不合法/,
  );
});

test("fixture 中全部 artifact 类型从持久化 payload 投影为稳定 ViewModel", () => {
  const messages = fixture.artifact_types.map((type, index) => persistedMessage(index, {
    type,
    title: `${type} 标题`,
    nested: { index },
  }));
  messages.push({
    ...persistedMessage(99, { type: "ignored" }),
    role: "system",
  });

  const projection = projectLegacyConversationSnapshot(detail({}, messages));
  assert.deepEqual(projection.messages.map((message) => message.id),
    fixture.artifact_types.map((_, index) => `client-${index}`));
  assert.deepEqual(projection.artifacts.map((artifact) => artifact.type), fixture.artifact_types);
  assert.equal(projection.messages[0].conversationId, fixture.conversation_id);
  assert.deepEqual(projection.messages[0].materials, [{ url: "https://example.com/0.png" }]);

  messages[0].payload.artifact.title = "外部修改";
  assert.equal(projection.messages[0].artifact.title, "brief 标题");
});

test("payload/direct artifact 双字段等价时兼容，冲突时拒绝", () => {
  const artifact = { type: "plan", title: "plan.md" };
  const projectedMessage = {
    id: "legacy-client-1",
    conversationId: fixture.conversation_id,
    role: "assistant",
    content: "历史 Plan",
    time: "10:00",
    artifact,
    payload: { artifact: structuredClone(artifact) },
  };
  const projection = projectLegacyConversationSnapshot(detail({}, [projectedMessage]));
  assert.deepEqual(projection.messages[0].artifact, artifact);

  assert.throws(
    () => projectLegacyConversationSnapshot(detail({}, [{
      ...projectedMessage,
      payload: { artifact: { type: "image_result", title: "冲突产物" } },
    }])),
    /历史对话 artifact 状态不一致/,
  );
});

test("缺少编排归属时固定为 frontend_v2，服务端归属存在时保持只读值", () => {
  const legacy = projectLegacyConversationSnapshot(detail());
  assert.equal(legacy.orchestrationMode, "frontend_v2");
  assert.equal(legacy.orchestrationVersion, 1);

  const supervisor = detail();
  supervisor.conversation.orchestration_mode = "supervisor_v1";
  supervisor.conversation.orchestration_version = 1;
  const projected = projectLegacyConversationSnapshot(supervisor);
  assert.equal(projected.orchestrationMode, "supervisor_v1");
});

test("旧 pending 未排空时即使服务端误标 supervisor 也保持 frontend_v2 恢复权", () => {
  const pending = fixture.pending_pairs[0];
  const unsafe = detail({ [pending.camel]: pending.value });
  unsafe.conversation.orchestration_mode = "supervisor_v1";
  unsafe.conversation.orchestration_version = 1;

  const projected = projectLegacyConversationSnapshot(unsafe);
  assert.equal(projected.orchestrationMode, "frontend_v2");
  assert.equal(projected.hasPendingWork, true);
});

test("重复 client_message_id 获得稳定唯一 ViewModel ID 且 artifact 引用同步", () => {
  const messages = [
    persistedMessage(1, { type: "plan", title: "第一版" }),
    persistedMessage(2, { type: "plan", title: "第二版" }),
  ];
  messages[1].payload.client_message_id = messages[0].payload.client_message_id;

  const projected = projectLegacyConversationSnapshot(detail({}, messages));
  assert.deepEqual(projected.messages.map(({ id }) => id), ["client-1", "client-1-2"]);
  assert.deepEqual(projected.artifacts.map(({ messageId }) => messageId), ["client-1", "client-1-2"]);
});

test("消息归属双字段必须同时属于当前对话", () => {
  const message = persistedMessage(1, { type: "plan", title: "历史 Plan" });
  message.conversationId = fixture.conversation_id;
  message.conversation_id = "other-conversation";

  assert.throws(
    () => projectLegacyConversationSnapshot(detail({}, [message])),
    /历史对话 Snapshot 状态不合法/,
  );
});

test("非法快照只返回固定中文错误，不暴露输入内容", () => {
  const secret = "Authorization=Bearer-sensitive-value";
  assert.throws(
    () => projectLegacyConversationSnapshot({ conversation: { conversation_id: "", title: secret }, messages: [] }),
    (error) => error instanceof TypeError
      && error.message === "历史对话 Snapshot 状态不合法"
      && !error.message.includes(secret),
  );
});
