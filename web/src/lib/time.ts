const EXPLICIT_TIMEZONE_RE = /(?:Z|[+-]\d{2}:\d{2})$/i;

export function normalizeIsoTimestamp(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (EXPLICIT_TIMEZONE_RE.test(trimmed)) return trimmed;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(trimmed)) return `${trimmed}Z`;
  return trimmed;
}

export function formatClockTime(
  value: string | undefined,
  locale = "zh-CN",
  timeZone?: string,
  fallback = "",
): string {
  const normalized = normalizeIsoTimestamp(value || "");
  if (!normalized) return fallback;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return fallback;
  return date.toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
    ...(timeZone ? { timeZone } : {}),
  });
}
