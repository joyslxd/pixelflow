import { Check, Pencil } from "lucide-react";
import type { Brief } from "@/lib/chat";

interface BriefCardProps {
  brief: Brief;
  onApprove: () => void;
  onRevise: () => void;
}

const SCENE_LABEL: Record<string, string> = {
  hook: "开场",
  pain_point: "痛点",
  solution: "卖点",
  demo: "演示",
  social_proof: "背书",
  cta: "转化",
};

export function BriefCard({ brief, onApprove, onRevise }: BriefCardProps) {
  return (
    <div className="rounded-card border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div>
          <div className="text-[14px] font-semibold text-ink">{brief.title}</div>
          <div className="mt-0.5 text-[12px] text-ink-soft">
            {brief.platform} · {brief.ratio} · {brief.durationSec}s · {brief.shots.length} 个分镜
          </div>
        </div>
        <span className="rounded-full bg-amber/10 px-2.5 py-1 text-[12px] font-medium text-amber">
          待确认
        </span>
      </div>

      <div className="divide-y divide-line">
        {brief.shots.map((s, i) => (
          <div key={s.shotId} className="flex gap-3 px-4 py-2.5">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-accent-soft text-[11px] font-semibold text-accent">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-[12px] text-ink-soft">
                <span className="font-medium text-ink/70">
                  {SCENE_LABEL[s.sceneType] ?? s.sceneType}
                </span>
                <span>{s.timeRange}</span>
                <span>{s.durationSec}s</span>
              </div>
              <div className="mt-0.5 text-[13px] text-ink">{s.narration}</div>
              {s.onscreen && (
                <div className="mt-0.5 text-[12px] text-accent">花字:{s.onscreen}</div>
              )}
            </div>
          </div>
        ))}
      </div>

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
    </div>
  );
}
