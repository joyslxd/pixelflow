/** Video Agent 资产包生图：模型选择与默认清晰度。 */

export const SCENE_ASSET_PREFERRED_MODELS = ["gpt-image-2", "seeddream-5.0"] as const;

export type SceneAssetPreferredModel = (typeof SCENE_ASSET_PREFERRED_MODELS)[number];

export function sceneAssetModelLabel(model: string): string {
  if (model === "gpt-image-2") return "image-2";
  if (model === "seeddream-5.0") return "Seedream 5.0";
  if (model === "seeddream-4.5") return "Seedream 4.5";
  return model;
}

/** 对齐 Borg DEFAULT_IMAGE_QUALITY_BY_MODEL：gpt→4K，seeddream→2K。 */
export function preferredSceneAssetImageSize(model: string, sizes: string[] = []): string {
  const normalized = sizes.map((item) => String(item || "").trim()).filter(Boolean);
  if (model === "gpt-image-2") {
    return normalized.find((item) => item.toLowerCase() === "4k")
      || normalized.find((item) => item.toLowerCase() === "2k")
      || "4K";
  }
  if (model.startsWith("seeddream")) {
    return normalized.find((item) => item.toLowerCase() === "2k")
      || normalized.find((item) => item.toLowerCase() === "4k")
      || "2K";
  }
  return normalized[0] || "2K";
}
