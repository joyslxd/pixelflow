import type { ReactNode } from "react";

/** QC Canvas。 */
export function QualityReviewCanvas({
  summary,
  children,
}: {
  summary?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-2 p-4 text-sm text-slate-700">
      <div className="font-medium text-slate-900">质量检查</div>
      {summary ? <p className="whitespace-pre-wrap text-slate-600">{summary}</p> : null}
      {children}
    </div>
  );
}
