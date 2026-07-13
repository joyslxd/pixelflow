export type PlanMessageResumeAction =
  | "complete"
  | "retain_pending"
  | "restart_same_client"
  | "clear_failed";

export function classifyPlanMessageResume(input: {
  status?: string;
  hasResult?: boolean;
  hidden?: boolean;
  errorStatus?: number;
}): PlanMessageResumeAction {
  if (input.hidden) return "retain_pending";
  if (input.status === "failed") return "clear_failed";
  if (input.status === "completed") return input.hasResult ? "complete" : "clear_failed";
  if (input.errorStatus === 404) return "restart_same_client";
  return "retain_pending";
}

export function isPendingPlanSaveForConversation(
  pending: { conversation_id?: string; continue_after_save?: { type?: string } } | null | undefined,
  conversationId: string,
): boolean {
  return Boolean(
    pending
      && pending.conversation_id === conversationId
      && pending.continue_after_save?.type === "plan_save",
  );
}

export function isSameMessageJobGeneration(
  current: { conversation_id?: string; job_id?: string; source_message_id?: string; restart_count?: number } | null | undefined,
  candidate: { conversation_id?: string; job_id?: string; source_message_id?: string; restart_count?: number },
): boolean {
  return Boolean(
    current
      && current.conversation_id === candidate.conversation_id
      && current.job_id === candidate.job_id
      && current.source_message_id === candidate.source_message_id
      && (current.restart_count || 0) === (candidate.restart_count || 0),
  );
}

export function planMessageResumeDelayMs(restartCount: number | undefined): number {
  const normalized = Number.isInteger(restartCount) && Number(restartCount) > 0 ? Number(restartCount) : 0;
  return normalized === 0 ? 0 : Math.min(30_000, 500 * (2 ** Math.min(normalized - 1, 6)));
}

interface RecoverableMessageJobStatus<TResult> {
  status: string;
  result: TResult | null;
}

type RecoverablePlanMessageJobStepResult<TPending, TResult> =
  | { kind: "completed"; result: TResult }
  | { kind: "pending"; pending: TPending }
  | { kind: "failed"; error: unknown };

function errorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object" || !("status" in error)) return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

/** 执行一次可恢复 Plan 消息 job 查询；不持有 UI 状态，只返回下一步。 */
export async function resumePlanMessageJobStep<
  TRequest,
  TResult,
  TPending extends { job_id: string; request: TRequest; started_at?: string },
>(
  pending: TPending,
  dependencies: {
    shouldContinue: () => boolean;
    getStatus: () => Promise<RecoverableMessageJobStatus<TResult>>;
    pollStatus: (
      onStatus: (status: RecoverableMessageJobStatus<TResult>) => void,
    ) => Promise<RecoverableMessageJobStatus<TResult> | null>;
    restart: (request: TRequest) => Promise<{ job_id: string }>;
  },
): Promise<RecoverablePlanMessageJobStepResult<TPending, TResult>> {
  let lastStatus: RecoverableMessageJobStatus<TResult> | null = null;
  try {
    if (!dependencies.shouldContinue()) return { kind: "pending", pending };
    lastStatus = await dependencies.getStatus();
    let action = classifyPlanMessageResume({
      status: lastStatus.status,
      hasResult: Boolean(lastStatus.result),
      hidden: !dependencies.shouldContinue(),
    });
    if (action === "complete" && lastStatus.result) return { kind: "completed", result: lastStatus.result };
    if (action === "clear_failed") return { kind: "failed", error: new Error("对话消息保存失败") };
    if (!dependencies.shouldContinue()) return { kind: "pending", pending };
    const polled = await dependencies.pollStatus((status) => {
      lastStatus = status;
    });
    if (polled) lastStatus = polled;
    action = classifyPlanMessageResume({
      status: lastStatus?.status,
      hasResult: Boolean(lastStatus?.result),
      hidden: !dependencies.shouldContinue(),
    });
    if (action === "complete" && lastStatus?.result) return { kind: "completed", result: lastStatus.result };
    if (action === "clear_failed") return { kind: "failed", error: new Error("对话消息保存失败") };
    return { kind: "pending", pending };
  } catch (error) {
    const action = classifyPlanMessageResume({
      status: lastStatus?.status,
      hasResult: Boolean(lastStatus?.result),
      hidden: !dependencies.shouldContinue(),
      errorStatus: errorStatus(error),
    });
    if (action === "clear_failed") return { kind: "failed", error };
    if (action === "restart_same_client") {
      try {
        const restarted = await dependencies.restart(pending.request);
        return {
          kind: "pending",
          pending: {
            ...pending,
            job_id: restarted.job_id,
            started_at: new Date().toISOString(),
          },
        };
      } catch {
        return { kind: "pending", pending };
      }
    }
    return { kind: "pending", pending };
  }
}

interface SavedPlanMessage {
  artifact?: {
    type?: string;
    selectedDirection?: unknown;
    plan?: {
      plan_markdown?: string;
      plan_version?: number;
      plan_history?: unknown[];
      creation_contract?: Record<string, unknown>;
      scene_durations_sec?: number[];
      restored_from_version?: number | null;
    };
  };
}

/** 只从服务端已保存消息派生 Plan context，避免 optimistic payload 成为权威。 */
export function planContextFromSavedMessage(
  savedMessage: SavedPlanMessage,
  baseContext: Record<string, unknown>,
): Record<string, unknown> {
  const artifact = savedMessage.artifact;
  const plan = artifact?.plan;
  if (artifact?.type !== "plan" || !plan || typeof plan.plan_markdown !== "string" || typeof plan.plan_version !== "number") {
    throw new Error("服务端 Plan 消息缺少权威 artifact");
  }
  return {
    ...structuredClone(baseContext),
    selected_direction: structuredClone(artifact.selectedDirection),
    plan_markdown: plan.plan_markdown,
    plan_version: plan.plan_version,
    plan_history: structuredClone(plan.plan_history || []),
    creation_contract: structuredClone(plan.creation_contract || {}),
    scene_durations_sec: structuredClone(plan.scene_durations_sec || []),
    restored_from_version: plan.restored_from_version ?? null,
  };
}
