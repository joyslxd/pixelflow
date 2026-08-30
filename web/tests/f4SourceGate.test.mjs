import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const webRoot = process.env.PIXELFLOW_WEB_ROOT;
assert.ok(webRoot, "PIXELFLOW_WEB_ROOT 必须指向 Web 根目录");

const forbidden = [
  /LegacyWorkspace/u,
  /native-video-agent/u,
  /lib\/supervisor/u,
  /\/agent\/flows/u,
  /\b(?:createTask|getTask|getResult)\s*\(/u,
  /\bpending[A-Za-z0-9_]*Job\b/u,
  /\/agent\/internal\//u,
  /\/internal\/v1\//u,
];

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(absolute);
    return /\.(?:ts|tsx)$/u.test(entry.name) ? [absolute] : [];
  });
}

test("F4 源码门禁：浏览器不回流旧工作台、任务 API 或 Sidecar 私有入口", () => {
  const files = sourceFiles(path.join(webRoot, "src"));
  for (const file of files) {
    const content = readFileSync(file, "utf8");
    for (const pattern of forbidden) {
      assert.doesNotMatch(content, pattern, `${path.relative(webRoot, file)} 命中已删除架构标识：${pattern}`);
    }
  }
});
