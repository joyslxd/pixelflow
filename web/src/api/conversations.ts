/** Conversation 的公开 Gateway Client；不承载 Runtime 或 Sidecar 状态。 */

import { agentRequest } from "./http";

export const HARNESS_ORCHESTRATION_MODE = "harness_v1";

export type ConversationV1 = {
  conversation_id: string;
  title: string;
  revision: number;
  orchestration_mode: string;
};

export type ConversationMessageV1 = {
  message_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  payload?: Record<string, unknown>;
};

export type ConversationDetailV1 = {
  conversation: ConversationV1;
  messages: ConversationMessageV1[];
  /** Gateway 依据公开 Outbox 回读，覆盖没有用户消息的确认/授权恢复 Run。 */
  latest_harness_run_id?: string | null;
  /** 自动恢复 Run 没有可安全重放的用户输入，失败时不能调用通用 /recover。 */
  latest_harness_run_is_user_turn?: boolean;
};

export async function listConversations(): Promise<ConversationV1[]> {
  /** 读取当前用户最近会话；分页扩展保持在该 Client 内。 */

  const result = await agentRequest<{ items: ConversationV1[] }>("/conversations?page_size=20");
  return result.items;
}

export function getConversation(conversationId: string): Promise<ConversationDetailV1> {
  /** 读取一条会话与已持久化消息，不由浏览器猜测运行状态。 */

  return agentRequest<ConversationDetailV1>(`/conversations/${encodeURIComponent(conversationId)}`);
}

export function createConversation(): Promise<ConversationV1> {
  /** 创建新的 Harness 会话；服务端决定编排归属。 */

  return agentRequest<ConversationV1>("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "新的 Harness 对话" }),
  });
}

export function updateConversationTitle(
  conversationId: string,
  title: string,
  expectedRevision: number,
): Promise<ConversationV1> {
  /** 仅修改会话导航标题；使用 revision 防止覆盖另一端刚完成的重命名。 */

  return agentRequest<ConversationV1>(`/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, expected_revision: expectedRevision }),
  });
}
