import { FileText } from "lucide-react";

import type { VideoAgentScriptEvidence } from "./state/workspace";

interface AgentScriptPreviewPanelProps {
  script: VideoAgentScriptEvidence;
  revision: number;
}

export function AgentScriptPreviewPanel({
  script,
  revision,
}: AgentScriptPreviewPanelProps) {
  return (
    <aside
      aria-label="脚本预览"
      data-workspace-revision={revision}
      className="flex min-h-0 w-full max-w-[440px] shrink-0 flex-col gap-4 overflow-y-auto border-l border-slate-200 bg-white p-4 xl:w-[440px]"
    >
      <header className="flex items-start gap-3">
        <div className="mt-0.5 rounded-lg bg-sky-50 p-2 text-sky-700">
          <FileText className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-slate-500">
            脚本草稿 · v{script.version}
            {script.reviewRequired ? " · 待确认" : ""}
            {" · "}revision {revision}
          </p>
          <h2 className="truncate text-base font-semibold text-slate-900">带货脚本预览</h2>
          <p className="mt-1 truncate text-xs text-slate-500">{script.artifactRef}</p>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3">
        <pre className="whitespace-pre-wrap break-words font-sans text-[13px] leading-6 text-slate-800">
          {script.content}
        </pre>
      </div>
    </aside>
  );
}
