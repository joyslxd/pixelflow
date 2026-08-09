import {
  CheckCircle2,
  CircleDashed,
  CircleX,
  LoaderCircle,
} from "lucide-react";
import { useEffect, useState } from "react";

export type AgentPipelineStepStatus = "pending" | "running" | "completed" | "failed";

export interface AgentPipelineProgressStep {
  id: string;
  title: string;
  status: AgentPipelineStepStatus;
  detail?: string;
  startedAt?: string | null;
  completedAt?: string | null;
  durationMs?: number | null;
}

export interface SceneAssetGenerationProgress {
  completed: number;
  total: number;
  asset_id?: string;
  asset_name?: string;
  asset_type?: string;
  ok?: boolean;
  quota_insufficient?: boolean;
}

interface AgentPipelineProgressProps {
  title: string;
  subtitle?: string;
  steps: AgentPipelineProgressStep[];
  now?: number;
}

function StatusIcon({ status }: { status: AgentPipelineStepStatus }) {
  const className = "size-4 shrink-0";
  if (status === "running") return <LoaderCircle className={`${className} animate-spin text-sky-600`} />;
  if (status === "completed") return <CheckCircle2 className={`${className} text-emerald-600`} />;
  if (status === "failed") return <CircleX className={`${className} text-rose-600`} />;
  return <CircleDashed className={`${className} text-slate-400`} />;
}

function stepTone(status: AgentPipelineStepStatus): string {
  if (status === "running") return "border-sky-200 bg-sky-50/70";
  if (status === "completed") return "border-emerald-200 bg-white";
  if (status === "failed") return "border-rose-200 bg-rose-50/60";
  return "border-slate-200 bg-white";
}

const statusLabel: Record<AgentPipelineStepStatus, string> = {
  pending: "待执行",
  running: "正在执行",
  completed: "已完成",
  failed: "失败",
};

export function formatPipelineStepDuration(durationMs: number | null | undefined): string | null {
  if (durationMs == null || durationMs < 0) return null;
  const totalSeconds = Math.floor(durationMs / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}分${seconds}秒` : `${seconds}秒`;
}

function displayedStepDuration(step: AgentPipelineProgressStep, now: number): string | null {
  if (step.durationMs != null) return formatPipelineStepDuration(step.durationMs);
  if (step.status !== "running" || !step.startedAt) return null;
  const startedAt = Date.parse(step.startedAt);
  return Number.isNaN(startedAt) ? null : formatPipelineStepDuration(Math.max(0, now - startedAt));
}

function stampStepTransition(
  previous: AgentPipelineProgressStep | undefined,
  next: AgentPipelineProgressStep,
  nowIso: string,
): AgentPipelineProgressStep {
  const startedAt = next.startedAt
    ?? (next.status === "running" || next.status === "completed" || next.status === "failed"
      ? (previous?.startedAt || (previous?.status === next.status ? null : nowIso) || nowIso)
      : previous?.startedAt ?? null);
  if (next.status === "completed" || next.status === "failed") {
    const completedAt = next.completedAt || previous?.completedAt || nowIso;
    const durationMs = next.durationMs
      ?? previous?.durationMs
      ?? (startedAt ? Math.max(0, Date.parse(completedAt) - Date.parse(startedAt)) : null);
    return {
      ...next,
      startedAt: startedAt ?? previous?.startedAt ?? null,
      completedAt,
      durationMs: Number.isFinite(durationMs as number) ? durationMs : null,
    };
  }
  if (next.status === "running") {
    return {
      ...next,
      startedAt: startedAt || nowIso,
      completedAt: null,
      durationMs: null,
    };
  }
  return {
    ...next,
    startedAt: previous?.startedAt ?? next.startedAt ?? null,
    completedAt: previous?.completedAt ?? next.completedAt ?? null,
    durationMs: previous?.durationMs ?? next.durationMs ?? null,
  };
}

export function AgentPipelineProgress({
  title,
  subtitle,
  steps,
  now: nowProp,
}: AgentPipelineProgressProps) {
  const [now, setNow] = useState(nowProp ?? Date.now());
  useEffect(() => {
    if (nowProp != null) {
      setNow(nowProp);
      return;
    }
    if (!steps.some((step) => step.status === "running" && step.startedAt)) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [nowProp, steps]);
  if (steps.length === 0) return null;
  const completedCount = steps.filter((step) => step.status === "completed").length;
  return (
    <section
      aria-label={title}
      className="mr-auto w-full max-w-[720px] rounded-2xl border border-slate-200 bg-[#fbfcfd] p-3 shadow-sm"
    >
      <header className="mb-3 flex flex-wrap items-center gap-2 border-b border-slate-100 pb-3">
        <p className="text-[13px] font-semibold text-slate-900">{title}</p>
        {subtitle ? (
          <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600">
            {subtitle}
          </span>
        ) : null}
        <span className="text-[11px] text-slate-400">
          已完成 {completedCount}/{steps.length}
        </span>
      </header>
      <ol className="space-y-2">
        {steps.map((step, index) => {
          const durationLabel = displayedStepDuration(step, now);
          return (
            <li key={step.id}>
              <article className={`rounded-xl border px-3 py-2.5 ${stepTone(step.status)}`}>
                <div className="flex items-start gap-2">
                  <StatusIcon status={step.status} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium text-slate-800">
                        {index + 1}. {step.title}
                      </span>
                      <span className="text-[11px] text-slate-500">{statusLabel[step.status]}</span>
                      {durationLabel ? (
                        <span className="text-[11px] text-slate-400">
                          {step.status === "running" ? `已用时 ${durationLabel}` : `耗时 ${durationLabel}`}
                        </span>
                      ) : null}
                    </div>
                    {step.detail ? (
                      <p className="mt-1 text-[12px] leading-5 text-slate-600">{step.detail}</p>
                    ) : null}
                  </div>
                </div>
              </article>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function createAssetPackageProgressSteps(nowIso = new Date().toISOString()): AgentPipelineProgressStep[] {
  return [
    {
      id: "script",
      title: "读取脚本与镜头结构",
      status: "completed",
      detail: "已载入当前脚本，准备拆分场景",
      startedAt: nowIso,
      completedAt: nowIso,
      durationMs: 0,
    },
    {
      id: "packages",
      title: "调用场景包生成 Skill",
      status: "running",
      detail: "prepare-scene-packages · 生成可编辑分镜与全局资产",
      startedAt: nowIso,
      completedAt: null,
      durationMs: null,
    },
    {
      id: "assets",
      title: "生成场景参考图",
      status: "pending",
      detail: "generate-scene-assets · 按分镜产出参考图",
    },
    {
      id: "ready",
      title: "产出可确认视频资产包",
      status: "pending",
      detail: "完成后可在卡片中确认并继续成片",
    },
  ];
}

export function applyAssetPackageJobStage(
  steps: AgentPipelineProgressStep[],
  stage: string | null | undefined,
  nowIso = new Date().toISOString(),
): AgentPipelineProgressStep[] {
  const normalized = String(stage || "");
  const previousById = new Map(steps.map((step) => [step.id, step]));
  return steps.map((step) => {
    let next: AgentPipelineProgressStep = step;
    if (step.id === "script") {
      next = { ...step, status: "completed", detail: step.detail || "已载入当前脚本，准备拆分场景" };
    } else if (step.id === "packages") {
      if (
        normalized === "generate_scene_assets"
        || normalized === "awaiting_image_model"
        || normalized === "completed"
      ) {
        next = {
          ...step,
          status: "completed",
          detail: normalized === "awaiting_image_model"
            ? "场景包结构已生成，请选择生图模型"
            : "场景包结构已生成，可打开卡片查看详情",
        };
      } else {
        next = {
          ...step,
          status: "running",
          detail: "prepare-scene-packages · 正在生成可编辑分镜与全局资产",
        };
      }
    } else if (step.id === "assets") {
      if (normalized === "completed") {
        next = { ...step, status: "completed", detail: step.detail || "参考图已生成" };
      } else if (normalized === "generate_scene_assets") {
        next = {
          ...step,
          status: "running",
          detail: step.detail?.includes("参考图进度")
            ? step.detail
            : "generate-scene-assets · 正在生成场景参考图",
        };
      } else if (normalized === "awaiting_image_model") {
        next = {
          ...step,
          status: "pending",
          detail: "请选择生图模型（image-2 / Seedream 5.0）后再生成参考图",
        };
      } else {
        next = { ...step, status: "pending" };
      }
    } else if (step.id === "ready") {
      if (normalized === "completed") {
        next = { ...step, status: "completed", detail: "资产包已就绪，请确认" };
      } else {
        next = { ...step, status: "pending" };
      }
    }
    return stampStepTransition(previousById.get(step.id), next, nowIso);
  });
}

export function applyAssetPackageAssetProgress(
  steps: AgentPipelineProgressStep[],
  progress: SceneAssetGenerationProgress | null | undefined,
  nowIso = new Date().toISOString(),
): AgentPipelineProgressStep[] {
  if (!progress || progress.total <= 0) return steps;
  const typeLabel = progress.asset_type === "character"
    ? "角色"
    : progress.asset_type === "scene_image"
      ? "场景"
      : progress.asset_type === "prop_image"
        ? "道具"
        : "素材";
  const assetName = (progress.asset_name || "参考图").trim() || "参考图";
  const statusText = progress.ok === false ? "失败" : "已完成";
  const detail = `参考图进度 ${progress.completed}/${progress.total}：${typeLabel}「${assetName}」${statusText}`;
  const previousById = new Map(steps.map((step) => [step.id, step]));
  return steps.map((step) => {
    if (step.id !== "assets") return step;
    const next: AgentPipelineProgressStep = {
      ...step,
      status: progress.completed >= progress.total ? "completed" : "running",
      detail,
    };
    return stampStepTransition(previousById.get(step.id), next, nowIso);
  });
}
