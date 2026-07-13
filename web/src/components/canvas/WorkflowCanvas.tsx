import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Check, CircleAlert, Clock3, Film, GitBranch, Image, Package, Scissors, ShieldCheck, Sparkles } from "lucide-react";
import type { CanvasState } from "@/lib/chat";
import { buildWorkflowGraph, type WorkflowNodeData, type WorkflowNodeKind, type WorkflowNodeStatus } from "@/lib/workflow";
import { cn } from "@/lib/utils";

interface WorkflowCanvasProps {
  state: CanvasState;
}

const KIND_ICON: Record<WorkflowNodeKind, typeof Package> = {
  input: Package,
  agent: Sparkles,
  model: Image,
  edit: Scissors,
  review: ShieldCheck,
  export: Film,
};

const STATUS_LABEL: Record<WorkflowNodeStatus, string> = {
  pending: "等待",
  running: "运行中",
  review: "待确认",
  success: "完成",
  error: "失败",
};

const STATUS_STYLE: Record<WorkflowNodeStatus, string> = {
  pending: "border-line bg-surface text-ink-soft",
  running: "border-amber/30 bg-amber/10 text-amber",
  review: "border-accent/30 bg-accent-soft text-accent",
  success: "border-emerald/20 bg-emerald/10 text-emerald",
  error: "border-rose-200 bg-rose-50 text-rose-600",
};

function statusIcon(status: WorkflowNodeStatus) {
  if (status === "success") return <Check size={12} />;
  if (status === "error") return <CircleAlert size={12} />;
  if (status === "running" || status === "review") return <Clock3 size={12} />;
  return <GitBranch size={12} />;
}

function FlowNode({ data, selected }: NodeProps<Node<WorkflowNodeData>>) {
  const Icon = KIND_ICON[data.kind];
  return (
    <div
      className={cn(
        "w-[210px] rounded-lg border bg-surface shadow-sm transition-shadow",
        selected ? "border-accent shadow-md" : "border-line",
      )}
    >
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-accent" />
      <div className="border-b border-line px-3 py-2">
        <div className="flex items-start gap-2">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-canvas text-ink-soft">
            <Icon size={15} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-semibold text-ink">{data.title}</div>
            <div className="mt-0.5 truncate text-[11px] text-ink-soft">{data.subtitle}</div>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className={cn("inline-flex h-6 items-center gap-1 rounded-md border px-2 text-[11px]", STATUS_STYLE[data.status])}>
            {statusIcon(data.status)}
            {STATUS_LABEL[data.status]}
          </span>
          {data.model && <span className="truncate text-[11px] text-ink-soft">{data.model}</span>}
        </div>
      </div>
      <div className="px-3 py-2">
        <p className="line-clamp-2 min-h-[34px] text-[12px] leading-4 text-ink-soft">{data.description || "等待 Agent 更新节点。"}</p>
        {(data.outputs?.length || 0) > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {data.outputs?.slice(0, 3).map((item) => (
              <span key={item} className="rounded-md bg-canvas px-1.5 py-1 text-[11px] text-ink-soft">
                {item}
              </span>
            ))}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-accent" />
    </div>
  );
}

const nodeTypes = { workflow: FlowNode };

export function WorkflowCanvas({ state }: WorkflowCanvasProps) {
  const graph = useMemo(() => buildWorkflowGraph(state), [state]);
  const initialNodes = useMemo<Node<WorkflowNodeData>[]>(
    () => graph.nodes.map((node) => ({ ...node, type: "workflow", draggable: true })),
    [graph.nodes],
  );
  const initialEdges = useMemo<Edge[]>(
    () =>
      graph.edges.map((edge) => ({
        ...edge,
        animated: true,
        type: "smoothstep",
        style: { stroke: "#9ca3af", strokeWidth: 1.5 },
      })),
    [graph.edges],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selected, setSelected] = useState<WorkflowNodeData | null>(initialNodes[0]?.data ?? null);

  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
    setSelected((current) => {
      if (!current) return initialNodes[0]?.data ?? null;
      return initialNodes.find((node) => node.data.title === current.title)?.data ?? initialNodes[0]?.data ?? null;
    });
  }, [initialEdges, initialNodes, setEdges, setNodes]);

  const handleNodeClick = useCallback((_: unknown, node: Node<WorkflowNodeData>) => {
    setSelected(node.data);
  }, []);

  return (
    <div className="flex h-full min-h-[560px] flex-col overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-3">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-ink">Workflow Canvas</div>
          <div className="text-[11px] text-ink-soft">Agent 生成链路、模型节点与人工确认点</div>
        </div>
        <div className="flex items-center gap-1">
          {(["running", "review", "success"] as WorkflowNodeStatus[]).map((status) => (
            <span key={status} className={cn("inline-flex h-6 items-center gap-1 rounded-md border px-2 text-[11px]", STATUS_STYLE[status])}>
              {statusIcon(status)}
              {STATUS_LABEL[status]}
            </span>
          ))}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_260px]">
        <div className="min-h-0">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={handleNodeClick}
            fitView
            fitViewOptions={{ padding: 0.18 }}
            minZoom={0.35}
            maxZoom={1.4}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="#d7dbe3" />
            <MiniMap pannable zoomable className="!border !border-line !bg-white" nodeStrokeWidth={2} />
            <Controls className="!border !border-line !bg-white" />
          </ReactFlow>
        </div>

        <aside className="min-h-0 border-l border-line bg-canvas/60 p-3">
          {selected ? (
            <div className="space-y-3">
              <div>
                <div className="text-[13px] font-semibold text-ink">{selected.title}</div>
                <div className="mt-0.5 text-[12px] text-ink-soft">{selected.subtitle}</div>
              </div>
              <span className={cn("inline-flex h-6 items-center gap-1 rounded-md border px-2 text-[11px]", STATUS_STYLE[selected.status])}>
                {statusIcon(selected.status)}
                {STATUS_LABEL[selected.status]}
              </span>
              {selected.model && (
                <div className="rounded-lg border border-line bg-surface p-3">
                  <div className="text-[11px] font-medium uppercase text-ink-soft">Model</div>
                  <div className="mt-1 break-words text-[12px] text-ink">{selected.model}</div>
                </div>
              )}
              <div className="rounded-lg border border-line bg-surface p-3">
                <div className="text-[11px] font-medium uppercase text-ink-soft">Description</div>
                <p className="mt-1 text-[12px] leading-5 text-ink/85">{selected.description || "暂无详情。"}</p>
              </div>
              {(selected.inputs?.length || 0) > 0 && (
                <div className="rounded-lg border border-line bg-surface p-3">
                  <div className="text-[11px] font-medium uppercase text-ink-soft">Inputs</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {selected.inputs?.map((item) => (
                      <span key={item} className="rounded-md bg-canvas px-2 py-1 text-[11px] text-ink-soft">
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {(selected.outputs?.length || 0) > 0 && (
                <div className="rounded-lg border border-line bg-surface p-3">
                  <div className="text-[11px] font-medium uppercase text-ink-soft">Outputs</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {selected.outputs?.map((item) => (
                      <span key={item} className="rounded-md bg-accent-soft px-2 py-1 text-[11px] text-accent">
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {selected.error && <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-[12px] text-rose-600">{selected.error}</div>}
            </div>
          ) : (
            <div className="text-[12px] text-ink-soft">选择一个节点查看详情。</div>
          )}
        </aside>
      </div>
    </div>
  );
}
