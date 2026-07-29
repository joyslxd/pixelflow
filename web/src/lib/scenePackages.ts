export const MIN_SCENE_DURATION_MS = 4_000;
export const MAX_SCENE_DURATION_MS = 15_000;
export const MAX_REFERENCE_IMAGE_COUNT = 9;
export const DEFAULT_TARGET_DURATION_MS = 30_000;

export type SceneAssetCollection = "characters" | "scene_images" | "prop_images";
export type GlobalSceneAssetGroup = "characters" | "scenes" | "props";
export type SceneAssetRetryType = "character" | "scene_image" | "prop_image";

export interface SceneAssetRetryTarget {
  asset_id: string;
  asset_type: SceneAssetRetryType;
}

const SCENE_ASSET_RETRY_TYPES = new Set<SceneAssetRetryType>(["character", "scene_image", "prop_image"]);

export function sceneAssetRetryTargets(
  failures: Array<Record<string, unknown>> | null | undefined,
): SceneAssetRetryTarget[] {
  const targets: SceneAssetRetryTarget[] = [];
  const seen = new Set<string>();
  for (const failure of failures || []) {
    const assetId = firstString(failure, "asset_id", "assetId");
    const assetType = firstString(failure, "asset_type", "assetType") as SceneAssetRetryType;
    if (!assetId || !SCENE_ASSET_RETRY_TYPES.has(assetType)) continue;
    const key = `${assetType}:${assetId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    targets.push({ asset_id: assetId, asset_type: assetType });
  }
  return targets;
}

export function mergeSceneAssetRetryFailures(
  previousFailures: Array<Record<string, unknown>> | null | undefined,
  retryFailures: Array<Record<string, unknown>> | null | undefined,
  targets: SceneAssetRetryTarget[],
): Array<Record<string, unknown>> {
  const targetKeys = new Set(targets.map((target) => `${target.asset_type}:${target.asset_id}`));
  const merged = (previousFailures || []).filter((failure) => {
    const assetId = firstString(failure, "asset_id", "assetId");
    const assetType = firstString(failure, "asset_type", "assetType");
    return !assetId || !assetType || !targetKeys.has(`${assetType}:${assetId}`);
  });
  const seen = new Set(
    merged.map((failure) => `${firstString(failure, "asset_type", "assetType")}:${firstString(failure, "asset_id", "assetId")}`),
  );
  for (const failure of retryFailures || []) {
    const assetId = firstString(failure, "asset_id", "assetId");
    const assetType = firstString(failure, "asset_type", "assetType");
    const key = assetId && assetType ? `${assetType}:${assetId}` : "";
    if (key && seen.has(key)) continue;
    if (key) seen.add(key);
    merged.push(failure);
  }
  return merged;
}

export interface SceneGlobalAssetReference extends Record<string, unknown> {
  source: "scene_global_asset";
  asset_id: string;
  asset_group: GlobalSceneAssetGroup;
  scene_global_asset_action?: "edit" | "delete";
  name: string;
  source_image_url: string;
  url: string;
  type: "image";
  filename: string;
  description?: string;
  storyboard_message_id?: string;
}

export type SceneGlobalAssetReplacementSource = "digital_human" | "image_asset" | "local_upload";

export interface SceneGlobalAssetReplacement {
  source: SceneGlobalAssetReplacementSource;
  displayImageUrl: string;
  generationReferenceUrl: string;
  thirdAssetId?: string;
  assetType?: string;
  contentAssetId?: string;
  assetName?: string;
  raw?: Record<string, unknown>;
}

export interface AddedGlobalSceneAsset<T extends GlobalSceneAssets = GlobalSceneAssets> {
  global_assets: T;
  added_asset: Record<string, unknown>;
}

export interface GlobalSceneAssets {
  characters?: Array<Record<string, unknown>>;
  scenes?: Array<Record<string, unknown>>;
  props?: Array<Record<string, unknown>>;
  visual_style?: Record<string, unknown>;
}

export interface ScenePackageRecord {
  scene_id: string;
  scene_index: number;
  duration_ms: number | "";
  title?: string;
  storyline?: string;
  prompt: string;
  narration?: string;
  transition?: string;
  shot_description?: Record<string, unknown>;
  revision_contract?: string;
  reference_asset_ids?: string[];
  generation_mode?: string | null;
  image_urls?: string[];
  video_urls?: string[];
  audio_urls?: string[];
  characters?: Array<Record<string, unknown>>;
  scene_images?: Array<Record<string, unknown>>;
  prop_images?: Array<Record<string, unknown>>;
}

export interface ScenePackagePatch {
  title?: string;
  storyline?: string;
  prompt?: string;
  narration?: string;
  transition?: string;
  duration_ms?: number | string;
  shot_description?: Record<string, unknown>;
  reference_asset_ids?: string[];
  generation_mode?: string | null;
}

export interface SceneQualityReviewLike {
  affected_scene_ids?: unknown;
  target_scene_ids?: unknown;
  excluded_scene_ids?: unknown;
  revision_prompt?: string;
}

export interface SceneGenerationPayloadLike {
  scene_id: string;
  scene_index: number;
  duration_ms: number;
  prompt: string;
  storyline?: string;
  shot_description?: Record<string, unknown>;
  narration?: string;
  transition?: string;
  generation_mode?: string | null;
  image_urls?: string[];
  video_urls?: string[];
  audio_urls?: string[];
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

export function defaultGlobalSceneAssetRatio(assetGroup: GlobalSceneAssetGroup | string): string {
  return assetGroup === "scenes" ? "9:16" : "1:1";
}

export function aspectRatioValue(label: string): number | null {
  const match = label.trim().match(/^(\d+(?:\.\d+)?)\s*[:xX/]\s*(\d+(?:\.\d+)?)/);
  if (!match) return null;
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  return width / height;
}

export function nearestSupportedAspectRatio(
  width: number,
  height: number,
  supportedRatios: string[] = [],
  fallback = "1:1",
): string {
  const target = width > 0 && height > 0 ? width / height : aspectRatioValue(fallback);
  const candidates = uniqueAspectRatios(supportedRatios).filter((ratio) => aspectRatioValue(ratio));
  if (!target || candidates.length === 0) return fallback;
  return candidates.reduce((best, candidate) => {
    const bestValue = aspectRatioValue(best) || target;
    const candidateValue = aspectRatioValue(candidate) || target;
    const bestDistance = Math.abs(Math.log(bestValue / target));
    const candidateDistance = Math.abs(Math.log(candidateValue / target));
    return candidateDistance < bestDistance ? candidate : best;
  }, candidates[0]);
}

export function globalSceneAssetRatioFromMetadata(
  asset: Record<string, unknown> | null | undefined,
  supportedRatios: string[] = [],
): string | null {
  if (!asset) return null;
  const explicit = firstString(asset, "ratio", "aspectRatio", "aspect_ratio", "image_ratio", "imageRatio");
  if (explicit) {
    const normalized = explicit.replace(/\s+/g, "");
    if (aspectRatioValue(normalized)) {
      return supportedRatios.length > 0
        ? nearestSupportedAspectRatioFromLabel(normalized, supportedRatios, supportedRatios[0])
        : normalized;
    }
  }
  const width = firstNumber(asset, "width", "image_width", "imageWidth", "naturalWidth", "natural_width");
  const height = firstNumber(asset, "height", "image_height", "imageHeight", "naturalHeight", "natural_height");
  if (width && height) {
    return nearestSupportedAspectRatio(width, height, supportedRatios, supportedRatios[0] || "1:1");
  }
  return null;
}

export function inferGlobalSceneAssetRatioFromMetadata(
  asset: Record<string, unknown> | null | undefined,
  assetGroup: GlobalSceneAssetGroup | string,
  supportedRatios: string[] = [],
): string {
  const fallback = defaultGlobalSceneAssetRatio(assetGroup);
  const fromMetadata = globalSceneAssetRatioFromMetadata(asset, supportedRatios);
  if (fromMetadata) return fromMetadata;
  if (supportedRatios.length > 0 && !supportedRatios.includes(fallback)) {
    return nearestSupportedAspectRatioFromLabel(fallback, supportedRatios, supportedRatios[0]);
  }
  return fallback;
}

export function updateScenePackageField<T extends ScenePackageRecord>(scenes: T[], sceneId: string, patch: ScenePackagePatch): T[] {
  return scenes.map((scene) => {
    if (scene.scene_id !== sceneId) return scene;
    return { ...scene, ...normalizeScenePackagePatch(patch) };
  });
}

export function updateScenePackageAssetField<T extends ScenePackageRecord>(
  scenes: T[],
  sceneId: string,
  collection: SceneAssetCollection,
  index: number,
  field: string,
  value: unknown,
): T[] {
  return scenes.map((scene) => {
    if (scene.scene_id !== sceneId) return scene;
    const items = Array.isArray(scene[collection]) ? scene[collection] : [];
    return {
      ...scene,
      [collection]: items.map((item, itemIndex) => (itemIndex === index ? { ...item, [field]: value } : item)),
    };
  });
}

function materialImageUrl(material: Record<string, unknown>): string {
  return String(
    material.url ||
      material.image_url ||
      material.imageUrl ||
      material.download_url ||
      material.downloadUrl ||
      material.path ||
      material.src ||
      material.artifact_url ||
      material.artifactUrl ||
      "",
  ).trim();
}

export function uploadedReferenceMaterials(materials: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const uploaded: Array<Record<string, unknown>> = [];
  for (const material of materials) {
    if (material.source === "scene_global_asset") continue;
    const url = materialImageUrl(material);
    if (!url.startsWith("http://") && !url.startsWith("https://")) continue;
    const kind = String(material.type || material.kind || material.media_type || material.mediaType || material.mime_type || material.mimeType || "").toLowerCase();
    if (
      kind === "image" ||
      kind === "picture" ||
      kind === "reference_image" ||
      kind.startsWith("image/") ||
      kind.startsWith("image") ||
      /\.(png|jpe?g|webp|gif)$/i.test((url.split("?")[0] || ""))
    ) {
      uploaded.push(material);
    }
  }
  return uploaded.slice(0, MAX_REFERENCE_IMAGE_COUNT);
}

export function globalAssetsContainAsset(globalAssets: GlobalSceneAssets | undefined, assetId: string): boolean {
  if (!globalAssets || !assetId) return false;
  for (const group of ["characters", "scenes", "props"] as const) {
    const records = globalAssets[group];
    if (!Array.isArray(records)) continue;
    if (records.some((record) => stringValue(record.asset_id) === assetId || stringValue(record.id) === assetId)) {
      return true;
    }
  }
  return false;
}

export function applyGlobalSceneAssetImageEdit<T extends GlobalSceneAssets, S extends ScenePackageRecord[]>(
  globalAssets: T,
  scenePackages: S,
  input: { assetId: string; assetGroup: GlobalSceneAssetGroup; editedImageUrl: string },
): { global_assets: T; scene_packages: S } {
  return {
    global_assets: replaceGlobalSceneAssetImage(globalAssets, input) as T,
    scene_packages: syncScenePackageMentionImageUrls(scenePackages, {
      assetId: input.assetId,
      editedImageUrl: input.editedImageUrl,
    }) as S,
  };
}

export function applyGlobalSceneAssetReplacement<T extends GlobalSceneAssets, S extends ScenePackageRecord[]>(
  globalAssets: T,
  scenePackages: S,
  input: { assetId: string; assetGroup: GlobalSceneAssetGroup; replacement: SceneGlobalAssetReplacement },
): { global_assets: T; scene_packages: S } {
  return {
    global_assets: replaceGlobalSceneAssetReference(globalAssets, input) as T,
    scene_packages: syncScenePackageMentionImageUrls(scenePackages, {
      assetId: input.assetId,
      editedImageUrl: input.replacement.displayImageUrl,
      generationReferenceUrl: input.replacement.generationReferenceUrl,
      thirdAssetId: input.replacement.thirdAssetId,
      replacementSource: input.replacement.source,
    }) as S,
  };
}

export function addGlobalSceneAssetReference<T extends GlobalSceneAssets>(
  globalAssets: T,
  input: {
    assetGroup: GlobalSceneAssetGroup;
    manualId: string;
    replacement: SceneGlobalAssetReplacement;
  },
): AddedGlobalSceneAsset<T> {
  const prefix: Record<GlobalSceneAssetGroup, string> = {
    characters: "character",
    scenes: "scene",
    props: "prop",
  };
  const fallbackName: Record<GlobalSceneAssetGroup, string> = {
    characters: "新增角色",
    scenes: "新增场景",
    props: "新增道具",
  };
  const existingRecords = (["characters", "scenes", "props"] as const).flatMap((group) => {
    const records = globalAssets[group];
    return Array.isArray(records) ? records : [];
  });
  const existingIds = new Set(
    [
      ...existingRecords.map((record) => stringValue(record.asset_id) || stringValue(record.id)),
      stringValue(globalAssets.visual_style?.asset_id) || stringValue(globalAssets.visual_style?.id),
    ].filter(Boolean),
  );
  const existingNames = new Set(
    existingRecords
      .map((record) => stringValue(record.name) || stringValue(record.label) || stringValue(record.description))
      .filter(Boolean),
  );
  const manualId = input.manualId.trim().replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "asset";
  const assetId = uniqueManualValue(`${prefix[input.assetGroup]}-manual-${manualId}`, existingIds);
  const requestedName = input.replacement.assetName?.trim() || fallbackName[input.assetGroup];
  const name = uniqueManualValue(requestedName, existingNames);
  const imageKey = input.assetGroup === "characters" ? "three_view_images" : "images";
  const addedAsset: Record<string, unknown> = {
    asset_id: assetId,
    name,
    description: name,
    [imageKey]: [input.replacement.displayImageUrl],
    image_url: input.replacement.displayImageUrl,
    url: input.replacement.displayImageUrl,
    generation_reference_url: input.replacement.generationReferenceUrl,
    replacement_source: input.replacement.source,
    manual_added: true,
    asset_origin: "manual_addition",
    ...(input.replacement.thirdAssetId ? { third_asset_id: input.replacement.thirdAssetId } : {}),
    ...(input.replacement.assetType ? { replacement_asset_type: input.replacement.assetType } : {}),
    ...(input.replacement.contentAssetId ? { replacement_asset_id: input.replacement.contentAssetId } : {}),
    ...(input.replacement.assetName ? { replacement_asset_name: input.replacement.assetName } : {}),
  };
  const rawGroupRecords = globalAssets[input.assetGroup];
  const groupRecords: Array<Record<string, unknown>> = Array.isArray(rawGroupRecords) ? rawGroupRecords : [];
  return {
    global_assets: {
      ...globalAssets,
      [input.assetGroup]: [...groupRecords, addedAsset],
    } as T,
    added_asset: addedAsset,
  };
}

export function replaceGlobalSceneAssetImage<T extends GlobalSceneAssets>(
  globalAssets: T,
  input: { assetId: string; assetGroup: GlobalSceneAssetGroup; editedImageUrl: string },
): T {
  const rawGroupRecords = globalAssets[input.assetGroup];
  const groupRecords: Array<Record<string, unknown>> = Array.isArray(rawGroupRecords) ? rawGroupRecords : [];
  return {
    ...globalAssets,
    [input.assetGroup]: groupRecords.map((asset) => {
      if (stringValue(asset.asset_id) !== input.assetId && stringValue(asset.id) !== input.assetId) return asset;
      if (input.assetGroup === "characters") {
        return replaceFirstUrl(asset, "three_view_images", input.editedImageUrl);
      }
      return replaceFirstUrl(asset, "images", input.editedImageUrl);
    }),
  } as T;
}

export function replaceGlobalSceneAssetReference<T extends GlobalSceneAssets>(
  globalAssets: T,
  input: { assetId: string; assetGroup: GlobalSceneAssetGroup; replacement: SceneGlobalAssetReplacement },
): T {
  const rawGroupRecords = globalAssets[input.assetGroup];
  const groupRecords: Array<Record<string, unknown>> = Array.isArray(rawGroupRecords) ? rawGroupRecords : [];
  return {
    ...globalAssets,
    [input.assetGroup]: groupRecords.map((asset) => {
      if (stringValue(asset.asset_id) !== input.assetId && stringValue(asset.id) !== input.assetId) return asset;
      const key = input.assetGroup === "characters" ? "three_view_images" : "images";
      return replaceFirstUrlWithReference(asset, key, input.replacement);
    }),
  } as T;
}

export function syncScenePackageMentionImageUrls<T extends ScenePackageRecord>(
  scenes: T[],
  input: {
    assetId: string;
    editedImageUrl: string;
    generationReferenceUrl?: string;
    thirdAssetId?: string;
    replacementSource?: SceneGlobalAssetReplacementSource;
  },
): T[] {
  return scenes.map((scene) => {
    const shotDescription = scene.shot_description;
    if (!shotDescription || typeof shotDescription !== "object") return scene;
    const mentions = (shotDescription as Record<string, unknown>).mentions;
    if (!Array.isArray(mentions)) return scene;
    let changed = false;
    const nextMentions = mentions.map((mention) => {
      if (!mention || typeof mention !== "object") return mention;
      const record = mention as Record<string, unknown>;
      const mentionAssetId = stringValue(record.asset_id) || stringValue(record.assetId) || stringValue(record.id);
      if (mentionAssetId !== input.assetId) return mention;
      changed = true;
      const cleanedRecord = input.generationReferenceUrl ? record : withoutGenerationReferenceFields(record);
      return {
        ...cleanedRecord,
        image_url: input.editedImageUrl,
        ...(input.generationReferenceUrl ? { generation_reference_url: input.generationReferenceUrl } : {}),
        ...(input.thirdAssetId ? { third_asset_id: input.thirdAssetId } : {}),
        ...(input.replacementSource ? { replacement_source: input.replacementSource } : {}),
      };
    });
    if (!changed) return scene;
    return {
      ...scene,
      shot_description: {
        ...shotDescription,
        mentions: nextMentions,
      },
    };
  });
}

export function deleteGlobalSceneAssetReference<T extends GlobalSceneAssets, S extends ScenePackageRecord[]>(
  globalAssets: T,
  scenes: S,
  input: { assetId: string; assetGroup: GlobalSceneAssetGroup; assetName?: string; sourceImageUrl?: string },
): { global_assets: T; scene_packages: S } {
  return {
    global_assets: clearGlobalSceneAssetImage(globalAssets, input) as T,
    scene_packages: removeSceneAssetReferences(scenes, input) as S,
  };
}

export function collectSceneImageUrls(
  scene: Pick<ScenePackageRecord, "image_urls" | "characters" | "scene_images" | "prop_images" | "reference_asset_ids" | "shot_description">,
  globalAssets?: GlobalSceneAssets,
): string[] {
  const urls = new Set(stringArray(scene.image_urls));
  const mentionUrls = collectMentionImageUrls(scene.shot_description);
  mentionUrls.forEach((url) => urls.add(url));
  if (mentionUrls.length > 0) {
    return Array.from(urls).slice(0, MAX_REFERENCE_IMAGE_COUNT);
  }
  if (globalAssets && stringArray(scene.reference_asset_ids).length > 0) {
    stringArray(scene.reference_asset_ids).forEach((assetId) => {
      collectGlobalAssetUrls(globalAssets, assetId).forEach((url) => urls.add(url));
    });
    return Array.from(urls).slice(0, MAX_REFERENCE_IMAGE_COUNT);
  }
  const collectFromRecords = (items: unknown, keys: string[]) => {
    if (!Array.isArray(items)) return;
    items.forEach((item) => {
      if (!item || typeof item !== "object") return;
      keys.forEach((key) => stringArray((item as Record<string, unknown>)[key]).forEach((url) => urls.add(url)));
    });
  };
  collectFromRecords(scene.characters, ["three_view_images", "images"]);
  collectFromRecords(scene.scene_images, ["images"]);
  collectFromRecords(scene.prop_images, ["images"]);
  return Array.from(urls).slice(0, MAX_REFERENCE_IMAGE_COUNT);
}

export function sceneGenerationPayloadFromPackage(
  scene: ScenePackageRecord,
  globalAssets?: GlobalSceneAssets,
  options: { edited?: boolean } = {},
): SceneGenerationPayloadLike {
  const normalizedScene = normalizeSceneAssetMentionsForGeneration(scene, globalAssets);
  return {
    scene_id: scene.scene_id,
    scene_index: scene.scene_index,
    duration_ms: durationMsForSubmit(scene.duration_ms),
    prompt: options.edited ? editedSceneGenerationPrompt(normalizedScene) : normalizedScene.prompt,
    storyline: normalizedScene.storyline,
    shot_description: normalizedScene.shot_description,
    narration: normalizedScene.narration,
    transition: normalizedScene.transition,
    generation_mode: scene.generation_mode,
    image_urls: options.edited ? collectExplicitSceneGenerationImageUrls(scene, globalAssets) : collectSceneGenerationImageUrls(scene, globalAssets),
    video_urls: scene.video_urls || [],
    audio_urls: scene.audio_urls || [],
  };
}

function explicitRevisionSceneIds(
  scenes: Array<Pick<ScenePackageRecord, "scene_id" | "scene_index">>,
  feedback: string,
): Set<string> {
  const ids = new Set<string>();
  const normalizedFeedback = feedback.trim();
  scenes.forEach((scene) => {
    const pattern = new RegExp(
      `(?:只|仅)?\\s*(?:修改|修复|重(?:新)?生成)\\s*第\\s*${scene.scene_index}\\s*(?:个)?\\s*(?:分镜|段)`,
      "g",
    );
    for (const match of normalizedFeedback.matchAll(pattern)) {
      const prefix = normalizedFeedback.slice(
        Math.max(0, (match.index ?? 0) - 12),
        match.index ?? 0,
      );
      if (/(?:不要|不用|无需|不需要|不|别|不可|禁止)\s*(?:再\s*)?$/u.test(prefix)) {
        continue;
      }
      ids.add(scene.scene_id);
      break;
    }
  });
  return ids;
}

export function sceneIdsForRevision(
  scenes: Array<Pick<ScenePackageRecord, "scene_id" | "scene_index">>,
  feedback: string,
  qualityReview: SceneQualityReviewLike | undefined,
  useQualityReview: boolean,
): Set<string> {
  const ids = new Set<string>();
  if (useQualityReview) {
    stringArray(qualityReview?.target_scene_ids).forEach((sceneId) => ids.add(sceneId));
    if (ids.size > 0) return ids;
    stringArray(qualityReview?.affected_scene_ids).forEach((sceneId) => ids.add(sceneId));
    return ids.size > 0 ? ids : explicitRevisionSceneIds(scenes, feedback);
  }
  const normalizedFeedback = feedback.trim();
  const explicitOnlyIds = explicitRevisionSceneIds(scenes, normalizedFeedback);
  if (explicitOnlyIds.size > 0) {
    return explicitOnlyIds;
  }
  scenes.forEach((scene) => {
    if (normalizedFeedback.includes(scene.scene_id) || normalizedFeedback.includes(`第${scene.scene_index}`)) {
      ids.add(scene.scene_id);
    }
  });
  if (ids.size === 0) {
    scenes.forEach((scene) => ids.add(scene.scene_id));
  }
  return ids;
}

export function scenePackagesWithRevisionContract<T extends ScenePackageRecord>(
  scenes: T[],
  affectedSceneIds: Set<string>,
  feedback: string,
  qualityReview: SceneQualityReviewLike | undefined,
  globalAssets?: GlobalSceneAssets,
  baselineScenes?: ScenePackageRecord[],
): T[] {
  const revisionPrompt = qualityReview?.revision_prompt?.trim();
  const feedbackText = feedback.trim();
  if (!revisionPrompt && !feedbackText) return scenes;
  const continuityReferences = revisionContinuityReferences(globalAssets);
  const continuityReferenceIds = continuityReferences.map((reference) => reference.asset_id);
  const baselineById = new Map((baselineScenes || []).map((scene) => [scene.scene_id, scene]));
  return scenes.map((scene) => {
    if (!affectedSceneIds.has(scene.scene_id)) return scene;
    const baselineScene = baselineById.get(scene.scene_id);
    const contractScene = baselineScene ? { ...scene, ...baselineScene } : scene;
    const repairContract = [
      revisionPrompt ? `质检修复建议：${revisionPrompt}` : "",
      feedbackText ? `用户修改/质检意见：${feedbackText}` : "",
      "只生成符合原方案产品主体和卖点的画面。不要沿用旧分镜中被质检判定为错误的画面主体、旁白、道具或场景。",
      continuityReferences.length > 0 ? `连续性要求：必须与前后未受影响分镜保持同一人物、同一产品道具、同一场景质感和同一视觉风格；优先参考 ${continuityReferenceIds.map((id) => `@${id}`).join("、")}。` : "",
    ]
      .filter(Boolean)
      .join("\n");
    return {
      ...scene,
      prompt: contractScene.prompt,
      storyline: contractScene.storyline,
      shot_description: {
        ...(contractScene.shot_description || {}),
        mentions: continuityReferences,
      },
      narration: contractScene.narration || scene.narration || "",
      revision_contract: repairContract,
      reference_asset_ids: continuityReferenceIds,
      image_urls: [],
      video_urls: [],
      audio_urls: [],
      characters: [],
      scene_images: [],
      prop_images: [],
    };
  });
}

export function scenePackagesWithoutRevisionContract<T extends ScenePackageRecord>(scenes: T[]): T[] {
  return scenes.map((scene) => {
    if (!scene.revision_contract) return scene;
    const { revision_contract: _revisionContract, ...cleanScene } = scene;
    return cleanScene as T;
  });
}

export function inferTargetDurationMs(texts: Array<string | undefined | null>): number {
  const joined = texts.filter(Boolean).join("\n");
  const minuteMatch = joined.match(/(\d+(?:\.\d+)?)\s*(?:分钟|分|minute|minutes|min)/i);
  if (minuteMatch) {
    return normalizeTargetDurationMs(Number(minuteMatch[1]) * 60_000);
  }
  const secondMatch = joined.match(/(\d+(?:\.\d+)?)\s*(?:秒|s|sec|secs|second|seconds)/i);
  if (secondMatch) {
    return normalizeTargetDurationMs(Number(secondMatch[1]) * 1000);
  }
  return DEFAULT_TARGET_DURATION_MS;
}

export function durationMsForSubmit(value: number | string | ""): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return MIN_SCENE_DURATION_MS;
  return Math.max(MIN_SCENE_DURATION_MS, Math.min(MAX_SCENE_DURATION_MS, Math.round(parsed)));
}

function editedSceneGenerationPrompt(scene: ScenePackageRecord): string {
  const shotText = shotDescriptionText(scene.shot_description);
  const pieces = [
    "请严格按照用户已编辑的分镜内容生成本段视频。用户编辑后的故事线、镜头描述和旁白是最高优先级合同；如果历史生成内容或旧参考素材与它们冲突，必须忽略旧内容。",
    scene.storyline ? `故事线：${scene.storyline}` : "",
    shotText ? `镜头描述：${shotText}` : "",
    scene.narration ? `旁白：${scene.narration}` : "",
    scene.revision_contract ? `质检修复合同：${scene.revision_contract}` : "",
  ];
  return pieces.filter(Boolean).join("\n");
}

function collectSceneGenerationImageUrls(
  scene: Pick<ScenePackageRecord, "image_urls" | "characters" | "scene_images" | "prop_images" | "reference_asset_ids" | "shot_description">,
  globalAssets?: GlobalSceneAssets,
): string[] {
  const urls = new Set(stringArray(scene.image_urls));
  const mentionUrls = collectMentionGenerationReferenceUrls(scene.shot_description);
  mentionUrls.forEach((url) => urls.add(url));
  if (globalAssets && stringArray(scene.reference_asset_ids).length > 0) {
    stringArray(scene.reference_asset_ids).forEach((assetId) => {
      collectGlobalAssetGenerationUrls(globalAssets, assetId).forEach((url) => urls.add(url));
    });
    return Array.from(urls).slice(0, MAX_REFERENCE_IMAGE_COUNT);
  }
  if (mentionUrls.length > 0) {
    return Array.from(urls).slice(0, MAX_REFERENCE_IMAGE_COUNT);
  }
  const collectFromRecords = (items: unknown, keys: string[]) => {
    if (!Array.isArray(items)) return;
    items.forEach((item) => {
      if (!item || typeof item !== "object") return;
      const generationReference = generationReferenceUrlFromRecord(item as Record<string, unknown>);
      if (generationReference) {
        urls.add(generationReference);
        return;
      }
      keys.forEach((key) => stringArray((item as Record<string, unknown>)[key]).forEach((url) => urls.add(url)));
    });
  };
  collectFromRecords(scene.characters, ["three_view_images", "images"]);
  collectFromRecords(scene.scene_images, ["images"]);
  collectFromRecords(scene.prop_images, ["images"]);
  return Array.from(urls).slice(0, MAX_REFERENCE_IMAGE_COUNT);
}

function normalizeSceneAssetMentionsForGeneration(scene: ScenePackageRecord, globalAssets?: GlobalSceneAssets): ScenePackageRecord {
  const mentionNames = new Map<string, string>();
  const staleMentionAliases = new Map<string, string>();
  if (globalAssets) {
    for (const collection of [globalAssets.characters, globalAssets.scenes, globalAssets.props]) {
      for (const asset of collection || []) {
        const assetId = stringValue(asset.asset_id) || stringValue(asset.id);
        const name = stringValue(asset.name) || stringValue(asset.label) || stringValue(asset.description);
        if (assetId && name) mentionNames.set(assetId, name);
      }
    }
  }
  const mentions = scene.shot_description?.mentions;
  if (Array.isArray(mentions)) {
    for (const mention of mentions) {
      if (!mention || typeof mention !== "object") continue;
      const record = mention as Record<string, unknown>;
      const assetId = stringValue(record.asset_id) || stringValue(record.assetId) || stringValue(record.id);
      const name = stringValue(record.name) || stringValue(record.label);
      const canonicalName = assetId ? mentionNames.get(assetId) : undefined;
      if (assetId && canonicalName && name && name !== canonicalName) staleMentionAliases.set(name, canonicalName);
      if (assetId && name && !mentionNames.has(assetId)) mentionNames.set(assetId, name);
    }
  }
  if (mentionNames.size === 0) return scene;

  const normalizeText = (value: unknown): unknown => {
    if (typeof value !== "string") return value;
    const assetIdNormalized = Array.from(mentionNames.entries())
      .sort(([left], [right]) => right.length - left.length)
      .reduce((text, [assetId, name]) => text.split(`@${assetId}`).join(`@${name}`), value);
    return Array.from(staleMentionAliases.entries())
      .sort(([left], [right]) => right.length - left.length)
      .reduce((text, [staleName, canonicalName]) => text.split(`@${staleName}`).join(`@${canonicalName}`), assetIdNormalized);
  };
  const shotDescription = scene.shot_description && typeof scene.shot_description === "object"
    ? {
        ...scene.shot_description,
        text: normalizeText(scene.shot_description.text),
        description_text: normalizeText(scene.shot_description.description_text),
        shotText: normalizeText(scene.shot_description.shotText),
        mentions: Array.isArray(scene.shot_description.mentions)
          ? scene.shot_description.mentions.map((mention) => {
              if (!mention || typeof mention !== "object") return mention;
              const record = mention as Record<string, unknown>;
              const assetId = stringValue(record.asset_id) || stringValue(record.assetId) || stringValue(record.id);
              const canonicalName = assetId ? mentionNames.get(assetId) : undefined;
              return canonicalName ? { ...record, name: canonicalName } : mention;
            })
          : scene.shot_description.mentions,
      }
    : scene.shot_description;
  return {
    ...scene,
    prompt: normalizeText(scene.prompt) as string,
    storyline: normalizeText(scene.storyline) as string,
    narration: normalizeText(scene.narration) as string,
    shot_description: shotDescription,
  };
}

function collectExplicitSceneGenerationImageUrls(scene: ScenePackageRecord, globalAssets?: GlobalSceneAssets): string[] {
  const urls = new Set<string>();
  collectMentionGenerationReferenceUrls(scene.shot_description).forEach((url) => urls.add(url));
  if (globalAssets) {
    stringArray(scene.reference_asset_ids).forEach((assetId) => {
      collectGlobalAssetGenerationUrls(globalAssets, assetId).forEach((url) => urls.add(url));
    });
  }
  return Array.from(urls).slice(0, MAX_REFERENCE_IMAGE_COUNT);
}

function shotDescriptionText(shotDescription: Record<string, unknown> | undefined): string {
  if (!shotDescription || typeof shotDescription !== "object") return "";
  const text = shotDescription.text || shotDescription.description_text || shotDescription.shotText || shotDescription.description;
  return typeof text === "string" ? text.trim() : "";
}

function revisionContinuityReferences(globalAssets?: GlobalSceneAssets): Array<{ asset_id: string; type: string; name: string; image_url?: string }> {
  if (!globalAssets) return [];
  const references = [
    firstGlobalAssetReference(globalAssets.characters, "character"),
    firstGlobalAssetReference(globalAssets.scenes, "scene"),
    firstGlobalAssetReference(globalAssets.props, "prop"),
  ].filter((reference): reference is { asset_id: string; type: string; name: string; image_url?: string } => Boolean(reference));
  return references.slice(0, MAX_REFERENCE_IMAGE_COUNT);
}

function firstGlobalAssetReference(records: Array<Record<string, unknown>> | undefined, type: string): { asset_id: string; type: string; name: string; image_url?: string } | undefined {
  if (!Array.isArray(records)) return undefined;
  for (const record of records) {
    const assetId = stringValue(record.asset_id) || stringValue(record.id);
    if (!assetId) continue;
    const imageUrl = firstAssetImageUrl(record);
    if (!imageUrl) continue;
    return {
      asset_id: assetId,
      type,
      name: stringValue(record.name) || stringValue(record.label) || stringValue(record.description) || assetId,
      image_url: imageUrl,
    };
  }
  return undefined;
}

function firstAssetImageUrl(record: Record<string, unknown>): string {
  const direct =
    stringValue(record.image_url) ||
    stringValue(record.imageUrl) ||
    stringValue(record.url) ||
    stringValue(record.download_url) ||
    stringValue(record.downloadUrl);
  if (direct) return direct;
  for (const key of ["images", "image_urls", "imageUrls", "three_view_images", "threeViewImages"]) {
    const values = stringArray(record[key]);
    if (values[0]) return values[0];
  }
  return "";
}

function normalizeScenePackagePatch(patch: ScenePackagePatch): ScenePackagePatch {
  const normalized: ScenePackagePatch = {};
  if (patch.title !== undefined) normalized.title = patch.title;
  if (patch.storyline !== undefined) normalized.storyline = patch.storyline;
  if (patch.prompt !== undefined) normalized.prompt = patch.prompt;
  if (patch.narration !== undefined) normalized.narration = patch.narration;
  if (patch.transition !== undefined) normalized.transition = patch.transition;
  if (patch.shot_description !== undefined) normalized.shot_description = patch.shot_description;
  if (patch.reference_asset_ids !== undefined) normalized.reference_asset_ids = patch.reference_asset_ids;
  if (patch.generation_mode !== undefined) normalized.generation_mode = patch.generation_mode;
  if (patch.duration_ms !== undefined) normalized.duration_ms = normalizeDurationMs(patch.duration_ms);
  return normalized;
}

function normalizeDurationMs(value: number | string): number | "" {
  if (value === "") return "";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return MIN_SCENE_DURATION_MS;
  return Math.max(MIN_SCENE_DURATION_MS, Math.min(MAX_SCENE_DURATION_MS, Math.round(parsed)));
}

function normalizeTargetDurationMs(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_TARGET_DURATION_MS;
  return Math.max(1_000, Math.min(180_000, Math.round(value)));
}

function replaceFirstUrl(record: Record<string, unknown>, key: string, editedImageUrl: string): Record<string, unknown> {
  const current = stringArray(record[key]);
  const cleanedRecord = withoutGenerationReferenceFields(record);
  return {
    ...cleanedRecord,
    [key]: current.length > 0 ? [editedImageUrl, ...current.slice(1)] : [editedImageUrl],
    image_url: editedImageUrl,
    url: editedImageUrl,
  };
}

function replaceFirstUrlWithReference(
  record: Record<string, unknown>,
  key: string,
  replacement: SceneGlobalAssetReplacement,
): Record<string, unknown> {
  const current = stringArray(record[key]);
  const displayImageUrl = replacement.displayImageUrl;
  return {
    ...record,
    [key]: current.length > 0 ? [displayImageUrl, ...current.slice(1)] : [displayImageUrl],
    image_url: displayImageUrl,
    url: displayImageUrl,
    generation_reference_url: replacement.generationReferenceUrl,
    replacement_source: replacement.source,
    ...(replacement.thirdAssetId ? { third_asset_id: replacement.thirdAssetId } : {}),
    ...(replacement.assetType ? { replacement_asset_type: replacement.assetType } : {}),
    ...(replacement.contentAssetId ? { replacement_asset_id: replacement.contentAssetId } : {}),
    ...(replacement.assetName ? { replacement_asset_name: replacement.assetName } : {}),
  };
}

function clearGlobalSceneAssetImage<T extends GlobalSceneAssets>(
  globalAssets: T,
  input: { assetId: string; assetGroup: GlobalSceneAssetGroup },
): T {
  const rawGroupRecords = globalAssets[input.assetGroup];
  const groupRecords: Array<Record<string, unknown>> = Array.isArray(rawGroupRecords) ? rawGroupRecords : [];
  return {
    ...globalAssets,
    [input.assetGroup]: groupRecords.map((asset) => {
      if (stringValue(asset.asset_id) !== input.assetId && stringValue(asset.id) !== input.assetId) return asset;
      return clearAssetImageFields(asset);
    }),
  } as T;
}

function clearAssetImageFields(asset: Record<string, unknown>): Record<string, unknown> {
  const next = withoutGenerationReferenceFields(asset);
  for (const key of ["three_view_images", "images", "image_urls"]) {
    if (Array.isArray(next[key])) next[key] = [];
  }
  for (const key of ["image_url", "url"]) {
    if (typeof next[key] === "string") next[key] = "";
  }
  return next;
}

function withoutGenerationReferenceFields(record: Record<string, unknown>): Record<string, unknown> {
  const next = { ...record };
  for (const key of [
    "generation_reference_url",
    "generationReferenceUrl",
    "asset_reference",
    "assetReference",
    "third_asset_id",
    "thirdAssetId",
    "replacement_source",
    "replacementSource",
    "replacement_asset_type",
    "replacement_asset_id",
    "replacement_asset_name",
  ]) {
    delete next[key];
  }
  return next;
}

function removeSceneAssetReferences<T extends ScenePackageRecord[]>(
  scenes: T,
  input: { assetId: string; assetName?: string; sourceImageUrl?: string },
): T {
  return scenes.map((scene) => removeSceneAssetReference(scene, input)) as T;
}

function removeSceneAssetReference<T extends ScenePackageRecord>(
  scene: T,
  input: { assetId: string; assetName?: string; sourceImageUrl?: string },
): T {
  const nextReferenceAssetIds = stringArray(scene.reference_asset_ids).filter((assetId) => assetId !== input.assetId);
  const nextImageUrls = stringArray(scene.image_urls).filter((url) => !input.sourceImageUrl || url !== input.sourceImageUrl);
  const nextShotDescription = removeShotDescriptionAssetReference(scene.shot_description, input);
  const changed =
    nextReferenceAssetIds.length !== stringArray(scene.reference_asset_ids).length ||
    nextImageUrls.length !== stringArray(scene.image_urls).length ||
    nextShotDescription !== scene.shot_description;
  if (!changed) return scene;
  return {
    ...scene,
    reference_asset_ids: nextReferenceAssetIds,
    image_urls: nextImageUrls,
    shot_description: nextShotDescription,
  };
}

function removeShotDescriptionAssetReference(
  shotDescription: Record<string, unknown> | undefined,
  input: { assetId: string; assetName?: string },
): Record<string, unknown> | undefined {
  if (!shotDescription || typeof shotDescription !== "object") return shotDescription;
  const mentions = Array.isArray(shotDescription.mentions) ? shotDescription.mentions : [];
  const nextMentions = mentions.filter((mention) => {
    if (!mention || typeof mention !== "object") return true;
    const record = mention as Record<string, unknown>;
    const mentionAssetId = stringValue(record.asset_id) || stringValue(record.assetId) || stringValue(record.id);
    return mentionAssetId !== input.assetId;
  });
  const text = stringValue(shotDescription.text);
  const nextText = text ? removeAssetMentionTokens(text, [input.assetName, input.assetId]) : text;
  if (nextMentions.length === mentions.length && nextText === text) return shotDescription;
  return {
    ...shotDescription,
    text: nextText,
    mentions: nextMentions,
  };
}

function removeAssetMentionTokens(text: string, tokens: Array<string | undefined>): string {
  return tokens.filter((token): token is string => Boolean(token?.trim())).reduce((current, token) => {
    const escaped = escapeRegExp(token.trim());
    return current
      .replace(new RegExp(`@${escaped}(?=\\s|[，。,.、；;：:！!？?）)】\\]}]|$)`, "g"), "")
      .replace(/\s{2,}/g, " ")
      .replace(/\s+([，。,.、；;：:！!？?）)】\]}])/g, "$1")
      .trim();
  }, text);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function uniqueManualValue(base: string, existing: Set<string>): string {
  if (!existing.has(base)) return base;
  let suffix = 2;
  while (existing.has(`${base}-${suffix}`)) suffix += 1;
  return `${base}-${suffix}`;
}

function collectGlobalAssetUrls(globalAssets: GlobalSceneAssets, assetId: string): string[] {
  const asset = findGlobalAsset(globalAssets, assetId);
  if (!asset) return [];
  return [
    ...stringArray(asset.images),
    ...stringArray(asset.image_urls),
    ...stringArray(asset.three_view_images),
  ];
}

function collectGlobalAssetGenerationUrls(globalAssets: GlobalSceneAssets, assetId: string): string[] {
  const asset = findGlobalAsset(globalAssets, assetId);
  if (!asset) return [];
  const generationReference = generationReferenceUrlFromRecord(asset);
  if (generationReference) return [generationReference];
  return collectGlobalAssetUrls(globalAssets, assetId);
}

function collectMentionImageUrls(shotDescription: unknown): string[] {
  if (!shotDescription || typeof shotDescription !== "object") return [];
  const mentions = (shotDescription as Record<string, unknown>).mentions;
  if (!Array.isArray(mentions)) return [];
  const urls: string[] = [];
  for (const mention of mentions) {
    if (!mention || typeof mention !== "object") continue;
    const record = mention as Record<string, unknown>;
    const direct =
      stringValue(record.image_url) ||
      stringValue(record.imageUrl) ||
      stringValue(record.url) ||
      stringValue(record.download_url) ||
      stringValue(record.downloadUrl);
    if (direct) urls.push(direct);
    for (const key of ["images", "image_urls", "imageUrls"]) {
      stringArray(record[key]).forEach((url) => urls.push(url));
    }
  }
  return urls;
}

function collectMentionGenerationReferenceUrls(shotDescription: unknown): string[] {
  if (!shotDescription || typeof shotDescription !== "object") return [];
  const mentions = (shotDescription as Record<string, unknown>).mentions;
  if (!Array.isArray(mentions)) return [];
  const urls: string[] = [];
  for (const mention of mentions) {
    if (!mention || typeof mention !== "object") continue;
    const record = mention as Record<string, unknown>;
    const generationReference = generationReferenceUrlFromRecord(record);
    if (generationReference) {
      urls.push(generationReference);
      continue;
    }
    const direct =
      stringValue(record.image_url) ||
      stringValue(record.imageUrl) ||
      stringValue(record.url) ||
      stringValue(record.download_url) ||
      stringValue(record.downloadUrl);
    if (direct) urls.push(direct);
    for (const key of ["images", "image_urls", "imageUrls"]) {
      stringArray(record[key]).forEach((url) => urls.push(url));
    }
  }
  return urls;
}

function generationReferenceUrlFromRecord(record: Record<string, unknown>): string {
  const direct =
    stringValue(record.generation_reference_url) ||
    stringValue(record.generationReferenceUrl) ||
    stringValue(record.asset_reference) ||
    stringValue(record.assetReference);
  if (direct) return direct;
  const thirdAssetId = stringValue(record.third_asset_id) || stringValue(record.thirdAssetId);
  return thirdAssetId ? `asset://${thirdAssetId.replace(/^asset:\/\//, "")}` : "";
}

function findGlobalAsset(globalAssets: GlobalSceneAssets, assetId: string): Record<string, unknown> | undefined {
  for (const collection of [globalAssets.characters, globalAssets.scenes, globalAssets.props]) {
    const match = collection?.find((item) => stringValue(item.asset_id) === assetId || stringValue(item.id) === assetId);
    if (match) return match;
  }
  return undefined;
}

function nearestSupportedAspectRatioFromLabel(label: string, supportedRatios: string[], fallback: string): string {
  const value = aspectRatioValue(label);
  if (!value) return fallback;
  return nearestSupportedAspectRatio(value, 1, supportedRatios, fallback);
}

function uniqueAspectRatios(values: string[]): string[] {
  return Array.from(new Set(values.map((item) => item.trim()).filter((item) => Boolean(item) && Boolean(aspectRatioValue(item)))));
}

function firstString(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function firstNumber(record: Record<string, unknown>, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    const numeric = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
    if (Number.isFinite(numeric) && numeric > 0) return numeric;
  }
  return null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
