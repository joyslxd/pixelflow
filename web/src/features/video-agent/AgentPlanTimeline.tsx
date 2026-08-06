import {
  CheckCircle2,
  CircleDashed,
  CircleX,
  Clock3,
  LoaderCircle,
  PauseCircle,
  SkipForward,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { VideoAgentPlanState, VideoAgentStepState } from "./state/contracts";

interface AgentPlanTimelineProps {
  plan: VideoAgentPlanState | null;
  now?: number;
}

const statusLabel: Record<VideoAgentStepState["status"], string> = {
  pending: "待执行",
  running: "正在执行",
  awaiting_confirmation: "等待确认",
  completed: "已完成",
  failed: "执行失败",
  skipped: "已跳过",
};

function StatusIcon({ status }: Pick<VideoAgentStepState, "status">) {
  const className = "size-4";
  if (status === "running") return <LoaderCircle className={`${className} animate-spin text-sky-600`} />;
  if (status === "completed") return <CheckCircle2 className={`${className} text-emerald-600`} />;
  if (status === "failed") return <CircleX className={`${className} text-rose-600`} />;
  if (status === "awaiting_confirmation") return <PauseCircle className={`${className} text-amber-600`} />;
  if (status === "skipped") return <SkipForward className={`${className} text-slate-400`} />;
  return <CircleDashed className={`${className} text-slate-400`} />;
}

export function formatAgentStepDuration(durationMs: number | null): string | null {
  if (durationMs === null || durationMs < 0) return null;
  const totalSeconds = Math.floor(durationMs / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}分${seconds}秒` : `${seconds}秒`;
}

function displayedDuration(step: VideoAgentStepState, now: number): string | null {
  if (step.durationMs !== null) return formatAgentStepDuration(step.durationMs);
  if (step.status !== "running" || step.startedAt === null) return null;
  const startedAt = Date.parse(step.startedAt);
  return Number.isNaN(startedAt) ? null : formatAgentStepDuration(Math.max(0, now - startedAt));
}

export function AgentPlanTimeline({ plan, now }: AgentPlanTimelineProps) {
  const [liveNow, setLiveNow] = useState(() => now ?? Date.now());
  const hasRunningStep = plan !== null
    && Object.values(plan.steps).some((step) => step.status === "running");
  useEffect(() => {
    if (now !== undefined || !hasRunningStep) return undefined;
    const timer = window.setInterval(() => setLiveNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [hasRunningStep, now]);
  if (plan === null) return null;
  const steps = Object.values(plan.steps).sort((left, right) => left.sequence - right.sequence);
  if (steps.length === 0) return null;
  const displayNow = now ?? liveNow;

  return (
    <section aria-label="执行步骤" className="border-b border-slate-200 bg-white px-4 py-3">
      <div className="mx-auto max-w-6xl">
        {plan.publicGoal ? <p className="mb-2 text-sm font-medium text-slate-800">{plan.publicGoal}</p> : null}
        <ol className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {steps.map((step) => {
            const duration = displayedDuration(step, displayNow);
            return (
              <li key={step.stepId} className="min-w-0 border-l-2 border-slate-200 pl-3">
                <div className="flex items-center gap-2 text-sm text-slate-700">
                  <StatusIcon status={step.status} />
                  <span className="truncate font-medium">{step.sequence}. {step.title}</span>
                  <span className="ml-auto shrink-0 text-xs text-slate-500">{statusLabel[step.status]}</span>
                </div>
                <div className="mt-1 flex min-h-5 items-center gap-1 text-xs text-slate-500">
                  {duration ? <><Clock3 className="size-3" />{duration}</> : null}
                  {step.publicSummary ? <span className="truncate">{step.publicSummary}</span> : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
