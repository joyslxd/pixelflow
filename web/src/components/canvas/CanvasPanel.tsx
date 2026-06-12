import { LayoutPanelLeft } from "lucide-react";
import { BriefCard } from "./BriefCard";
import { VideoResultGrid } from "./VideoResultGrid";
import type { CanvasState } from "@/lib/chat";

interface CanvasPanelProps {
  state: CanvasState;
  onApprove: () => void;
  onRevise: () => void;
}

const PHASE_LABEL: Record<string, string> = {
  idle: "画布",
  intake: "采集中",
  creative: "策划中",
  brief_review: "Brief 待确认",
  generate: "生成中",
  edit: "剪辑中",
  qc: "质检中",
  done: "已完成",
};

export function CanvasPanel({ state, onApprove, onRevise }: CanvasPanelProps) {
  const { phase, brief, results, estCost, actualCost } = state;
  return (
    <div className="flex w-[46%] min-w-[380px] flex-col bg-canvas">
      <div className="flex h-12 shrink-0 items-center justify-between px-5">
        <span className="text-[14px] font-semibold text-ink">
          {PHASE_LABEL[phase] ?? "画布"}
        </span>
        {(estCost != null || actualCost != null) && (
          <div className="flex items-center gap-3 text-[12px]">
            {estCost != null && (
              <span className="text-amber">预计消耗 {estCost.toFixed(2)}</span>
            )}
            {actualCost != null && (
              <span className="text-emerald">实际扣减 {actualCost.toFixed(2)}</span>
            )}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-6">
        {phase === "brief_review" && brief ? (
          <BriefCard brief={brief} onApprove={onApprove} onRevise={onRevise} />
        ) : results.length > 0 ? (
          <VideoResultGrid results={results} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center text-ink-soft">
            <LayoutPanelLeft size={28} className="mb-2 opacity-40" />
            <p className="text-[14px]">画布</p>
            <p className="mt-1 text-[12px]">Brief、生成进度与成片会展示在这里。</p>
          </div>
        )}
      </div>
    </div>
  );
}
