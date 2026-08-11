/** VideoAgent 执行方案卡锚点：应挂在「已收到创作请求…」回执后，而不是用户消息后。 */

const VIDEO_AGENT_ACK_RE =
  /已收到创作请求|正在生成执行方案|已按你的新想法重新从选题开始|已确认选题创意/;

export function isVideoAgentAckNoticeContent(content: string | null | undefined): boolean {
  return VIDEO_AGENT_ACK_RE.test(String(content || ""));
}

export function isVideoAgentAckMessageId(messageId: string | null | undefined): boolean {
  const id = String(messageId || "");
  return id.startsWith("agent-ack:") || id.includes(":agent-ack:");
}

/**
 * 在触发用户消息之后找创作回执；找不到则回落用户消息（仍优于挂到对话底部）。
 */
export function resolveVideoAgentPlanAnchorId(input: {
  preferredUserMessageId?: string | null;
  messages: Array<{ id: string; role: string; content?: string }>;
}): string {
  const messages = input.messages || [];
  const preferredUserId = String(input.preferredUserMessageId || "").trim();
  const userIndex = preferredUserId
    ? messages.findIndex((message) => message.id === preferredUserId)
    : -1;

  const searchFrom = userIndex >= 0 ? userIndex + 1 : 0;
  for (let index = searchFrom; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role === "user") break;
    if (
      message.role === "assistant"
      && (
        isVideoAgentAckMessageId(message.id)
        || isVideoAgentAckNoticeContent(message.content)
      )
    ) {
      return message.id;
    }
  }

  if (preferredUserId && messages.some((message) => message.id === preferredUserId)) {
    return preferredUserId;
  }

  const latestAck = [...messages].reverse().find((message) => (
    message.role === "assistant"
    && (
      isVideoAgentAckMessageId(message.id)
      || isVideoAgentAckNoticeContent(message.content)
    )
  ));
  if (latestAck) return latestAck.id;

  return [...messages].reverse().find((message) => message.role === "user")?.id || "";
}
