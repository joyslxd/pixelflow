import type { ComponentType, ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChipProps {
  icon?: ComponentType<{ size?: number; className?: string }>;
  children: ReactNode;
  active?: boolean;
  dropdown?: boolean;
  onClick?: () => void;
}

/** 输入器底部的参数胶囊(视频生成 / 模型 / 比例 / 分辨率 …)。 */
export function Chip({ icon: Icon, children, active, dropdown, onClick }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-medium transition-colors",
        active
          ? "border-accent/30 bg-accent-soft text-accent"
          : "border-line bg-surface text-ink/80 hover:border-ink-soft/30 hover:text-ink",
      )}
    >
      {Icon && <Icon size={15} className="shrink-0" />}
      {children}
      {dropdown && <ChevronDown size={14} className="text-ink-soft" />}
    </button>
  );
}
