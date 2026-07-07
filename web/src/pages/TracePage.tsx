import { useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { api, type ConversationTraceEvent } from "@/lib/api";

const EVENT_LABEL: Record<string, string> = {
  llm_call: "LLM 调用",
  vendor_call: "供应商调用",
};

export function TracePage() {
  const navigate = useNavigate();
  const { conversationId = "" } = useParams<{ conversationId: string }>();
  const [events, setEvents] = useState<ConversationTraceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.fetchConversationTrace(conversationId);
      setEvents(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (conversationId) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  const toggleExpanded = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto px-6 py-5">
      <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => navigate("/")}
            className="inline-flex w-fit items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:border-accent/40 hover:text-accent"
          >
            <ArrowLeft size={16} />
            返回工作台
          </button>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            刷新
          </button>
        </div>

        <section className="rounded-2xl border border-line bg-surface p-6 shadow-sm">
          <div className="border-b border-line pb-4">
            <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-accent-soft px-3 py-1 text-[12px] font-semibold text-accent">
              内部调试 · 对话 Trace
            </div>
            <h1 className="text-[20px] font-semibold tracking-normal text-ink">
              对话 <span className="font-mono text-[16px] text-ink-soft">{conversationId}</span>
            </h1>
            <p className="mt-2 text-[13px] leading-6 text-ink-soft">
              只记录 LLM 调用和 content-app/Borgrise 供应商调用，包含原始 prompt 与请求/响应，仅限内部排查使用，需要 content-app ROLE_ADMIN。
            </p>
          </div>

          <div className="mt-5 space-y-3">
            {error && (
              <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] leading-6 text-red-700">
                {error}
              </div>
            )}
            {!error && !loading && events.length === 0 && (
              <div className="rounded-xl border border-line bg-canvas px-4 py-3 text-[13px] leading-6 text-ink-soft">
                这个对话还没有 trace 记录。
              </div>
            )}
            {events.map((event) => {
              const expanded = expandedIds.has(event.id);
              const durationMs = typeof event.data.duration_ms === "number" ? event.data.duration_ms : null;
              const hasError = "error" in event.data && Boolean(event.data.error);
              return (
                <div key={event.id} className="rounded-xl border border-line bg-canvas">
                  <button
                    type="button"
                    onClick={() => toggleExpanded(event.id)}
                    className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={
                          hasError
                            ? "rounded-full bg-red-50 px-2 py-0.5 text-[12px] font-semibold text-red-700"
                            : "rounded-full bg-accent-soft px-2 py-0.5 text-[12px] font-semibold text-accent"
                        }
                      >
                        {EVENT_LABEL[event.event] || event.event}
                      </span>
                      {typeof event.data.endpoint === "string" && (
                        <span className="font-mono text-[12px] text-ink-soft">{event.data.endpoint}</span>
                      )}
                      {typeof event.data.model === "string" && (
                        <span className="font-mono text-[12px] text-ink-soft">{event.data.model}</span>
                      )}
                      {durationMs != null && <span className="text-[12px] text-ink-soft">{durationMs}ms</span>}
                    </div>
                    <span className="whitespace-nowrap text-[12px] text-ink-soft">{event.created_at}</span>
                  </button>
                  {expanded && (
                    <pre className="overflow-x-auto whitespace-pre-wrap break-all border-t border-line px-4 py-3 font-mono text-[12px] leading-5 text-ink">
                      {JSON.stringify(event.data, null, 2)}
                    </pre>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}
