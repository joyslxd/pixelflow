import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { spawnSync } from "node:child_process";

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pixelflow-scene-video-batch-total-"));
const src = path.resolve("src/features/video-agent/sceneVideoBatchTotal.ts");

const compile = spawnSync(
  "npx",
  [
    "tsc",
    src,
    "--target",
    "ES2022",
    "--module",
    "ES2022",
    "--moduleResolution",
    "bundler",
    "--outDir",
    tmpDir,
    "--skipLibCheck",
    "--strict",
  ],
  { encoding: "utf8" },
);
assert.equal(compile.status, 0, compile.stderr || compile.stdout || "tsc failed");
fs.writeFileSync(path.join(tmpDir, "package.json"), JSON.stringify({ type: "module" }));

const { resolveNativeSceneVideoBatchTotal } = await import(
  `file://${path.join(tmpDir, "sceneVideoBatchTotal.js")}`
);

test("单镜生成优先用 progress.total，不回落全量包数", () => {
  assert.equal(
    resolveNativeSceneVideoBatchTotal({
      progressTotal: 1,
      jobTotal: 1,
      finishedCount: 0,
      generatingFallback: 1,
    }),
    1,
  );
});

test("progress 未到时用 generation_jobs 数", () => {
  assert.equal(
    resolveNativeSceneVideoBatchTotal({
      progressTotal: null,
      jobTotal: 1,
      finishedCount: 0,
      generatingFallback: 1,
    }),
    1,
  );
});

test("全量启动时 progress.total 为包数", () => {
  assert.equal(
    resolveNativeSceneVideoBatchTotal({
      progressTotal: 14,
      jobTotal: 14,
      finishedCount: 0,
      generatingFallback: 1,
    }),
    14,
  );
});

test("并发生成时取 progress 与 jobTotal 的较大值", () => {
  assert.equal(
    resolveNativeSceneVideoBatchTotal({
      progressTotal: 1,
      jobTotal: 3,
      finishedCount: 1,
      generatingFallback: 1,
    }),
    3,
  );
});
