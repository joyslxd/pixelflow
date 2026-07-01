import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_PACKAGES_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_PACKAGES_TEST_MODULE must point to the compiled scenePackages module");

const {
  collectSceneImageUrls,
  deleteGlobalSceneAssetReference,
  durationMsForSubmit,
  inferTargetDurationMs,
  MAX_REFERENCE_IMAGE_COUNT,
  MAX_SCENE_DURATION_MS,
  MIN_SCENE_DURATION_MS,
  replaceGlobalSceneAssetImage,
  sceneIdsForRevision,
  syncScenePackageMentionImageUrls,
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

test("collectSceneImageUrls also uses inline shot description mention image urls", () => {
  const [scene] = sampleScenes();
  const sceneWithMentions = {
    ...scene,
    image_urls: [],
    reference_asset_ids: [],
    shot_description: {
      text: "地点:@桌面场景 中,角色:@讲解者 展示道具:@耳机。",
      mentions: [
        { asset_id: "character-host", image_url: "https://x/role-mention.png" },
        { asset_id: "scene-desk", image_url: "https://x/scene-mention.png" },
      ],
    },
  };

  assert.deepEqual(collectSceneImageUrls(sceneWithMentions, sampleGlobalAssets()), [
    "https://x/role-mention.png",
    "https://x/scene-mention.png",
  ]);
});

test("replaceGlobalSceneAssetImage replaces character three-view image as one asset", () => {
  const assets = sampleGlobalAssets();

  const updated = replaceGlobalSceneAssetImage(assets, {
    assetId: "character-host",
    assetGroup: "characters",
    editedImageUrl: "https://x/global-role-white.png",
  });

  assert.equal(assets.characters[0].three_view_images[0], "https://x/global-role.png");
  assert.equal(updated.characters[0].three_view_images[0], "https://x/global-role-white.png");
  assert.equal(updated.characters[0].image_url, "https://x/global-role-white.png");
  assert.equal(updated.scenes[0], assets.scenes[0]);
});

test("replaceGlobalSceneAssetImage replaces scene and prop first image", () => {
  const assets = sampleGlobalAssets();

  const sceneUpdated = replaceGlobalSceneAssetImage(assets, {
    assetId: "scene-desk",
    assetGroup: "scenes",
    editedImageUrl: "https://x/global-scene-new.png",
  });
  const propUpdated = replaceGlobalSceneAssetImage(sceneUpdated, {
    assetId: "prop-product",
    assetGroup: "props",
    editedImageUrl: "https://x/global-prop-new.png",
  });

  assert.equal(propUpdated.scenes[0].images[0], "https://x/global-scene-new.png");
  assert.equal(propUpdated.props[0].images[0], "https://x/global-prop-new.png");
});

test("syncScenePackageMentionImageUrls updates mention image urls by asset id", () => {
  const [scene] = sampleScenes();
  const scenes = [
    {
      ...scene,
      shot_description: {
        text: "地点:@桌面场景 中,角色:@讲解者 展示道具:@耳机。",
        mentions: [
          { asset_id: "character-host", image_url: "https://x/role-old.png" },
          { asset_id: "prop-product", image_url: "https://x/prop-old.png" },
        ],
      },
    },
  ];

  const updated = syncScenePackageMentionImageUrls(scenes, {
    assetId: "character-host",
    editedImageUrl: "https://x/role-new.png",
  });

  assert.equal(scenes[0].shot_description.mentions[0].image_url, "https://x/role-old.png");
  assert.equal(updated[0].shot_description.mentions[0].image_url, "https://x/role-new.png");
  assert.equal(updated[0].shot_description.mentions[1].image_url, "https://x/prop-old.png");
});

test("deleteGlobalSceneAssetReference clears a character image but keeps the asset placeholder", () => {
  const assets = {
    characters: [
      {
        asset_id: "character-host",
        name: "Host",
        three_view_images: ["https://x/host.png"],
        image_url: "https://x/host.png",
        url: "https://x/host.png",
      },
      {
        asset_id: "character-guest",
        name: "Guest",
        three_view_images: ["https://x/guest.png"],
      },
    ],
  };
  const scenes = [
    {
      scene_id: "scene-1",
      scene_index: 1,
      duration_ms: 8000,
      prompt: "Keep this scene",
      image_urls: ["https://x/host.png", "https://x/unrelated.png"],
      reference_asset_ids: ["character-host", "character-guest"],
      shot_description: {
        text: "Host stands near @Host with @character-host while @Guest stays visible.",
        mentions: [
          { asset_id: "character-host", name: "Host", image_url: "https://x/host.png" },
          { asset_id: "character-guest", name: "Guest", image_url: "https://x/guest.png" },
        ],
      },
    },
  ];

  const updated = deleteGlobalSceneAssetReference(assets, scenes, {
    assetId: "character-host",
    assetGroup: "characters",
    assetName: "Host",
    sourceImageUrl: "https://x/host.png",
  });

  assert.equal(updated.global_assets.characters.length, 2);
  assert.deepEqual(updated.global_assets.characters[0].three_view_images, []);
  assert.equal(updated.global_assets.characters[0].image_url, "");
  assert.equal(updated.global_assets.characters[0].url, "");
  assert.deepEqual(updated.global_assets.characters[1].three_view_images, ["https://x/guest.png"]);
  assert.deepEqual(updated.scene_packages[0].reference_asset_ids, ["character-guest"]);
  assert.deepEqual(updated.scene_packages[0].image_urls, ["https://x/unrelated.png"]);
  assert.deepEqual(updated.scene_packages[0].shot_description.mentions, [
    { asset_id: "character-guest", name: "Guest", image_url: "https://x/guest.png" },
  ]);
  assert.equal(updated.scene_packages[0].shot_description.text, "Host stands near with while @Guest stays visible.");
});

test("deleteGlobalSceneAssetReference clears scene and prop image placeholders without changing unrelated content", () => {
  const assets = {
    scenes: [
      {
        asset_id: "scene-room",
        name: "Gaming Room",
        images: ["https://x/room.png"],
        image_urls: ["https://x/room-alt.png"],
        url: "https://x/room.png",
      },
    ],
    props: [
      {
        asset_id: "prop-mouse",
        name: "Mouse",
        images: ["https://x/mouse.png"],
      },
    ],
  };
  const scenes = [
    {
      scene_id: "scene-1",
      scene_index: 1,
      duration_ms: 8000,
      prompt: "Do not edit this prompt mentioning Gaming Room as plain text.",
      reference_asset_ids: ["scene-room", "prop-mouse"],
      shot_description: {
        text: "Camera moves through @Gaming Room and ends on @Mouse.",
        mentions: [
          { asset_id: "scene-room", name: "Gaming Room", image_url: "https://x/room.png" },
          { asset_id: "prop-mouse", name: "Mouse", image_url: "https://x/mouse.png" },
        ],
      },
    },
  ];

  const withoutScene = deleteGlobalSceneAssetReference(assets, scenes, {
    assetId: "scene-room",
    assetGroup: "scenes",
    assetName: "Gaming Room",
    sourceImageUrl: "https://x/room.png",
  });
  const withoutProp = deleteGlobalSceneAssetReference(withoutScene.global_assets, withoutScene.scene_packages, {
    assetId: "prop-mouse",
    assetGroup: "props",
    assetName: "Mouse",
    sourceImageUrl: "https://x/mouse.png",
  });

  assert.deepEqual(withoutProp.global_assets.scenes[0].images, []);
  assert.deepEqual(withoutProp.global_assets.scenes[0].image_urls, []);
  assert.equal(withoutProp.global_assets.scenes[0].url, "");
  assert.deepEqual(withoutProp.global_assets.props[0].images, []);
  assert.deepEqual(withoutProp.scene_packages[0].reference_asset_ids, []);
  assert.deepEqual(withoutProp.scene_packages[0].shot_description.mentions, []);
  assert.equal(withoutProp.scene_packages[0].shot_description.text, "Camera moves through and ends on.");
  assert.equal(withoutProp.scene_packages[0].prompt, "Do not edit this prompt mentioning Gaming Room as plain text.");
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
