type ScenePackageLike = {
  global_assets?: unknown;
  scene_packages?: Array<{ image_urls?: unknown }>;
};

type ScenePackageArtifact = {
  type?: string;
  videoScenePackages?: ScenePackageLike | null;
  sceneAssetsGenerating?: boolean;
  sceneAssetsAwaitingModel?: boolean;
  sceneAssetFailures?: Array<Record<string, unknown>>;
  sceneAssetModelConfirmed?: boolean;
};

type ScenePackageMessage = {
  id: string;
  artifact?: ScenePackageArtifact | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

/** 场景包是否已有任意参考图（用于区分“仅结构”与“生图完成”）。 */
export function scenePackageHasGeneratedImages(
  packages: ScenePackageLike | null | undefined,
): boolean {
  if (!packages) return false;
  const globalAssets = asRecord(packages.global_assets) || {};
  for (const key of ["characters", "scenes", "props"] as const) {
    const list = globalAssets[key];
    if (!Array.isArray(list)) continue;
    for (const item of list) {
      const record = asRecord(item);
      if (!record) continue;
      if (stringArray(record.images).length > 0 || stringArray(record.three_view_images).length > 0) {
        return true;
      }
    }
  }
  return (packages.scene_packages || []).some((scene) => stringArray(scene.image_urls).length > 0);
}

export type ScenePackagePendingKind =
  | "scene_package_generation"
  | "scene_asset_generation"
  | "scene_asset_revision"
  | string;

/**
 * scene_asset_generation 完成判定：必须已有参考图，且卡片不在 generating。
 * 仅有结构卡（early card）不算完成，否则刷新会误清 pending 并留下假 spinner。
 */
export function isSceneAssetGenerationMaterialized(
  messages: ScenePackageMessage[],
  jobKind: ScenePackagePendingKind,
): boolean {
  if (jobKind !== "scene_asset_generation") return false;
  return messages.some((message) => {
    const artifact = message.artifact;
    if (artifact?.type !== "video_scene_packages" || !artifact.videoScenePackages) return false;
    if (artifact.sceneAssetsGenerating) return false;
    return scenePackageHasGeneratedImages(artifact.videoScenePackages);
  });
}

/**
 * 刷新后若没有活跃 pending，清掉过期的 sceneAssetsGenerating，避免“参考图生成中”假忙碌。
 * 无图时回到 awaiting model，便于用户重新确认模型继续。
 */
export function reconcileStaleSceneAssetUiFlags<T extends ScenePackageMessage>(
  messages: T[],
  options: {
    hasActiveAssetJob: boolean;
  },
): T[] {
  if (options.hasActiveAssetJob) return messages;
  let changed = false;
  const next = messages.map((message) => {
    const artifact = message.artifact;
    if (!artifact) return message;
    if (artifact.type === "scene_asset_model_options" && artifact.sceneAssetModelConfirmed) {
      changed = true;
      return {
        ...message,
        artifact: {
          ...artifact,
          sceneAssetModelConfirmed: false,
        },
      };
    }
    if (artifact.type !== "video_scene_packages" || !artifact.sceneAssetsGenerating) return message;
    const hasImages = scenePackageHasGeneratedImages(artifact.videoScenePackages);
    changed = true;
    return {
      ...message,
      artifact: {
        ...artifact,
        sceneAssetsGenerating: false,
        sceneAssetsAwaitingModel: !hasImages,
      },
    };
  });
  return changed ? next : messages;
}
