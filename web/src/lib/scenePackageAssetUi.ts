type ScenePackageLike = {
  global_assets?: unknown;
  scene_packages?: Array<Record<string, unknown> & { image_urls?: unknown }>;
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

function hasImageReference(value: unknown): boolean {
  if (typeof value === "string") {
    return value.startsWith("http://") || value.startsWith("https://") || value.startsWith("asset://");
  }
  if (Array.isArray(value)) return value.some(hasImageReference);
  const record = asRecord(value);
  if (!record) return false;
  return hasImageReference(record.url) || hasImageReference(record.image_url);
}

/** 权威场景包内容指纹；同数量镜头的文字/状态变更也必须触发 UI 投影。 */
export function scenePackageContentSignature(
  packages: ScenePackageLike | null | undefined,
): string {
  return JSON.stringify((packages?.scene_packages || []).map((scene) => ({
    scene_id: scene.scene_id,
    title: scene.title,
    storyline: scene.storyline,
    shot_description: scene.shot_description,
    prompt: scene.prompt,
    narration: scene.narration,
    transition: scene.transition,
    duration_ms: scene.duration_ms,
    reference_asset_ids: scene.reference_asset_ids,
    edit_status: scene.edit_status,
    regenerated_at: scene.regenerated_at,
  })));
}

export type ScenePackageAssetSummary = {
  status: "empty" | "partial" | "ready";
  requiredCount: number;
  readyCount: number;
  missingCount: number;
  complete: boolean;
  missingTargets: Array<{ asset_id: string; asset_type: string }>;
};

/** 场景包参考图完整度；生成视频必须使用 complete，不能使用“任意一张图”。 */
export function scenePackageAssetSummary(
  packages: ScenePackageLike | null | undefined,
): ScenePackageAssetSummary {
  if (!packages) {
    return {
      status: "empty",
      requiredCount: 0,
      readyCount: 0,
      missingCount: 0,
      complete: false,
      missingTargets: [],
    };
  }
  const globalAssets = asRecord(packages.global_assets) || {};
  const assetTypes: Record<string, string> = {
    characters: "character",
    scenes: "scene_image",
    props: "prop_image",
  };
  let requiredCount = 0;
  let readyCount = 0;
  const missingTargets: Array<{ asset_id: string; asset_type: string }> = [];
  for (const [key, assetType] of Object.entries(assetTypes)) {
    const list = globalAssets[key];
    if (!Array.isArray(list)) continue;
    for (const item of list) {
      const record = asRecord(item);
      if (!record) continue;
      requiredCount += 1;
      const ready = hasImageReference(record.images)
        || hasImageReference(record.three_view_images)
        || hasImageReference(record.image_url)
        || hasImageReference(record.url);
      if (ready) {
        readyCount += 1;
        continue;
      }
      const assetId = String(record.asset_id || record.id || "").trim();
      if (assetId) missingTargets.push({ asset_id: assetId, asset_type: assetType });
    }
  }
  // 兼容只有 scene_packages.image_urls 的旧场景包。
  if (requiredCount === 0 && packages.scene_packages?.length) {
    requiredCount = packages.scene_packages.length;
    readyCount = packages.scene_packages.filter((scene) => hasImageReference(scene.image_urls)).length;
  }
  const missingCount = Math.max(0, requiredCount - readyCount);
  const complete = requiredCount > 0 && missingCount === 0;
  return {
    status: complete ? "ready" : (readyCount > 0 ? "partial" : "empty"),
    requiredCount,
    readyCount,
    missingCount,
    complete,
    missingTargets,
  };
}

export function scenePackageAssetPrimaryAction(
  packages: ScenePackageLike | null | undefined,
):
  | { kind: "retry_assets"; label: string; missingCount: number }
  | { kind: "generate_video"; label: string; missingCount: 0 }
  | { kind: "generate_assets"; label: string; missingCount: number } {
  const summary = scenePackageAssetSummary(packages);
  if (summary.complete) {
    return { kind: "generate_video", label: "确认并生成视频", missingCount: 0 };
  }
  if (summary.readyCount > 0 && summary.missingCount > 0) {
    return {
      kind: "retry_assets",
      label: `继续生成剩余 ${summary.missingCount} 项参考图`,
      missingCount: summary.missingCount,
    };
  }
  return {
    kind: "generate_assets",
    label: "生成参考图",
    missingCount: summary.missingCount,
  };
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
  return scenePackageAssetSummary(packages).readyCount > 0;
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
    return scenePackageAssetSummary(artifact.videoScenePackages).complete;
  });
}

/**
 * 刷新后若没有活跃 pending，清掉过期的 sceneAssetsGenerating，避免“参考图生成中”假忙碌。
 * 无图时回到 awaiting model，并解锁模型卡，便于用户重新确认（热重载或异常任务后常见）。
 */
export function reconcileStaleSceneAssetUiFlags<T extends ScenePackageMessage>(
  messages: T[],
  options: {
    hasActiveAssetJob: boolean;
  },
): T[] {
  if (options.hasActiveAssetJob) return messages;
  const hasAnyImages = messages.some((message) => (
    message.artifact?.type === "video_scene_packages"
    && scenePackageHasGeneratedImages(message.artifact.videoScenePackages)
  ));
  let changed = false;
  const next = messages.map((message) => {
    const artifact = message.artifact;
    if (!artifact) return message;
    if (artifact.type === "video_scene_packages") {
      const summary = scenePackageAssetSummary(artifact.videoScenePackages);
      if (summary.complete) return message;
      const awaitingModel = summary.readyCount === 0;
      if (
        !artifact.sceneAssetsGenerating
        && artifact.sceneAssetsAwaitingModel === awaitingModel
      ) {
        return message;
      }
      changed = true;
      return {
        ...message,
        artifact: {
          ...artifact,
          sceneAssetsGenerating: false,
          sceneAssetsAwaitingModel: awaitingModel,
        },
      };
    }
    if (
      artifact.type === "scene_asset_model_options"
      && artifact.sceneAssetModelConfirmed
      && !hasAnyImages
    ) {
      changed = true;
      return {
        ...message,
        artifact: {
          ...artifact,
          sceneAssetModelConfirmed: false,
        },
      };
    }
    return message;
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
