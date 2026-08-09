/** 资产包进度卡锚点解析：应挂在脚本确认后的回执消息下方，不能回落到首条用户消息。 */

const ASSET_PACKAGE_NOTICE_RE = /正在生成视频资产包|已确认脚本方案，正在生成|场景包结构已就绪/;

export function isAssetPackageNoticeContent(content: string | null | undefined): boolean {
  return ASSET_PACKAGE_NOTICE_RE.test(String(content || ""));
}

export function isScriptPlanConfirmMessage(message: {
  artifact?: unknown;
}): boolean {
  const artifact = message.artifact;
  if (!artifact || typeof artifact !== "object") return false;
  const record = artifact as {
    scriptPlanConfirmForAssets?: boolean;
    title?: string;
  };
  return Boolean(
    record.scriptPlanConfirmForAssets
    || record.title === "脚本方案待确认"
    || record.title === "已确认脚本方案",
  );
}

export function resolveAssetPackageProgressAnchorId(input: {
  preferredAnchorId?: string | null;
  messages: Array<{ id: string; role: string; content?: string; artifact?: unknown }>;
}): string {
  const messages = input.messages || [];
  const preferred = String(input.preferredAnchorId || "").trim();
  if (preferred && messages.some((message) => message.id === preferred)) {
    return preferred;
  }

  const notice = [...messages].reverse().find((message) => (
    message.role === "assistant" && isAssetPackageNoticeContent(message.content)
  ));
  if (notice) return notice.id;

  const confirm = [...messages].reverse().find((message) => (
    message.role === "assistant" && isScriptPlanConfirmMessage(message)
  ));
  if (confirm) return confirm.id;

  // 宁可挂在最近助手消息后，也不要挂回首条用户消息（会把进度卡顶到脚本确认之前）。
  return [...messages].reverse().find((message) => message.role === "assistant")?.id || "";
}

export function remapMessageAnchorId(
  anchors: Record<string, string>,
  fromId: string,
  toId: string,
): Record<string, string> {
  if (!fromId || !toId || fromId === toId) return anchors;
  let changed = false;
  const next: Record<string, string> = {};
  for (const [key, value] of Object.entries(anchors)) {
    if (value === fromId) {
      next[key] = toId;
      changed = true;
    } else {
      next[key] = value;
    }
  }
  return changed ? next : anchors;
}
