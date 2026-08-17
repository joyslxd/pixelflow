import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.SHOT_DESCRIPTION_DISPLAY_TEST_MODULE;
assert.ok(moduleUrl, "SHOT_DESCRIPTION_DISPLAY_TEST_MODULE must point to compiled module");

const {
  composeShotDescriptionFields,
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

test("parses episode six-field shot description for storyboard table", () => {
  const fields = parseShotDescriptionFields(
    [
      "0-10秒: 景别：近景",
      "运镜：缓推",
      "画面：安然盯着手机",
      "旁白（对白）：如果失败呢？",
      "屏幕文案：倒计时 40:00",
      "行动引导：无",
    ].join("\n"),
  );
  assert.equal(fields.find((field) => field.label === "时间")?.value, "0-10秒");
  assert.equal(fields.find((field) => field.label === "景别")?.value, "近景");
  assert.equal(fields.find((field) => field.label === "运镜")?.value, "缓推");
  assert.equal(fields.find((field) => field.label === "画面")?.value, "安然盯着手机");
  assert.equal(fields.find((field) => field.label === "旁白（对白）")?.value, "如果失败呢？");
  assert.equal(fields.find((field) => field.label === "屏幕文案")?.value, "倒计时 40:00");
  assert.equal(fields.find((field) => field.label === "行动引导")?.value, "无");
});

test("normalizes traditional narration aliases to 旁白（对白）", () => {
  const fields = parseShotDescriptionFields(
    ["景别：近景", "画面：安然盯着手机", "旁白/對白：安然：「如果失敗呢？」"].join("\n"),
  );
  assert.equal(fields.find((field) => field.label === "旁白（对白）")?.value, "安然：「如果失敗呢？」");
  assert.equal(
    fields.some((field) => field.label === "旁白/對白"),
    false,
  );
  const composed = composeShotDescriptionFields(fields);
  assert.match(composed, /旁白（对白）：安然：「如果失敗呢？」/);
});

test("composeShotDescriptionFields round-trips episode six fields", () => {
  const source = [
    "0-10秒: 景别：近景",
    "运镜：缓推",
    "画面：回到@办公室梳妆台",
    "旁白（对白）：安然：“今天氧气了。”",
    "屏幕文案：倒计时 40:00",
    "行动引导：无",
  ].join("\n");
  const fields = parseShotDescriptionFields(source);
  const composed = composeShotDescriptionFields(fields);
  const again = parseShotDescriptionFields(composed);
  assert.deepEqual(
    again.map((field) => ({ label: field.label, value: field.value })),
    fields.map((field) => ({ label: field.label, value: field.value })),
  );
  const patched = composeShotDescriptionFields(
    fields.map((field) => (field.label === "画面" ? { ...field, value: "回到@安然" } : field)),
  );
  assert.match(patched, /画面：回到@安然/);
  assert.match(patched, /旁白（对白）：安然：“今天氧气了。”/);
});

test("live compose keeps empty fields and internal spaces", () => {
  const fields = [
    { label: "时间", value: "0-10秒" },
    { label: "画面", value: "安然  盯着 手机" },
    { label: "屏幕文案", value: "" },
    { label: "行动引导", value: "无" },
  ];
  const live = composeShotDescriptionFields(fields, { mode: "live" });
  assert.match(live, /画面：安然  盯着 手机/);
  assert.match(live, /屏幕文案：\n|屏幕文案：$|屏幕文案：$/m);
  assert.match(live, /屏幕文案：/);
  assert.match(live, /行动引导：无/);
  const persist = composeShotDescriptionFields(fields, { mode: "persist" });
  assert.doesNotMatch(persist, /屏幕文案：/);
  assert.match(persist, /画面：安然 盯着 手机/);
});
