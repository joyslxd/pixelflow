/** 连接状态只展示用户可理解的中文，不暴露 Harness Session 或 Engine。 */

import type { ConnectionState } from "@/features/agent-runtime/state";

type ConnectionNoticeProps = {
  connection: ConnectionState;
};

function label(connection: ConnectionState): string {
  if (connection === "reconnecting") return "正在重连";
  if (connection === "connected") return "已连接";
  if (connection === "connecting") return "正在连接";
  if (connection === "disconnected") return "连接已断开";
  return "";
}

export function ConnectionNotice({ connection }: ConnectionNoticeProps) {
  const text = label(connection);
  if (!text) return null;
  return <span className="text-ink-soft">{text}</span>;
}
