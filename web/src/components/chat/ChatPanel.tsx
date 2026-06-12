import { useEffect, useRef } from "react";
import { Composer } from "@/components/composer/Composer";
import { MessageBubble } from "./MessageBubble";
import type { ChatMessage } from "@/lib/chat";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSubmit: (text: string) => void;
  onOpenArtifact?: (msg: ChatMessage) => void;
  busy?: boolean;
}

export function ChatPanel({ messages, onSubmit, onOpenArtifact, busy }: ChatPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className="flex min-w-0 flex-1 flex-col border-r border-line">
      <div className="flex h-12 shrink-0 items-center px-5 text-[14px] font-semibold text-ink">
        对话
      </div>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-2">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center text-ink-soft">
            <p className="text-[15px] font-medium text-ink">描述你想要的视频</p>
            <p className="mt-1 text-[13px]">
              输入商品信息与创意诉求,Agent 会生成 Brief 并产出成片。
            </p>
          </div>
        ) : (
          messages.map((m) => <MessageBubble key={m.id} msg={m} onOpenArtifact={onOpenArtifact} />)
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 p-4">
        <Composer onSubmit={onSubmit} busy={busy} />
      </div>
    </div>
  );
}
