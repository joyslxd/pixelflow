export interface GenerateModelParamConfig {
  modelType: string;
  modelCategoryType?: string;
  paramConfig?: {
    sizeList?: string[];
    aspectRatioList?: string[];
    onSoundList?: string[];
    videoDurationList?: string[];
    imageNumList?: string[];
    modelGenerateTypeList?: string[];
    uploadFileTypeList?: string[];
  };
  isEnabled?: boolean;
}

export interface ImageModelCapabilities {
  aspect_ratios: string[];
  sizes: string[];
}

export interface VideoModelCapabilities {
  generation_types: string[];
  upload_file_types: string[];
}

export const FALLBACK_VIDEO_MODEL_CONFIG: GenerateModelParamConfig = {
  modelType: "seedance-2.0",
  modelCategoryType: "video_generate",
  paramConfig: {
    sizeList: ["1080p"],
    aspectRatioList: ["9:16", "16:9", "1:1"],
    onSoundList: ["on", "off"],
    videoDurationList: ["4", "5", "10", "15"],
  },
  isEnabled: true,
};

export const FALLBACK_IMAGE_MODEL_CONFIG: GenerateModelParamConfig = {
  modelType: "gpt-image-2",
  modelCategoryType: "image_generate",
  paramConfig: {
    sizeList: ["1080p", "2K", "4K"],
    aspectRatioList: ["1:1", "16:9", "9:16"],
  },
  isEnabled: true,
};

export function filterSeedanceConfigs(configs: GenerateModelParamConfig[]): GenerateModelParamConfig[] {
  return enabledConfigs(configs).filter((config) => normalizedModelType(config).includes("seedance"));
}

export function resolveVideoModel(configs: GenerateModelParamConfig[], requested: string): GenerateModelParamConfig {
  const available = filterSeedanceConfigs(configs);
  return (
    findModel(available, requested)
    || findModel(available, FALLBACK_VIDEO_MODEL_CONFIG.modelType)
    || available[0]
    || FALLBACK_VIDEO_MODEL_CONFIG
  );
}

export function resolveImageModel(configs: GenerateModelParamConfig[], requested: string): GenerateModelParamConfig {
  const available = enabledConfigs(configs);
  return (
    findModel(available, requested)
    || findModel(available, FALLBACK_IMAGE_MODEL_CONFIG.modelType)
    || available[0]
    || FALLBACK_IMAGE_MODEL_CONFIG
  );
}

export function videoRatios(config: GenerateModelParamConfig): string[] {
  return normalizedOptions(config.paramConfig?.aspectRatioList, FALLBACK_VIDEO_MODEL_CONFIG.paramConfig?.aspectRatioList || ["9:16"]);
}

export function videoModelCapabilities(config: GenerateModelParamConfig): VideoModelCapabilities {
  return {
    generation_types: normalizedOptions(config.paramConfig?.modelGenerateTypeList, []),
    upload_file_types: normalizedOptions(config.paramConfig?.uploadFileTypeList, []),
  };
}

export function imageModelCapabilities(config: GenerateModelParamConfig): ImageModelCapabilities {
  return {
    aspect_ratios: normalizedOptions(
      config.paramConfig?.aspectRatioList,
      FALLBACK_IMAGE_MODEL_CONFIG.paramConfig?.aspectRatioList || ["1:1", "16:9", "9:16"],
    ),
    sizes: normalizedOptions(config.paramConfig?.sizeList, FALLBACK_IMAGE_MODEL_CONFIG.paramConfig?.sizeList || ["1080p", "2K", "4K"]),
  };
}

function enabledConfigs(configs: GenerateModelParamConfig[]): GenerateModelParamConfig[] {
  return configs.filter((config) => Boolean(normalizedModelType(config)) && config.isEnabled !== false);
}

function findModel(configs: GenerateModelParamConfig[], requested: string): GenerateModelParamConfig | undefined {
  const normalized = requested.trim().toLowerCase();
  if (!normalized) return undefined;
  return configs.find((config) => normalizedModelType(config) === normalized);
}

function normalizedModelType(config: GenerateModelParamConfig): string {
  return String(config?.modelType || "").trim().toLowerCase();
}

function normalizedOptions(values: string[] | undefined, fallback: string[]): string[] {
  const result: string[] = [];
  for (const value of values || []) {
    const normalized = String(value || "").trim();
    if (normalized && !result.includes(normalized)) result.push(normalized);
  }
  return result.length > 0 ? result : [...fallback];
}
