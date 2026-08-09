export type ScenePackageJobResumeAction = "retain_pending" | "clear_not_found" | "clear_failed";

function errorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object" || !("status" in error)) return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === "object" && "message" in error) {
    return String((error as { message?: unknown }).message || "");
  }
  return String(error || "");
}

/** 断网 / content-app 认证暂不可用等瞬时错误应保留 pending 并重试。 */
export function isTransientScenePackageResumeError(error: unknown): boolean {
  const status = errorStatus(error);
  if (status === 0 || status === 408 || status === 429) return true;
  if (status === 502 || status === 503 || status === 504) return true;
  return /auth_service_unavailable|认证服务暂不可用|网络异常|Failed to fetch|NetworkError|ECONNRESET|ETIMEDOUT|503|502|504|408/i.test(
    errorMessage(error),
  );
}

export function classifyScenePackageJobResume(error: unknown): ScenePackageJobResumeAction {
  const status = errorStatus(error);
  if (status === 404) return "clear_not_found";
  const message = errorMessage(error);
  if (/\b404\b/.test(message) || /不存在或已过期/.test(message)) return "clear_not_found";
  if (isTransientScenePackageResumeError(error)) return "retain_pending";
  return "clear_failed";
}

export function scenePackageJobResumeDelayMs(attempt: number): number {
  const normalized = Number.isInteger(attempt) && attempt > 0 ? attempt : 0;
  return Math.min(30_000, 1000 * (2 ** Math.min(normalized, 5)));
}
