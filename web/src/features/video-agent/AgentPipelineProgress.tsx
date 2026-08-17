import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDashed,
  CircleX,
  LoaderCircle,
} from "lucide-react";
import { useEffect, useState } from "react";

export { resolveNativeSceneVideoBatchTotal } from "@/features/video-agent/sceneVideoBatchTotal";

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
  /** 默认收起为底栏一行；点击标题区展开步骤。 */
  defaultCollapsed?: boolean;
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

function collapsedHint(steps: AgentPipelineProgressStep[]): string {
  const failed = steps.find((step) => step.status === "failed");
  if (failed) return failed.detail || `${failed.title}失败`;
  const running = steps.find((step) => step.status === "running");
  if (running) return running.detail || `${running.title}进行中`;
  const pending = steps.find((step) => step.status === "pending");
  if (pending) return `下一步：${pending.title}`;
  return "流程已完成，点击展开查看";
}

export function AgentPipelineProgress({
  title,
  subtitle,
  steps,
  now: nowProp,
  defaultCollapsed = true,
}: AgentPipelineProgressProps) {
  const [now, setNow] = useState(nowProp ?? Date.now());
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
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
  const hint = collapsedHint(steps);
  return (
    <section
      aria-label={title}
      className="mr-auto w-full max-w-[720px] rounded-2xl border border-slate-200 bg-[#fbfcfd] shadow-sm"
    >
      <button
        type="button"
        aria-expanded={!collapsed}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left"
        onClick={() => setCollapsed((value) => !value)}
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[13px] font-semibold text-slate-900">{title}</p>
            {subtitle ? (
              <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600">
                {subtitle}
              </span>
            ) : null}
            <span className="text-[11px] text-slate-400">
              已完成 {completedCount}/{steps.length}
            </span>
          </div>
          {collapsed ? (
            <p className="mt-1 truncate text-[12px] leading-5 text-slate-600">{hint}</p>
          ) : null}
        </div>
        {collapsed ? (
          <ChevronDown className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
        ) : (
          <ChevronUp className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
        )}
      </button>
      {!collapsed ? (
        <ol className="space-y-2 border-t border-slate-100 px-3 pb-3 pt-3">
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
      ) : null}
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
        || normalized === "generate_scene_assets_failed"
      ) {
        next = {
          ...step,
          status: "completed",
          detail: normalized === "awaiting_image_model"
            ? "场景包已生成，请选择生图模型"
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
      } else if (normalized === "generate_scene_assets_failed") {
        next = {
          ...step,
          status: "failed",
          detail: "generate_scene_assets · 参考图生成失败，请检查场景包资产后重试",
        };
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
          detail: "请先选择生图模型，确认后再生成参考图",
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

export function applyAssetPackageStructureProgress(
  steps: AgentPipelineProgressStep[],
  progress: { phase?: string; message?: string } | null | undefined,
  nowIso = new Date().toISOString(),
): AgentPipelineProgressStep[] {
  const message = String(progress?.message || "").trim();
  if (!message) return steps;
  const phase = String(progress?.phase || "").trim();
  const detail = phase
    ? `prepare-scene-packages · ${message}`
    : message;
  const previousById = new Map(steps.map((step) => [step.id, step]));
  return steps.map((step) => {
    if (step.id !== "packages" || step.status === "completed" || step.status === "failed") {
      return step;
    }
    const next: AgentPipelineProgressStep = {
      ...step,
      status: "running",
      detail,
    };
    return stampStepTransition(previousById.get(step.id), next, nowIso);
  });
}

/** Job 404 / resume 失败时收掉「假运行中」进度，避免已用时继续涨、心跳文案冻结。 */
export function failAssetPackageProgressSteps(
  steps: AgentPipelineProgressStep[],
  detail = "任务已中断或过期，请从最新 plan / 场景包卡片手动重试",
  nowIso = new Date().toISOString(),
): AgentPipelineProgressStep[] {
  if (!steps.length) return steps;
  const previousById = new Map(steps.map((step) => [step.id, step]));
  return steps.map((step) => {
    if (step.status === "completed" || step.status === "failed") return step;
    if (step.status !== "running" && step.id !== "packages" && step.id !== "assets") {
      return step;
    }
    const next: AgentPipelineProgressStep = {
      ...step,
      status: "failed",
      detail,
    };
    return stampStepTransition(previousById.get(step.id), next, nowIso);
  });
}

export interface SceneVideoGenerationProgress {
  completed: number;
  total: number;
  scene_id?: string | null;
  scene_index?: number | null;
  ok?: boolean | null;
}

/** 分镜视频生成进度板（确认并生成后替换「视频资产包」进度）。 */
export function createSceneVideoProgressSteps(
  total: number,
  nowIso = new Date().toISOString(),
): AgentPipelineProgressStep[] {
  const safeTotal = Number.isFinite(total) && total > 0 ? Math.floor(total) : 0;
  return [
    {
      id: "videos",
      title: "生成分镜视频",
      status: "running",
      detail: safeTotal > 0
        ? `generate_scenes · 已启动 ${safeTotal} 个分镜视频，完成后回填预览`
        : "generate_scenes · 正在启动分镜视频生成",
      startedAt: nowIso,
      completedAt: null,
      durationMs: null,
    },
  ];
}

export function applySceneVideoProgress(
  steps: AgentPipelineProgressStep[],
  progress: SceneVideoGenerationProgress | null | undefined,
  nowIso = new Date().toISOString(),
): AgentPipelineProgressStep[] {
  if (!progress || progress.total <= 0) return steps;
  const completed = Math.max(0, Math.min(progress.completed, progress.total));
  const sceneLabel = progress.scene_index
    ? `第 ${progress.scene_index} 镜`
    : (progress.scene_id || "分镜");
  const statusText = progress.ok === false ? "失败" : "已完成，可预览";
  const detail = completed > 0
    ? `分镜视频 ${completed}/${progress.total}：${sceneLabel}${statusText}`
    : `分镜视频 0/${progress.total}：生成中，可打开「查看分镜」等待回填`;
  const done = completed >= progress.total;
  const base = steps.length > 0 ? steps : createSceneVideoProgressSteps(progress.total, nowIso);
  const previousById = new Map(base.map((step) => [step.id, step]));
  return base.map((step) => {
    if (step.id !== "videos") return step;
    const next: AgentPipelineProgressStep = {
      ...step,
      status: done ? "completed" : "running",
      detail: done ? `分镜视频已全部完成（${completed}/${progress.total}）` : detail,
    };
    return stampStepTransition(previousById.get(step.id), next, nowIso);
  });
}
