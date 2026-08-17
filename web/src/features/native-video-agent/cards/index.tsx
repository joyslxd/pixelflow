import type { ReactNode } from "react";

interface ConfirmationCardProps {
  confirmationId: string;
  title: string;
  costSummary: string;
  confirmLabel?: string;
  cancelLabel?: string;
  submitting?: boolean;
  actionAvailable?: boolean;
  unavailableReason?: string | null;
  submissionError?: string | null;
  onSubmit(decision: "confirm" | "cancel"): void;
}

/** 确认卡：只走确认/取消 API，不伪造自然语言 Turn。 */
export function ConfirmationCard({
  confirmationId,
  title,
  costSummary,
  confirmLabel = "确认执行",
  cancelLabel = "取消",
  submitting = false,
  actionAvailable = true,
  unavailableReason = null,
  submissionError = null,
  onSubmit,
}: ConfirmationCardProps) {
  return (
    <div
      className="rounded-xl border border-amber-200 bg-amber-50/80 p-3 text-sm text-slate-800"
      data-confirmation-id={confirmationId}
    >
      <div className="font-medium text-amber-950">{title}</div>
      <p className="mt-1 whitespace-pre-wrap text-slate-700">{costSummary}</p>
      {unavailableReason ? (
        <p className="mt-2 text-xs text-rose-700">{unavailableReason}</p>
      ) : null}
      {submissionError ? (
        <p className="mt-2 text-xs text-rose-700">{submissionError}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={submitting || !actionAvailable}
          className="rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          onClick={() => onSubmit("confirm")}
        >
          {confirmLabel}
        </button>
        <button
          type="button"
          disabled={submitting || !actionAvailable}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 disabled:opacity-50"
          onClick={() => onSubmit("cancel")}
        >
          {cancelLabel}
        </button>
      </div>
    </div>
  );
}

interface OperationCardProps {
  operationId: string;
  status: string;
  publicSummary: string;
  children?: ReactNode;
}

/** 异步 Operation 进度卡。 */
export function OperationCard({
  operationId,
  status,
  publicSummary,
  children,
}: OperationCardProps) {
  return (
    <div
      className="rounded-xl border border-sky-200 bg-sky-50/70 p-3 text-sm text-slate-800"
      data-operation-id={operationId}
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium text-sky-950">任务进行中</span>
        <span className="text-xs text-slate-500">{status}</span>
      </div>
      <p className="mt-1 text-slate-700">{publicSummary}</p>
      {children}
    </div>
  );
}

interface QuotaCardProps {
  quotaInterruptId: string;
  submitting?: boolean;
  actionAvailable?: boolean;
  unavailableReason?: string | null;
  submissionError?: string | null;
  onSubmit(decision: "resume" | "cancel"): void;
}

/** 额度不足可恢复卡。 */
export function QuotaCard({
  quotaInterruptId,
  submitting = false,
  actionAvailable = true,
  unavailableReason = null,
  submissionError = null,
  onSubmit,
}: QuotaCardProps) {
  return (
    <div
      className="rounded-xl border border-rose-200 bg-rose-50/80 p-3 text-sm text-slate-800"
      data-quota-interrupt-id={quotaInterruptId}
    >
      <div className="font-medium text-rose-950">额度不足</div>
      <p className="mt-1 text-slate-700">
        计费任务已暂停。充值后可继续，或取消本轮计划。
      </p>
      {unavailableReason ? (
        <p className="mt-2 text-xs text-rose-700">{unavailableReason}</p>
      ) : null}
      {submissionError ? (
        <p className="mt-2 text-xs text-rose-700">{submissionError}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={submitting || !actionAvailable}
          className="rounded-lg bg-rose-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          onClick={() => onSubmit("resume")}
        >
          继续
        </button>
        <button
          type="button"
          disabled={submitting || !actionAvailable}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 disabled:opacity-50"
          onClick={() => onSubmit("cancel")}
        >
          取消
        </button>
      </div>
    </div>
  );
}

interface ErrorCardProps {
  title?: string;
  message: string;
}

/** 安全错误摘要卡，不展示内部堆栈。 */
export function ErrorCard({ title = "执行失败", message }: ErrorCardProps) {
  return (
    <div className="rounded-xl border border-rose-200 bg-white p-3 text-sm text-slate-800">
      <div className="font-medium text-rose-900">{title}</div>
      <p className="mt-1 text-slate-700">{message}</p>
    </div>
  );
}
