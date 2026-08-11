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

/** 服务端幂等结果卡 client_message_id：media-result:{kind}:{job_id} */
export function mediaResultClientMessageId(kind: string, jobId: string): string {
  return `media-result:${kind}:${jobId}`;
}

/**
 * 恢复时优先选「已有参考图」的场景包卡，避免无图 early/generating 卡挡住 context 里的有图快照。
 * 返回 messages 正序下标；没有场景包卡时返回 -1。
 */
export function preferredVideoScenePackagesMessageIndex(
  messages: ScenePackageMessage[],
): number {
  const withImagesFromEnd = [...messages]
    .reverse()
    .findIndex((message) => {
      const artifact = message.artifact;
      return (
        artifact?.type === "video_scene_packages"
        && Boolean(artifact.videoScenePackages)
        && scenePackageHasGeneratedImages(artifact.videoScenePackages)
      );
    });
  if (withImagesFromEnd >= 0) return messages.length - 1 - withImagesFromEnd;
  const anyFromEnd = [...messages]
    .reverse()
    .findIndex((message) => (
      message.artifact?.type === "video_scene_packages"
      && Boolean(message.artifact.videoScenePackages)
    ));
  if (anyFromEnd < 0) return -1;
  return messages.length - 1 - anyFromEnd;
}

/**
 * context 有图而当前卡无图时，采用 context 快照；否则保留消息卡。
 */
export function resolveVideoScenePackagesForRestore<T extends ScenePackageLike>(
  messagePackages: T | null | undefined,
  contextPackages: T | null | undefined,
): T | null | undefined {
  if (
    contextPackages
    && scenePackageHasGeneratedImages(contextPackages)
    && !scenePackageHasGeneratedImages(messagePackages)
  ) {
    return contextPackages;
  }
  return messagePackages ?? contextPackages;
}

/**
 * Poll 完成后：本地已有同 job 的 media-result 卡则应跳过再次 pushArtifact。
 */
export function hasMediaResultMessage(
  messages: Array<{ id: string }>,
  kind: string,
  jobId: string,
): boolean {
  const expectedId = mediaResultClientMessageId(kind, jobId);
  return messages.some((message) => message.id === expectedId);
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
 * 不改写历史模型选择卡的 confirmed 状态——需要再选模型时应新推一张卡，而不是原地翻旧卡。
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

/**
 * 已开始/完成参考图生成时，历史「选择生图模型」卡应保持已确认，避免刷新后重新可点。
 * 证据：生成中卡、已有参考图、或进度归档卡。
 */
export function markConfirmedSceneAssetModelOptions<T extends {
  id: string;
  artifact?: (ScenePackageArtifact & {
    type?: string;
    sceneAssetModelConfirmed?: boolean;
    sceneAssetProgressArchived?: boolean;
  }) | null;
}>(messages: T[]): T[] {
  const hasGenerationEvidence = messages.some((message) => {
    const artifact = message.artifact;
    if (!artifact || artifact.type !== "video_scene_packages") return false;
    if (artifact.sceneAssetsGenerating || artifact.sceneAssetProgressArchived) return true;
    return scenePackageHasGeneratedImages(artifact.videoScenePackages);
  });
  if (!hasGenerationEvidence) return messages;
  let changed = false;
  const next = messages.map((message) => {
    const artifact = message.artifact;
    if (!artifact || artifact.type !== "scene_asset_model_options" || artifact.sceneAssetModelConfirmed) {
      return message;
    }
    changed = true;
    return {
      ...message,
      artifact: {
        ...artifact,
        sceneAssetModelConfirmed: true,
      },
    };
  });
  return changed ? next : messages;
}
