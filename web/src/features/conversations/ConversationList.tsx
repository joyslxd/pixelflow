/** 左侧对话列表：新建、切换，并标记非 Harness 历史为只读。 */

import { HARNESS_ORCHESTRATION_MODE, type ConversationV1 } from "@/api/conversations";

type ConversationListProps = {
  conversations: ConversationV1[];
  activeConversationId?: string;
  onCreate: () => void;
  onOpen: (conversationId: string) => void;
};

export function ConversationList({
  conversations,
  activeConversationId,
  onCreate,
  onOpen,
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
            <button
              key={item.conversation_id}
              onClick={() => onOpen(item.conversation_id)}
              className={`block w-full rounded px-2 py-2 text-left text-sm hover:bg-accent-soft ${active ? "bg-accent-soft" : ""}`}
            >
              <span className="block truncate">{item.title || item.conversation_id}</span>
              {readOnly ? <span className="mt-1 block text-xs text-ink-soft">历史只读</span> : null}
            </button>
          );
        })}
      </div>
    </aside>
  );
}
