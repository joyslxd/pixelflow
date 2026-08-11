import { ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export interface AgentThinkingStreamModel {
  turnId: string;
  title: string;
  subtitle: string;
  /** reasoning：Thought 折叠区。 */
  text: string;
  /** answer：完成后由工作台写入对话框气泡，不在此组件展示。 */
  answer?: string;
  startedAt: string | null;
  status: "streaming" | "completed";
}

interface AgentThinkingStreamProps {
  thinking: AgentThinkingStreamModel;
  now?: number;
  /** 历史思考默认折叠；当前流式思考保持展开。 */
  defaultExpanded?: boolean;
  /** 打字机是否仍在追平：父级据此延后展示本轮 Plan 卡。 */
  onRevealStateChange?: (state: { catchingUp: boolean; status: "streaming" | "completed" }) => void;
}

/** 积压越大步进略增，但上限要小，避免大段 delta 一次「砸」到屏幕上。 */
export function nextThinkingRevealStep(lag: number): number {
  if (lag <= 0) return 0;
  if (lag > 80) return 3;
  if (lag > 32) return 2;
  return 1;
}

function elapsedSeconds(startedAt: string | null, now: number): number {
  if (!startedAt) return 0;
  const started = Date.parse(startedAt);
  if (Number.isNaN(started)) return 0;
  return Math.max(0, Math.floor((now - started) / 1000));
}

/** 与参考样式一致：Thought for 4s / Thought for 1m 12s */
export function thoughtForLabel(startedAt: string | null, now: number): string {
  const seconds = elapsedSeconds(startedAt, now);
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  if (minutes > 0) return `Thought for ${minutes}m ${rem}s`;
  return `Thought for ${seconds}s`;
}

/** 取思考正文首段作折叠态摘要（参考样式：标题下直接是结论句）。 */
export function thinkingConclusionPreview(text: string, maxChars = 96): string {
  const trimmed = text.trim();
  if (!trimmed) return "";
  const lines = trimmed.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const first = lines[0] || trimmed;
  if (first.length <= maxChars) return first;
  return `${first.slice(0, maxChars).trim()}…`;
}

/** 扁平思考块：Thought for Xs + 正文 + 灰状态行，无卡片边框。 */
export function AgentThinkingStream({
  thinking,
  now: nowProp,
  defaultExpanded,
  onRevealStateChange,
}: AgentThinkingStreamProps) {
  const live = thinking.status === "streaming";
  const [now, setNow] = useState(nowProp ?? Date.now());
  // 新 Turn / 新流式：从空串打字机；历史归档默认直接展示全文。
  const [visibleText, setVisibleText] = useState(() => (
    thinking.status === "streaming" ? "" : thinking.text
  ));
  const [expanded, setExpanded] = useState(defaultExpanded ?? live);
  const targetRef = useRef(thinking.text);
  const visibleRef = useRef(thinking.status === "streaming" ? "" : thinking.text);
  const turnRef = useRef(thinking.turnId);

  useEffect(() => {
    if (nowProp != null) {
      setNow(nowProp);
      return;
    }
    const ticking = thinking.status === "streaming"
      || visibleRef.current.length < targetRef.current.length;
    if (!ticking) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [nowProp, thinking.status, thinking.text]);

  useEffect(() => {
    if (turnRef.current !== thinking.turnId) {
      turnRef.current = thinking.turnId;
      const startEmpty = thinking.status === "streaming";
      visibleRef.current = startEmpty ? "" : thinking.text;
      targetRef.current = thinking.text;
      setVisibleText(startEmpty ? "" : thinking.text);
      setExpanded(defaultExpanded ?? thinking.status === "streaming");
      return;
    }
    targetRef.current = thinking.text;
  }, [thinking.text, thinking.turnId, thinking.status, defaultExpanded]);

  const catchingUp = visibleText.length < thinking.text.length;
  const onRevealStateChangeRef = useRef(onRevealStateChange);
  onRevealStateChangeRef.current = onRevealStateChange;
  const lastRevealRef = useRef<{ catchingUp: boolean; status: "streaming" | "completed" } | null>(null);

  useEffect(() => {
    const next = {
      catchingUp: thinking.status === "streaming" || catchingUp,
      status: thinking.status,
    };
    const prev = lastRevealRef.current;
    if (
      prev
      && prev.catchingUp === next.catchingUp
      && prev.status === next.status
    ) {
      return;
    }
    lastRevealRef.current = next;
    onRevealStateChangeRef.current?.(next);
  }, [catchingUp, thinking.status, thinking.turnId]);

  // 流式中强制展开；完成后等打字机追平再折叠，避免「Thought for 38s」后整段一次性砸出。
  useEffect(() => {
    if (thinking.status === "streaming" || catchingUp) {
      setExpanded(true);
      return;
    }
    if (thinking.status === "completed") {
      setExpanded(false);
    }
  }, [thinking.status, thinking.turnId, catchingUp]);

  useEffect(() => {
    let frame = 0;
    let lastTick = 0;

    const loop = (ts: number) => {
      frame = window.requestAnimationFrame(loop);
      if (ts - lastTick < 28) return;
      lastTick = ts;
      const target = targetRef.current;
      let prev = visibleRef.current;
      if (prev === target) return;
      if (!target.startsWith(prev)) {
        prev = "";
      }
      const next = target.slice(0, prev.length + nextThinkingRevealStep(target.length - prev.length));
      visibleRef.current = next;
      setVisibleText(next);
    };

    frame = window.requestAnimationFrame(loop);
    return () => window.cancelAnimationFrame(frame);
  }, [thinking.turnId]);

  const showCaret = live || catchingUp;
  // 禁止 `visibleText || thinking.text`：visible 被重置成 "" 时会整段兜底闪现。
  const displayText = visibleText;
  const conclusion = thinkingConclusionPreview(thinking.text);
  const canToggle = thinking.status === "completed" && !catchingUp && Boolean(thinking.text.trim());
  const headerLabel = thoughtForLabel(thinking.startedAt, now);
  const statusLine = (thinking.title || thinking.subtitle || "").trim();
  const showBody = live || expanded || catchingUp;

  return (
    <section
      aria-label={headerLabel}
      aria-live="polite"
      className="mr-auto w-full max-w-[720px] py-1"
    >
      {canToggle ? (
        <button
          type="button"
          className="group inline-flex items-center gap-1 text-[13px] leading-5 text-[#8b8b8b] hover:text-[#6b6b6b]"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <span>{headerLabel}</span>
          <ChevronRight
            aria-hidden="true"
            className={`size-3.5 shrink-0 transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
          />
        </button>
      ) : (
        <p className="text-[13px] leading-5 text-[#8b8b8b]">{headerLabel}</p>
      )}

      {/* 折叠态：只露首句摘要，对齐参考样式「标题下直接一句结论」。 */}
      {canToggle && !expanded && !catchingUp && conclusion ? (
        <p className="mt-1.5 text-[14px] leading-6 text-[#1a1a1a]">
          {conclusion}
        </p>
      ) : null}

      {showBody ? (
        displayText.trim() ? (
          <div className="mt-1.5">
            <p className="whitespace-pre-wrap text-[14px] leading-6 text-[#1a1a1a]">
              {displayText}
              {showCaret ? (
                <span className="ml-0.5 inline-block animate-pulse text-[#8b8b8b]">▍</span>
              ) : null}
            </p>
          </div>
        ) : (
          <p className="mt-1.5 text-[13px] text-[#8b8b8b]">
            {live || catchingUp ? "Thinking…" : "Connecting…"}
          </p>
        )
      ) : null}

      {/* 灰状态行：对应参考图里 Explored / Chat context summarized */}
      {(live || expanded || catchingUp) && statusLine ? (
        <p className="mt-2 text-[13px] leading-5 text-[#8b8b8b]">{statusLine}</p>
      ) : null}

      {live || catchingUp ? (
        <p className="mt-2 text-[13px] leading-5 text-[#8b8b8b]">Thinking</p>
      ) : null}
    </section>
  );
}
