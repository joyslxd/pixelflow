import assert from "node:assert/strict";
import { test } from "node:test";

const modulePath = process.env.REVIEW_WINDOW_TEST_MODULE || "../src/lib/reviewWindow.ts";
const { isReviewExpired, reviewExpiresAt, timeoutReviewMessage } = await import(modulePath);

test("reviewExpiresAt stores an absolute timeout", () => {
  assert.equal(reviewExpiresAt(Date.UTC(2026, 0, 1, 0, 0, 0), 60_000), "2026-01-01T00:01:00.000Z");
});

test("isReviewExpired only expires after the deadline", () => {
  const expiresAt = "2026-01-01T00:01:00.000Z";
  assert.equal(isReviewExpired(expiresAt, Date.UTC(2026, 0, 1, 0, 0, 59)), false);
  assert.equal(isReviewExpired(expiresAt, Date.UTC(2026, 0, 1, 0, 1, 0)), true);
  assert.equal(isReviewExpired("", Date.UTC(2026, 0, 1, 0, 1, 0)), false);
});

test("timeoutReviewMessage names the ended flow", () => {
  assert.match(timeoutReviewMessage("video", 60), /视频修改意见/);
  assert.match(timeoutReviewMessage("image", 60), /图片修改意见/);
});
