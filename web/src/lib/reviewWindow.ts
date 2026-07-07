export type ReviewTarget = "image" | "video";

export function reviewExpiresAt(startedAtMs: number, timeoutMs: number): string {
  return new Date(startedAtMs + timeoutMs).toISOString();
}

export function isReviewExpired(expiresAt: string | undefined | null, nowMs: number = Date.now()): boolean {
  if (!expiresAt) return false;
  const expiresAtMs = Date.parse(expiresAt);
  if (Number.isNaN(expiresAtMs)) return false;
  return nowMs >= expiresAtMs;
}

export function timeoutReviewMessage(target: ReviewTarget, timeoutSeconds: number): string {
  if (target === "video") {
    return `已超过 ${timeoutSeconds} 秒未收到视频修改意见，已默认无意见并结束流程。`;
  }
  return `已超过 ${timeoutSeconds} 秒未收到图片修改意见，已默认满意并结束流程。`;
}
