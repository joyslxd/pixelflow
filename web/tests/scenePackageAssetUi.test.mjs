import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_PACKAGE_ASSET_UI_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_PACKAGE_ASSET_UI_TEST_MODULE must point to compiled module");

const {
  hasMediaResultMessage,
  isSceneAssetGenerationMaterialized,
  mediaResultClientMessageId,
  markConfirmedSceneAssetModelOptions,
  preferredVideoScenePackagesMessageIndex,
  reconcileStaleSceneAssetUiFlags,
  resolveVideoScenePackagesForRestore,
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
  // 历史模型卡保持已确认，避免对话记录被原地翻牌
  assert.equal(next[1].artifact.sceneAssetModelConfirmed, true);
});

test("media result message id is stable for poll dedupe", () => {
  assert.equal(mediaResultClientMessageId("scene_assets", "abc"), "media-result:scene_assets:abc");
  assert.equal(
    hasMediaResultMessage([{ id: "media-result:scene_assets:abc" }], "scene_assets", "abc"),
    true,
  );
  assert.equal(
    hasMediaResultMessage([{ id: "scene-package-job:scene_asset_generation:abc" }], "scene_assets", "abc"),
    false,
  );
});

test("restore prefers packages with images over empty early card", () => {
  const messages = [
    {
      id: "media-result:scene_assets:old",
      artifact: {
        type: "video_scene_packages",
        sceneAssetsGenerating: false,
        videoScenePackages: {
          global_assets: { characters: [{ asset_id: "c1", images: ["https://cdn/c1.png"] }] },
          scene_packages: [{ scene_id: "s1", image_urls: ["https://cdn/s1.png"] }],
        },
      },
    },
    {
      id: "scene-package-job:scene_asset_generation:new",
      artifact: {
        type: "video_scene_packages",
        sceneAssetsGenerating: true,
        videoScenePackages: {
          global_assets: { characters: [{ asset_id: "c1" }] },
          scene_packages: [{ scene_id: "s1", image_urls: [] }],
        },
      },
    },
  ];
  assert.equal(preferredVideoScenePackagesMessageIndex(messages), 0);

  const emptyCard = messages[1].artifact.videoScenePackages;
  const contextPackages = {
    global_assets: { characters: [{ asset_id: "c1", images: ["https://cdn/from-context.png"] }] },
    scene_packages: [{ scene_id: "s1", image_urls: ["https://cdn/from-context-s1.png"] }],
  };
  const resolved = resolveVideoScenePackagesForRestore(emptyCard, contextPackages);
  assert.equal(resolved, contextPackages);
});

test("markConfirmedSceneAssetModelOptions locks model card once generation evidence exists", () => {
  const messages = [
    {
      id: "scene-asset-model-options:job-1",
      artifact: {
        type: "scene_asset_model_options",
        sceneAssetModelConfirmed: false,
      },
    },
    {
      id: "scene-package-job:scene_asset_generation:job-1",
      artifact: {
        type: "video_scene_packages",
        sceneAssetsGenerating: true,
        videoScenePackages: {
          global_assets: { scenes: [{ asset_id: "s1", images: ["https://cdn/s1.png"] }] },
          scene_packages: [{ scene_id: "s1", image_urls: ["https://cdn/s1.png"] }],
        },
      },
    },
  ];
  const next = markConfirmedSceneAssetModelOptions(messages);
  assert.equal(next[0].artifact.sceneAssetModelConfirmed, true);
  assert.equal(next[1].artifact.sceneAssetsGenerating, true);
});
