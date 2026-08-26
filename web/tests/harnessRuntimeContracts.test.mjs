import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const fixturePath = process.env.AGENT_HARNESS_CONTRACT_FIXTURE;
assert.ok(fixturePath, "AGENT_HARNESS_CONTRACT_FIXTURE 必须指向 Harness 合同 fixture");
const contractsPath = process.env.AGENT_HARNESS_TYPES_SOURCE;
assert.ok(contractsPath, "AGENT_HARNESS_TYPES_SOURCE 必须指向浏览器公开合同");

const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
const source = readFileSync(contractsPath, "utf8");

function typeBlock(name) {
  const marker = `export type ${name}`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `缺少 ${name} 浏览器公开类型`);
  const end = source.indexOf("};", start);
  assert.notEqual(end, -1, `${name} 类型未闭合`);
  return source.slice(start, end);
}

function assertFields(block, fields, label) {
  for (const field of fields) {
    assert.match(block, new RegExp(`\\b${field}\\??:`, "u"), `${label} 缺少 ${field}`);
  }
}

test("Harness fixture 仅冻结公开跨端 DTO", () => {
  assert.deepEqual(Object.keys(fixture).sort(), [
    "event",
    "interrupt_response",
    "schema_version",
    "snapshot",
    "turn_start",
    "workspace_command",
  ]);
  assert.equal(fixture.schema_version, 1);
});

test("TypeScript Harness 合同覆盖冻结字段", () => {
  assertFields(typeBlock("TurnStartV1"), Object.keys(fixture.turn_start), "TurnStartV1");
  assertFields(typeBlock("InterruptResponseV1"), Object.keys(fixture.interrupt_response), "InterruptResponseV1");
  assertFields(typeBlock("WorkspaceCommandV1"), Object.keys(fixture.workspace_command), "WorkspaceCommandV1");
  assertFields(typeBlock("PublicAgentEventV1"), Object.keys(fixture.event), "PublicAgentEventV1");
  assertFields(typeBlock("AgentSnapshotV1"), Object.keys(fixture.snapshot), "AgentSnapshotV1");
  assertFields(
    typeBlock("InterruptResponseV1"),
    Object.keys(fixture.interrupt_response.value),
    "InterruptResponseV1.value",
  );
});
