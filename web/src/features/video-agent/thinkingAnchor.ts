/** 思考流挂回触发该 Turn 的用户消息后（与执行方案卡同锚，避免沉到最新一条下面）。 */

export function resolveThinkingAfterMessageId(
  turnId: string,
  messages: Array<{ id: string; role: string }>,
  options?: {
    pendingTurns?: Array<{ clientInputId: string; runId?: string | null }>;
    knownAnchor?: string | null;
  },
): string {
  const known = String(options?.knownAnchor || "").trim();
  if (known && messages.some((message) => message.id === known)) {
    return known;
  }

  const tid = String(turnId || "").trim();
  if (!tid) {
    return [...messages].reverse().find((message) => message.role === "user")?.id
      || messages[0]?.id
      || "";
  }

  // turns/start 前乐观壳 turnId=用户消息 id；返回后 turnId=runId，靠 pending 映射回用户消息。
  const pending = (options?.pendingTurns || []).find(
    (item) => item.runId === tid || item.clientInputId === tid,
  );
  if (
    pending?.clientInputId
    && messages.some((message) => message.id === pending.clientInputId)
  ) {
    return pending.clientInputId;
  }

  if (messages.some((message) => message.id === tid && message.role === "user")) {
    return tid;
  }

  const answerId = `thinking-answer:${tid}`;
  const answerIndex = messages.findIndex((message) => message.id === answerId);
  if (answerIndex > 0) {
    for (let index = answerIndex - 1; index >= 0; index -= 1) {
      if (messages[index]?.role === "user" && messages[index]?.id) {
        return messages[index].id;
      }
    }
  }

  // 无映射时才退回最近用户消息（仅 live 兜底）；历史 Turn 应靠 pending/knownAnchor。
  return [...messages].reverse().find((message) => message.role === "user")?.id
    || messages[0]?.id
    || "";
}
