import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";
import test from "node:test";

const sourcePath = path.resolve("src/lib/sceneAssetFailures.ts");
const modulePath = process.env.SCENE_ASSET_FAILURES_TEST_MODULE;

test("scene asset failure formatter exists", () => {
  assert.ok(existsSync(sourcePath), "sceneAssetFailures.ts must normalize backend failure details");
});

test("scene asset failure formatter exposes asset identity and readable reason", async () => {
  assert.ok(modulePath, "SCENE_ASSET_FAILURES_TEST_MODULE must point to the compiled helper");
  const { sceneAssetFailureDetails } = await import(pathToFileURL(modulePath).href);
  const details = sceneAssetFailureDetails([
    {
      asset_id: "scene-bedroom",
      asset_name: "阳光卧室",
      asset_type: "scene_image",
      scene_id: "scene-1",
      scene_index: 1,
      endpoint: "/api/picture/text_to_image",
      model: "gpt-image-2",
      ratio: "9:16",
      size: "4K",
      error: "参数验证失败；size：当前模型不支持4K",
    },
  ]);

  assert.deepEqual(details, [
    {
      id: "scene-bedroom-1",
      title: "阳光卧室",
      typeLabel: "场景图",
      sceneLabel: "分镜 1",
      endpoint: "/api/picture/text_to_image",
      model: "gpt-image-2",
      ratio: "9:16",
      size: "4K",
      error: "参数验证失败；size：当前模型不支持4K",
    },
  ]);
});
