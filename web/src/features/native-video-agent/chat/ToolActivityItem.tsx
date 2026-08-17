import { CheckCircle2, CircleX, LoaderCircle } from "lucide-react";
import type { NativeToolActivity } from "../state/contracts";

interface ToolActivityItemProps {
  activity: NativeToolActivity;
  now?: number;
}

function durationLabel(activity: NativeToolActivity, now: number): string | null {
  if (activity.durationMs != null && activity.durationMs >= 0) {
    const seconds = Math.floor(activity.durationMs / 1000);
    return `${seconds}s`;
  }
  if (activity.status !== "running" || !activity.startedAt) return null;
  const started = Date.parse(activity.startedAt);
  if (Number.isNaN(started)) return null;
  return `${Math.max(0, Math.floor((now - started) / 1000))}s`;
}

/** 单个 Tool 活动行。 */
export function ToolActivityItem({ activity, now = Date.now() }: ToolActivityItemProps) {
  const duration = durationLabel(activity, now);
  const title = activity.title || activity.toolName;
  const showToolName = Boolean(
    activity.toolName
    && activity.toolName !== title
    && (activity.status === "failed" || activity.status === "running"),
  );
  return (
    <div className="flex items-start gap-2 rounded-md border border-slate-200 bg-white px-2.5 py-2 text-sm">
      {activity.status === "running" ? (
        <LoaderCircle className="mt-0.5 size-4 shrink-0 animate-spin text-sky-600" />
      ) : activity.status === "failed" ? (
        <CircleX className="mt-0.5 size-4 shrink-0 text-rose-600" />
      ) : (
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-medium text-slate-800">{title}</span>
          {showToolName ? (
            <span className="font-mono text-[11px] text-slate-500">{activity.toolName}</span>
          ) : null}
          {duration ? <span className="text-xs text-slate-500">{duration}</span> : null}
        </div>
        {activity.publicSummary ? (
          <p className="mt-0.5 text-slate-600">{activity.publicSummary}</p>
        ) : null}
      </div>
    </div>
  );
}
