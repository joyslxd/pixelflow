import { NavLink, useNavigate } from "react-router-dom";
import { SquarePen } from "lucide-react";
import { cn } from "@/lib/utils";

// 占位对话列表(待接 /api/tasks)
const THREADS = [
  { id: "t1", title: "45度俯拍水果刀切果肉", count: 5 },
  { id: "t2", title: "保温杯冬季通勤种草", count: 3 },
  { id: "t3", title: "口红草莓釉质特写", count: 6 },
];

export function Sidebar() {
  const navigate = useNavigate();
  return (
    <aside className="flex w-[244px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center px-5 pb-3 pt-5">
        <span className="text-[18px] font-extrabold tracking-tight text-brand">
          BORG RISE
        </span>
      </div>

      <div className="px-3">
        <button
          onClick={() => navigate("/")}
          className="flex w-full items-center gap-2 rounded-xl border border-line bg-canvas px-3 py-2.5 text-[14px] font-medium text-ink transition-colors hover:border-accent/30 hover:text-accent"
        >
          <SquarePen size={16} />
          新建对话
        </button>
      </div>

      <div className="mt-5 px-5 text-[12px] font-medium text-ink-soft/70">
        最近对话
      </div>
      <nav className="mt-1 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
        {THREADS.map((t) => (
          <NavLink
            key={t.id}
            to={`/c/${t.id}`}
            className={({ isActive }) =>
              cn(
                "flex items-center justify-between rounded-lg px-3 py-2 text-[13px] transition-colors",
                isActive
                  ? "bg-accent-soft text-accent"
                  : "text-ink/80 hover:bg-canvas",
              )
            }
          >
            <span className="truncate">{t.title}</span>
            <span className="ml-2 shrink-0 text-[12px] text-ink-soft/70">
              {t.count}
            </span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
