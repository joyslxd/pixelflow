import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.WORKSPACE_V2_TEST_MODULE;
assert.ok(moduleUrl, "WORKSPACE_V2_TEST_MODULE 必须指向编译后的 Workspace V2 投影模块");
const { generationJobCounts, projectWorkspaceV2 } = await import(moduleUrl);

test("旧 Snapshot 缺失 V2 字段时回退脚本、资产与分镜摘要", () => {
  const projection = projectWorkspaceV2({
    video_ratio: "9:16",
    script_editor_content: "旧脚本",
    character_summaries: [{ asset_id: "actor", name: "主角" }],
    scene_summaries: [{ scene_id: "scene-a", scene_index: 1, title: "开场", state: "idle" }],
  });
  assert.equal(projection.schemaVersion, 1);
  assert.equal(projection.creativeBrief.aspect_ratio, "9:16");
  assert.equal(projection.narrativePlan.script, "旧脚本");
  assert.equal(projection.assets[0].role, "主角");
  assert.equal(projection.packages[0].segmentId, "scene-a");
});

test("V2 长片完整保留 17 段与 GenerationJob 状态，单个失败不覆盖其它状态", () => {
  const projection = projectWorkspaceV2({
    workspace_schema_version: 2,
    creative_brief: { brand: "PixelFlow", target_duration_sec: 368 },
    narrative_plan: { concept: "长片" },
    asset_registry: [{ asset_id: "asset-1", slot: "@图片1", kind: "product", role: "产品", state: "ready", usable_for_video: true, provider_artifact_ref: "artifact:product-1", provider_url: "https://should-not-render.invalid" }],
    prompt_packages: Array.from({ length: 17 }, (_, index) => ({
      segment_id: String.fromCharCode(65 + index), sequence: index + 1, duration_sec: index === 16 ? 16 : 22,
      generation_mode: "reference", prompt: `第 ${index + 1} 段`, state: "planned",
    })),
    generation_jobs: [
      ...Array.from({ length: 6 }, (_, index) => ({ generation_job_id: `a-${index}`, kind: "video", item_id: `scene-a-${index}`, status: "succeeded" })),
      ...Array.from({ length: 6 }, (_, index) => ({ generation_job_id: `b-${index}`, kind: "video", item_id: `scene-b-${index}`, status: index === 0 ? "failed" : "polling" })),
      ...Array.from({ length: 5 }, (_, index) => ({ generation_job_id: `c-${index}`, kind: "video", item_id: `scene-c-${index}`, status: "queued" })),
    ],
  });
  assert.equal(projection.packages.length, 17);
  assert.equal(projection.packages.reduce((total, item) => total + item.durationSec, 0), 368);
  assert.equal(projection.packages[0].promptSummary, "第 1 段");
  assert.equal(projection.packages[0].durationSec, 22);
  assert.deepEqual(generationJobCounts(projection.generationJobs), { queued: 5, polling: 5, succeeded: 6, failed: 1, paused: 0 });
  assert.equal(JSON.stringify(projection).includes("should-not-render"), false);
});
