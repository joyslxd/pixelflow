/** M06 Operation 进度面板：仅展示公开事件投影，不启动或轮询 Provider。 */

import type { PublicOperationV1 } from "@/api/contracts";

type OperationProgressProps = {
  operations: PublicOperationV1[];
};

function statusLabel(status: PublicOperationV1["status"]): string {
  return ({
    queued: "等待调度",
    running: "处理中",
    paused: "已暂停",
    completed: "已完成",
    failed: "未完成",
  } as Record<PublicOperationV1["status"], string>)[status];
}

export function OperationProgress({ operations }: OperationProgressProps) {
  if (operations.length === 0) return null;
  return (
    <section className="space-y-2" aria-label="外部任务进度">
      {operations.map((operation) => (
        <div key={operation.operation_id} className="rounded border border-line bg-canvas px-3 py-2 text-xs text-ink-soft">
          <p className="font-medium text-ink">外部任务：{statusLabel(operation.status)}</p>
          {operation.completed !== null && operation.total !== null ? (
            <p className="mt-1">进度：{operation.completed}/{operation.total}</p>
          ) : null}
        </div>
      ))}
    </section>
  );
}
