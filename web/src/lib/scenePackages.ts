export const MIN_SCENE_DURATION_MS = 4_000;
export const MAX_SCENE_DURATION_MS = 15_000;
export const MAX_REFERENCE_IMAGE_COUNT = 9;
export const DEFAULT_TARGET_DURATION_MS = 30_000;

export type SceneAssetCollection = "characters" | "scene_images" | "prop_images";

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

export function collectSceneImageUrls(
  scene: Pick<ScenePackageRecord, "image_urls" | "characters" | "scene_images" | "prop_images" | "reference_asset_ids">,
  globalAssets?: GlobalSceneAssets,
): string[] {
  const urls = new Set(stringArray(scene.image_urls));
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
  if (useFlawAnalysis) {
    stringArray(flawAnalysis?.affected_scene_ids).forEach((sceneId) => ids.add(sceneId));
  }
  const normalizedFeedback = feedback.trim();
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

function collectGlobalAssetUrls(globalAssets: GlobalSceneAssets, assetId: string): string[] {
  const asset = findGlobalAsset(globalAssets, assetId);
  if (!asset) return [];
  return [
    ...stringArray(asset.images),
    ...stringArray(asset.image_urls),
    ...stringArray(asset.three_view_images),
  ];
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
