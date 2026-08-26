/** F1 任务看板：只展示当前 Run 公开状态，不推进 Workflow。 */

type AgentTaskBoardProps = {
  status: string | undefined;
};

function statusLabel(status: string | undefined): string {
  /** 把固定 Run 状态映射为用户可读文本，不暴露 Harness 内部概念。 */

  return ({
    accepted: "已受理",
    running: "正在处理",
    completed: "已完成",
    failed: "处理失败",
    cancelled: "已取消",
  } as Record<string, string>)[status ?? ""] ?? "未启动";
}

export function AgentTaskBoard({ status }: AgentTaskBoardProps) {
  return (
    <div className="mb-2 rounded bg-accent-soft px-3 py-2 text-xs text-ink-soft" aria-live="polite">
      任务看板：{statusLabel(status)}
    </div>
  );
}
