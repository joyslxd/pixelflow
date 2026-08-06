export type AgentConfirmationDecision = "confirm" | "cancel";

export interface AgentConfirmationSubmission {
  confirmationId: string;
  stepId: string;
  decision: AgentConfirmationDecision;
}

interface AgentConfirmationCardProps {
  confirmationId: string;
  stepId: string;
  title: string;
  costSummary: string;
  affectedSceneIds: string[];
  submitting?: boolean;
  actionAvailable?: boolean;
  unavailableReason?: string | null;
  onSubmit?(submission: AgentConfirmationSubmission): void;
}

export function AgentConfirmationCard({
  confirmationId,
  stepId,
  title,
  costSummary,
  affectedSceneIds,
  submitting = false,
  actionAvailable = true,
  unavailableReason = null,
  onSubmit,
}: AgentConfirmationCardProps) {
  const submit = (decision: AgentConfirmationDecision) => {
    onSubmit?.({ confirmationId, stepId, decision });
  };
  const disabled = submitting || !actionAvailable || !onSubmit;

  return (
    <section aria-label="执行确认" className="rounded-xl border border-amber-200 bg-amber-50 p-4">
      <h2 className="text-base font-semibold text-amber-950">{title}</h2>
      <p className="mt-1 text-sm text-amber-900">{costSummary}</p>
      {affectedSceneIds.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2" aria-label="受影响镜头">
          {affectedSceneIds.map((sceneId) => (
            <span key={sceneId} className="rounded-full bg-white px-2.5 py-1 text-xs text-amber-900 shadow-sm">
              {sceneId}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => submit("cancel")}
          className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm text-amber-900 disabled:opacity-50"
        >
          取消
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => submit("confirm")}
          className="rounded-lg bg-amber-700 px-3 py-2 text-sm text-white disabled:opacity-50"
        >
          确认执行
        </button>
      </div>
      {!actionAvailable && unavailableReason ? (
        <p className="mt-3 text-xs text-amber-800">{unavailableReason}</p>
      ) : null}
    </section>
  );
}
