export const MIN_SCENE_DURATION_MS = 4_000;
export const MAX_SCENE_DURATION_MS = 15_000;
export const MAX_REFERENCE_IMAGE_COUNT = 9;
export const DEFAULT_TARGET_DURATION_MS = 30_000;

export type SceneAssetCollection = "characters" | "scene_images" | "prop_images";
export type GlobalSceneAssetGroup = "characters" | "scenes" | "props";

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
  shot_description?: Record<string, unknown>;
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
  duration_ms?: number | string;
  shot_description?: Record<string, unknown>;
  reference_asset_ids?: string[];
  generation_mode?: string | null;
}

export interface SceneFlawLike {
  affected_scene_ids?: unknown;
  revision_prompt?: string;
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
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

export function syncScenePackageMentionImageUrls<T extends ScenePackageRecord>(
  scenes: T[],
  input: { assetId: string; editedImageUrl: string },
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
      return { ...record, image_url: input.editedImageUrl };
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

export function sceneIdsForRevision(
  scenes: Array<Pick<ScenePackageRecord, "scene_id" | "scene_index">>,
  feedback: string,
  flawAnalysis: SceneFlawLike | undefined,
  useFlawAnalysis: boolean,
): Set<string> {
  const ids = new Set<string>();
  const normalizedFeedback = feedback.trim();
  const explicitOnlyIds = new Set<string>();
  scenes.forEach((scene) => {
    const pattern = new RegExp(`(?:只|仅)\\s*(?:修复|修改)\\s*第\\s*${scene.scene_index}\\s*(?:个)?\\s*分镜`);
    if (pattern.test(normalizedFeedback)) {
      explicitOnlyIds.add(scene.scene_id);
    }
  });
  if (explicitOnlyIds.size > 0) {
    return explicitOnlyIds;
  }
  if (useFlawAnalysis) {
    stringArray(flawAnalysis?.affected_scene_ids).forEach((sceneId) => ids.add(sceneId));
  }
  scenes.forEach((scene) => {
    if (normalizedFeedback.includes(scene.scene_id) || normalizedFeedback.includes(`第${scene.scene_index}`)) {
      ids.add(scene.scene_id);
    }
  });
  if (ids.size === 0 && !useFlawAnalysis) {
    scenes.forEach((scene) => ids.add(scene.scene_id));
  }
  return ids;
}

export function scenePackagesWithRevisionContract<T extends ScenePackageRecord>(
  scenes: T[],
  affectedSceneIds: Set<string>,
  feedback: string,
  flawAnalysis: SceneFlawLike | undefined,
): T[] {
  const revisionPrompt = flawAnalysis?.revision_prompt?.trim();
  if (!revisionPrompt) return scenes;
  return scenes.map((scene) => {
    if (!affectedSceneIds.has(scene.scene_id)) return scene;
    const repairContract = [
      `质检修复建议：${revisionPrompt}`,
      `用户修改/质检意见：${feedback.trim()}`,
      "只生成符合原方案产品主体和卖点的画面。不要沿用旧分镜中被质检判定为错误的画面主体、旁白、道具或场景。",
    ]
      .filter(Boolean)
      .join("\n");
    return {
      ...scene,
      prompt: repairContract,
      storyline: repairContract,
      shot_description: {
        ...(scene.shot_description || {}),
        text: repairContract,
        mentions: [],
      },
      narration: "",
      reference_asset_ids: [],
      image_urls: [],
      video_urls: [],
      audio_urls: [],
      characters: [],
      scene_images: [],
      prop_images: [],
    };
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

function normalizeScenePackagePatch(patch: ScenePackagePatch): ScenePackagePatch {
  const normalized: ScenePackagePatch = {};
  if (patch.title !== undefined) normalized.title = patch.title;
  if (patch.storyline !== undefined) normalized.storyline = patch.storyline;
  if (patch.prompt !== undefined) normalized.prompt = patch.prompt;
  if (patch.narration !== undefined) normalized.narration = patch.narration;
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
  return {
    ...record,
    [key]: current.length > 0 ? [editedImageUrl, ...current.slice(1)] : [editedImageUrl],
    image_url: editedImageUrl,
    url: editedImageUrl,
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
  const next = { ...asset };
  for (const key of ["three_view_images", "images", "image_urls"]) {
    if (Array.isArray(next[key])) next[key] = [];
  }
  for (const key of ["image_url", "url"]) {
    if (typeof next[key] === "string") next[key] = "";
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

function collectGlobalAssetUrls(globalAssets: GlobalSceneAssets, assetId: string): string[] {
  const asset = findGlobalAsset(globalAssets, assetId);
  if (!asset) return [];
  return [
    ...stringArray(asset.images),
    ...stringArray(asset.image_urls),
    ...stringArray(asset.three_view_images),
  ];
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

function findGlobalAsset(globalAssets: GlobalSceneAssets, assetId: string): Record<string, unknown> | undefined {
  for (const collection of [globalAssets.characters, globalAssets.scenes, globalAssets.props]) {
    const match = collection?.find((item) => stringValue(item.asset_id) === assetId || stringValue(item.id) === assetId);
    if (match) return match;
  }
  return undefined;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
