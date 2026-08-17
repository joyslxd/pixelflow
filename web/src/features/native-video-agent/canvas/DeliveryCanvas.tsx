import type { ReactNode } from "react";

/** 成片 / 剪映交付 Canvas。 */
export function DeliveryCanvas({
  summary,
  children,
}: {
  summary?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-2 p-4 text-sm text-slate-700">
      <div className="font-medium text-slate-900">成片交付</div>
      {summary ? <p className="whitespace-pre-wrap text-slate-600">{summary}</p> : null}
      {children}
    </div>
  );
}
