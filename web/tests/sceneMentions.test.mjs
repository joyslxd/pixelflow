import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_MENTIONS_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_MENTIONS_TEST_MODULE must point to the compiled sceneMentions module");

const {
  buildMentionCandidates,
  collectMentionImageUrls,
  filterMentionCandidates,
  normalizeShotMentions,
  upsertShotMention,
} = await import(moduleUrl);

const globalAssets = {
  characters: [{ asset_id: "character-host", name: "讲解者", images: ["https://x/role.png"] }],
  scenes: [{ asset_id: "scene-desk", name: "桌面场景", images: ["https://x/scene.png"] }],
  props: [{ asset_id: "prop-product", name: "耳机", images: ["https://x/prop.png"] }],
  visual_style: { asset_id: "style-main", name: "真实摄影" },
};

test("buildMentionCandidates returns only character scene and prop image candidates", () => {
  assert.deepEqual(
    buildMentionCandidates(globalAssets).map((item) => [item.asset_id, item.type, item.name, item.image_url]),
    [
      ["character-host", "character", "讲解者", "https://x/role.png"],
      ["scene-desk", "scene", "桌面场景", "https://x/scene.png"],
      ["prop-product", "prop", "耳机", "https://x/prop.png"],
    ],
  );
});

test("buildMentionCandidates uses generated asset images before stale direct urls", () => {
  const candidates = buildMentionCandidates({
    characters: [
      {
        asset_id: "character-host",
        name: "Host",
        image_url: "https://x/stale-role.png",
        three_view_images: ["https://x/generated-role.png"],
      },
    ],
    scenes: [
      {
        asset_id: "scene-room",
        name: "Room",
        url: "https://x/stale-room.png",
        images: ["https://x/generated-room.png"],
      },
    ],
    props: [
      {
        asset_id: "prop-product",
        name: "Product",
        image_url: "https://x/stale-product.png",
        images: ["https://x/generated-product.png"],
      },
    ],
  });

  assert.deepEqual(
    candidates.map((item) => [item.asset_id, item.image_url]),
    [
      ["character-host", "https://x/generated-role.png"],
      ["scene-room", "https://x/generated-room.png"],
      ["prop-product", "https://x/generated-product.png"],
    ],
  );
});

test("filterMentionCandidates supports Chinese asset group queries", () => {
  const candidates = buildMentionCandidates(globalAssets);

  assert.deepEqual(filterMentionCandidates(candidates, "@角色").map((item) => item.asset_id), ["character-host"]);
  assert.deepEqual(filterMentionCandidates(candidates, "@场景").map((item) => item.asset_id), ["scene-desk"]);
  assert.deepEqual(filterMentionCandidates(candidates, "@道具").map((item) => item.asset_id), ["prop-product"]);
});

test("filterMentionCandidates keeps props reachable after the first eight candidates", () => {
  const candidates = buildMentionCandidates({
    characters: Array.from({ length: 2 }, (_item, index) => ({ asset_id: `character-${index}`, name: `角色${index}` })),
    scenes: Array.from({ length: 6 }, (_item, index) => ({ asset_id: `scene-${index}`, name: `场景${index}` })),
    props: [{ asset_id: "prop-product", name: "商品" }],
  });
  const filtered = filterMentionCandidates(candidates, "@");

  assert.equal(filtered.length, 9);
  assert.equal(filtered.at(-1)?.asset_id, "prop-product");
  assert.notEqual(filtered, candidates);
});

test("normalizeShotMentions migrates legacy reference ids and caps at nine image mentions", () => {
  const manyAssets = {
    characters: Array.from({ length: 11 }, (_item, index) => ({
      asset_id: `character-${index}`,
      name: `角色${index}`,
      images: [`https://x/role-${index}.png`],
    })),
  };

  const mentions = normalizeShotMentions({ text: "镜头描述" }, Array.from({ length: 11 }, (_item, index) => `character-${index}`), manyAssets);

  assert.equal(mentions.length, 9);
  assert.equal(mentions[0].asset_id, "character-0");
  assert.equal(mentions[0].image_url, "https://x/role-0.png");
});

test("normalizeShotMentions refreshes existing mentions from matching generated assets", () => {
  const mentions = normalizeShotMentions(
    {
      mentions: [
        {
          asset_id: "prop-product",
          name: "Product",
          image_url: "https://x/stale-product.png",
        },
      ],
    },
    [],
    {
      props: [
        {
          asset_id: "prop-product",
          name: "Product",
          images: ["https://x/generated-product.png"],
        },
      ],
    },
  );

  assert.deepEqual(mentions, [
    {
      asset_id: "prop-product",
      type: "prop",
      name: "Product",
      image_url: "https://x/generated-product.png",
    },
  ]);
});

test("normalizeShotMentions preserves generation reference metadata from global assets", () => {
  const mentions = normalizeShotMentions(
    {
      mentions: [
        {
          asset_id: "character-host",
          name: "Host",
          image_url: "https://x/stale-role.png",
        },
      ],
    },
    [],
    {
      characters: [
        {
          asset_id: "character-host",
          name: "Host",
          three_view_images: ["https://x/digital-human-cover.png"],
          generation_reference_url: "asset://asset-123",
          third_asset_id: "asset-123",
          replacement_source: "digital_human",
        },
      ],
    },
  );

  assert.deepEqual(mentions, [
    {
      asset_id: "character-host",
      type: "character",
      name: "Host",
      image_url: "https://x/digital-human-cover.png",
      generation_reference_url: "asset://asset-123",
      third_asset_id: "asset-123",
      replacement_source: "digital_human",
    },
  ]);
});

test("upsertShotMention updates shot description text mentions without duplicating images", () => {
  const candidates = buildMentionCandidates(globalAssets);
  const shot = upsertShotMention(
    { text: "地点:@scene-desk 中,角色:@讲解者 展示道具。", mentions: [candidates[1]] },
    candidates[0],
  );

  assert.deepEqual(
    shot.mentions.map((item) => item.asset_id),
    ["scene-desk", "character-host"],
  );
  assert.equal(collectMentionImageUrls(shot.mentions).join(","), "https://x/scene.png,https://x/role.png");
});
