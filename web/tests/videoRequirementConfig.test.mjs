import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = process.env.VIDEO_REQUIREMENT_CONFIG_TEST_MODULE;
assert.ok(moduleUrl, "VIDEO_REQUIREMENT_CONFIG_TEST_MODULE must point to the compiled video requirement config module");

const {
  filterSeedanceConfigs,
  imageModelCapabilities,
  resolveImageModel,
  resolveVideoModel,
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
      aspectRatioList: ["9:16", "16:9"],
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

  assert.deepEqual(filtered.map((item) => item.modelType), ["seedance-2.0-mini", "seedance-2.0"]);
});

test("submits selected Seedance generation capabilities for backend endpoint routing", () => {
  const selected = resolveVideoModel(VIDEO_CONFIGS, "seedance-2.0-mini");

  assert.deepEqual(videoModelCapabilities(selected), {
    generation_types: ["文生视频", "首尾帧", "全能参考"],
    upload_file_types: ["JPG", "PNG", "MP4"],
  });
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
  });
  assert.deepEqual(videoModelCapabilities(resolveVideoModel([], "")), {
    generation_types: [],
    upload_file_types: [],
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
