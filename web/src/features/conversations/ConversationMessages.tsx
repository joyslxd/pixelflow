/** 中间消息流：只渲染已持久化消息与当前 Run 的公开回复预览。 */

import type { PublicMessageV1 } from "@/api/contracts";

type ConversationMessagesProps = {
  messages: PublicMessageV1[];
  responsePreview: string;
  executionSummary: string;
  loading: boolean;
};

export function ConversationMessages({
  messages,
  responsePreview,
  executionSummary,
  loading,
}: ConversationMessagesProps) {
  const lastAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  const showPreview = responsePreview.length > 0 && responsePreview !== lastAssistant?.content;

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-6">
      {loading ? <p className="text-sm text-ink-soft">正在恢复权威状态…</p> : null}
      {messages.map((message) => message.role === "user" ? (
        <article key={message.message_id} className="ml-auto max-w-[85%] rounded-2xl bg-accent px-4 py-3 text-sm text-white">
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        </article>
      ) : (
        <article key={message.message_id} className="max-w-[92%] rounded-2xl border border-line bg-canvas px-4 py-3">
          <p className="mb-2 text-xs font-medium text-ink-soft">Agent 结果</p>
          <div className="whitespace-pre-wrap break-words text-sm leading-7 text-ink">{message.content}</div>
        </article>
      ))}
      {executionSummary ? (
        <details className="max-w-[92%] rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink-soft">
          <summary className="cursor-pointer font-medium text-ink">执行过程摘要</summary>
          <p className="mt-3 max-h-52 overflow-y-auto whitespace-pre-wrap break-words leading-6">{executionSummary}</p>
        </details>
      ) : null}
      {showPreview ? (
        <article className="max-w-[92%] rounded-2xl border border-accent/30 bg-accent/5 px-4 py-3" aria-live="polite">
          <p className="mb-2 text-xs font-medium text-accent">正在生成结果</p>
          <div className="whitespace-pre-wrap break-words text-sm leading-7 text-ink">{responsePreview}</div>
        </article>
      ) : null}
    </div>
  );
}
