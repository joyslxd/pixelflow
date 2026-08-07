export type AgentQuotaDecision = "resume" | "cancel";

export interface AgentQuotaSubmission {
  quotaInterruptId: string;
  decision: AgentQuotaDecision;
}

interface AgentQuotaCardProps {
  quotaInterruptId: string;
  submitting?: boolean;
  actionAvailable?: boolean;
  unavailableReason?: string | null;
  submissionError?: string | null;
  onSubmit?(submission: AgentQuotaSubmission): void;
}

export function AgentQuotaCard({
  quotaInterruptId,
  submitting = false,
  actionAvailable = true,
  unavailableReason = null,
  submissionError = null,
  onSubmit,
}: AgentQuotaCardProps) {
  const disabled = submitting || !actionAvailable || !onSubmit;
  return (
    <section aria-label="额度恢复" className="rounded-xl border border-orange-200 bg-orange-50 p-4">
      <h2 className="text-base font-semibold text-orange-950">视频任务因额度不足已暂停</h2>
      <p className="mt-1 text-sm text-orange-900">
        充值后可继续轮询原任务，不会重新发起 Provider start；也可以取消当前计划。
      </p>
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => onSubmit?.({ quotaInterruptId, decision: "cancel" })}
          className="rounded-lg border border-orange-300 bg-white px-3 py-2 text-sm text-orange-900 disabled:opacity-50"
        >
          取消计划
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onSubmit?.({ quotaInterruptId, decision: "resume" })}
          className="rounded-lg bg-orange-700 px-3 py-2 text-sm text-white disabled:opacity-50"
        >
          已充值，继续
        </button>
      </div>
      {!actionAvailable && unavailableReason ? (
        <p className="mt-3 text-xs text-orange-800">{unavailableReason}</p>
      ) : null}
      {submissionError ? (
        <p role="alert" className="mt-3 text-xs text-red-700">{submissionError}</p>
      ) : null}
    </section>
  );
}
