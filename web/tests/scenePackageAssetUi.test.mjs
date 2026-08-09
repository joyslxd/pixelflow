import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_PACKAGE_ASSET_UI_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_PACKAGE_ASSET_UI_TEST_MODULE must point to compiled module");

const {
  isSceneAssetGenerationMaterialized,
  reconcileStaleSceneAssetUiFlags,
  scenePackageHasGeneratedImages,
} = await import(moduleUrl);

test("structure-only packages are not treated as generated images", () => {
  assert.equal(
    scenePackageHasGeneratedImages({
      global_assets: {
        characters: [{ asset_id: "c1", name: "角色", three_view_prompt: "三视图" }],
      },
      scene_packages: [{ scene_id: "s1", image_urls: [] }],
    }),
    false,
  );
});

test("early generating card does not materialize scene_asset_generation", () => {
  const messages = [{
    id: "scene-package-job:scene_package_generation:old",
    artifact: {
      type: "video_scene_packages",
      sceneAssetsGenerating: true,
      videoScenePackages: {
        global_assets: { characters: [{ asset_id: "c1" }] },
        scene_packages: [{ scene_id: "s1", image_urls: [] }],
      },
    },
  }];
  assert.equal(isSceneAssetGenerationMaterialized(messages, "scene_asset_generation"), false);
});

test("reconcile clears stale generating spinner when no active job", () => {
  const messages = [
    {
      id: "pkg",
      artifact: {
        type: "video_scene_packages",
        sceneAssetsGenerating: true,
        videoScenePackages: {
          global_assets: { props: [{ asset_id: "p1" }] },
          scene_packages: [{ scene_id: "s1", image_urls: [] }],
        },
      },
    },
    {
      id: "model",
      artifact: {
        type: "scene_asset_model_options",
        sceneAssetModelConfirmed: true,
      },
    },
  ];
  const next = reconcileStaleSceneAssetUiFlags(messages, { hasActiveAssetJob: false });
  assert.equal(next[0].artifact.sceneAssetsGenerating, false);
  assert.equal(next[0].artifact.sceneAssetsAwaitingModel, true);
  assert.equal(next[1].artifact.sceneAssetModelConfirmed, false);
});
