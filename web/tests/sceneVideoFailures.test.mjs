import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";
import test from "node:test";

const sourcePath = path.resolve("src/lib/sceneVideoFailures.ts");
const modulePath = process.env.SCENE_VIDEO_FAILURES_TEST_MODULE;

test("scene video failure formatter exists", () => {
  assert.ok(existsSync(sourcePath), "sceneVideoFailures.ts must format readable failure reasons");
});

test("scene video failure formatter expands generic provider failure", async () => {
  assert.ok(modulePath, "SCENE_VIDEO_FAILURES_TEST_MODULE must point to the compiled helper");
  const { formatSceneVideoFailureReason } = await import(pathToFileURL(modulePath).href);
  const text = formatSceneVideoFailureReason(
    {
      scene_id: "scene-2",
      scene_index: 2,
      reason_code: "provider_business_failed",
      error: "供应商任务执行失败。",
    },
    { sceneTitle: "交接手机", storyline: "Yann把手机放进安然手里" },
  );
  assert.match(text, /第 2 镜「交接手机」/);
  assert.match(text, /提示词不合规|参考图无效|模型拒绝生成/);
  assert.match(text, /重新生成失败分镜/);
  assert.equal((text.match(/可点击「重新生成失败分镜」/g) || []).length, 1);
});

test("scene video failure formatter does not nest already enriched text", async () => {
  assert.ok(modulePath, "SCENE_VIDEO_FAILURES_TEST_MODULE must point to the compiled helper");
  const { formatSceneVideoFailureReason, enrichFailedSceneForDisplay } = await import(
    pathToFileURL(modulePath).href,
  );
  const enriched = enrichFailedSceneForDisplay(
    {
      scene_id: "scene-2",
      scene_index: 2,
      reason_code: "provider_business_failed",
      error: "分镜视频生成失败",
    },
    { title: "Yann第一次不兜底", storyline: "Yann第一次不兜底" },
  );
  const again = formatSceneVideoFailureReason(enriched, {
    sceneTitle: "Yann第一次不兜底",
    storyline: "Yann第一次不兜底",
  });
  assert.equal(again, enriched.error);
  assert.equal((again.match(/可点击「重新生成失败分镜」/g) || []).length, 1);
  assert.equal((again.match(/详情：/g) || []).length, 1);
});
