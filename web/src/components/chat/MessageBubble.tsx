import { FileVideo, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/chat";

export function MessageBubble({ msg, onOpenArtifact }: { msg: ChatMessage; onOpenArtifact?: (msg: ChatMessage) => void }) {
  const isUser = msg.role === "user";
  return (
    <div className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold",
          isUser ? "bg-accent text-white" : "bg-accent-soft text-accent",
        )}
      >
        {isUser ? "A" : <Sparkles size={15} />}
      </div>
      <div className={cn("flex max-w-[78%] flex-col", isUser ? "items-end" : "items-start")}>
        <div
          className={cn(
            "whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed",
            isUser
              ? "bg-accent-soft text-ink"
              : "border border-line bg-surface text-ink",
          )}
        >
          {msg.content}
        </div>
        {msg.artifact && (
          <button
            type="button"
            onClick={() => onOpenArtifact?.(msg)}
            className="mt-2 flex w-full max-w-[320px] items-center gap-3 rounded-2xl border border-accent/20 bg-accent-soft/70 px-3 py-3 text-left transition-colors hover:border-accent/40 hover:bg-accent-soft"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/70 text-accent">
              <FileVideo size={18} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
              <span className="mt-0.5 block truncate text-[12px] text-ink-soft">{msg.artifact.description}</span>
            </span>
            <span className="shrink-0 rounded-lg bg-white/70 px-2 py-1 text-[12px] font-medium text-accent">
              {msg.artifact.actionLabel}
            </span>
          </button>
        )}
        <span className="mt-1 px-1 text-[11px] text-ink-soft/60">{msg.time}</span>
      </div>
    </div>
  );
}
