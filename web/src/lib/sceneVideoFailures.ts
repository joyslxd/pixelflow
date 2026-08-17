/** 分镜视频失败原因：把 reason_code / 泛化文案转成用户可读说明（只格式化一次）。 */

const REASON_HINTS: Record<string, string> = {
  provider_business_failed:
    "内容生成服务判定该镜失败（常见：提示词不合规、参考图无效、模型拒绝生成）。",
  provider_timeout: "等待生成结果超时，可重试该镜。",
  provider_job_expired: "供应商任务已过期，需要重新发起该镜生成。",
  provider_quota_insufficient: "额度不足，充值后可继续生成失败分镜。",
  provider_call_failed: "调用生成服务失败，请稍后重试该镜。",
  failed: "该镜视频生成未成功。",
  timeout: "该镜视频生成超时。",
  expired: "该镜生成任务已过期。",
};

const RETRY_HINT = "可点击「重新生成失败分镜」只重试失败片段。";

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function sceneIndex(value: unknown): number | null {
  const normalized = Number(value);
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null;
}

function alreadyFormatted(value: string): boolean {
  return value.includes(RETRY_HINT) || /^第\s*\d+\s*镜/.test(value);
}

export function formatSceneVideoFailureReason(
  failed: Record<string, unknown>,
  options: {
    sceneTitle?: string | null;
    storyline?: string | null;
  } = {},
): string {
  const rawError = text(failed.error || failed.message);
  // 已 enrich 过的文案直接展示，避免「详情 / 可点击」套娃重复。
  if (alreadyFormatted(rawError)) {
    return rawError;
  }

  const index = sceneIndex(failed.scene_index ?? failed.sceneIndex);
  const sceneId = text(failed.scene_id || failed.sceneId) || "未知分镜";
  const reasonCode = text(failed.reason_code || failed.reasonCode || failed.status) || "failed";
  const hint = REASON_HINTS[reasonCode]
    || (rawError && !/^供应商任务执行失败/.test(rawError) ? rawError : null)
    || REASON_HINTS.failed;
  const title = text(options.sceneTitle)
    || text(failed.scene_title)
    || text(options.storyline)?.slice(0, 24)
    || "";
  const head = index
    ? `第 ${index} 镜${title ? `「${title}」` : ""}`
    : `分镜 ${sceneId}${title ? `「${title}」` : ""}`;
  // 仅在原始错误与 hint 明显不同时追加详情，且不再嵌套整段已格式化文案。
  const detail = rawError
    && rawError !== hint
    && !hint.includes(rawError)
    && !rawError.includes(hint)
    && !/^供应商任务执行失败/.test(rawError)
    ? `详情：${rawError}`
    : "";
  return [head, hint, detail, RETRY_HINT].filter(Boolean).join(" ");
}

export function enrichFailedSceneForDisplay(
  failed: Record<string, unknown>,
  sceneMeta?: { title?: string | null; storyline?: string | null } | null,
): Record<string, unknown> {
  const error = formatSceneVideoFailureReason(failed, {
    sceneTitle: sceneMeta?.title,
    storyline: sceneMeta?.storyline,
  });
  return {
    ...failed,
    error,
    scene_title: text(sceneMeta?.title) || text(failed.scene_title) || undefined,
  };
}
