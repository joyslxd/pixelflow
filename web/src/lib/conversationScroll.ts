/** 对话消息列表的贴底滚动判定；不依赖 DOM 实现，便于单测。 */

export type ScrollMetrics = {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
};

const DEFAULT_NEAR_BOTTOM_PX = 80;

export function isNearScrollBottom(
  metrics: ScrollMetrics,
  thresholdPx: number = DEFAULT_NEAR_BOTTOM_PX,
): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= thresholdPx;
}

export function pinScrollToBottom(node: { scrollTop: number; scrollHeight: number }): void {
  node.scrollTop = node.scrollHeight;
}
