export interface ConversationScopedMessage {
  conversationId?: string;
}

export function shouldRenderConversationMessage(activeConversationId: string, targetConversationId: string): boolean {
  return Boolean(activeConversationId && targetConversationId && activeConversationId === targetConversationId);
}

export function appendVisibleConversationMessage<T>(
  messages: T[],
  input: {
    activeConversationId: string;
    targetConversationId: string;
    message: T & { id?: string };
    /** 新建消息时插入到该 id 之前，用于场景包卡回到选模卡之前的原时间线位置。 */
    insertBeforeId?: string | null;
  },
): T[] {
  if (!shouldRenderConversationMessage(input.activeConversationId, input.targetConversationId)) return messages;
  if (input.message.id) {
    const existingIndex = messages.findIndex((message) => (message as { id?: string }).id === input.message.id);
    if (existingIndex >= 0) {
      return messages.map((message, index) => (index === existingIndex ? input.message : message)) as T[];
    }
  }
  const insertBeforeId = String(input.insertBeforeId || "").trim();
  if (insertBeforeId) {
    const beforeIndex = messages.findIndex((message) => (message as { id?: string }).id === insertBeforeId);
    if (beforeIndex >= 0) {
      return [
        ...messages.slice(0, beforeIndex),
        input.message,
        ...messages.slice(beforeIndex),
      ] as T[];
    }
  }
  return [...messages, input.message];
}

export function messageConversationId(message: ConversationScopedMessage, fallbackConversationId: string): string {
  return message.conversationId || fallbackConversationId;
}

export function replaceMessageById<T extends { id: string }>(messages: T[], messageId: string, replacement: T): T[] {
  let replaced = false;
  const next = messages.map((message) => {
    if (message.id !== messageId) return message;
    replaced = true;
    return replacement;
  });
  return replaced ? next : messages;
}

export function restoredConversationMessages<T extends { id: string }>(_snapshotMessages: T[] | undefined, persistedMessages: T[]): T[] {
  const usedIds = new Set<string>();
  return persistedMessages.map((message) => {
    const baseId = message.id || "message";
    let nextId = baseId;
    let suffix = 2;
    while (usedIds.has(nextId)) {
      nextId = `${baseId}-${suffix}`;
      suffix += 1;
    }
    usedIds.add(nextId);
    return nextId === message.id ? message : { ...message, id: nextId };
  });
}

export function shouldApplyVisibleConversationSideEffect(activeConversationId: string, targetConversationId: string): boolean {
  return shouldRenderConversationMessage(activeConversationId, targetConversationId);
}
