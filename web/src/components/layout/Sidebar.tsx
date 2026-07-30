import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { SquarePen } from "lucide-react";
import { api, type ConversationSummaryResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 5;

export function Sidebar() {
  const navigate = useNavigate();
  const [items, setItems] = useState<ConversationSummaryResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadConversations = async (cursor?: string | null, append = false) => {
    if (loading) return;
    setLoading(true);
    setError("");
    try {
      const page = await api.listConversations({ pageSize: PAGE_SIZE, cursor });
      setItems((prev) => (append ? [...prev, ...page.items] : page.items));
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "历史对话加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadConversations();
    const refresh = () => void loadConversations();
    window.addEventListener("pixelflow-conversations-updated", refresh);
    return () => window.removeEventListener("pixelflow-conversations-updated", refresh);
  }, []);

  return (
    <aside className="hidden xl:flex w-[244px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="px-3 pt-5">
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
        {items.map((t) => (
          <NavLink
            key={t.conversation_id}
            to={`/c/${t.conversation_id}`}
            className={({ isActive }) =>
              cn(
                "flex items-center justify-between rounded-lg px-3 py-2 text-[13px] transition-colors",
                isActive
                  ? "bg-accent-soft text-accent"
                  : "text-ink/80 hover:bg-canvas",
              )
            }
          >
            <span className="truncate">{t.title || "新的对话"}</span>
            <span className="ml-2 shrink-0 text-[12px] text-ink-soft/70">
              {t.last_phase === "idle" ? "新" : t.last_phase}
            </span>
          </NavLink>
        ))}
        {!loading && items.length === 0 && (
          <div className="px-3 py-2 text-[12px] text-ink-soft/70">
            {error || "暂无历史对话"}
          </div>
        )}
        {error && items.length > 0 && (
          <div className="px-3 py-2 text-[12px] text-red-500">
            历史加载失败
          </div>
        )}
        {nextCursor && (
          <button
            type="button"
            onClick={() => void loadConversations(nextCursor, true)}
            disabled={loading}
            className="mt-2 w-full rounded-lg px-3 py-2 text-left text-[12px] text-ink-soft hover:bg-canvas disabled:opacity-40"
          >
            {loading ? "加载中..." : "加载更多"}
          </button>
        )}
      </nav>
    </aside>
  );
}
