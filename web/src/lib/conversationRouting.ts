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
