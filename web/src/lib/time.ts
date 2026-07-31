const EXPLICIT_TIMEZONE_RE = /(?:Z|[+-]\d{2}:\d{2})$/i;
const ISO_FRACTION_RE = /(\.\d{3})\d+(?=(?:Z|[+-]\d{2}:\d{2})?$)/i;

export function normalizeIsoTimestamp(value: string): string {
  // Python datetime 会输出 6 位微秒；浏览器 Date 只稳定支持 3 位毫秒，先收敛精度再解析。
  const trimmed = value.trim().replace(ISO_FRACTION_RE, "$1");
  if (!trimmed) return "";
  if (EXPLICIT_TIMEZONE_RE.test(trimmed)) return trimmed;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(trimmed)) return `${trimmed}Z`;
  return trimmed;
}

export function formatMessageTime(
  value: string | undefined,
  locale = "zh-CN",
  timeZone?: string,
  fallback = "",
): string {
  const normalized = normalizeIsoTimestamp(value || "");
  if (!normalized) return fallback;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return fallback;
  const parts = new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    ...(timeZone ? { timeZone } : {}),
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  if (!values.year || !values.month || !values.day || !values.hour || !values.minute || !values.second) {
    return fallback;
  }
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
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
