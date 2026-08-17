import type { ReactNode } from "react";

/** 分镜视频 Canvas：优先展示结果摘要；完整编辑仍走场景包面。 */
export function SceneVideoCanvas({
  summary,
  children,
}: {
  summary?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-2 p-4 text-sm text-slate-700">
      <div className="font-medium text-slate-900">分镜视频</div>
      {summary ? <p className="whitespace-pre-wrap text-slate-600">{summary}</p> : null}
      {children}
    </div>
  );
}
