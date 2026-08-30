/** 统一人工中断宿主：只根据 Snapshot/SSE 投影展示，提交始终回到 Gateway。 */

import { useState } from "react";

import type { PublicInterruptV1 } from "@/api/contracts";

type InterruptHostProps = {
  interrupts: PublicInterruptV1[];
  confirmationSubmittingId: string | null;
  onConfirm: (interruptId: string) => Promise<void>;
  onResumeAuthorization: (interruptId: string) => Promise<void>;
  onSubmitForm: (interruptId: string, content: string, cancelled: boolean) => Promise<void>;
};

function actionLabel(kind: PublicInterruptV1["kind"]): string | null {
  if (kind === "awaiting_confirmation") return "确认并继续";
  return null;
}

export function InterruptHost({
  interrupts,
  confirmationSubmittingId,
  onConfirm,
  onResumeAuthorization,
  onSubmitForm,
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
            {label && interrupt.kind === "awaiting_confirmation" ? (
              <button
                className="mt-3 rounded border border-amber-400 px-3 py-1.5 text-xs disabled:opacity-50"
                disabled={submitting}
                onClick={() => void onConfirm(interrupt.interrupt_id)}
              >
                {submitting ? "正在提交…" : label}
              </button>
            ) : interrupt.kind === "authorization_required" ? (
              <button className="mt-3 rounded border border-amber-400 px-3 py-1.5 text-xs disabled:opacity-50" disabled={submitting} onClick={() => void onResumeAuthorization(interrupt.interrupt_id)}>
                {submitting ? "正在提交…" : "重新授权并继续"}
              </button>
            ) : interrupt.kind === "form" ? (
              <InterruptForm
                interrupt={interrupt}
                submitting={submitting}
                onSubmit={onSubmitForm}
              />
            ) : (
              <p className="mt-2 text-xs">请在更新授权或补充所需信息后重新发起该操作。</p>
            )}
          </div>
        );
      })}
    </section>
  );
}

function InterruptForm({
  interrupt,
  submitting,
  onSubmit,
}: {
  interrupt: PublicInterruptV1;
  submitting: boolean;
  onSubmit: (interruptId: string, content: string, cancelled: boolean) => Promise<void>;
}) {
  /** 表单草稿只保留在组件内；刷新后唯一事实仍由 Gateway 的中断投影决定。 */

  const [content, setContent] = useState("");
  return (
    <div className="mt-3 space-y-2">
      <label className="block">
        <span className="sr-only">补充信息</span>
        <textarea
          className="min-h-20 w-full rounded border border-amber-300 bg-white p-2 text-xs text-ink"
          value={content}
          maxLength={4000}
          disabled={submitting}
          onChange={(event) => setContent(event.target.value)}
          placeholder="请输入需要补充的信息"
        />
      </label>
      <div className="flex gap-2">
        <button
          className="rounded border border-amber-400 px-3 py-1.5 text-xs disabled:opacity-50"
          disabled={submitting || !content.trim()}
          onClick={() => void onSubmit(interrupt.interrupt_id, content, false)}
        >
          {submitting ? "正在提交…" : "提交"}
        </button>
        <button
          className="rounded border border-amber-300 px-3 py-1.5 text-xs disabled:opacity-50"
          disabled={submitting}
          onClick={() => void onSubmit(interrupt.interrupt_id, "", true)}
        >
          关闭表单
        </button>
      </div>
    </div>
  );
}
