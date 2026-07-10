import type { GlobalSceneAssets } from "./scenePackages";

const MAX_REFERENCE_IMAGE_COUNT = 9;

export type SceneMentionType = "character" | "scene" | "prop" | "reference";

export interface SceneMention {
  asset_id: string;
  type: SceneMentionType;
  name: string;
  image_url?: string;
  generation_reference_url?: string;
  third_asset_id?: string;
  replacement_source?: string;
}

export interface SceneMentionCandidate extends SceneMention {
  group: "characters" | "scenes" | "props";
}

export function buildMentionCandidates(globalAssets?: GlobalSceneAssets): SceneMentionCandidate[] {
  if (!globalAssets) return [];
  return [
    ...candidatesFromGroup(globalAssets.characters, "characters", "character"),
    ...candidatesFromGroup(globalAssets.scenes, "scenes", "scene"),
    ...candidatesFromGroup(globalAssets.props, "props", "prop"),
  ];
}

export function normalizeShotMentions(
  shotDescription: Record<string, unknown> | undefined,
  referenceAssetIds: string[] = [],
  globalAssets?: GlobalSceneAssets,
): SceneMention[] {
  const candidates = buildMentionCandidates(globalAssets);
  const byId = new Map(candidates.map((candidate) => [candidate.asset_id, candidate]));
  const rawMentions = Array.isArray(shotDescription?.mentions) ? shotDescription.mentions : [];
  const source = rawMentions.length > 0 ? rawMentions : referenceAssetIds.map((assetId) => ({ asset_id: assetId }));
  const mentions: SceneMention[] = [];
  const seen = new Set<string>();
  for (const item of source) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const assetId = stringValue(record.asset_id) || stringValue(record.assetId) || stringValue(record.id);
    const imageUrl = imageUrlFromRecord(record);
    const key = assetId || imageUrl;
    if (!key || seen.has(key)) continue;
    const candidate = assetId ? byId.get(assetId) : undefined;
    seen.add(key);
    const generationReferenceUrl = generationReferenceUrlFromRecord(record) || candidate?.generation_reference_url;
    const thirdAssetId = stringValue(record.third_asset_id) || stringValue(record.thirdAssetId) || candidate?.third_asset_id;
    const replacementSource = stringValue(record.replacement_source) || stringValue(record.replacementSource) || candidate?.replacement_source;
    mentions.push({
      asset_id: assetId || key,
      type: mentionType(record, candidate),
      name: stringValue(record.name) || stringValue(record.label) || candidate?.name || assetId || key,
      image_url: candidate?.image_url || imageUrl,
      ...(generationReferenceUrl ? { generation_reference_url: generationReferenceUrl } : {}),
      ...(thirdAssetId ? { third_asset_id: thirdAssetId } : {}),
      ...(replacementSource ? { replacement_source: replacementSource } : {}),
    });
    if (mentions.length >= MAX_REFERENCE_IMAGE_COUNT) break;
  }
  return mentions;
}

export function upsertShotMention(shotDescription: Record<string, unknown>, mention: SceneMention): { text: string; mentions: SceneMention[] } {
  const text = stringValue(shotDescription.text) || stringValue(shotDescription.description_text) || stringValue(shotDescription.shotText);
  const current = normalizeShotMentions(shotDescription);
  const exists = current.some((item) => item.asset_id === mention.asset_id);
  const mentions = exists ? current : [...current, mention].slice(0, MAX_REFERENCE_IMAGE_COUNT);
  return { text, mentions };
}

export function collectMentionImageUrls(mentions: unknown): string[] {
  const urls: string[] = [];
  const seen = new Set<string>();
  if (!Array.isArray(mentions)) return urls;
  for (const mention of mentions) {
    if (!mention || typeof mention !== "object") continue;
    const url = imageUrlFromRecord(mention as Record<string, unknown>);
    if (!url || seen.has(url)) continue;
    seen.add(url);
    urls.push(url);
    if (urls.length >= MAX_REFERENCE_IMAGE_COUNT) break;
  }
  return urls;
}

function candidatesFromGroup(
  records: Array<Record<string, unknown>> | undefined,
  group: SceneMentionCandidate["group"],
  type: SceneMentionType,
): SceneMentionCandidate[] {
  if (!Array.isArray(records)) return [];
  return records
    .map((record, index) => {
      const assetId = stringValue(record.asset_id) || stringValue(record.id) || `${group}-${index + 1}`;
      const generationReferenceUrl = generationReferenceUrlFromRecord(record);
      const thirdAssetId = stringValue(record.third_asset_id) || stringValue(record.thirdAssetId);
      const replacementSource = stringValue(record.replacement_source) || stringValue(record.replacementSource);
      return {
        asset_id: assetId,
        type,
        group,
        name: stringValue(record.name) || stringValue(record.label) || stringValue(record.description) || assetId,
        image_url: imageUrlFromRecord(record),
        ...(generationReferenceUrl ? { generation_reference_url: generationReferenceUrl } : {}),
        ...(thirdAssetId ? { third_asset_id: thirdAssetId } : {}),
        ...(replacementSource ? { replacement_source: replacementSource } : {}),
      };
    })
    .filter((candidate) => Boolean(candidate.asset_id));
}

function mentionType(record: Record<string, unknown>, candidate: SceneMentionCandidate | undefined): SceneMentionType {
  const raw = stringValue(record.type) || stringValue(record.asset_type) || candidate?.type || "reference";
  if (raw === "character" || raw === "scene" || raw === "prop") return raw;
  return "reference";
}

function imageUrlFromRecord(record: Record<string, unknown>): string {
  for (const key of ["images", "image_urls", "imageUrls", "three_view_images", "threeViewImages"]) {
    const values = stringArray(record[key]);
    if (values[0]) return values[0];
  }
  const direct =
    stringValue(record.image_url) ||
    stringValue(record.imageUrl) ||
    stringValue(record.url) ||
    stringValue(record.download_url) ||
    stringValue(record.downloadUrl) ||
    stringValue(record.src);
  return direct;
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

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
