import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SHOT_DESCRIPTION_DISPLAY_TEST_MODULE;
assert.ok(moduleUrl, "SHOT_DESCRIPTION_DISPLAY_TEST_MODULE must point to compiled module");

const {
  parseShotDescriptionFields,
  shotDescriptionHasStructuredFields,
} = await import(moduleUrl);

test("parses labeled shot description into table fields", () => {
  const fields = parseShotDescriptionFields(
    "0-6秒：地点：地铁口；主体：通勤者；动作：抬起背包；景别：中景；运镜：缓慢推进；光影：清晨逆光；声音：雨声；收束：定格品牌标识。",
  );
  assert.deepEqual(fields, [
    { label: "时间", value: "0-6秒" },
    { label: "地点", value: "地铁口" },
    { label: "主体", value: "通勤者" },
    { label: "动作", value: "抬起背包" },
    { label: "景别", value: "中景" },
    { label: "运镜", value: "缓慢推进" },
    { label: "光影", value: "清晨逆光" },
    { label: "声音", value: "雨声" },
    { label: "收束", value: "定格品牌标识" },
  ]);
  assert.equal(
    shotDescriptionHasStructuredFields(
      "0-6秒：地点：地铁口；主体：通勤者；动作：抬起背包；景别：中景；运镜：缓慢推进；光影：清晨逆光；声音：雨声；收束：定格品牌标识。",
    ),
    true,
  );
});

test("parses default scene package shot text with mentions", () => {
  const fields = parseShotDescriptionFields(
    "0-8秒: 地点:@scene-desk 中,角色:@character-presenter 展示商品,道具:@prop-product 清晰可见。景别:中景。视觉风格:@style-cinematic。",
  );
  assert.equal(fields[0]?.label, "时间");
  assert.equal(fields[0]?.value, "0-8秒");
  assert.equal(fields.find((field) => field.label === "地点")?.value.includes("@scene-desk"), true);
  assert.equal(fields.find((field) => field.label === "角色")?.value.includes("@character-presenter"), true);
  assert.equal(fields.find((field) => field.label === "道具")?.value.includes("@prop-product"), true);
  assert.equal(fields.find((field) => field.label === "景别")?.value, "中景");
  assert.equal(fields.find((field) => field.label === "视觉风格")?.value, "@style-cinematic");
});

test("falls back to single description field for free text", () => {
  const fields = parseShotDescriptionFields("镜头缓缓推进，产品细节逐渐清晰。");
  assert.deepEqual(fields, [{ label: "描述", value: "镜头缓缓推进，产品细节逐渐清晰" }]);
  assert.equal(shotDescriptionHasStructuredFields("镜头缓缓推进，产品细节逐渐清晰。"), false);
});
