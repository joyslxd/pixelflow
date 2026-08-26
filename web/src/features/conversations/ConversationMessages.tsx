/** 中间消息流：只渲染已持久化消息与当前 Run 的公开回复预览。 */

import type { PublicMessageV1 } from "@/api/contracts";

type ConversationMessagesProps = {
  messages: PublicMessageV1[];
  responsePreview: string;
  loading: boolean;
};

export function ConversationMessages({
  messages,
  responsePreview,
  loading,
}: ConversationMessagesProps) {
  const lastAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  const showPreview = responsePreview.length > 0 && responsePreview !== lastAssistant?.content;

  return (
    <div className="flex-1 space-y-3 overflow-y-auto p-6">
      {loading ? <p className="text-sm text-ink-soft">正在恢复权威状态…</p> : null}
      {messages.map((message) => (
        <p
          key={message.message_id}
          className={message.role === "user" ? "text-right" : "text-left"}
        >
          {message.content}
        </p>
      ))}
      {showPreview ? (
        <p className="text-left text-ink-soft" aria-live="polite">{responsePreview}</p>
      ) : null}
    </div>
  );
}
