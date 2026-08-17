import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const cardsPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../src/features/native-video-agent/cards/index.tsx",
);
const source = readFileSync(cardsPath, "utf8");

test("native cards 导出确认/额度/Operation/错误卡且确认走决策回调", () => {
  assert.match(source, /export function ConfirmationCard/);
  assert.match(source, /export function QuotaCard/);
  assert.match(source, /export function OperationCard/);
  assert.match(source, /export function ErrorCard/);
  assert.match(source, /onSubmit\(decision: "confirm" \| "cancel"\)/);
  assert.match(source, /onSubmit\(decision: "resume" \| "cancel"\)/);
  assert.doesNotMatch(source, /startTurn\(/);
});
