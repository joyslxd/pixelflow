import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.TIME_TEST_MODULE;
assert.ok(moduleUrl, "TIME_TEST_MODULE must point to the compiled time module");

const { formatClockTime, formatMessageTime, normalizeIsoTimestamp } = await import(moduleUrl);

test("normalizeIsoTimestamp treats timezone-less API timestamps as UTC", () => {
  assert.equal(normalizeIsoTimestamp("2026-06-26T10:06:14"), "2026-06-26T10:06:14Z");
  assert.equal(normalizeIsoTimestamp("2026-06-26T10:06:14.123"), "2026-06-26T10:06:14.123Z");
});

test("normalizeIsoTimestamp keeps explicit timezone offsets unchanged", () => {
  assert.equal(normalizeIsoTimestamp("2026-06-26T10:06:14Z"), "2026-06-26T10:06:14Z");
  assert.equal(normalizeIsoTimestamp("2026-06-26T10:06:14+00:00"), "2026-06-26T10:06:14+00:00");
  assert.equal(normalizeIsoTimestamp("2026-06-26T18:06:14+08:00"), "2026-06-26T18:06:14+08:00");
});

test("normalizeIsoTimestamp trims Python microseconds to browser-safe milliseconds", () => {
  assert.equal(
    normalizeIsoTimestamp("2026-07-31T16:00:43.776811+00:00"),
    "2026-07-31T16:00:43.776+00:00",
  );
});

test("formatClockTime formats UTC API timestamps in the browser timezone", () => {
  assert.equal(formatClockTime("2026-06-26T10:06:14", "zh-CN", "Asia/Shanghai"), "18:06");
  assert.equal(formatClockTime("2026-06-26T10:06:14+00:00", "zh-CN", "Asia/Shanghai"), "18:06");
});

test("formatClockTime falls back when the timestamp is empty or invalid", () => {
  assert.equal(formatClockTime("", "zh-CN", "Asia/Shanghai", "10:06"), "10:06");
  assert.equal(formatClockTime("not-a-date", "zh-CN", "Asia/Shanghai", "10:06"), "10:06");
});

test("formatMessageTime renders complete local date and seconds", () => {
  assert.equal(
    formatMessageTime("2026-07-31T16:00:43.776811+00:00", "zh-CN", "Asia/Shanghai"),
    "2026-08-01 00:00:43",
  );
});
