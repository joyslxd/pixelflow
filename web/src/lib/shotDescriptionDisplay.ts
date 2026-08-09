/** 镜头描述可读化：把整段文本拆成表格/分段字段。 */

export interface ShotDescriptionField {
  label: string;
  value: string;
}

const FIELD_LABELS = [
  "时间范围",
  "时间",
  "地点",
  "角色",
  "道具",
  "主体",
  "动作",
  "景别",
  "运镜",
  "光影",
  "声音",
  "视觉风格",
  "收束",
  "镜头",
] as const;

const LABEL_PATTERN = FIELD_LABELS
  .slice()
  .sort((a, b) => b.length - a.length)
  .map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
  .join("|");

const LABEL_SPLIT_RE = new RegExp(
  `(?=\\s*(?:${LABEL_PATTERN})\\s*[：:])`,
  "u",
);

const LABEL_VALUE_RE = new RegExp(
  `^(${LABEL_PATTERN})\\s*[：:]\\s*(.+)$`,
  "u",
);

const TIME_RANGE_RE = /^(\d+\s*[-~—–]\s*\d+\s*秒)\s*[：:：]?\s*/u;

function normalizeSeparators(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/[；;]+/g, "；")
    .replace(/[，,]+/g, "，")
    .trim();
}

function cleanFieldValue(value: string): string {
  return value
    .replace(/^[\s，,、；;。．.]+/u, "")
    .replace(/[\s，,、；;。．.]+$/u, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * 将镜头描述正文拆成「标签 → 内容」列表，便于表格展示。
 * 无法识别标签时回落到单行「描述」。
 */
export function parseShotDescriptionFields(raw: string): ShotDescriptionField[] {
  const text = normalizeSeparators(raw);
  if (!text) return [];

  const fields: ShotDescriptionField[] = [];
  let remainder = text;

  const timeMatch = remainder.match(TIME_RANGE_RE);
  if (timeMatch) {
    fields.push({ label: "时间", value: cleanFieldValue(timeMatch[1]) });
    remainder = remainder.slice(timeMatch[0].length).trim();
  }

  if (!remainder) return fields;

  const chunks = remainder
    .split(LABEL_SPLIT_RE)
    .map((chunk) => chunk.trim())
    .filter(Boolean);

  const unlabeled: string[] = [];
  for (const chunk of chunks) {
    const match = chunk.match(LABEL_VALUE_RE);
    if (!match) {
      unlabeled.push(cleanFieldValue(chunk));
      continue;
    }
    const label = match[1];
    const value = cleanFieldValue(match[2]);
    if (!value) continue;
    fields.push({
      label: label === "时间范围" ? "时间" : label,
      value,
    });
  }

  const leftover = unlabeled.filter(Boolean).join(" ").trim();
  if (leftover) {
    fields.push({ label: "描述", value: leftover });
  }

  if (fields.length === 0) {
    return [{ label: "描述", value: text }];
  }
  return fields;
}

export function shotDescriptionHasStructuredFields(raw: string): boolean {
  const fields = parseShotDescriptionFields(raw);
  return fields.length > 1 || (fields.length === 1 && fields[0]?.label !== "描述");
}
