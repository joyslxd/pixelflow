/** 把 Gateway 固定错误码映射为中文提示，不回显服务端异常正文。 */

const PUBLIC_ERROR_MESSAGES: Record<string, string> = {
  not_authenticated: "请先登录后再继续。",
  conversation_unselected: "请先选择或新建一个对话。",
  conversation_read_only: "旧对话仅供查看，请基于产物创建新对话。",
  harness_workspace_not_found: "当前对话还没有可用的工作区。",
  harness_workspace_revision_conflict: "工作区已更新，请基于最新版本重新发送。",
  harness_context_budget_rejected: "当前工作区内容正在整理，请刷新后重试。",
  harness_run_unavailable_retryable: "Agent 暂时不可用，请稍后重试。",
  harness_run_protocol_invalid: "当前运行协议无效，请刷新后重试。",
  harness_event_stream_unavailable: "公开进度暂时中断，正在尝试重连。",
  harness_event_invalid: "收到无法识别的公开事件，已回读权威快照。",
  snapshot_event_sequence_invalid: "快照事件顺序不合法，已停止局部猜测。",
  snapshot_sequence_invalid: "快照序号不合法，请刷新当前对话。",
  http_401: "登录已失效，请重新登录。",
  http_403: "没有权限访问该对话。",
  http_404: "对话或运行不存在。",
  http_409: "版本已变化，请刷新后基于最新状态继续。",
  http_503: "服务暂时不可用，请稍后重试。",
};

const FALLBACK_MESSAGE = "请求未完成，请稍后重试。";

export function publicErrorMessage(code: string | undefined): string {
  /** 只接受固定公开错误码；未知码使用同一句安全提示。 */

  if (!code) return FALLBACK_MESSAGE;
  return PUBLIC_ERROR_MESSAGES[code] ?? FALLBACK_MESSAGE;
}
