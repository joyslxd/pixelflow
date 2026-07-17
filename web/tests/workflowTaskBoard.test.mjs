import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const moduleUrl =
  process.env.WORKFLOW_TASK_BOARD_TEST_MODULE ||
  pathToFileURL(path.join(os.tmpdir(), "pixelflow-workflow-task-board-test", "workflowTaskBoard.js")).href;
const { deriveWorkflowTaskBoard, workflowStatusLabel } = await import(moduleUrl);

const progress = (intent, lastPhase, extra = {}) => ({
  version: 1,
  intent,
  flow_kind: "standard",
  source_message_id: "u1",
  last_phase: lastPhase,
  updated_at: "2026-07-16T10:00:00.000Z",
  ...extra,
});

const message = (id, artifact) => ({ id, role: artifact ? "assistant" : "user", artifact });

test("hides the board before a supported intent is known", () => {
  assert.equal(deriveWorkflowTaskBoard({ progress: progress(null, "intake_analyze_running") }), null);
  assert.equal(deriveWorkflowTaskBoard({ lastPhase: "video_analysis_done", messages: [] }), null);
});

test("maps video scene package runtime stages to execution and material generation", () => {
  const execution = deriveWorkflowTaskBoard({
    progress: progress("video", "scene_package_generation_running", { scene_package_stage: "prepare_scene_packages" }),
    messages: [message("u1")],
  });
  assert.equal(execution.currentStep.label, "执行规划");
  assert.equal(execution.currentStep.status, "processing");

  const materials = deriveWorkflowTaskBoard({
    progress: progress("video", "scene_package_generation_running", { scene_package_stage: "generate_scene_assets" }),
    messages: [message("u1")],
  });
  assert.equal(materials.currentStep.label, "素材生成");
  assert.equal(materials.currentStep.status, "processing");
});

test("keeps video delivery pending until the latest final video is downloaded", () => {
  const baseArtifact = {
    type: "video_result",
    intent: "video",
    mergedVideo: { ok: true, merged_video_url: "https://cdn/final.mp4" },
  };
  const pending = deriveWorkflowTaskBoard({
    progress: progress("video", "video_generated"),
    messages: [message("u1"), message("a1", baseArtifact)],
  });
  assert.equal(pending.currentStep.label, "导出交付");
  assert.equal(pending.currentStep.status, "waiting_download");

  const completed = deriveWorkflowTaskBoard({
    progress: progress("video", "video_accepted"),
    messages: [message("u1"), message("a1", { ...baseArtifact, deliveryDownloadedAt: "2026-07-16T10:05:00.000Z" })],
  });
  assert.equal(completed.currentStep.status, "completed");
  assert.ok(completed.steps.every((step) => step.status === "completed"));
});

test("maps PPT summary generation and outline review to separate steps", () => {
  const content = deriveWorkflowTaskBoard({
    progress: progress("ppt", "ppt_outline_running"),
    messages: [message("u1")],
  });
  assert.equal(content.currentStep.label, "内容规划");

  const outline = deriveWorkflowTaskBoard({
    progress: progress("ppt", "ppt_outline_review"),
    messages: [message("u1"), message("a1", { type: "ppt_outline", intent: "ppt", pptSummary: { ok: true } })],
  });
  assert.equal(outline.currentStep.label, "大纲规划");
  assert.equal(outline.currentStep.status, "waiting");
});

test("completes PPT delivery only after the final file message records a download", () => {
  const fileArtifact = {
    type: "ppt_file",
    intent: "ppt",
    pptFile: { ok: true, ppt_url: "https://cdn/report.pptx" },
  };
  const pending = deriveWorkflowTaskBoard({
    progress: progress("ppt", "ppt_file_ready"),
    messages: [message("u1"), message("ppt-file", fileArtifact)],
  });
  assert.equal(pending.currentStep.status, "waiting_download");

  const completed = deriveWorkflowTaskBoard({
    progress: progress("ppt", "ppt_done"),
    messages: [message("u1"), message("ppt-file", { ...fileArtifact, deliveryDownloadedAt: "2026-07-16T10:08:00.000Z" })],
  });
  assert.equal(completed.currentStep.status, "completed");
});

test("marks direct image edit planning steps as skipped", () => {
  const board = deriveWorkflowTaskBoard({
    progress: progress("image", "image_edit_generation_running", { flow_kind: "direct_image_edit" }),
    messages: [message("u1")],
  });
  assert.equal(board.currentStep.label, "图片生成");
  assert.equal(board.steps[1].status, "skipped");
  assert.equal(board.steps[2].status, "skipped");
});

test("downloading any image completes only that latest result", () => {
  const oldResult = {
    type: "image_result",
    intent: "image",
    imageResult: { ok: true, images: [{ url: "https://cdn/old.png" }] },
    deliveryDownloadedAt: "2026-07-16T10:05:00.000Z",
  };
  const newResult = {
    type: "image_result",
    intent: "image",
    imageResult: { ok: true, images: [{ url: "https://cdn/new-1.png" }, { url: "https://cdn/new-2.png" }] },
  };
  const board = deriveWorkflowTaskBoard({
    progress: progress("image", "image_regenerated"),
    messages: [message("u1"), message("old", oldResult), message("new", newResult)],
  });
  assert.equal(board.currentStep.status, "waiting_download");

  const downloaded = deriveWorkflowTaskBoard({
    progress: progress("image", "image_regenerated"),
    messages: [message("u1"), message("old", oldResult), message("new", { ...newResult, deliveryDownloadedAt: "2026-07-16T10:06:00.000Z" })],
  });
  assert.equal(downloaded.currentStep.status, "completed");
});

test("shows paused, failed, and cancelled states without advancing later steps", () => {
  const paused = deriveWorkflowTaskBoard({ progress: progress("image", "image_generation_quota_paused"), messages: [message("u1")] });
  assert.equal(paused.currentStep.status, "paused");
  assert.equal(paused.currentStep.label, "图片生成");

  const failed = deriveWorkflowTaskBoard({ progress: progress("ppt", "ppt_images_failed"), messages: [message("u1")] });
  assert.equal(failed.currentStep.status, "failed");
  assert.equal(failed.currentStep.label, "页面生成");

  const cancelled = deriveWorkflowTaskBoard({ progress: progress("video", "form_cancelled"), messages: [message("u1")] });
  assert.equal(cancelled.currentStep.status, "cancelled");
  assert.equal(cancelled.steps[1].status, "pending");
});

test("scopes artifacts to the current root user message", () => {
  const board = deriveWorkflowTaskBoard({
    progress: { ...progress("image", "intake_form_pending"), source_message_id: "u2" },
    messages: [
      message("u1"),
      message("old-video", { type: "video_result", intent: "video", mergedVideo: { ok: true, merged_video_url: "https://cdn/old.mp4" }, deliveryDownloadedAt: "done" }),
      message("u2"),
    ],
  });
  assert.equal(board.intent, "image");
  assert.equal(board.currentStep.label, "需求收集");
});

test("restores an old conversation from artifacts when its legacy phase is idle", () => {
  const board = deriveWorkflowTaskBoard({
    lastPhase: "idle",
    messages: [
      message("u1"),
      message("result", {
        type: "video_result",
        intent: "video",
        mergedVideo: { ok: true, merged_video_url: "https://cdn/legacy.mp4" },
      }),
    ],
  });
  assert.equal(board.intent, "video");
  assert.equal(board.currentStep.status, "waiting_download");
});

test("exposes the compact status copy used by the collapsed board", () => {
  assert.equal(workflowStatusLabel("processing"), "处理中");
  assert.equal(workflowStatusLabel("waiting_download"), "待下载");
  assert.equal(workflowStatusLabel("failed"), "需处理");
});
