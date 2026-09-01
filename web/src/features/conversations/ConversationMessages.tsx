/** 中间消息流：只渲染已持久化消息与当前 Run 的公开回复预览。 */

import type { PublicMessageV1 } from "@/api/contracts";

type ConversationMessagesProps = {
  messages: PublicMessageV1[];
  responsePreview: string;
  executionSummary: string;
  processing: boolean;
  loading: boolean;
};

export function ConversationMessages({
  messages,
  responsePreview,
  executionSummary,
  processing,
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
      {processing || executionSummary ? (
        <section className="max-w-[92%] rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink-soft" aria-live="polite">
          <p className="font-medium text-ink">{processing ? "正在处理" : "执行过程摘要"}</p>
          <p className="mt-2 max-h-52 overflow-y-auto whitespace-pre-wrap break-words leading-6">
            {executionSummary || "任务已受理，正在分析你的请求并核对工作区。"}
          </p>
        </section>
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
