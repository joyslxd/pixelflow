import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SCENE_MENTIONS_TEST_MODULE;
assert.ok(moduleUrl, "SCENE_MENTIONS_TEST_MODULE must point to the compiled sceneMentions module");

const {
  buildMentionCandidates,
  collectMentionImageUrls,
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
