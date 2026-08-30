/** F1 任务看板：只展示当前 Run 公开状态，不推进 Workflow。 */

type AgentTaskBoardProps = {
  status: string | undefined;
  latestProgress?: string;
  recoveryRequired?: boolean;
  recovering?: boolean;
  onRecover?: () => void;
};

function statusLabel(status: string | undefined, recoveryRequired: boolean): string {
  /** 把固定 Run 状态映射为用户可读文本，不暴露 Harness 内部概念。 */

  return ({
    accepted: "已受理",
    running: "正在处理",
    completed: "已完成",
    failed: recoveryRequired ? "等待继续" : "处理失败",
    cancelled: "已取消",
  } as Record<string, string>)[status ?? ""] ?? "未启动";
}

export function AgentTaskBoard({ status, latestProgress, recoveryRequired = false, recovering = false, onRecover }: AgentTaskBoardProps) {
  const active = status === "accepted" || status === "running";
  return (
    <div className="mb-2 flex items-center justify-between gap-3 rounded bg-accent-soft px-3 py-2 text-xs text-ink-soft" aria-live="polite">
      <span className="flex min-w-0 items-center gap-2">
        {active ? <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-accent border-t-transparent" aria-label="正在执行" /> : null}
        <span className="truncate">任务看板：{statusLabel(status, recoveryRequired)}{active ? ` · ${latestProgress || "正在调用工具并整理结果"}` : ""}</span>
      </span>
      {recoveryRequired && onRecover ? (
        <button
          type="button"
          className="rounded border border-accent px-2 py-1 text-xs text-accent disabled:opacity-50"
          disabled={recovering}
          onClick={onRecover}
        >
          {recovering ? "继续中…" : "继续执行"}
        </button>
      ) : null}
    </div>
  );
}
