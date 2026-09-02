import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.CONVERSATION_SCROLL_TEST_MODULE;
assert.ok(moduleUrl, "CONVERSATION_SCROLL_TEST_MODULE 必须指向编译后的滚动判定模块");
const { isNearScrollBottom, pinScrollToBottom } = await import(moduleUrl);

test("距底部不超过阈值时视为贴底", () => {
  assert.equal(
    isNearScrollBottom({ scrollTop: 920, clientHeight: 80, scrollHeight: 1000 }, 80),
    true,
  );
  assert.equal(
    isNearScrollBottom({ scrollTop: 0, clientHeight: 80, scrollHeight: 1000 }, 80),
    false,
  );
});

test("打开历史会话时应把滚动位置钉到内容底部", () => {
  const node = { scrollTop: 0, scrollHeight: 2400 };
  pinScrollToBottom(node);
  assert.equal(node.scrollTop, 2400);
});
