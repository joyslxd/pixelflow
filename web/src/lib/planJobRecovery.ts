export type PlanJobResumeAction =
  | "complete"
  | "retain_pending"
  | "clear_not_found"
  | "clear_failed";

export function classifyPlanJobResume(input: {
  status?: string;
  hasResult?: boolean;
  hidden?: boolean;
  errorStatus?: number;
}): PlanJobResumeAction {
  if (input.hidden) return "retain_pending";
  if (input.status === "completed") return input.hasResult ? "complete" : "clear_failed";
  if (input.status === "failed") return "clear_failed";
  if (input.errorStatus === 404) return "clear_not_found";
  if (input.errorStatus === 409 || input.errorStatus === 422) return "clear_failed";
  return "retain_pending";
}

export function planJobResumeDelayMs(attempt: number): number {
  const normalized = Number.isInteger(attempt) && attempt > 0 ? attempt : 0;
  return Math.min(30_000, 1000 * (2 ** Math.min(normalized, 5)));
}
