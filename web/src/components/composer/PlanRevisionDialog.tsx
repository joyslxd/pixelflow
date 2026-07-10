import { useEffect, useState } from "react";
import { Check, Pencil, RefreshCw, X } from "lucide-react";

export type PlanRevisionMode = "extend_current" | "regenerate_directions";

interface PlanRevisionDialogProps {
  open: boolean;
  feedback: string;
  onConfirm: (mode: PlanRevisionMode) => void;
  onCancel: () => void;
}

const OPTIONS: Array<{
  value: PlanRevisionMode;
  label: string;
  description: string;
  icon: typeof Pencil;
}> = [
  {
    value: "extend_current",
    label: "在当前创意基础上扩展/修改",
    description: "保留当前创意方向，只更新 plan.md 并生成一个新版本。",
    icon: Pencil,
  },
  {
    value: "regenerate_directions",
    label: "放弃当前创意，重新生成新创意",
    description: "返回创意阶段，重新生成 3 个创意方向供你选择。",
    icon: RefreshCw,
  },
];

export function PlanRevisionDialog({ open, feedback, onConfirm, onCancel }: PlanRevisionDialogProps) {
  const [mode, setMode] = useState<PlanRevisionMode>("extend_current");

  useEffect(() => {
    if (open) setMode("extend_current");
  }, [open, feedback]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-ink/25 p-5">
      <div className="w-full max-w-[600px] rounded-2xl border border-line bg-surface p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-[20px] font-semibold text-ink">你希望如何处理这次修改？</h2>
            <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">修改意见：{feedback}</p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-soft hover:bg-canvas hover:text-ink"
            aria-label="关闭 Plan 修改方式选择"
          >
            <X size={18} />
          </button>
        </div>

        <div className="mt-5 space-y-3" role="radiogroup" aria-label="Plan 修改方式">
          {OPTIONS.map((option) => {
            const selected = mode === option.value;
            const Icon = option.icon;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setMode(option.value)}
                className={`flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors ${
                  selected ? "border-accent bg-accent-soft/60" : "border-line bg-white hover:border-accent/40"
                }`}
              >
                <span className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${selected ? "bg-accent text-white" : "bg-canvas text-ink-soft"}`}>
                  <Icon size={18} />
                </span>
                <span className="min-w-0">
                  <span className="block text-[14px] font-semibold text-ink">{option.label}</span>
                  <span className="mt-1 block text-[12px] leading-relaxed text-ink-soft">{option.description}</span>
                </span>
              </button>
            );
          })}
        </div>

        <button
          type="button"
          onClick={() => onConfirm(mode)}
          className="mt-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-brand text-[14px] font-medium text-white hover:opacity-90"
        >
          <Check size={18} />
          确认修改方式
        </button>
      </div>
    </div>
  );
}
