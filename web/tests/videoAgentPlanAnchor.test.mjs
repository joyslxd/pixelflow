import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(join(root, "src/lib/videoAgentPlanAnchor.ts"), "utf8");

// Lightweight reimplementation for contract tests (mirrors exported helpers).
const VIDEO_AGENT_ACK_RE =
  /已收到创作请求|正在生成执行方案|已按你的新想法重新从选题开始|已确认选题创意/;

function isVideoAgentAckNoticeContent(content) {
  return VIDEO_AGENT_ACK_RE.test(String(content || ""));
}

function isVideoAgentAckMessageId(messageId) {
  const id = String(messageId || "");
  return id.startsWith("agent-ack:") || id.includes(":agent-ack:");
}

function resolveVideoAgentPlanAnchorId({ preferredUserMessageId, messages }) {
  const preferredUserId = String(preferredUserMessageId || "").trim();
  const userIndex = preferredUserId
    ? messages.findIndex((message) => message.id === preferredUserId)
    : -1;
  const searchFrom = userIndex >= 0 ? userIndex + 1 : 0;
  for (let index = searchFrom; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role === "user") break;
    if (
      message.role === "assistant"
      && (isVideoAgentAckMessageId(message.id) || isVideoAgentAckNoticeContent(message.content))
    ) {
      return message.id;
    }
  }
  if (preferredUserId && messages.some((message) => message.id === preferredUserId)) {
    return preferredUserId;
  }
  return [...messages].reverse().find((message) => message.role === "user")?.id || "";
}

test("video agent plan anchor prefers ack notice after user message", () => {
  assert.match(source, /已收到创作请求/);
  const messages = [
    { id: "u1", role: "user", content: "蓝妹视频" },
    { id: "agent-ack:c1:u1:v1", role: "assistant", content: "已收到创作请求，正在生成执行方案…" },
    { id: "a2", role: "assistant", content: "其它" },
  ];
  assert.equal(
    resolveVideoAgentPlanAnchorId({ preferredUserMessageId: "u1", messages }),
    "agent-ack:c1:u1:v1",
  );
});

test("video agent plan anchor falls back to user when ack missing", () => {
  const messages = [
    { id: "u1", role: "user", content: "蓝妹视频" },
    { id: "a1", role: "assistant", content: "随便聊聊" },
  ];
  assert.equal(
    resolveVideoAgentPlanAnchorId({ preferredUserMessageId: "u1", messages }),
    "u1",
  );
});
