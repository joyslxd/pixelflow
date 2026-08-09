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
  aspect_ratios: string[];
  sizes: string[];
  sound_options: Array<"on" | "off">;
  durations_sec: number[];
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
    // 与 Borg Skill DEFAULT_IMAGE_QUALITY_BY_MODEL['gpt-image-2']=4K 对齐；
    // content-app 对 gpt-image-2 + 1080p 常无价格配置。
    sizeList: ["4K", "2K", "1080p"],
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
    aspect_ratios: normalizedOptions(config.paramConfig?.aspectRatioList, []),
    sizes: normalizedOptions(config.paramConfig?.sizeList, []),
    sound_options: normalizedSoundOptions(config.paramConfig?.onSoundList),
    durations_sec: normalizedDurations(config.paramConfig?.videoDurationList),
  };
}

export function preferredVideoSize(config: GenerateModelParamConfig, current = ""): string {
  const sizes = videoModelCapabilities(config).sizes;
  const selected = supportedOption(current, sizes);
  if (selected) return selected;
  for (const preferred of ["1080p", "720p", "480p"]) {
    const supported = supportedOption(preferred, sizes);
    if (supported) return supported;
  }
  return sizes[0] || current.trim() || "720p";
}

export function preferredVideoSound(config: GenerateModelParamConfig, current: "on" | "off" = "on"): "on" | "off" {
  const options = videoModelCapabilities(config).sound_options;
  return options.includes(current) ? current : (options[0] || current);
}

export function hasCompleteVideoModelCapabilities(capabilities: VideoModelCapabilities): boolean {
  return capabilities.generation_types.length > 0
    && capabilities.aspect_ratios.length > 0
    && capabilities.sizes.length > 0
    && capabilities.durations_sec.length > 0;
}

export function imageModelCapabilities(config: GenerateModelParamConfig): ImageModelCapabilities {
  return {
    aspect_ratios: normalizedOptions(
      config.paramConfig?.aspectRatioList,
      FALLBACK_IMAGE_MODEL_CONFIG.paramConfig?.aspectRatioList || ["1:1", "16:9", "9:16"],
    ),
    sizes: normalizedOptions(
      config.paramConfig?.sizeList,
      FALLBACK_IMAGE_MODEL_CONFIG.paramConfig?.sizeList || ["4K", "2K", "1080p"],
    ),
  };
}

/** 场景资产生图清晰度：优先 4K（gpt-image-2 Borg 默认），再 2K，最后才回落 1080p。 */
export function preferredImageSize(sizes: string[], current = ""): string {
  const selected = supportedOption(current, sizes);
  if (selected && selected.toLowerCase() !== "1080p") return selected;
  // 当前值是 1080p 时仍优先升级到模型支持的 4K/2K，避免计费配置缺失。
  for (const preferred of ["4K", "2K", "1080p"]) {
    const supported = supportedOption(preferred, sizes);
    if (supported) return supported;
  }
  return sizes[0] || current.trim() || "4K";
}

export function preferredImageRatio(ratios: string[], current = "9:16"): string {
  return supportedOption(current, ratios) || supportedOption("9:16", ratios) || ratios[0] || current || "9:16";
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

function normalizedSoundOptions(values: string[] | undefined): Array<"on" | "off"> {
  const result: Array<"on" | "off"> = [];
  for (const value of values || []) {
    const normalized = String(value || "").trim().toLowerCase();
    let option: "on" | "off" | null = null;
    if (["on", "yes", "true", "1", "开启", "有声"].includes(normalized)) option = "on";
    if (["off", "no", "false", "0", "关闭", "静音"].includes(normalized)) option = "off";
    if (option && !result.includes(option)) result.push(option);
  }
  return result;
}

function normalizedDurations(values: string[] | undefined): number[] {
  const result: number[] = [];
  for (const value of values || []) {
    const duration = Number(value);
    if (Number.isInteger(duration) && duration > 0 && !result.includes(duration)) result.push(duration);
  }
  return result;
}

function supportedOption(value: string, options: string[]): string | undefined {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return undefined;
  return options.find((option) => option.toLowerCase() === normalized);
}
