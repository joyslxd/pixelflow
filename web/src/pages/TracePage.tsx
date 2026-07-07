import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, CircleDot, Database, ListTree, RefreshCw } from "lucide-react";
import { api, type RunEvent, type RunResponse, type TaskEvent, type TaskResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

const CATEGORY_TABS = ["all", "trace", "message", "lifecycle"] as const;
type CategoryTab = (typeof CATEGORY_TABS)[number];

function fmtTime(value: string) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function taskTitle(task: TaskResponse) {
  const product = task.product_info?.product_name;
  if (typeof product === "string" && product.trim()) return product;
  const core = task.creative_direction?.core_message;
  if (typeof core === "string" && core.trim()) return core;
  return task.task_id.slice(0, 8);
}

function eventContent(event: RunEvent) {
  if (typeof event.content === "string") return event.content;
  try {
    return JSON.stringify(event.content, null, 2);
  } catch {
    return String(event.content);
  }
}

function taskEventContent(event: TaskEvent) {
  const error = event.data?.error;
  if (typeof error === "string" && error.trim()) return error;
  try {
    return JSON.stringify(event.data, null, 2);
  } catch {
    return String(event.data);
  }
}

function Badge({ children, tone = "neutral" }: { children: string; tone?: "neutral" | "ok" | "warn" | "bad" }) {
  const styles = {
    neutral: "border-line bg-canvas text-ink-soft",
    ok: "border-emerald/20 bg-emerald/10 text-emerald",
    warn: "border-amber/20 bg-amber/10 text-amber",
    bad: "border-rose-200 bg-rose-50 text-rose-600",
  };
  return <span className={cn("inline-flex h-6 items-center rounded-md border px-2 text-[12px]", styles[tone])}>{children}</span>;
}

function runTone(status: string): "neutral" | "ok" | "warn" | "bad" {
  if (status === "success" || status === "done" || status === "completed") return "ok";
  if (status === "running" || status === "pending") return "warn";
  if (status === "error" || status === "failed") return "bad";
  return "neutral";
}

function taskEventTone(event: string): "neutral" | "ok" | "warn" | "bad" {
  if (event.includes("failed") || event.includes("error")) return "bad";
  if (event.includes("done") || event.includes("confirmed")) return "ok";
  if (event.includes("started") || event.includes("phase") || event.includes("ready")) return "warn";
  return "neutral";
}

export function TracePage() {
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [category, setCategory] = useState<CategoryTab>("all");
  const followLatestRef = useRef(true);

  const tasksQuery = useQuery({
    queryKey: ["trace", "tasks"],
    queryFn: () => api.listTasks(100),
    refetchInterval: 8000,
  });

  const tasks = useMemo(
    () =>
      [...(tasksQuery.data ?? [])].sort((a, b) => {
        const bt = new Date(b.updated_at || b.created_at).getTime();
        const at = new Date(a.updated_at || a.created_at).getTime();
        return (Number.isNaN(bt) ? 0 : bt) - (Number.isNaN(at) ? 0 : at);
      }),
    [tasksQuery.data],
  );
  const selectedTask = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? tasks[0],
    [selectedTaskId, tasks],
  );

  useEffect(() => {
    if (!tasks[0]) return;
    const selectedExists = tasks.some((task) => task.task_id === selectedTaskId);
    if (followLatestRef.current || !selectedTaskId || !selectedExists) {
      setSelectedTaskId(tasks[0].task_id);
      setSelectedRunId(tasks[0].run_id || "");
    }
  }, [selectedTaskId, tasks]);

  const runsQuery = useQuery({
    queryKey: ["trace", "runs", selectedTask?.thread_id],
    queryFn: () => api.listRuns(selectedTask!.thread_id),
    enabled: Boolean(selectedTask?.thread_id),
    refetchInterval: 5000,
  });

  const runs = runsQuery.data ?? [];
  const selectedRun = useMemo(
    () => runs.find((run) => run.run_id === selectedRunId) ?? runs.find((run) => run.run_id === selectedTask?.run_id) ?? runs[0],
    [runs, selectedRunId, selectedTask?.run_id],
  );

  useEffect(() => {
    if (selectedRun?.run_id && selectedRun.run_id !== selectedRunId) setSelectedRunId(selectedRun.run_id);
  }, [selectedRun?.run_id, selectedRunId]);

  const eventsQuery = useQuery({
    queryKey: ["trace", "events", selectedTask?.thread_id, selectedRun?.run_id, category],
    queryFn: () =>
      api.listRunEvents(
        selectedTask!.thread_id,
        selectedRun!.run_id,
        category === "all" ? undefined : category,
        1000,
      ),
    enabled: Boolean(selectedTask?.thread_id && selectedRun?.run_id),
    refetchInterval: selectedRun?.status === "running" ? 2000 : false,
  });

  const events = eventsQuery.data ?? [];

  const taskEventsQuery = useQuery({
    queryKey: ["trace", "task-events", selectedTask?.task_id],
    queryFn: () => api.eventsHistory(selectedTask!.task_id),
    enabled: Boolean(selectedTask?.task_id),
    refetchInterval: selectedTask?.status === "running" ? 2000 : 8000,
  });

  const taskEvents = taskEventsQuery.data?.data ?? [];

  return (
    <div className="grid h-full min-h-0 grid-cols-[300px_minmax(0,1fr)] overflow-hidden">
      <aside className="min-h-0 border-r border-line bg-surface">
        <div className="flex h-14 items-center justify-between border-b border-line px-4">
          <div className="flex items-center gap-2 text-[14px] font-semibold text-ink">
            <Database size={16} />
            Runs
          </div>
          <button
            onClick={() => {
              followLatestRef.current = true;
              void tasksQuery.refetch();
              void runsQuery.refetch();
              void eventsQuery.refetch();
              void taskEventsQuery.refetch();
            }}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-soft hover:bg-canvas hover:text-ink"
            aria-label="刷新"
          >
            <RefreshCw size={15} />
          </button>
        </div>
        <div className="border-b border-line px-4 py-2">
          <button
            type="button"
            onClick={() => {
              followLatestRef.current = true;
              if (tasks[0]) {
                setSelectedTaskId(tasks[0].task_id);
                setSelectedRunId(tasks[0].run_id || "");
                setCategory("all");
              }
            }}
            className="h-8 w-full rounded-md border border-line bg-canvas text-[12px] font-medium text-ink-soft hover:text-ink"
          >
            跟随最新任务
          </button>
        </div>

        <div className="h-[calc(100%-6.5rem)] overflow-y-auto p-2">
          {tasksQuery.isLoading && <div className="px-3 py-4 text-[13px] text-ink-soft">加载中…</div>}
          {!tasksQuery.isLoading && tasks.length === 0 && <div className="px-3 py-4 text-[13px] text-ink-soft">暂无任务</div>}
          {tasks.map((task) => (
            <button
              key={task.task_id}
              onClick={() => {
                followLatestRef.current = false;
                setSelectedTaskId(task.task_id);
                setSelectedRunId(task.run_id || "");
                setCategory("all");
              }}
              className={cn(
                "mb-1 w-full rounded-lg px-3 py-2.5 text-left transition-colors",
                selectedTask?.task_id === task.task_id ? "bg-accent-soft" : "hover:bg-canvas",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[13px] font-medium text-ink">{taskTitle(task)}</span>
                <Badge tone={runTone(task.status)}>{task.status}</Badge>
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[12px] text-ink-soft">
                <span className="truncate">{task.phase || "idle"}</span>
                <span className="shrink-0">{fmtTime(task.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-h-0 flex-col overflow-hidden">
        <div className="border-b border-line bg-surface px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-[15px] font-semibold text-ink">
                <Activity size={17} />
                Agent Trace
              </div>
              <div className="mt-1 truncate text-[12px] text-ink-soft">
                {selectedTask ? `thread ${selectedTask.thread_id}` : "未选择任务"}
              </div>
            </div>
            {selectedRun && (
              <div className="flex shrink-0 items-center gap-2">
                <Badge tone={runTone(selectedRun.status)}>{selectedRun.status}</Badge>
                <Badge>{`${selectedRun.total_tokens || 0} tokens`}</Badge>
                <Badge>{`${selectedRun.llm_call_count || 0} llm`}</Badge>
              </div>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {runs.map((run: RunResponse) => (
              <button
                key={run.run_id}
                onClick={() => setSelectedRunId(run.run_id)}
                className={cn(
                  "flex h-8 items-center gap-2 rounded-md border px-2.5 text-[12px] transition-colors",
                  selectedRun?.run_id === run.run_id
                    ? "border-accent/30 bg-accent-soft text-accent"
                    : "border-line bg-canvas text-ink-soft hover:text-ink",
                )}
              >
                <CircleDot size={12} />
                {run.run_id.slice(0, 8)}
              </button>
            ))}
            {!runsQuery.isLoading && selectedTask && runs.length === 0 && (
              <div className="text-[13px] text-ink-soft">这个任务还没有 run 记录</div>
            )}
          </div>

          <div className="mt-3 flex items-center gap-1">
            {CATEGORY_TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => setCategory(tab)}
                className={cn(
                  "h-8 rounded-md px-3 text-[12px] font-medium transition-colors",
                  category === tab ? "bg-brand text-white" : "text-ink-soft hover:bg-canvas hover:text-ink",
                )}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <div className="mb-4 rounded-lg border border-line bg-surface">
            <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2">
              <div className="flex min-w-0 items-center gap-2">
                <ListTree size={14} className="shrink-0 text-ink-soft" />
                <span className="text-[13px] font-semibold text-ink">Task Events</span>
                <Badge>{`${taskEvents.length} events`}</Badge>
              </div>
              {taskEventsQuery.isFetching && <span className="text-[12px] text-ink-soft">刷新中</span>}
            </div>
            <div className="divide-y divide-line">
              {taskEventsQuery.isLoading && <div className="px-3 py-3 text-[13px] text-ink-soft">加载 task events…</div>}
              {!taskEventsQuery.isLoading && taskEvents.length === 0 && (
                <div className="px-3 py-3 text-[13px] text-ink-soft">暂无 task events</div>
              )}
              {taskEvents.map((event) => (
                <article
                  key={`task-${event.id}`}
                  className={cn("px-3 py-3", taskEventTone(event.event) === "bad" && "bg-rose-50/80")}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="text-[12px] font-semibold text-ink">#{event.id}</span>
                      <Badge tone={taskEventTone(event.event)}>{event.event}</Badge>
                    </div>
                    <span className="shrink-0 text-[12px] text-ink-soft">{fmtTime(event.created_at || "")}</span>
                  </div>
                  <pre className="mt-2 max-h-[220px] overflow-auto whitespace-pre-wrap break-words text-[12px] leading-5 text-ink/85">
                    {taskEventContent(event)}
                  </pre>
                </article>
              ))}
            </div>
          </div>

          {eventsQuery.isLoading && <div className="text-[13px] text-ink-soft">加载 run events…</div>}
          {!eventsQuery.isLoading && selectedRun && events.length === 0 && (
            <div className="text-[13px] text-ink-soft">暂无 run events</div>
          )}
          {!selectedRun && !runsQuery.isLoading && <div className="text-[13px] text-ink-soft">请选择一个 run</div>}

          <div className="space-y-2">
            {events.map((event) => (
              <article key={`${event.run_id}-${event.seq}`} className="rounded-lg border border-line bg-surface">
                <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <ListTree size={14} className="shrink-0 text-ink-soft" />
                    <span className="text-[12px] font-semibold text-ink">#{event.seq}</span>
                    <Badge>{event.category}</Badge>
                    <span className="truncate text-[13px] text-ink">{event.event_type}</span>
                  </div>
                  <span className="shrink-0 text-[12px] text-ink-soft">{fmtTime(event.created_at)}</span>
                </div>
                <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words px-3 py-3 text-[12px] leading-5 text-ink/85">
                  {eventContent(event)}
                </pre>
              </article>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
