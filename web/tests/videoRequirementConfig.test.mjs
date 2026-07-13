import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.VIDEO_REQUIREMENT_CONFIG_TEST_MODULE;
assert.ok(moduleUrl, "VIDEO_REQUIREMENT_CONFIG_TEST_MODULE must point to the compiled video requirement config module");

const {
  filterSeedanceConfigs,
  hasCompleteVideoModelCapabilities,
  imageModelCapabilities,
  resolveImageModel,
  resolveVideoModel,
  preferredVideoSize,
  videoModelCapabilities,
  videoRatios,
} = await import(moduleUrl);

const VIDEO_CONFIGS = [
  {
    modelType: "kling-3.0",
    isEnabled: true,
    paramConfig: { aspectRatioList: ["16:9"] },
  },
  {
    modelType: "seedance-2.0-mini",
    isEnabled: true,
    paramConfig: {
      sizeList: ["480p", "720p"],
      aspectRatioList: ["9:16", "16:9"],
      onSoundList: ["yes", "no"],
      videoDurationList: ["4", "5", "10", "15"],
      modelGenerateTypeList: ["文生视频", "首尾帧", "全能参考"],
      uploadFileTypeList: ["JPG", "PNG", "MP4"],
    },
  },
  {
    modelType: "seedance-2.0-fast",
    isEnabled: true,
    paramConfig: {
      sizeList: ["480p", "720p"],
      aspectRatioList: ["9:16", "16:9", "1:1"],
      onSoundList: ["yes", "no"],
      videoDurationList: ["4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"],
      modelGenerateTypeList: ["文生视频", "首尾帧", "全能参考"],
      uploadFileTypeList: ["JPG", "PNG", "MP4"],
    },
  },
  {
    modelType: "seedance-2.0",
    isEnabled: true,
    paramConfig: { aspectRatioList: ["9:16", "16:9", "1:1"] },
  },
  {
    modelType: "seedance-disabled",
    isEnabled: false,
    paramConfig: { aspectRatioList: ["1:1"] },
  },
];

const IMAGE_CONFIGS = [
  {
    modelType: "seeddream-5.0",
    isEnabled: true,
    paramConfig: { aspectRatioList: ["1:1", "9:16"], sizeList: ["2K", "4K"] },
  },
  {
    modelType: "gpt-image-2",
    isEnabled: true,
    paramConfig: { aspectRatioList: ["1:1", "16:9", "9:16"], sizeList: ["1080p", "2K", "4K"] },
  },
];

test("filters non-Seedance and disabled video models", () => {
  const filtered = filterSeedanceConfigs(VIDEO_CONFIGS);

  assert.deepEqual(filtered.map((item) => item.modelType), ["seedance-2.0-mini", "seedance-2.0-fast", "seedance-2.0"]);
});

test("submits selected Seedance generation capabilities for backend endpoint routing", () => {
  const selected = resolveVideoModel(VIDEO_CONFIGS, "seedance-2.0-mini");

  assert.deepEqual(videoModelCapabilities(selected), {
    generation_types: ["文生视频", "首尾帧", "全能参考"],
    upload_file_types: ["JPG", "PNG", "MP4"],
    aspect_ratios: ["9:16", "16:9"],
    sizes: ["480p", "720p"],
    sound_options: ["on", "off"],
    durations_sec: [4, 5, 10, 15],
  });
});

test("mini and fast replace an unsupported 1080p default with their highest realtime size", () => {
  const mini = resolveVideoModel(VIDEO_CONFIGS, "seedance-2.0-mini");
  const fast = resolveVideoModel(VIDEO_CONFIGS, "seedance-2.0-fast");

  assert.equal(preferredVideoSize(mini, "1080p"), "720p");
  assert.equal(preferredVideoSize(fast, "1080p"), "720p");
});

test("complete realtime video capabilities require routing, ratio, size and duration while sound may be unsupported", () => {
  const complete = videoModelCapabilities(resolveVideoModel(VIDEO_CONFIGS, "seedance-2.0-mini"));
  const missingSize = { ...complete, sizes: [] };
  const missingSound = { ...complete, sound_options: [] };
  const missingDuration = { ...complete, durations_sec: [] };

  assert.equal(hasCompleteVideoModelCapabilities(complete), true);
  assert.equal(hasCompleteVideoModelCapabilities(missingSize), false);
  assert.equal(hasCompleteVideoModelCapabilities(missingSound), true);
  assert.equal(hasCompleteVideoModelCapabilities(missingDuration), false);
});

test("missing realtime model capabilities stay unknown instead of being invented", () => {
  const missingCapabilities = {
    modelType: "seedance-future",
    isEnabled: true,
    paramConfig: { aspectRatioList: ["9:16"] },
  };

  assert.deepEqual(videoModelCapabilities(missingCapabilities), {
    generation_types: [],
    upload_file_types: [],
    aspect_ratios: ["9:16"],
    sizes: [],
    sound_options: [],
    durations_sec: [],
  });
  assert.deepEqual(videoModelCapabilities(resolveVideoModel([], "")), {
    generation_types: [],
    upload_file_types: [],
    aspect_ratios: ["9:16", "16:9", "1:1"],
    sizes: ["1080p"],
    sound_options: ["on", "off"],
    durations_sec: [4, 5, 10, 15],
  });
});

test("defaults video model to seedance-2.0 and preserves a supported requested model", () => {
  const filtered = filterSeedanceConfigs(VIDEO_CONFIGS);

  assert.equal(resolveVideoModel(filtered, "").modelType, "seedance-2.0");
  assert.equal(resolveVideoModel(filtered, "seedance-2.0-mini").modelType, "seedance-2.0-mini");
  assert.deepEqual(videoRatios(resolveVideoModel(filtered, "seedance-2.0")), ["9:16", "16:9", "1:1"]);
});

test("defaults image model to gpt-image-2 and submits capabilities without visible image spec fields", () => {
  const selected = resolveImageModel(IMAGE_CONFIGS, "");
  const capabilities = imageModelCapabilities(selected);

  assert.equal(selected.modelType, "gpt-image-2");
  assert.deepEqual(capabilities, {
    aspect_ratios: ["1:1", "16:9", "9:16"],
    sizes: ["1080p", "2K", "4K"],
  });
  assert.equal("image_ratio" in capabilities, false);
  assert.equal("image_quality" in capabilities, false);
});
