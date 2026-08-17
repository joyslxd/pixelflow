import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import os from "node:os";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const typesPath = path.join(root, "src/features/native-video-agent/canvas/types.ts");
const indexPath = path.join(root, "src/features/native-video-agent/canvas/index.ts");
const shellPath = path.join(root, "src/features/native-video-agent/canvas/VideoCanvasShell.tsx");
const routerPath = path.join(
  root,
  "src/features/native-video-agent/canvas/ArtifactCanvasRouter.tsx",
);

test("Canvas 模块导出壳与路由组件", () => {
  const indexSource = readFileSync(indexPath, "utf8");
  assert.match(indexSource, /VideoCanvasShell/);
  assert.match(indexSource, /ArtifactCanvasRouter/);
  assert.match(indexSource, /ScriptCanvas/);
  assert.match(indexSource, /ScenePackageCanvas/);
  assert.match(indexSource, /SceneAssetCanvas/);
  assert.match(indexSource, /SceneVideoCanvas/);
  assert.match(indexSource, /QualityReviewCanvas/);
  assert.match(indexSource, /DeliveryCanvas/);
  assert.match(indexSource, /markDirtySceneIds/);
});

test("VideoCanvasShell 展示脏镜头与重新生成完成", () => {
  const source = readFileSync(shellPath, "utf8");
  assert.match(source, /待重生/);
  assert.match(source, /重新生成完成/);
  assert.match(source, /data-native-canvas-shell/);
});

test("ArtifactCanvasRouter 按 kind 路由插槽", () => {
  const source = readFileSync(routerPath, "utf8");
  assert.match(source, /kind === "scene_package"/);
  assert.match(source, /kind === "script"/);
  assert.match(source, /kind === "delivery"/);
});

test("dirty_scene helpers 单镜标记与清空", async () => {
  const outDir = mkdtempSync(path.join(os.tmpdir(), "pixelflow-native-canvas-"));
  try {
    const tsc = path.join(root, "node_modules", "typescript", "bin", "tsc");
    const result = spawnSync(
      process.execPath,
      [
        tsc,
        typesPath,
        "--target",
        "ES2022",
        "--module",
        "ES2022",
        "--moduleResolution",
        "bundler",
        "--outDir",
        outDir,
        "--skipLibCheck",
        "--strict",
      ],
      { cwd: root, encoding: "utf8" },
    );
    assert.equal(result.status, 0, result.stderr || result.stdout);
    writeFileSync(path.join(outDir, "package.json"), JSON.stringify({ type: "module" }));
    const mod = await import(pathToFileURL(path.join(outDir, "types.js")).href);
    assert.deepEqual(mod.markDirtySceneIds(["s1"], "s2"), ["s1", "s2"]);
    assert.deepEqual(mod.markDirtySceneIds(["s1"], "s1"), ["s1"]);
    const cleared = mod.clearDirtyScenesAfterRegenerate(["a", "b"]);
    assert.deepEqual(cleared.dirtySceneIds, []);
    assert.match(cleared.message, /重新生成完成/);
    assert.equal(
      mod.resolveCanvasKindFromArtifact({ type: "video_scene_packages", videoScenePackages: {} }),
      "scene_package",
    );
    assert.equal(mod.resolveCanvasKindFromArtifact({ plan: {} }), "plan_markdown");
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }
});
