import type {
  SupervisorCompressionState,
  SupervisorInputQueueItem,
  SupervisorRunStatus,
} from "./reducer.js";

export type SupervisorRuntimeNoticeTone = "working" | "success" | "warning" | "queued";

export interface SupervisorRuntimeNoticeModel {
  kind: "compression" | "queue";
  tone: SupervisorRuntimeNoticeTone;
  title: string;
  detail: string | null;
  progressPercent: number | null;
  queueBadge: string | null;
}

export interface SupervisorRuntimeNoticeInput {
  enabled: boolean;
  runStatus: SupervisorRunStatus;
  compression: SupervisorCompressionState;
  inputQueue: SupervisorInputQueueItem[];
}

const COMPRESSION_STARTED_COPY = "对话内容较长，正在整理上下文，当前任务和已生成内容不会丢失。";
const COMPRESSION_COMPLETED_COPY = "上下文整理完成，正在继续处理刚才的请求。";
const COMPRESSION_FAILED_COPY = "上下文整理暂时未完成，你的输入已保留，系统将继续重试。";

function resolveQueueBadge(
  inputQueue: SupervisorInputQueueItem[],
): string | null {
  const queuedItems = inputQueue.filter((item) => item.status === "queued");
  const queuedCount = queuedItems.length;
  if (queuedCount === 0) return null;
  const queuePosition = queuedItems
    .map((item) => item.queuePosition)
    .filter((value): value is number => value !== null)
    .sort((left, right) => left - right)[0];
  return `已排队 ${queuedCount} 条${queuePosition ? ` · 第 ${queuePosition} 位` : ""}`;
}

export function resolveSupervisorRuntimeNotice(
  input: SupervisorRuntimeNoticeInput,
): SupervisorRuntimeNoticeModel | null {
  if (!input.enabled) return null;
  const queueBadge = resolveQueueBadge(input.inputQueue);

  if (input.compression.status === "compacting") {
    const progressPercent = input.compression.progressPercent;
    return {
      kind: "compression",
      tone: "working",
      title: COMPRESSION_STARTED_COPY,
      detail: progressPercent !== null && progressPercent > 0
        ? `整理进度 ${progressPercent}%。你仍可继续发送，新输入会安全排队。`
        : "你仍可继续发送，新输入会安全排队。",
      progressPercent,
      queueBadge,
    };
  }

  if (input.compression.status === "blocked") {
    return {
      kind: "compression",
      tone: "warning",
      title: COMPRESSION_FAILED_COPY,
      detail: null,
      progressPercent: input.compression.progressPercent,
      queueBadge,
    };
  }

  if (input.compression.lastOutcome === "completed" && input.runStatus === "running") {
    return {
      kind: "compression",
      tone: "success",
      title: COMPRESSION_COMPLETED_COPY,
      detail: null,
      progressPercent: 100,
      queueBadge,
    };
  }

  if (queueBadge) {
    return {
      kind: "queue",
      tone: "queued",
      title: "输入已排队，系统会按顺序处理。",
      detail: null,
      progressPercent: null,
      queueBadge,
    };
  }

  return null;
}
