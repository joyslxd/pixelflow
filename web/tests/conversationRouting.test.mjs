import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.CONVERSATION_ROUTING_TEST_MODULE;
assert.ok(moduleUrl, "CONVERSATION_ROUTING_TEST_MODULE must point to the compiled conversationRouting module");

const {
  appendVisibleConversationMessage,
  messageConversationId,
  replaceMessageById,
  restoredConversationMessages,
  shouldApplyVisibleConversationSideEffect,
  shouldRenderConversationMessage,
} = await import(moduleUrl);

test("shouldRenderConversationMessage only renders messages for the active conversation", () => {
  assert.equal(shouldRenderConversationMessage("conversation-a", "conversation-a"), true);
  assert.equal(shouldRenderConversationMessage("conversation-b", "conversation-a"), false);
  assert.equal(shouldRenderConversationMessage("", "conversation-a"), false);
});

test("appendVisibleConversationMessage keeps async results out of another active conversation", () => {
  const currentMessages = [{ id: "m1", content: "B 当前消息" }];
  const next = appendVisibleConversationMessage(currentMessages, {
    activeConversationId: "conversation-b",
    targetConversationId: "conversation-a",
    message: { id: "m2", content: "A 的异步结果" },
  });

  assert.equal(next, currentMessages);
  assert.deepEqual(
    appendVisibleConversationMessage(currentMessages, {
      activeConversationId: "conversation-a",
      targetConversationId: "conversation-a",
      message: { id: "m2", content: "A 的异步结果" },
    }),
    [...currentMessages, { id: "m2", content: "A 的异步结果" }],
  );
});

test("appendVisibleConversationMessage upserts duplicate client ids instead of duplicating React keys", () => {
  const currentMessages = [{ id: "m1", content: "旧内容" }];
  const next = appendVisibleConversationMessage(currentMessages, {
    activeConversationId: "conversation-a",
    targetConversationId: "conversation-a",
    message: { id: "m1", content: "新内容" },
  });

  assert.deepEqual(next, [{ id: "m1", content: "新内容" }]);
});

test("messageConversationId prefers the message owner over current visible conversation", () => {
  assert.equal(messageConversationId({ conversationId: "conversation-a" }, "conversation-b"), "conversation-a");
  assert.equal(messageConversationId({}, "conversation-b"), "conversation-b");
});

test("replaceMessageById patches an optimistic message without reordering or appending missing messages", () => {
  const currentMessages = [
    { id: "m1", content: "first", time: "10:00" },
    { id: "m2", content: "second", time: "10:01" },
  ];

  assert.deepEqual(replaceMessageById(currentMessages, "m1", { id: "server-1", content: "first", time: "10:02" }), [
    { id: "server-1", content: "first", time: "10:02" },
    { id: "m2", content: "second", time: "10:01" },
  ]);
  assert.equal(replaceMessageById(currentMessages, "missing", { id: "server-2" }), currentMessages);
});

test("restoredConversationMessages ignores stale snapshot messages and uses persisted messages", () => {
  const snapshotMessages = [{ id: "wrong", conversationId: "other", content: "其他会话消息" }];
  const persistedMessages = [{ id: "right", conversationId: "current", content: "当前会话消息" }];

  assert.deepEqual(restoredConversationMessages(snapshotMessages, persistedMessages), persistedMessages);
});

test("restoredConversationMessages makes duplicate persisted client ids unique for rendering", () => {
  assert.deepEqual(
    restoredConversationMessages(undefined, [
      { id: "m1", content: "第一条" },
      { id: "m1", content: "第二条" },
      { id: "m1", content: "第三条" },
    ]),
    [
      { id: "m1", content: "第一条" },
      { id: "m1-2", content: "第二条" },
      { id: "m1-3", content: "第三条" },
    ],
  );
});

test("shouldApplyVisibleConversationSideEffect only allows visible conversation UI updates", () => {
  assert.equal(shouldApplyVisibleConversationSideEffect("active", "active"), true);
  assert.equal(shouldApplyVisibleConversationSideEffect("active", "other"), false);
  assert.equal(shouldApplyVisibleConversationSideEffect("", "other"), false);
});
