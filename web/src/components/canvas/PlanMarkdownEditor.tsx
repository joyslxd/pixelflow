import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, FileText, LoaderCircle, LockKeyhole } from "lucide-react";
import MDEditor from "@uiw/react-md-editor";
import "@uiw/react-md-editor/markdown-editor.css";
import "@uiw/react-markdown-preview/markdown.css";

interface PlanMarkdownEditorProps {
  planVersion: number;
  initialMarkdown: string;
  saving?: boolean;
  onClose: () => void;
  onConfirm: (markdown: string) => Promise<void> | void;
}

const EXECUTION_CONTRACT_HEADING = /^##\s+制作执行合同\s*$/m;

function splitPlanMarkdown(markdown: string): { editable: string; locked: string } {
  const match = EXECUTION_CONTRACT_HEADING.exec(markdown);
  if (!match || match.index === undefined) return { editable: markdown, locked: "" };
  return {
    editable: markdown.slice(0, match.index).trimEnd(),
    locked: markdown.slice(match.index).trim(),
  };
}

function composePlanMarkdown(editable: string, locked: string): string {
  const normalizedEditable = editable.trim();
  const normalizedLocked = locked.trim();
  if (!normalizedLocked) return normalizedEditable;
  if (!normalizedEditable) return normalizedLocked;
  return `${normalizedEditable}\n\n${normalizedLocked}`;
}

export function PlanMarkdownEditor({
  planVersion,
  initialMarkdown,
  saving = false,
  onClose,
  onConfirm,
}: PlanMarkdownEditorProps) {
  const initialParts = useMemo(() => splitPlanMarkdown(initialMarkdown), [initialMarkdown]);
  const [editableMarkdown, setEditableMarkdown] = useState(initialParts.editable);

  useEffect(() => setEditableMarkdown(initialParts.editable), [initialParts.editable, planVersion]);

  const completeMarkdown = useMemo(
    () => composePlanMarkdown(editableMarkdown, initialParts.locked),
    [editableMarkdown, initialParts.locked],
  );
  const dirty = editableMarkdown !== initialParts.editable;

  const closeEditor = () => {
    if (dirty && !window.confirm("当前修改尚未发布，确定关闭编辑器吗？")) return;
    onClose();
  };

  return (
    <aside className="fixed inset-0 z-50 flex h-full w-full min-w-0 max-w-none flex-col border-l border-line bg-[#f8fafc] xl:static xl:z-auto xl:w-[52vw] xl:min-w-[680px] xl:max-w-[980px]">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-white px-4">
        <button type="button" onClick={closeEditor} className="flex h-9 w-9 items-center justify-center rounded-full hover:bg-canvas" aria-label="返回">
          <ArrowLeft size={18} />
        </button>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
          <FileText size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-semibold text-ink">编辑 plan.md v{planVersion}</div>
          <div className="text-[12px] text-ink-soft">修改后将发布为新版本，原版本仍保留在历史记录中</div>
        </div>
        {dirty && <span className="rounded-full bg-amber/10 px-2.5 py-1 text-[11px] text-ink-soft">未发布</span>}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 p-4" data-color-mode="light">
        <div className="min-h-[260px] flex-1">
          <MDEditor
            value={editableMarkdown}
            onChange={(value) => setEditableMarkdown(value || "")}
            height="100%"
            preview="edit"
            visibleDragbar={false}
            textareaProps={{
              placeholder: "请输入 plan.md 创意正文",
              "aria-label": "plan.md 创意正文编辑器",
            }}
            previewOptions={{ skipHtml: true }}
            className="h-full overflow-hidden rounded-2xl border border-line shadow-none"
          />
        </div>

        {initialParts.locked && (
          <section className="flex max-h-[38%] min-h-[180px] shrink-0 flex-col overflow-hidden rounded-2xl border border-amber/35 bg-white">
            <div className="flex shrink-0 items-center gap-2 border-b border-amber/25 bg-amber/10 px-3 py-2.5">
              <LockKeyhole size={15} className="text-amber-700" />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-semibold text-ink">制作执行合同（只读）</div>
                <div className="text-[11px] text-ink-soft">合同和精确分镜时间线由已确认的制作参数控制，不能在 Plan 编辑器中修改</div>
              </div>
            </div>
            <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap px-4 py-3 font-mono text-[12px] leading-6 text-ink">
              {initialParts.locked}
            </pre>
          </section>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line bg-white px-4 py-3">
        <div className="text-[12px] text-ink-soft">{completeMarkdown.length.toLocaleString()} 字符 · 后续生成将使用发布后的新版本</div>
        <div className="flex gap-2">
          <button type="button" onClick={closeEditor} disabled={saving} className="rounded-xl border border-line px-4 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas disabled:opacity-50">
            取消
          </button>
          <button
            type="button"
            onClick={() => void onConfirm(completeMarkdown)}
            disabled={saving || !dirty || !editableMarkdown.trim()}
            className="flex min-w-[132px] items-center justify-center gap-1.5 rounded-xl bg-brand px-4 py-2.5 text-[13px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {saving ? <LoaderCircle size={15} className="animate-spin" /> : <Check size={15} />}
            {saving ? "正在发布" : "确认修改"}
          </button>
        </div>
      </div>
    </aside>
  );
}
