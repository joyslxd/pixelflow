import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/chat";

export function MessageBubble({ msg }: { msg: ChatMessage }) {
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
        <span className="mt-1 px-1 text-[11px] text-ink-soft/60">{msg.time}</span>
      </div>
    </div>
  );
}
