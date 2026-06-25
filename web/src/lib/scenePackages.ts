export const MAX_SCENE_DURATION_MS = 10_000;
export const DEFAULT_TARGET_DURATION_MS = 30_000;

export type SceneAssetCollection = "characters" | "scene_images" | "prop_images";

export interface ScenePackageRecord {
  scene_id: string;
  scene_index: number;
  duration_ms: number;
  title?: string;
  storyline?: string;
  prompt: string;
  narration?: string;
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

export function collectSceneImageUrls(scene: Pick<ScenePackageRecord, "image_urls" | "characters" | "scene_images" | "prop_images">): string[] {
  const urls = new Set(stringArray(scene.image_urls));
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
  return Array.from(urls);
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

function normalizeScenePackagePatch(patch: ScenePackagePatch): ScenePackagePatch {
  const normalized: ScenePackagePatch = {};
  if (patch.title !== undefined) normalized.title = patch.title;
  if (patch.storyline !== undefined) normalized.storyline = patch.storyline;
  if (patch.prompt !== undefined) normalized.prompt = patch.prompt;
  if (patch.narration !== undefined) normalized.narration = patch.narration;
  if (patch.duration_ms !== undefined) normalized.duration_ms = normalizeDurationMs(patch.duration_ms);
  return normalized;
}

function normalizeDurationMs(value: number | string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 1;
  return Math.max(1, Math.min(MAX_SCENE_DURATION_MS, Math.round(parsed)));
}

function normalizeTargetDurationMs(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_TARGET_DURATION_MS;
  return Math.max(1_000, Math.min(180_000, Math.round(value)));
}
