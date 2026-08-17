import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { History, SquarePen, X } from "lucide-react";
import { api, type ConversationSummaryResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 5;

const CONVERSATION_PHASE_LABELS: Record<string, string> = {
  idle: "新",
  plan_generation_running: "正在生成方案",
  plan_revision_running: "正在修改方案",
  plan_manual_edit_running: "正在发布编辑",
  plan_review: "待确认方案",
  scene_package_generation_running: "正在准备分镜",
  scene_global_asset_edit_model_pending: "待确认素材参数",
  scene_global_asset_revision_requested: "正在编辑素材",
  scene_global_asset_added: "素材已添加",
  scene_global_asset_deleted: "素材已删除",
  video_accepted: "已完成",
  form_cancelled: "已取消",
};

function conversationPhaseLabel(lastPhase: string): string {
  const normalized = lastPhase.trim().toLowerCase();
  const exactLabel = CONVERSATION_PHASE_LABELS[normalized];
  if (exactLabel) return exactLabel;
  if (normalized.includes("failed") || normalized.includes("error")) return "执行失败";
  if (normalized.includes("running") || normalized.includes("processing")) return "处理中";
  if (normalized.includes("review") || normalized.includes("pending") || normalized.includes("waiting")) return "待确认";
  if (
    normalized.includes("done")
    || normalized.includes("accepted")
    || normalized.includes("completed")
  ) {
    return "已完成";
  }
  return normalized ? "进行中" : "新";
}

export function Sidebar() {
  const navigate = useNavigate();
  const [items, setItems] = useState<ConversationSummaryResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // 窄屏（小于 lg）固定侧栏会藏住历史；用抽屉入口保证随时可打开。
  const [drawerOpen, setDrawerOpen] = useState(false);

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

  const startNewConversation = () => {
    setDrawerOpen(false);
    window.dispatchEvent(new Event("pixelflow-new-conversation"));
    navigate("/", { replace: true });
  };

  const conversationList = (
    <nav className="mt-1 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
      {items.map((t) => (
        <NavLink
          key={t.conversation_id}
          to={`/c/${t.conversation_id}`}
          onClick={(event) => {
            setDrawerOpen(false);
            // 抽屉内点击偶发被遮罩打断；显式 navigate 保证 hash 路由切到历史会话。
            event.preventDefault();
            navigate(`/c/${t.conversation_id}`);
          }}
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
            {conversationPhaseLabel(t.last_phase)}
          </span>
        </NavLink>
      ))}
      {loading && items.length === 0 && (
        <div className="px-3 py-2 text-[12px] text-ink-soft/70">加载中...</div>
      )}
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
  );

  return (
    <>
      {/* 桌面/宽 iframe：lg(1024) 起固定显示，避免 1280 断点把历史栏藏掉 */}
      <aside className="hidden lg:flex w-[244px] shrink-0 flex-col border-r border-line bg-surface">
        <div className="px-3 pt-5">
          <button
            type="button"
            onClick={startNewConversation}
            className="flex w-full items-center gap-2 rounded-xl border border-line bg-canvas px-3 py-2.5 text-[14px] font-medium text-ink transition-colors hover:border-accent/30 hover:text-accent"
          >
            <SquarePen size={16} />
            新建对话
          </button>
        </div>

        <div className="mt-5 px-5 text-[12px] font-medium text-ink-soft/70">
          最近对话
        </div>
        {conversationList}
      </aside>

      {/* 窄屏：浮动入口 + 左侧抽屉，保证历史列表始终可打开 */}
      <div className="lg:hidden">
        <button
          type="button"
          aria-label="打开历史对话"
          onClick={() => setDrawerOpen(true)}
          className="fixed left-3 top-3 z-40 flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-2 text-[12px] font-medium text-ink shadow-sm"
        >
          <History size={14} />
          历史
        </button>
        {drawerOpen ? (
          <div className="fixed inset-0 z-50 flex">
            <button
              type="button"
              aria-label="关闭历史对话"
              className="absolute inset-0 bg-black/35"
              onClick={() => setDrawerOpen(false)}
            />
            <aside className="relative z-10 flex h-full w-[min(288px,86vw)] flex-col border-r border-line bg-surface shadow-lg">
              <div className="flex items-center justify-between px-3 pt-4">
                <span className="text-[13px] font-medium text-ink">最近对话</span>
                <button
                  type="button"
                  aria-label="关闭"
                  onClick={() => setDrawerOpen(false)}
                  className="rounded-lg p-1.5 text-ink-soft hover:bg-canvas"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="px-3 pt-3">
                <button
                  type="button"
                  onClick={startNewConversation}
                  className="flex w-full items-center gap-2 rounded-xl border border-line bg-canvas px-3 py-2.5 text-[14px] font-medium text-ink"
                >
                  <SquarePen size={16} />
                  新建对话
                </button>
              </div>
              {conversationList}
            </aside>
          </div>
        ) : null}
      </div>
    </>
  );
}
