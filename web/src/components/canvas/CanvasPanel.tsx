import { LayoutPanelLeft, X } from "lucide-react";
import { BriefCard } from "./BriefCard";
import { VideoResultGrid } from "./VideoResultGrid";
import type { CanvasState } from "@/lib/chat";

interface CanvasPanelProps {
  state: CanvasState;
  onApprove: () => void;
  onRevise: () => void;
  onConfirmStage?: (stage: "segments" | "edit" | "qc", approved: boolean) => void;
  onClose?: () => void;
  briefConfirmed?: boolean;
}

const PHASE_LABEL: Record<string, string> = {
  idle: "画布",
  intake: "采集中",
  creative: "策划中",
  brief_review: "Brief 待确认",
  generate: "生成中",
  edit: "剪辑中",
  qc: "质检中",
  segment_review: "片段待确认",
  edit_review: "剪辑待确认",
  qc_review: "质检待确认",
  done: "已完成",
};

const REVIEW_COPY = {
  segment_review: { stage: "segments", title: "生成片段已就绪", approve: "确认片段,开始剪辑", reject: "重新生成片段" },
  edit_review: { stage: "edit", title: "剪辑结果已就绪", approve: "确认剪辑,开始质检", reject: "重新剪辑" },
  qc_review: { stage: "qc", title: "质检结果已就绪", approve: "确认通过,完成任务", reject: "重新生成" },
} as const;

export function CanvasPanel({ state, onApprove, onRevise, onConfirmStage, onClose, briefConfirmed = false }: CanvasPanelProps) {
  const { phase, brief, results, qcReport, estCost, actualCost } = state;
  const canReviewBrief = phase === "brief_review" && Boolean(brief) && !briefConfirmed;
  const review = phase in REVIEW_COPY ? REVIEW_COPY[phase as keyof typeof REVIEW_COPY] : null;
  return (
    <div className="flex w-[46%] min-w-[380px] flex-col bg-canvas">
      <div className="flex h-12 shrink-0 items-center justify-between px-5">
        <span className="text-[14px] font-semibold text-ink">
          {PHASE_LABEL[phase] ?? "画布"}
        </span>
        <div className="flex items-center gap-3 text-[12px]">
          {(estCost != null || actualCost != null) && (
            <>
            {estCost != null && (
              <span className="text-amber">预计消耗 {estCost.toFixed(2)}</span>
            )}
            {actualCost != null && (
              <span className="text-emerald">实际扣减 {actualCost.toFixed(2)}</span>
            )}
            </>
          )}
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-soft hover:bg-white hover:text-ink"
            aria-label="关闭画布"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-6">
        {canReviewBrief ? (
          <BriefCard brief={brief!} onApprove={onApprove} onRevise={onRevise} />
        ) : brief && phase === "brief_review" ? (
          <BriefCard brief={brief} onApprove={onApprove} onRevise={onRevise} readonly />
        ) : review ? (
          <div className="space-y-3">
            {results.length > 0 && <VideoResultGrid results={results} />}
            {phase === "qc_review" && qcReport && (
              <div className="rounded-card border border-line bg-surface p-4">
                <div className="flex items-center justify-between">
                  <div className="text-[14px] font-semibold text-ink">质检报告</div>
                  <span className={qcReport.passed ? "text-[12px] font-medium text-emerald" : "text-[12px] font-medium text-rose-500"}>
                    {qcReport.passed ? "通过" : "未通过"}
                  </span>
                </div>
                {qcReport.score != null && (
                  <div className="mt-1 text-[12px] text-ink-soft">评分: {Math.round(qcReport.score * 100)} / 100</div>
                )}
                <div className="mt-3 divide-y divide-line">
                  {(qcReport.check_results || []).map((item, index) => (
                    <div key={`${item.item || "check"}-${index}`} className="py-2">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-[13px] font-medium text-ink">{item.item || `检查项 ${index + 1}`}</span>
                        <span className={item.status === "pass" ? "text-[12px] text-emerald" : "text-[12px] text-rose-500"}>
                          {item.status === "pass" ? "通过" : "需处理"}
                        </span>
                      </div>
                      {item.message && <div className="mt-0.5 text-[12px] text-ink-soft">{item.message}</div>}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="rounded-card border border-line bg-surface p-4">
              <div className="text-[14px] font-semibold text-ink">{review.title}</div>
              <p className="mt-1 text-[12px] text-ink-soft">请人工确认后再继续下一步。</p>
              <div className="mt-4 flex gap-2">
                <button
                  type="button"
                  onClick={() => onConfirmStage?.(review.stage, true)}
                  className="flex-1 rounded-xl bg-brand py-2.5 text-[14px] font-medium text-white hover:opacity-90"
                >
                  {review.approve}
                </button>
                <button
                  type="button"
                  onClick={() => onConfirmStage?.(review.stage, false)}
                  className="rounded-xl border border-line px-4 py-2.5 text-[14px] font-medium text-ink hover:bg-canvas"
                >
                  {review.reject}
                </button>
              </div>
            </div>
          </div>
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
