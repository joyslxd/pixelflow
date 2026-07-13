export interface SceneAssetFailureAttempt {
  endpoint: string;
  error: string;
}

export interface SceneAssetFailureDetail {
  id: string;
  title: string;
  typeLabel: string;
  sceneLabel: string;
  endpoint: string;
  model: string;
  ratio: string;
  size: string;
  error: string;
  attempts?: SceneAssetFailureAttempt[];
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(recordValue).filter((item): item is Record<string, unknown> => Boolean(item)) : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function sceneIndex(value: unknown): number | null {
  const normalized = Number(value);
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null;
}

function typeLabel(value: unknown): string {
  const labels: Record<string, string> = {
    character: "人物三视图",
    scene_image: "场景图",
    prop_image: "道具图",
  };
  return labels[text(value)] || "参考图";
}

function failureError(failure: Record<string, unknown>): string {
  const raw = recordValue(failure.raw);
  return text(failure.error) || text(raw?.message) || text(raw?.error) || "content-app 未返回具体失败原因";
}

export function sceneAssetFailureDetails(value: unknown): SceneAssetFailureDetail[] {
  return records(value).map((failure, index) => {
    const assetId = text(failure.asset_id);
    const currentSceneIndex = sceneIndex(failure.scene_index);
    const currentSceneId = text(failure.scene_id);
    const attempts = records(failure.attempts)
      .map((attempt) => ({ endpoint: text(attempt.endpoint), error: text(attempt.error) }))
      .filter((attempt) => attempt.endpoint || attempt.error);
    const detail: SceneAssetFailureDetail = {
      id: `${assetId || currentSceneId || "asset"}-${index + 1}`,
      title: text(failure.asset_name) || assetId || `参考图 ${index + 1}`,
      typeLabel: typeLabel(failure.asset_type),
      sceneLabel: currentSceneIndex ? `分镜 ${currentSceneIndex}` : currentSceneId || "全局素材",
      endpoint: text(failure.endpoint),
      model: text(failure.model),
      ratio: text(failure.ratio),
      size: text(failure.size),
      error: failureError(failure),
    };
    if (attempts.length > 0) detail.attempts = attempts;
    return detail;
  });
}
