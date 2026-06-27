import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_PACKAGES_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_PACKAGES_TEST_MODULE must point to the compiled scenePackages module");

const {
  collectSceneImageUrls,
  durationMsForSubmit,
  inferTargetDurationMs,
  MAX_REFERENCE_IMAGE_COUNT,
  MAX_SCENE_DURATION_MS,
  MIN_SCENE_DURATION_MS,
  sceneIdsForRevision,
  updateScenePackageAssetField,
  updateScenePackageField,
} = await import(moduleUrl);

function sampleScenes() {
  return [
    {
      scene_id: "scene-1",
      scene_index: 1,
      title: "开场钩子",
      duration_ms: 8000,
      storyline: "旧故事线",
      prompt: "旧提示词",
      narration: "旧旁白",
      image_urls: ["https://x/material.png"],
      shot_description: {
        time_range: "00:00-00:08",
        location: "@scene-desk",
        characters: ["@character-host"],
        props: ["@prop-product"],
        shot_size: "中景",
        description: "讲解者拿起产品",
      },
      reference_asset_ids: ["character-host", "scene-desk", "prop-product"],
      characters: [
        {
          name: "讲解者",
          description: "旧角色",
          three_view_prompt: "旧三视图",
          three_view_images: ["https://x/role.png"],
        },
      ],
      scene_images: [
        {
          description: "旧场景",
          image_prompt: "旧场景图",
          images: ["https://x/scene.png"],
        },
      ],
      prop_images: [
        {
          name: "耳机",
          description: "旧道具",
          image_prompt: "旧道具图",
          images: ["https://x/prop.png"],
        },
      ],
    },
    {
      scene_id: "scene-2",
      scene_index: 2,
      duration_ms: 9000,
      prompt: "第二段",
    },
  ];
}

function sampleGlobalAssets() {
  return {
    characters: [
      {
        asset_id: "character-host",
        name: "讲解者",
        three_view_images: ["https://x/global-role.png"],
      },
    ],
    scenes: [
      {
        asset_id: "scene-desk",
        name: "桌面场景",
        images: ["https://x/global-scene.png"],
      },
    ],
    props: [
      {
        asset_id: "prop-product",
        name: "耳机",
        images: ["https://x/global-prop.png"],
      },
    ],
    visual_style: {
      asset_id: "style-main",
      name: "真实摄影",
    },
  };
}

test("scene duration constants match backend-only 4 to 15 second contract", () => {
  assert.equal(MIN_SCENE_DURATION_MS, 4000);
  assert.equal(MAX_SCENE_DURATION_MS, 15000);
});

test("updateScenePackageField edits top-level fields immutably and clamps backend duration to 15 seconds", () => {
  const original = sampleScenes();

  const updated = updateScenePackageField(original, "scene-1", {
    title: "新版开场",
    duration_ms: 22000,
    storyline: "新版故事线",
    prompt: "新版提示词",
    narration: "新版旁白",
  });

  assert.notEqual(updated, original);
  assert.equal(original[0].title, "开场钩子");
  assert.equal(updated[0].title, "新版开场");
  assert.equal(updated[0].duration_ms, 15000);
  assert.equal(updated[0].storyline, "新版故事线");
  assert.equal(updated[0].prompt, "新版提示词");
  assert.equal(updated[0].narration, "新版旁白");
  assert.equal(updated[1], original[1]);
});

test("updateScenePackageField lets users temporarily clear duration before retyping", () => {
  const original = sampleScenes();

  const updated = updateScenePackageField(original, "scene-1", {
    duration_ms: "",
  });

  assert.equal(updated[0].duration_ms, "");
});

test("updateScenePackageAssetField edits nested character scene and prop fields immutably", () => {
  const original = sampleScenes();

  const updatedRole = updateScenePackageAssetField(original, "scene-1", "characters", 0, "three_view_prompt", "新版三视图");
  const updatedScene = updateScenePackageAssetField(updatedRole, "scene-1", "scene_images", 0, "image_prompt", "新版场景图");
  const updatedProp = updateScenePackageAssetField(updatedScene, "scene-1", "prop_images", 0, "description", "新版道具");

  assert.equal(original[0].characters[0].three_view_prompt, "旧三视图");
  assert.equal(updatedRole[0].characters[0].three_view_prompt, "新版三视图");
  assert.equal(updatedScene[0].scene_images[0].image_prompt, "新版场景图");
  assert.equal(updatedProp[0].prop_images[0].description, "新版道具");
  assert.equal(updatedProp[1], original[1]);
});

test("collectSceneImageUrls prefers selected global @ references and limits all-power references to 9 images", () => {
  const [scene] = sampleScenes();

  assert.equal(MAX_REFERENCE_IMAGE_COUNT, 9);
  assert.deepEqual(collectSceneImageUrls(scene, sampleGlobalAssets()), [
    "https://x/material.png",
    "https://x/global-role.png",
    "https://x/global-scene.png",
    "https://x/global-prop.png",
  ]);

  const manyGlobalAssets = {
    characters: Array.from({ length: 11 }, (_item, index) => ({
      asset_id: `character-${index}`,
      three_view_images: [`https://x/role-${index}.png`],
    })),
  };
  const manyScene = {
    ...scene,
    image_urls: [],
    reference_asset_ids: Array.from({ length: 11 }, (_item, index) => `character-${index}`),
  };

  assert.equal(collectSceneImageUrls(manyScene, manyGlobalAssets).length, 9);
});

test("sceneIdsForRevision maps explicit scene mentions and falls back to all scenes", () => {
  const scenes = sampleScenes();

  assert.deepEqual([...sceneIdsForRevision(scenes, "请修改第2段节奏", undefined, false)], ["scene-2"]);
  assert.deepEqual([...sceneIdsForRevision(scenes, "颜色穿帮", { affected_scene_ids: ["scene-1"] }, true)], ["scene-1"]);
  assert.deepEqual([...sceneIdsForRevision(scenes, "整体更高级", undefined, false)], ["scene-1", "scene-2"]);
});

test("inferTargetDurationMs reads seconds and minutes from user-facing flow text", () => {
  assert.equal(inferTargetDurationMs(["帮我生成90秒左右的视频"]), 90_000);
  assert.equal(inferTargetDurationMs(["做一个1.5分钟的复杂种草视频"]), 90_000);
  assert.equal(inferTargetDurationMs(["没有明确时长"]), 30_000);
});

test("durationMsForSubmit converts empty edit values to a valid minimum duration", () => {
  assert.equal(durationMsForSubmit(""), 4000);
  assert.equal(durationMsForSubmit(1), 4000);
  assert.equal(durationMsForSubmit(22000), 15000);
});
