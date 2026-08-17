import { useEffect, useState } from "react";
import type { NativePlanStepView, NativeToolActivity } from "../state/contracts";
import { ToolActivityItem } from "./ToolActivityItem";

interface AgentActivityTimelineProps {
  planSteps: NativePlanStepView[];
  tools: NativeToolActivity[];
  now?: number;
}

/** 计划步骤（最多 3）+ Tool 活动时间线。 */
export function AgentActivityTimeline({
  planSteps,
  tools,
  now: nowProp,
}: AgentActivityTimelineProps) {
  const [now, setNow] = useState(nowProp ?? Date.now());
  const hasRunning = tools.some((item) => item.status === "running")
    || planSteps.some((step) => step.status === "running");

  useEffect(() => {
    if (nowProp != null) {
      setNow(nowProp);
      return;
    }
    if (!hasRunning) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasRunning, nowProp]);

  if (planSteps.length === 0 && tools.length === 0) return null;

  return (
    <div className="space-y-2">
      {planSteps.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">计划</div>
          <ol className="space-y-1.5">
            {planSteps.slice(0, 3).map((step) => (
              <li
                key={step.stepId}
                className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2 text-sm text-slate-700"
              >
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-slate-800">{step.title}</span>
                  <span className="text-xs text-slate-500">{step.status}</span>
                </div>
                {step.publicSummary ? (
                  <p className="mt-0.5 text-slate-600">{step.publicSummary}</p>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {tools.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">活动</div>
          <div className="space-y-1.5">
            {tools.map((activity) => (
              <ToolActivityItem key={activity.toolCallId} activity={activity} now={now} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
