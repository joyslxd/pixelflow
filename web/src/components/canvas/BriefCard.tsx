import { Check, ChevronDown, Pencil } from "lucide-react";
import type { Brief } from "@/lib/chat";

interface BriefCardProps {
  brief: Brief;
  onApprove: () => void;
  onRevise: () => void;
  readonly?: boolean;
}

const SCENE_LABEL: Record<string, string> = {
  hook: "开场",
  pain_point: "痛点",
  solution: "卖点",
  demo: "演示",
  social_proof: "背书",
  cta: "转化",
};

const ASSET_LABEL: Record<string, string> = {
  use_real_asset: "实拍素材",
  generate_asset: "生成素材",
  use_reference_structure: "参考复刻",
  mixed: "混合策略",
};

function Detail({ label, children }: { label: string; children?: string | null }) {
  if (!children) return null;
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium text-ink-soft">{label}</div>
      <div className="mt-0.5 break-words text-[12px] leading-5 text-ink/85">{children}</div>
    </div>
  );
}

export function BriefCard({ brief, onApprove, onRevise, readonly = false }: BriefCardProps) {
  const gv = brief.globalVisual;
  return (
    <div className="rounded-card border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div>
          <div className="text-[14px] font-semibold text-ink">{brief.title}</div>
          <div className="mt-0.5 text-[12px] text-ink-soft">
            {brief.platform} · {brief.ratio} · {brief.size || "默认尺寸"} · {brief.durationSec}s · {brief.shots.length} 个分镜
          </div>
        </div>
        <span className="rounded-full bg-amber/10 px-2.5 py-1 text-[12px] font-medium text-amber">
          {readonly ? "已确认" : "待确认"}
        </span>
      </div>

      {gv && (
        <div className="grid gap-3 border-b border-line px-4 py-3 md:grid-cols-2">
          <Detail label="主体" children={gv.subjectType} />
          <Detail label="场景" children={gv.environment} />
          <Detail label="光线" children={gv.lighting} />
          <Detail label="整体风格" children={gv.overallStyle} />
          <Detail label="人物/手部风格" children={gv.characterStyle} />
          <Detail label="禁止元素" children={gv.forbiddenElements} />
        </div>
      )}

      <div className="divide-y divide-line">
        {brief.shots.map((s, i) => (
          <details key={s.shotId} className="group px-4 py-3" open={i === 0}>
            <summary className="flex cursor-pointer list-none gap-3">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-accent-soft text-[11px] font-semibold text-accent">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-soft">
                <span className="font-medium text-ink/70">
                  {SCENE_LABEL[s.sceneType] ?? s.sceneType}
                </span>
                <span>{s.timeRange}</span>
                <span>{s.durationSec}s</span>
                {s.shotType && <span>{s.shotType}</span>}
                {s.cameraMovement && <span>{s.cameraMovement}</span>}
                {s.assetStrategy && (
                  <span className="rounded bg-canvas px-1.5 py-0.5 text-[11px] text-ink-soft">
                    {ASSET_LABEL[s.assetStrategy] ?? s.assetStrategy}
                  </span>
                )}
              </div>
              <div className="mt-1 text-[13px] font-medium leading-5 text-ink">
                {s.visualDescription || s.narration || "暂无画面描述"}
              </div>
              {s.onscreen && (
                <div className="mt-1 text-[12px] text-accent">花字: {s.onscreen}</div>
              )}
            </div>
            <ChevronDown size={15} className="mt-1 shrink-0 text-ink-soft transition-transform group-open:rotate-180" />
            </summary>
            <div className="ml-8 mt-3 grid gap-3 rounded-lg bg-canvas px-3 py-3 md:grid-cols-2">
              <Detail label="旁白" children={s.narration} />
              <Detail label="转场" children={[s.transitionIn, s.transitionOut].filter(Boolean).join(" → ")} />
              <Detail label="BGM" children={s.audio?.bgmVibe || ""} />
              <Detail label="音效" children={s.audio?.sfx || ""} />
              <div className="md:col-span-2">
                <Detail label="生成 Prompt" children={s.generationPrompt} />
              </div>
            </div>
          </details>
        ))}
      </div>

      {!readonly && (
        <div className="flex gap-2 border-t border-line p-3">
          <button
            onClick={onApprove}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[14px] font-medium text-white hover:opacity-90"
          >
            <Check size={16} /> 确认,开始生成
          </button>
          <button
            onClick={onRevise}
            className="flex items-center justify-center gap-1.5 rounded-xl border border-line px-4 py-2.5 text-[14px] font-medium text-ink hover:bg-canvas"
          >
            <Pencil size={15} /> 修改
          </button>
        </div>
      )}
    </div>
  );
}
