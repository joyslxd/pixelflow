import assert from "node:assert/strict";
import test from "node:test";

import { resolveThinkingAfterMessageId } from "../src/features/video-agent/thinkingAnchor.ts";

test("thinking anchors to pending turn user message, not latest user", () => {
  const messages = [
    { id: "u1", role: "user" },
    { id: "a1", role: "assistant" },
    { id: "u2", role: "user" },
  ];
  const anchor = resolveThinkingAfterMessageId("run-1", messages, {
    pendingTurns: [{ clientInputId: "u1", runId: "run-1" }],
  });
  assert.equal(anchor, "u1");
});

test("thinking anchors by optimistic clientInputId turnId", () => {
  const messages = [
    { id: "u1", role: "user" },
    { id: "u2", role: "user" },
  ];
  assert.equal(resolveThinkingAfterMessageId("u1", messages), "u1");
});

test("knownAnchor wins over latest user fallback", () => {
  const messages = [
    { id: "u1", role: "user" },
    { id: "u2", role: "user" },
  ];
  assert.equal(
    resolveThinkingAfterMessageId("run-x", messages, { knownAnchor: "u1" }),
    "u1",
  );
});

test("thinking-answer bubble still resolves previous user", () => {
  const messages = [
    { id: "u1", role: "user" },
    { id: "thinking-answer:run-1", role: "assistant" },
    { id: "u2", role: "user" },
  ];
  assert.equal(resolveThinkingAfterMessageId("run-1", messages), "u1");
});
