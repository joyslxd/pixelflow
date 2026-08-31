/** 左侧对话列表：新建、切换，并标记非 Harness 历史为只读。 */

import { HARNESS_ORCHESTRATION_MODE, type ConversationV1 } from "@/api/conversations";

type ConversationListProps = {
  conversations: ConversationV1[];
  activeConversationId?: string;
  onCreate: () => void;
  onOpen: (conversationId: string) => void;
  onRename: (conversation: ConversationV1) => void;
};

export function ConversationList({
  conversations,
  activeConversationId,
  onCreate,
  onOpen,
  onRename,
}: ConversationListProps) {
  return (
    <aside className="min-h-0 overflow-y-auto bg-surface p-4">
      <button className="w-full rounded-lg bg-brand px-3 py-2 text-sm text-white" onClick={onCreate}>
        新建对话
      </button>
      <div className="mt-4 space-y-1">
        {conversations.map((item) => {
          const readOnly = item.orchestration_mode !== HARNESS_ORCHESTRATION_MODE;
          const active = activeConversationId === item.conversation_id;
          return (
            <div key={item.conversation_id} className={`group flex w-full items-center gap-1 rounded px-2 py-2 hover:bg-accent-soft ${active ? "bg-accent-soft" : ""}`}>
              <button
                onClick={() => onOpen(item.conversation_id)}
                className="min-w-0 flex-1 text-left text-sm"
                title={item.title || item.conversation_id}
              >
                <span className="block truncate">{item.title || item.conversation_id}</span>
                {readOnly ? <span className="mt-1 block text-xs text-ink-soft">历史只读</span> : null}
              </button>
              <button
                type="button"
                className="shrink-0 rounded px-1.5 py-0.5 text-xs text-ink-soft opacity-0 hover:bg-surface hover:text-ink group-hover:opacity-100 focus:opacity-100"
                aria-label={`重命名会话：${item.title || item.conversation_id}`}
                onClick={() => onRename(item)}
              >
                编辑
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
