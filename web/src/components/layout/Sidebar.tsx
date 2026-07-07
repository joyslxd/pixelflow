import { NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, SquarePen } from "lucide-react";
import { api, type SessionContextResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

function titleForSession(session: SessionContextResponse): string {
  const messages = Array.isArray(session.context.messages) ? session.context.messages : [];
  const firstUser = messages.find((message) => {
    if (!message || typeof message !== "object") return false;
    return (message as { role?: unknown }).role === "user";
  }) as { content?: unknown } | undefined;
  if (typeof firstUser?.content === "string" && firstUser.content.trim()) return firstUser.content.trim();
  if (session.task_id.startsWith("chat-")) return "未命名对话";
  return `任务 ${session.task_id.slice(0, 8)}`;
}

function countForSession(session: SessionContextResponse): number {
  const messages = Array.isArray(session.context.messages) ? session.context.messages : [];
  return messages.length;
}

export function Sidebar() {
  const navigate = useNavigate();
  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.listSessionContexts(50),
    refetchInterval: 10000,
  });
  const sessions = sessionsQuery.data ?? [];
  return (
    <aside className="flex w-[244px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center px-5 pb-3 pt-5">
        <span className="text-[18px] font-extrabold tracking-tight text-brand">
          Pixel Flow
        </span>
      </div>

      <div className="px-3">
        <button
          onClick={() => {
            localStorage.removeItem("pixelflow.workspace.session.v1");
            navigate("/?new=1");
          }}
          className="flex w-full items-center gap-2 rounded-xl border border-line bg-canvas px-3 py-2.5 text-[14px] font-medium text-ink transition-colors hover:border-accent/30 hover:text-accent"
        >
          <SquarePen size={16} />
          新建对话
        </button>
      </div>

      <nav className="mt-3 px-2">
        <NavLink
          to="/trace"
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors",
              isActive ? "bg-accent-soft text-accent" : "text-ink/80 hover:bg-canvas",
            )
          }
        >
          <Activity size={16} />
          Agent Trace
        </NavLink>
      </nav>

      <div className="mt-5 px-5 text-[12px] font-medium text-ink-soft/70">
        最近对话
      </div>
      <nav className="mt-1 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
        {sessions.length === 0 && (
          <div className="px-3 py-2 text-[13px] text-ink-soft">
            暂无历史
          </div>
        )}
        {sessions.map((session) => (
          <NavLink
            key={session.task_id}
            to={`/?session=${encodeURIComponent(session.task_id)}`}
            className={({ isActive }) =>
              cn(
                "flex items-center justify-between rounded-lg px-3 py-2 text-[13px] transition-colors",
                isActive
                  ? "bg-accent-soft text-accent"
                  : "text-ink/80 hover:bg-canvas",
              )
            }
          >
            <span className="truncate">{titleForSession(session)}</span>
            <span className="ml-2 shrink-0 text-[12px] text-ink-soft/70">
              {countForSession(session)}
            </span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
