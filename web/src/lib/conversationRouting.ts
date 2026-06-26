export interface ConversationScopedMessage {
  conversationId?: string;
}

export function shouldRenderConversationMessage(activeConversationId: string, targetConversationId: string): boolean {
  return Boolean(activeConversationId && targetConversationId && activeConversationId === targetConversationId);
}

export function appendVisibleConversationMessage<T>(
  messages: T[],
  input: { activeConversationId: string; targetConversationId: string; message: T },
): T[] {
  if (!shouldRenderConversationMessage(input.activeConversationId, input.targetConversationId)) return messages;
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

export function restoredConversationMessages<T>(_snapshotMessages: T[] | undefined, persistedMessages: T[]): T[] {
  return persistedMessages;
}

export function shouldApplyVisibleConversationSideEffect(activeConversationId: string, targetConversationId: string): boolean {
  return shouldRenderConversationMessage(activeConversationId, targetConversationId);
}
