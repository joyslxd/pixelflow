import { CheckCircle2, CircleDot, PackageCheck, Sparkles, Wrench } from "lucide-react";
import type { FlowTimelineEntry } from "@/lib/types";

interface FlowTimelineProps {
  entries?: FlowTimelineEntry[];
}

const EVENT_LABEL: Record<FlowTimelineEntry["event"], string> = {
  step_started: "步骤开始",
  step_finished: "步骤完成",
  llm_summary: "思考摘要",
  vendor_call_started: "能力调用",
  vendor_call_finished: "调用完成",
  asset_ready: "资产就绪",
};

const EVENT_ICON = {
  step_started: CircleDot,
  step_finished: CheckCircle2,
  llm_summary: Sparkles,
  vendor_call_started: Wrench,
  vendor_call_finished: CheckCircle2,
  asset_ready: PackageCheck,
} as const;

export function FlowTimeline({ entries = [] }: FlowTimelineProps) {
  if (entries.length === 0) return null;
  return (
    <section className="mb-4 rounded-card border border-line bg-surface p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[14px] font-semibold text-ink">Agent 执行时间线</h2>
        <span className="text-[12px] text-ink-soft">{entries.length} 条</span>
      </div>
      <div className="space-y-3">
        {entries.map((entry) => {
          const Icon = EVENT_ICON[entry.event];
          return (
            <div key={entry.id} className="grid grid-cols-[28px_1fr] gap-3">
              <div className="flex justify-center pt-0.5">
                <span className="flex h-7 w-7 items-center justify-center rounded-full border border-line bg-canvas text-brand">
                  <Icon size={15} />
                </span>
              </div>
              <div className="min-w-0 border-b border-line pb-3 last:border-b-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-[13px] font-semibold text-ink">{entry.title || EVENT_LABEL[entry.event]}</span>
                  <span className="text-[11px] text-ink-soft">{EVENT_LABEL[entry.event]}</span>
                  {entry.phase && <span className="text-[11px] text-ink-soft">{entry.phase}</span>}
                  <span className="ml-auto text-[11px] text-ink-soft">{entry.time}</span>
                </div>
                {entry.summary && <p className="mt-1 text-[12px] leading-5 text-ink-soft">{entry.summary}</p>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
