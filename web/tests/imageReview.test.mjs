import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.IMAGE_REVIEW_TEST_MODULE;
assert.ok(moduleUrl, "IMAGE_REVIEW_TEST_MODULE must point to the compiled imageReview module");

const {
  buildImageRevisionPreparePayload,
  canAcceptImageResult,
  imageResultSummary,
} = await import(moduleUrl);

test("canAcceptImageResult requires ok result with at least one image url", () => {
  assert.equal(canAcceptImageResult({ ok: true, images: [{ url: "https://x/a.png" }] }), true);
  assert.equal(canAcceptImageResult({ ok: true, images: [{ download_url: "https://x/a.png" }] }), true);
  assert.equal(canAcceptImageResult({ ok: true, images: [{}] }), false);
  assert.equal(canAcceptImageResult({ ok: false, images: [{ url: "https://x/a.png" }] }), false);
  assert.equal(canAcceptImageResult(undefined), false);
});

test("imageResultSummary reports usable image count or failure message", () => {
  assert.equal(imageResultSummary({ ok: true, images: [{ url: "https://x/a.png" }, {}] }), "1 张图片已返回");
  assert.equal(imageResultSummary({ ok: false, images: [], message: "额度不足" }), "额度不足");
  assert.equal(imageResultSummary({ ok: false, images: [] }), "图片生成失败");
});

test("buildImageRevisionPreparePayload keeps original plan context and trims user feedback", () => {
  const payload = buildImageRevisionPreparePayload({
    formValues: { image_goal: "科技感海报" },
    selectedDirection: { title: "冷启动视觉" },
    planMarkdown: "# plan",
    feedback: "  背景改成白色，产品放大  ",
  });

  assert.deepEqual(payload, {
    form_values: { image_goal: "科技感海报" },
    selected_direction: { title: "冷启动视觉" },
    plan_markdown: "# plan",
    revision_feedback: "背景改成白色，产品放大",
  });
});
