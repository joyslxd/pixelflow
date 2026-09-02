import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.WORKSPACE_V2_TEST_MODULE;
assert.ok(moduleUrl, "WORKSPACE_V2_TEST_MODULE 必须指向编译后的 Workspace V2 投影模块");
const { generationJobCounts, generationProgressText, projectWorkspaceV2, workspaceHasInFlightGeneration } = await import(moduleUrl);

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
  assert.equal(projection.packages[0].hasPreview, false);
  assert.equal(projection.packages[0].previewUrl, "");
  assert.deepEqual(generationJobCounts(projection.generationJobs), { queued: 5, polling: 5, succeeded: 6, failed: 1, paused: 0 });
  assert.equal(JSON.stringify(projection).includes("should-not-render"), false);
});

test("planned 资产带 generation_job_id 时投影为生成中，并生成看板进度", () => {
  const summary = {
    workspace_schema_version: 2,
    asset_registry: [
      {
        asset_id: "asset_character_01",
        slot: "@女主人",
        kind: "character",
        role: "女主设定图",
        origin: "planned_generation",
        state: "planned",
        generation_job_id: "generation-job-character",
        generation_job_status: "queued",
      },
      {
        asset_id: "asset_scene_01",
        slot: "@厨房",
        kind: "scene",
        role: "厨房场景图",
        origin: "planned_generation",
        state: "failed",
        generation_job_id: "generation-job-scene",
        generation_job_status: "queued",
        failure_reason_code: "provider_start_provider_response_not_json",
      },
    ],
  };
  const projection = projectWorkspaceV2(summary);
  assert.equal(projection.assets[0].state, "generating");
  assert.equal(projection.assets[0].generationStatus, "queued");
  assert.equal(projection.assets[1].state, "failed");
  assert.equal(projection.generationJobs.length, 2);
  assert.deepEqual(generationJobCounts(projection.generationJobs), { queued: 1, polling: 0, succeeded: 0, failed: 1, paused: 0 });
  assert.equal(workspaceHasInFlightGeneration(summary), true);
  assert.match(generationProgressText(summary), /正在生成 @女主人/);
});

test("成片就绪的 Prompt Package 投影白名单 TOS 地址，拒绝其它外链", () => {
  const v2 = projectWorkspaceV2({
    workspace_schema_version: 2,
    prompt_packages: [{
      segment_id: "s1", sequence: 1, duration_sec: 12, generation_mode: "independent",
      prompt_summary: "厨房开场", state: "ready", has_preview: true,
      preview_url: "https://bucket.tos-cn-beijing.volces.com/s1.mp4",
      video_url: "https://should-not-render.invalid/s1.mp4",
    }],
  });
  assert.equal(v2.packages[0].hasPreview, true);
  assert.equal(v2.packages[0].previewUrl, "https://bucket.tos-cn-beijing.volces.com/s1.mp4");
  assert.equal(JSON.stringify(v2).includes("should-not-render"), false);

  const blocked = projectWorkspaceV2({
    workspace_schema_version: 2,
    prompt_packages: [{
      segment_id: "s1", sequence: 1, duration_sec: 12, generation_mode: "independent",
      prompt_summary: "厨房开场", state: "ready", has_preview: true,
      preview_url: "https://should-not-render.invalid/s1.mp4",
    }],
  });
  assert.equal(blocked.packages[0].hasPreview, false);
  assert.equal(blocked.packages[0].previewUrl, "");
});

test("合并成片只回显白名单 TOS 地址", () => {
  const ready = projectWorkspaceV2({
    merged_video: {
      ok: true,
      preview_url: "https://bucket.tos-cn-beijing.volces.com/merged.mp4",
    },
  });
  assert.equal(ready.mergedReady, true);
  assert.equal(ready.mergedPreviewUrl, "https://bucket.tos-cn-beijing.volces.com/merged.mp4");

  const blocked = projectWorkspaceV2({
    merged_video: {
      ok: true,
      preview_url: "https://should-not-render.invalid/merged.mp4",
    },
  });
  assert.equal(blocked.mergedReady, true);
  assert.equal(blocked.mergedPreviewUrl, "");
  assert.equal(JSON.stringify(blocked).includes("should-not-render"), false);
});

test("分镜视频排队时看板不能被已完成参考图写成生成完成", () => {
  const summary = {
    workspace_schema_version: 2,
    scene_videos_polling_count: 2,
    asset_registry: [
      {
        asset_id: "asset_character_01",
        slot: "@女主人",
        kind: "character",
        role: "女主设定图",
        origin: "planned_generation",
        state: "ready",
        generation_job_id: "generation-job-character",
        generation_job_status: "succeeded",
      },
      {
        asset_id: "asset_product_01",
        slot: "@产品",
        kind: "product",
        role: "产品图",
        origin: "planned_generation",
        state: "ready",
        generation_job_id: "generation-job-product",
        generation_job_status: "succeeded",
      },
    ],
    generation_jobs: [
      { generation_job_id: "generation-job-scene-1", kind: "video", item_id: "scene_01", status: "queued" },
      { generation_job_id: "generation-job-scene-2", kind: "video", item_id: "scene_02", status: "queued" },
    ],
    prompt_packages: [
      { segment_id: "scene_01", sequence: 1, duration_sec: 12, generation_mode: "independent", prompt_summary: "痛点", state: "generating" },
      { segment_id: "scene_02", sequence: 2, duration_sec: 12, generation_mode: "independent", prompt_summary: "能力", state: "generating" },
    ],
  };
  assert.equal(workspaceHasInFlightGeneration(summary), true);
  assert.match(generationProgressText(summary), /正在生成 2 个分镜视频/);
  assert.equal(generationProgressText(summary).includes("生成完成"), false);
});
