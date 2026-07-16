import { useState } from "react";
import { AlertCircle, Ban, Check, ChevronUp, Circle, Loader2, Minus, Pause } from "lucide-react";
import { cn } from "@/lib/utils";
import { workflowStatusLabel, type WorkflowTaskBoardModel, type WorkflowTaskItemStatus } from "@/lib/workflowTaskBoard";

interface WorkflowTaskBoardProps {
  model: WorkflowTaskBoardModel;
}

const STATUS_CLASS: Record<WorkflowTaskItemStatus, string> = {
  completed: "bg-[#e6f5ef] text-[#009b6b]",
  processing: "bg-accent-soft text-accent",
  waiting: "bg-amber/10 text-amber",
  waiting_download: "bg-amber/10 text-amber",
  pending: "bg-canvas text-ink-soft",
  skipped: "bg-canvas text-ink-soft",
  paused: "bg-amber/10 text-amber",
  failed: "bg-rose-50 text-rose-600",
  cancelled: "bg-canvas text-ink-soft",
};

function StatusIcon({ status }: { status: WorkflowTaskItemStatus }) {
  if (status === "completed") return <Check size={14} />;
  if (status === "processing") return <Loader2 size={14} className="animate-spin" />;
  if (status === "paused") return <Pause size={13} />;
  if (status === "failed") return <AlertCircle size={14} />;
  if (status === "cancelled") return <Ban size={13} />;
  if (status === "skipped") return <Minus size={14} />;
  return <Circle size={10} fill="currentColor" className="opacity-60" />;
}

function RowStatus({ status }: { status: WorkflowTaskItemStatus }) {
  if (status === "completed") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[#b9e2d4] bg-[#e8f6f1] px-2.5 py-1 text-[11px] font-medium text-[#009b6b]">
        <Check size={13} />
        已完成
      </span>
    );
  }
  if (status === "processing") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-accent/15 bg-accent-soft px-2.5 py-1 text-[11px] font-medium text-accent">
        <Loader2 size={13} className="animate-spin" />
        处理中
      </span>
    );
  }
  return (
    <span className={cn("shrink-0 rounded-full border border-line px-2.5 py-1 text-[11px] font-medium", STATUS_CLASS[status])}>
      {workflowStatusLabel(status)}
    </span>
  );
}

export function WorkflowTaskBoard({ model }: WorkflowTaskBoardProps) {
  const [expanded, setExpanded] = useState(false);
  const current = model.currentStep;

  return (
    <div>
      <section
        className={cn(
          "overflow-hidden rounded-t-[14px] border border-[#dedede] bg-[#f7f7f7]",
          expanded ? "rounded-b-[10px]" : "rounded-b-none",
        )}
      >
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          className="flex min-h-12 w-full items-center gap-3 px-5 text-left transition-colors hover:bg-black/[0.025]"
        >
          {expanded ? (
            <span className="flex min-w-0 flex-1 items-center gap-2 text-[13px] text-[#666]">
              <span>任务</span>
              <span className="rounded-full bg-[#e9e9e9] px-2 py-0.5 text-[11px] text-[#777]">{model.steps.length}</span>
            </span>
          ) : (
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[#5f5f5f]">{current.label}</span>
          )}
          <span className="flex min-w-0 shrink-0 items-center gap-2">
            {expanded ? <span className="max-w-[240px] truncate text-[12px] text-[#777]">{current.label}</span> : null}
            <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium", STATUS_CLASS[current.status])}>
              <StatusIcon status={current.status} />
              {workflowStatusLabel(current.status)}
            </span>
          </span>
          <ChevronUp size={16} className={cn("shrink-0 text-[#777] transition-transform duration-300 ease-out", expanded && "rotate-180")} />
        </button>

        <div
          aria-hidden={!expanded}
          className={cn(
            "grid transition-[grid-template-rows,opacity,transform] duration-300 ease-out",
            expanded ? "grid-rows-[1fr] translate-y-0 opacity-100" : "pointer-events-none grid-rows-[0fr] translate-y-3 opacity-0",
          )}
        >
          <div className="min-h-0 overflow-hidden">
            <div className="border-t border-[#e6e6e6] px-5 pb-3">
              <div className="divide-y divide-[#e7e7e7]">
                {model.steps.map((step) => (
                  <div key={step.id} className="flex min-h-10 items-center gap-3 py-1.5">
                    <span className={cn("min-w-0 flex-1 truncate text-[13px]", step.status === "pending" || step.status === "skipped" ? "text-[#929292]" : "text-[#686868]")}>{step.label}</span>
                    <RowStatus status={step.status} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
