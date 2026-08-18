import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_ASSET_MODEL_SELECTION_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_ASSET_MODEL_SELECTION_TEST_MODULE must point to compiled module");

const {
  SCENE_ASSET_PREFERRED_MODELS,
  preferredSceneAssetImageSize,
  resolveSceneAssetImageRatio,
  sceneAssetModelLabel,
} = await import(moduleUrl);

test("preferred scene asset models are image-2 and Seedream 5.0", () => {
  assert.deepEqual([...SCENE_ASSET_PREFERRED_MODELS], ["gpt-image-2", "seeddream-5.0"]);
  assert.equal(sceneAssetModelLabel("gpt-image-2"), "image-2");
  assert.equal(sceneAssetModelLabel("seeddream-5.0"), "Seedream 5.0");
});

test("preferred scene asset image size follows model defaults", () => {
  assert.equal(preferredSceneAssetImageSize("gpt-image-2", ["2K", "4K"]), "4K");
  assert.equal(preferredSceneAssetImageSize("gpt-image-2", []), "4K");
  assert.equal(preferredSceneAssetImageSize("seeddream-5.0", ["4K", "2K"]), "2K");
  assert.equal(preferredSceneAssetImageSize("seeddream-5.0", []), "2K");
});

test("scene asset ratio inherits the confirmed video ratio", () => {
  assert.equal(resolveSceneAssetImageRatio([
    { scene_image_ratio: "9:16", video_ratio: "16:9" },
    { scene_image_ratio: "9:16" },
  ]), "16:9");
  assert.equal(resolveSceneAssetImageRatio([
    { aspect_ratio: "1:1" },
  ]), "1:1");
});
