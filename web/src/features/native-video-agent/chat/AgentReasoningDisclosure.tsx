import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";

interface AgentReasoningDisclosureProps {
  text: string;
  status: "idle" | "streaming" | "completed";
  startedAt: string | null;
  durationMs: number | null;
  now?: number;
  /** 流式默认展开；完成后默认折叠，可手动重开。 */
  defaultExpanded?: boolean;
}

function thoughtLabel(
  status: AgentReasoningDisclosureProps["status"],
  startedAt: string | null,
  durationMs: number | null,
  now: number,
): string {
  if (status === "streaming") return "思考中…";
  const ms = durationMs ?? (
    startedAt && !Number.isNaN(Date.parse(startedAt))
      ? Math.max(0, now - Date.parse(startedAt))
      : null
  );
  if (ms == null) return "思考摘要";
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  if (minutes > 0) return `Thought for ${minutes}m ${rem}s`;
  return `Thought for ${seconds}s`;
}

/** 思考摘要：流式展开；完成后折叠；不渲染 raw reasoning_content。 */
export function AgentReasoningDisclosure({
  text,
  status,
  startedAt,
  durationMs,
  now: nowProp,
  defaultExpanded,
}: AgentReasoningDisclosureProps) {
  const live = status === "streaming";
  const [now, setNow] = useState(nowProp ?? Date.now());
  const [expanded, setExpanded] = useState(defaultExpanded ?? live);

  useEffect(() => {
    if (nowProp != null) {
      setNow(nowProp);
      return;
    }
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live, nowProp]);

  useEffect(() => {
    if (live) setExpanded(true);
    else if (defaultExpanded == null) setExpanded(false);
  }, [live, defaultExpanded, status]);

  if (status === "idle" && !text.trim()) return null;

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-sm text-slate-700">
      <button
        type="button"
        className="flex w-full items-center gap-1 text-left font-medium text-slate-800"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <ChevronRight
          className={`size-4 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        <span>{thoughtLabel(status, startedAt, durationMs, now)}</span>
      </button>
      {expanded ? (
        <div className="mt-2 whitespace-pre-wrap break-words text-slate-600">
          {text.trim() || (live ? "…" : "")}
        </div>
      ) : null}
    </div>
  );
}
