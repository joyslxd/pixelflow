import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  CircleX,
  Clock3,
  LoaderCircle,
  PauseCircle,
  SkipForward,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { VideoAgentPlanState, VideoAgentStepState } from "./state/contracts";
import type { VideoAgentScriptStageEvidence } from "./state/workspace";
import {
  extractStageChangeHints,
  shortStageLabel,
  stageIdFromStep,
} from "./scriptSkillStages";

interface AgentPlanTimelineProps {
  plan: VideoAgentPlanState | null;
  now?: number;
  selectedStepId?: string | null;
  scriptStages?: VideoAgentScriptStageEvidence[];
  onSelectStep?(stepId: string): void;
  confirmationSlot?: ReactNode;
  quotaSlot?: ReactNode;
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
  const className = "size-4 shrink-0";
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

function stepTone(status: VideoAgentStepState["status"]): string {
  if (status === "running") return "border-sky-200 bg-sky-50/70";
  if (status === "completed") return "border-emerald-200 bg-white";
  if (status === "failed") return "border-rose-200 bg-rose-50/60";
  if (status === "awaiting_confirmation") return "border-amber-200 bg-amber-50/70";
  return "border-slate-200 bg-white";
}

function stageEvidenceForStep(
  step: VideoAgentStepState,
  scriptStages: readonly VideoAgentScriptStageEvidence[],
): VideoAgentScriptStageEvidence | null {
  const stageId = stageIdFromStep(step);
  if (!stageId) return null;
  return scriptStages.find((stage) => stage.stageId === stageId) ?? null;
}

function stepChangeHints(
  step: VideoAgentStepState,
  stage: VideoAgentScriptStageEvidence | null,
): string[] {
  if (stage?.changeSummary) return [stage.changeSummary];
  if (stage?.content) {
    const hints = extractStageChangeHints(stage.content);
    if (hints.length > 0) return hints;
  }
  if (step.publicSummary) {
    const cleaned = step.publicSummary
      .replace(/^已(?:完成|复用)\s*/u, "")
      .trim();
    if (cleaned) return [`本步产物：${cleaned}`];
  }
  return [];
}

function viewResultLabel(step: VideoAgentStepState, stage: VideoAgentScriptStageEvidence | null): string {
  const label = shortStageLabel(stageIdFromStep(step), step.title);
  return `查看本步新增：${label}`;
}

export function AgentPlanTimeline({
  plan,
  now,
  selectedStepId = null,
  scriptStages = [],
  onSelectStep,
  confirmationSlot,
  quotaSlot,
}: AgentPlanTimelineProps) {
  const [liveNow, setLiveNow] = useState(() => now ?? Date.now());
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});
  const hasRunningStep = plan !== null
    && Object.values(plan.steps).some((step) => step.status === "running");
  useEffect(() => {
    if (now !== undefined || !hasRunningStep) return undefined;
    const timer = window.setInterval(() => setLiveNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [hasRunningStep, now]);
  if (plan === null) return null;
  const steps = Object.values(plan.steps).sort((left, right) => left.sequence - right.sequence);
  // 步骤尚未推到前端时也保留标题卡，避免「执行方案 · …」闪一下就消失。
  const displayNow = now ?? liveNow;
  const completedCount = steps.filter((step) => step.status === "completed").length;
  const planStatusLabel = plan.status === "completed"
    ? "已完成"
    : plan.status === "awaiting_confirmation"
      ? "待确认"
      : plan.status === "failed"
        ? "失败"
        : plan.status === "planning" || steps.length === 0
          ? "规划中"
          : "进行中";

  return (
    <section
      aria-label="执行步骤"
      className="mr-auto w-full max-w-[720px] rounded-2xl border border-slate-200 bg-[#fbfcfd] p-3 shadow-sm"
    >
      <header className="mb-3 flex flex-wrap items-center gap-2 border-b border-slate-100 pb-3">
        <p className="text-[13px] font-semibold text-slate-900">
          执行方案{plan.publicGoal ? ` · ${plan.publicGoal}` : ""}
        </p>
        <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600">
          {steps.length} 步 · {planStatusLabel}
        </span>
        <span className="text-[11px] text-slate-400">
          已完成 {completedCount}/{steps.length || "?"}
        </span>
      </header>

      {steps.length === 0 ? (
        <p className="px-1 py-2 text-[12px] text-slate-500">规划中，正在生成执行步骤…</p>
      ) : (
      <ol className="space-y-2">
        {steps.map((step) => {
          const duration = displayedDuration(step, displayNow);
          const selected = selectedStepId === step.stepId;
          const expanded = expandedIds[step.stepId]
            ?? (step.status === "running" || step.status === "awaiting_confirmation" || selected);
          const hasProgressLog = step.progressLog.length > 0;
          const stage = stageEvidenceForStep(step, scriptStages);
          const changeHints = stepChangeHints(step, stage);
          const canExpand = Boolean(step.publicSummary)
            || hasProgressLog
            || step.artifactRefs.length > 0
            || changeHints.length > 0
            || step.status === "running"
            || step.status === "completed"
            || step.status === "failed";
          const liveStatus = step.status === "running"
            ? (step.publicSummary || "正在执行该步骤…")
            : null;
          const canViewResult = Boolean(onSelectStep)
            && (step.status === "completed" || Boolean(stage) || step.artifactRefs.length > 0);
          return (
            <li key={step.stepId}>
              <article
                className={`rounded-xl border px-3 py-2.5 transition-colors ${stepTone(step.status)} ${
                  selected ? "ring-2 ring-sky-300" : ""
                }`}
              >
                <button
                  type="button"
                  className="flex w-full items-start gap-2 text-left"
                  onClick={() => {
                    onSelectStep?.(step.stepId);
                    if (canExpand) {
                      setExpandedIds((current) => ({
                        ...current,
                        [step.stepId]: !expanded,
                      }));
                    }
                  }}
                >
                  <StatusIcon status={step.status} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium text-slate-800">
                        {step.sequence}. {step.title}
                      </span>
                      <span className="text-[11px] text-slate-500">{statusLabel[step.status]}</span>
                      {duration ? (
                        <span className="inline-flex items-center gap-1 text-[11px] text-slate-500">
                          <Clock3 className="size-3" />
                          {duration}
                        </span>
                      ) : null}
                    </div>
                    {liveStatus ? (
                      <p className="mt-1 text-[12px] leading-5 text-sky-700">
                        {liveStatus}
                      </p>
                    ) : !expanded && changeHints.length > 0 ? (
                      <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-slate-600">
                        {changeHints[0]}
                      </p>
                    ) : !expanded && step.publicSummary ? (
                      <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-slate-600">
                        {step.publicSummary}
                      </p>
                    ) : null}
                  </div>
                  {canExpand ? (
                    expanded
                      ? <ChevronDown className="mt-0.5 size-4 shrink-0 text-slate-400" />
                      : <ChevronRight className="mt-0.5 size-4 shrink-0 text-slate-400" />
                  ) : null}
                </button>

                {expanded ? (
                  <div className="mt-2 space-y-2 border-t border-black/5 pt-2 pl-6">
                    {step.status === "running" && hasProgressLog ? (
                      <ol className="space-y-1.5">
                        {step.progressLog.map((item, index) => {
                          const isLatest = index === step.progressLog.length - 1;
                          return (
                            <li
                              key={`${step.stepId}-phase-${index}`}
                              className={`flex items-start gap-2 text-[12px] leading-5 ${
                                isLatest ? "text-sky-700" : "text-slate-500"
                              }`}
                            >
                              {isLatest ? (
                                <LoaderCircle className="mt-0.5 size-3.5 shrink-0 animate-spin" />
                              ) : (
                                <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
                              )}
                              <span>{item}</span>
                            </li>
                          );
                        })}
                      </ol>
                    ) : hasProgressLog ? (
                      <ol className="space-y-1.5">
                        {step.progressLog.map((item, index) => (
                          <li
                            key={`${step.stepId}-done-${index}`}
                            className="flex items-start gap-2 text-[12px] leading-5 text-slate-600"
                          >
                            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ol>
                    ) : null}
                    {changeHints.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {changeHints.map((hint) => (
                          <span
                            key={`${step.stepId}-${hint}`}
                            className="rounded-full border border-emerald-200 bg-emerald-50/80 px-2 py-0.5 text-[11px] text-emerald-800"
                          >
                            {hint}
                          </span>
                        ))}
                      </div>
                    ) : step.publicSummary ? (
                      <p className="text-[12px] leading-5 text-slate-700">{step.publicSummary}</p>
                    ) : !hasProgressLog ? (
                      <p className="text-[12px] text-slate-400">暂无结果摘要</p>
                    ) : null}
                    {canViewResult ? (
                      <button
                        type="button"
                        className="text-[12px] font-medium text-sky-700 hover:underline"
                        onClick={() => onSelectStep?.(step.stepId)}
                      >
                        {viewResultLabel(step, stage)} →
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </article>

              {step.status === "awaiting_confirmation" && confirmationSlot ? (
                <div className="mt-2 pl-2">{confirmationSlot}</div>
              ) : null}
              {step.status === "running" && quotaSlot ? (
                <div className="mt-2 pl-2">{quotaSlot}</div>
              ) : null}
            </li>
          );
        })}
      </ol>
      )}
    </section>
  );
}
