export type PlanJobResumeAction =
  | "complete"
  | "retain_pending"
  | "clear_not_found"
  | "clear_failed";

export interface PlanJobRecoveryStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface PlanJobRecoveryHandle {
  job_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: "plan_generation" | "plan_revision";
  started_at: string;
  request: object;
  context: object;
}

interface StoredPlanJobRecovery {
  version: 1;
  pending: PlanJobRecoveryHandle;
}

const PLAN_JOB_RECOVERY_KEY_PREFIX = "pixelflow:pending-plan-job:";
const PLAN_JOB_PERSISTENCE_MAX_AGE_MS = 25 * 60 * 1000;

function recoveryKey(conversationId: string): string {
  return `${PLAN_JOB_RECOVERY_KEY_PREFIX}${conversationId}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isPlanJobRecoveryHandle(value: unknown): value is PlanJobRecoveryHandle {
  if (!isRecord(value)) return false;
  return (
    typeof value.job_id === "string"
    && Boolean(value.job_id)
    && typeof value.conversation_id === "string"
    && Boolean(value.conversation_id)
    && typeof value.source_message_id === "string"
    && (value.kind === "plan_generation" || value.kind === "plan_revision")
    && typeof value.started_at === "string"
    && isRecord(value.request)
    && isRecord(value.context)
  );
}

export function savePendingPlanJobRecovery<T extends PlanJobRecoveryHandle>(
  storage: PlanJobRecoveryStorage | null | undefined,
  pendingPlanJob: T,
): boolean {
  if (!storage || !pendingPlanJob.conversation_id) return false;
  try {
    const stored: StoredPlanJobRecovery = {
      version: 1,
      pending: pendingPlanJob,
    };
    storage.setItem(recoveryKey(pendingPlanJob.conversation_id), JSON.stringify(stored));
    return true;
  } catch {
    return false;
  }
}

export function loadPendingPlanJobRecovery<T extends PlanJobRecoveryHandle>(
  storage: PlanJobRecoveryStorage | null | undefined,
  conversationId: string,
): T | null {
  if (!storage || !conversationId) return null;
  const key = recoveryKey(conversationId);
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const stored = JSON.parse(raw) as unknown;
    if (
      !isRecord(stored)
      || stored.version !== 1
      || !isPlanJobRecoveryHandle(stored.pending)
      || stored.pending.conversation_id !== conversationId
    ) {
      storage.removeItem(key);
      return null;
    }
    return stored.pending as T;
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      return null;
    }
    return null;
  }
}

export function clearPendingPlanJobRecovery(
  storage: PlanJobRecoveryStorage | null | undefined,
  conversationId: string,
  expectedJobId?: string,
): void {
  if (!storage || !conversationId) return;
  const key = recoveryKey(conversationId);
  try {
    if (expectedJobId) {
      const pendingPlanJob = loadPendingPlanJobRecovery(storage, conversationId);
      if (pendingPlanJob && pendingPlanJob.job_id !== expectedJobId) return;
    }
    storage.removeItem(key);
  } catch {
    return;
  }
}

export async function continueStartedPlanJob<T>(input: {
  pendingPlanJob: T;
  saveRecovery: (pendingPlanJob: T) => unknown;
  persistPending: (pendingPlanJob: T) => Promise<void>;
  notifyRecovery: (pendingPlanJob: T) => unknown;
  schedulePersistenceRetry: (pendingPlanJob: T) => unknown;
  resumePending: (pendingPlanJob: T) => Promise<void>;
}): Promise<void> {
  try {
    input.saveRecovery(input.pendingPlanJob);
  } catch {
    // 当前标签页缓存不可用时，仍优先继续查询已启动的同一个任务。
  }
  try {
    await input.persistPending(input.pendingPlanJob);
  } catch {
    input.notifyRecovery(input.pendingPlanJob);
    input.schedulePersistenceRetry(input.pendingPlanJob);
  }
  await input.resumePending(input.pendingPlanJob);
}

export function shouldRetryPlanJobPersistence(input: {
  hidden: boolean;
  startedAt: string;
  nowMs?: number;
  maxAgeMs?: number;
}): boolean {
  if (input.hidden) return false;
  const startedAtMs = Date.parse(input.startedAt);
  if (!Number.isFinite(startedAtMs)) return false;
  const nowMs = input.nowMs ?? Date.now();
  const maxAgeMs = input.maxAgeMs ?? PLAN_JOB_PERSISTENCE_MAX_AGE_MS;
  return nowMs - startedAtMs <= maxAgeMs;
}

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
