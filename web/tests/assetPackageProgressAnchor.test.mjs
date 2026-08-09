import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.ASSET_PACKAGE_PROGRESS_ANCHOR_TEST_MODULE;
assert.ok(moduleUrl, "ASSET_PACKAGE_PROGRESS_ANCHOR_TEST_MODULE must point to compiled module");

const {
  remapMessageAnchorId,
  resolveAssetPackageProgressAnchorId,
} = await import(moduleUrl);

test("asset package progress prefers notice message after script confirm", () => {
  const messages = [
    { id: "u1", role: "user", content: "帮我做短剧脚本" },
    { id: "a1", role: "assistant", content: "脚本方案已就绪" },
    {
      id: "confirm",
      role: "assistant",
      content: "请确认脚本",
      artifact: { type: "plan", title: "脚本方案待确认", scriptPlanConfirmForAssets: true },
    },
    { id: "notice", role: "assistant", content: "已确认脚本方案，正在生成视频资产包…" },
  ];

  assert.equal(
    resolveAssetPackageProgressAnchorId({ preferredAnchorId: "stale", messages }),
    "notice",
  );
  assert.equal(
    resolveAssetPackageProgressAnchorId({ preferredAnchorId: "notice", messages }),
    "notice",
  );
});

test("asset package progress never falls back to the first user message", () => {
  const messages = [
    { id: "u1", role: "user", content: "帮我做短剧脚本" },
    {
      id: "confirm",
      role: "assistant",
      content: "请确认",
      artifact: { type: "plan", title: "已确认脚本方案" },
    },
  ];
  assert.equal(
    resolveAssetPackageProgressAnchorId({ preferredAnchorId: "", messages }),
    "confirm",
  );
});

test("remapMessageAnchorId rewrites matching values", () => {
  const next = remapMessageAnchorId(
    { planA: "client-1", planB: "keep" },
    "client-1",
    "server-1",
  );
  assert.deepEqual(next, { planA: "server-1", planB: "keep" });
});
