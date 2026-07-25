import { AlertTriangle, CheckCircle2, Clock3, Loader2 } from "lucide-react";
import type { SupervisorRuntimeNoticeModel } from "@/lib/supervisor/runtimeNotice";

interface ConversationRuntimeNoticeProps {
  notice: SupervisorRuntimeNoticeModel | null;
}

const toneClassNames = {
  working: "border-sky-200 bg-sky-50 text-sky-950",
  success: "border-emerald-200 bg-emerald-50 text-emerald-950",
  warning: "border-amber-200 bg-amber-50 text-amber-950",
  queued: "border-violet-200 bg-violet-50 text-violet-950",
} as const;

function NoticeIcon({ tone }: Pick<SupervisorRuntimeNoticeModel, "tone">) {
  if (tone === "working") return <Loader2 size={17} className="shrink-0 animate-spin" aria-hidden="true" />;
  if (tone === "success") return <CheckCircle2 size={17} className="shrink-0" aria-hidden="true" />;
  if (tone === "warning") return <AlertTriangle size={17} className="shrink-0" aria-hidden="true" />;
  return <Clock3 size={17} className="shrink-0" aria-hidden="true" />;
}

export function ConversationRuntimeNotice({ notice }: ConversationRuntimeNoticeProps) {
  if (!notice) return null;

  return (
    <div
      role={notice.tone === "warning" ? "alert" : "status"}
      aria-live="polite"
      className={`mb-2 rounded-2xl border px-3 py-2.5 shadow-sm ${toneClassNames[notice.tone]}`}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5"><NoticeIcon tone={notice.tone} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[13px] font-medium leading-5">{notice.title}</p>
            {notice.queueBadge ? (
              <span className="rounded-full border border-current/15 bg-white/70 px-2 py-0.5 text-[11px] font-semibold">
                {notice.queueBadge}
              </span>
            ) : null}
          </div>
          {notice.detail ? <p className="mt-0.5 text-[12px] leading-5 opacity-75">{notice.detail}</p> : null}
          {notice.kind === "compression" && notice.progressPercent !== null ? (
            <div
              role="progressbar"
              aria-label="上下文整理进度"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={notice.progressPercent}
              className="mt-2 h-1.5 overflow-hidden rounded-full bg-current/10"
            >
              <div
                className="h-full rounded-full bg-current transition-[width] duration-300"
                style={{ width: `${notice.progressPercent}%` }}
              />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
