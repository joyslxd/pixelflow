/** 镜头描述可读化：把整段文本拆成表格/分段字段。 */

export interface ShotDescriptionField {
  label: string;
  value: string;
}

export type ComposeShotDescriptionMode = "live" | "persist";

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
  "画面",
  "旁白（对白）",
  "旁白（對白）",
  "旁白/对白",
  "旁白/對白",
  "旁白／对白",
  "旁白／對白",
  "旁白",
  "对白",
  "對白",
  "屏幕文案",
  "行动引导",
  "光影",
  "声音",
  "视觉风格",
  "收束",
  "镜头",
] as const;

const NARRATION_LABEL_ALIASES = new Set([
  "旁白（对白）",
  "旁白（對白）",
  "旁白/对白",
  "旁白/對白",
  "旁白／对白",
  "旁白／對白",
  "旁白",
  "对白",
  "對白",
]);

function canonicalizeFieldLabel(label: string): string {
  if (NARRATION_LABEL_ALIASES.has(label)) return "旁白（对白）";
  if (label === "时间范围") return "时间";
  return label;
}

const LABEL_PATTERN = FIELD_LABELS
  .slice()
  .sort((a, b) => b.length - a.length)
  .map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
  .join("|");

const LABEL_SPLIT_RE = new RegExp(
  `(?=\\s*(?:${LABEL_PATTERN})\\s*[：:])`,
  "u",
);

// 允许空值，避免编辑中清空字段后整行从表格消失。
const LABEL_VALUE_RE = new RegExp(
  `^(${LABEL_PATTERN})\\s*[：:]\\s*(.*)$`,
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

/** 编辑中的字段值：只统一换行，不折叠空格、不剥标点，避免打字时光标乱跳。 */
function liveFieldValue(value: string): string {
  return value.replace(/\r\n/g, "\n");
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
    const label = canonicalizeFieldLabel(match[1]);
    // 解析入库仍做一次清洗；空值保留行结构供表格编辑。
    const value = match[2].trim() ? cleanFieldValue(match[2]) : "";
    fields.push({
      label,
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

/**
 * 把表格字段写回 `shot_description.text`（仍是一段文本，不拆后端字段）。
 * - live：编辑中调用，保留空字段与用户空格，保证与 contentEditable 往返一致
 * - persist：保存前清洗，去掉空字段
 */
export function composeShotDescriptionFields(
  fields: ShotDescriptionField[],
  options?: { mode?: ComposeShotDescriptionMode },
): string {
  const mode = options?.mode ?? "persist";
  const normalized = fields
    .map((field) => ({
      label: field.label.trim(),
      value: mode === "live" ? liveFieldValue(field.value) : cleanFieldValue(field.value),
    }))
    .filter((field) => {
      if (!field.label) return false;
      if (mode === "live") return true;
      return Boolean(field.value);
    });
  if (normalized.length === 0) return "";

  const time = normalized.find((field) => field.label === "时间");
  const rest = normalized.filter((field) => field.label !== "时间");
  if (!time) {
    return rest
      .map((field) => (field.label === "描述" ? field.value : `${field.label}：${field.value}`))
      .join("\n");
  }
  if (rest.length === 0) return time.value;

  const [first, ...more] = rest;
  const lines = [`${time.value}: ${first.label}：${first.value}`];
  for (const field of more) {
    lines.push(field.label === "描述" ? field.value : `${field.label}：${field.value}`);
  }
  return lines.join("\n");
}
