/** 统一人工中断宿主：只根据 Snapshot/SSE 投影展示，提交始终回到 Gateway。 */

import type { PublicInterruptV1 } from "@/api/contracts";

type InterruptHostProps = {
  interrupts: PublicInterruptV1[];
  confirmationSubmittingId: string | null;
  onConfirm: (interruptId: string) => Promise<void>;
};

function actionLabel(kind: PublicInterruptV1["kind"]): string | null {
  if (kind === "awaiting_confirmation") return "确认并继续";
  return null;
}

export function InterruptHost({
  interrupts,
  confirmationSubmittingId,
  onConfirm,
}: InterruptHostProps) {
  /** 未具备公开提交合同的中断只提示，不用旧 API 或浏览器状态伪造恢复。 */

  if (interrupts.length === 0) return null;
  return (
    <section className="space-y-2" aria-label="需要处理的事项">
      {interrupts.map((interrupt) => {
        const label = actionLabel(interrupt.kind);
        const submitting = confirmationSubmittingId === interrupt.interrupt_id;
        return (
          <div key={interrupt.interrupt_id} className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" role="status">
            <h2 className="font-medium">{interrupt.title}</h2>
            <p className="mt-1 text-xs">{interrupt.description}</p>
            {label ? (
              <button
                className="mt-3 rounded border border-amber-400 px-3 py-1.5 text-xs disabled:opacity-50"
                disabled={submitting}
                onClick={() => void onConfirm(interrupt.interrupt_id)}
              >
                {submitting ? "正在提交…" : label}
              </button>
            ) : (
              <p className="mt-2 text-xs">当前操作等待服务端开放相应的安全恢复入口。</p>
            )}
          </div>
        );
      })}
    </section>
  );
}
