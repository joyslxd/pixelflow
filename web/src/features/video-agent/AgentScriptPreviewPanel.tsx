import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, FileText, LoaderCircle, PencilLine, X } from "lucide-react";

import {
  shortStageLabel,
  type ScriptSkillStageId,
} from "./scriptSkillStages";
import type {
  VideoAgentScriptEvidence,
  VideoAgentScriptStageEvidence,
} from "./state/workspace";

interface AgentScriptPreviewPanelProps {
  revision: number;
  script: VideoAgentScriptEvidence | null;
  stages?: VideoAgentScriptStageEvidence[];
  focusStageId?: ScriptSkillStageId | string | null;
  saving?: boolean;
  confirming?: boolean;
  /** 仅在导出脚本产物完成后展示确认按钮 */
  exportReady?: boolean;
  onSave?(markdown: string): Promise<void> | void;
  /** 用户确认脚本方案后进入资产包；与仅保存区分。 */
  onConfirmScript?(markdown: string): Promise<void> | void;
  /** 关闭右侧预览（默认收起，仅从对话卡片打开）。 */
  onClose?(): void;
}

/** 轻量 Markdown 预览：不依赖 @uiw（本地常未装齐），覆盖脚本常见语法。 */
function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index));
    }
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key++}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith("`")) {
      nodes.push(
        <code key={key++} className="rounded bg-slate-100 px-1 py-0.5 text-[12px] text-slate-700">
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (link) {
        nodes.push(
          <a
            key={key++}
            href={link[2]}
            target="_blank"
            rel="noreferrer"
            className="text-sky-700 underline underline-offset-2"
          >
            {link[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function ScriptMarkdownView({ source }: { source: string }) {
  const blocks = useMemo(() => {
    const lines = source.replaceAll("\r\n", "\n").split("\n");
    const nodes: ReactNode[] = [];
    let i = 0;
    let key = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        i += 1;
        continue;
      }
      if (/^```/.test(line)) {
        const fence = [line.replace(/^```\w*/, "")];
        i += 1;
        while (i < lines.length && !/^```/.test(lines[i])) {
          fence.push(lines[i]);
          i += 1;
        }
        i += 1;
        nodes.push(
          <pre
            key={key++}
            className="overflow-x-auto rounded-lg bg-slate-900/90 px-3 py-2 font-mono text-[12px] leading-5 text-slate-100"
          >
            {fence.join("\n").trim() || " "}
          </pre>,
        );
        continue;
      }
      const heading = /^(#{1,4})\s+(.+)$/.exec(line);
      if (heading) {
        const level = heading[1].length;
        const className =
          level === 1
            ? "text-[18px] font-semibold text-slate-900"
            : level === 2
              ? "text-[16px] font-semibold text-slate-900"
              : level === 3
                ? "text-[14px] font-semibold text-slate-900"
                : "text-[13px] font-semibold text-slate-800";
        nodes.push(
          <p key={key++} className={`mt-1 ${className}`}>
            {renderInline(heading[2])}
          </p>,
        );
        i += 1;
        continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        nodes.push(<hr key={key++} className="border-slate-200" />);
        i += 1;
        continue;
      }
      if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
        const items: string[] = [];
        const ordered = /^\s*\d+\.\s+/.test(line);
        while (
          i < lines.length
          && (ordered ? /^\s*\d+\.\s+/.test(lines[i]) : /^\s*[-*+]\s+/.test(lines[i]))
        ) {
          items.push(lines[i].replace(/^\s*(?:[-*+]|\d+\.)\s+/, ""));
          i += 1;
        }
        const ListTag = ordered ? "ol" : "ul";
        nodes.push(
          <ListTag
            key={key++}
            className={`space-y-1 pl-5 text-[13px] leading-6 text-slate-800 ${
              ordered ? "list-decimal" : "list-disc"
            }`}
          >
            {items.map((item, index) => (
              <li key={index}>{renderInline(item)}</li>
            ))}
          </ListTag>,
        );
        continue;
      }
      if (/^\|.+\|/.test(line)) {
        const rows: string[][] = [];
        while (i < lines.length && /^\|.+\|/.test(lines[i])) {
          const raw = lines[i];
          i += 1;
          if (/^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(raw)) continue;
          rows.push(
            raw
              .replace(/^\|/, "")
              .replace(/\|$/, "")
              .split("|")
              .map((cell) => cell.trim()),
          );
        }
        if (rows.length > 0) {
          const [header, ...body] = rows;
          nodes.push(
            <div key={key++} className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-[12px]">
                <thead>
                  <tr className="border-b border-slate-200 bg-white/80">
                    {header.map((cell, index) => (
                      <th key={index} className="px-2 py-1.5 font-semibold text-slate-700">
                        {renderInline(cell)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {body.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-b border-slate-100">
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="px-2 py-1.5 text-slate-700">
                          {renderInline(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>,
          );
        }
        continue;
      }
      if (/^>\s?/.test(line)) {
        const quotes: string[] = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          quotes.push(lines[i].replace(/^>\s?/, ""));
          i += 1;
        }
        nodes.push(
          <blockquote
            key={key++}
            className="border-l-2 border-slate-300 pl-3 text-[13px] leading-6 text-slate-600"
          >
            {quotes.map((item, index) => (
              <p key={index}>{renderInline(item)}</p>
            ))}
          </blockquote>,
        );
        continue;
      }
      const paragraph: string[] = [line];
      i += 1;
      while (
        i < lines.length
        && lines[i].trim()
        && !/^(#{1,4}\s|```|\s*[-*+]\s|\s*\d+\.\s|>\s?|\|)/.test(lines[i])
        && !/^(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i])
      ) {
        paragraph.push(lines[i]);
        i += 1;
      }
      nodes.push(
        <p key={key++} className="text-[13px] leading-6 text-slate-800">
          {renderInline(paragraph.join(" "))}
        </p>,
      );
    }
    return nodes;
  }, [source]);

  return <div className="script-md-preview space-y-2">{blocks}</div>;
}

export function AgentScriptPreviewPanel({
  revision,
  script,
  stages = [],
  focusStageId = null,
  saving = false,
  confirming = false,
  exportReady = false,
  onSave,
  onConfirmScript,
  onClose,
}: AgentScriptPreviewPanelProps) {
  const [draft, setDraft] = useState(script?.content ?? "");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sectionRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    setDraft(script?.content ?? "");
    setEditing(false);
    setError(null);
  }, [script?.artifactRef, script?.version, script?.content, revision]);

  useEffect(() => {
    if (!focusStageId || editing) return;
    const node = sectionRefs.current[focusStageId];
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusStageId, editing, stages, script?.content, revision]);

  const dirty = Boolean(script) && draft !== script.content;
  const canSave = Boolean(onSave && script) && dirty && draft.trim().length > 0 && !saving && !confirming;
  const canConfirm = Boolean(onConfirmScript && script)
    && exportReady
    && draft.trim().length > 0
    && !saving
    && !confirming
    && !editing;
  const hasStages = stages.length > 0;
  const title = hasStages
    ? (focusStageId
      ? `脚本预览 · ${shortStageLabel(focusStageId as ScriptSkillStageId, String(focusStageId))}`
      : "脚本预览 · 分阶段产物")
    : "脚本预览";

  const handleSave = async () => {
    if (!onSave || !canSave) return;
    setError(null);
    try {
      await onSave(draft.trim());
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请稍后重试");
    }
  };

  const handleConfirm = async () => {
    if (!onConfirmScript || !canConfirm) return;
    setError(null);
    try {
      await onConfirmScript(draft.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败，请稍后重试");
    }
  };

  const handleCancel = () => {
    if (!script) return;
    if (dirty && !window.confirm("当前修改尚未保存，确定放弃吗？")) return;
    setDraft(script.content);
    setEditing(false);
    setError(null);
  };

  return (
    <aside
      aria-label="脚本预览"
      data-workspace-revision={revision}
      className="flex min-h-0 w-full max-w-[440px] shrink-0 flex-col gap-3 overflow-hidden border-l border-slate-200 bg-white p-4 xl:w-[440px]"
    >
      <header className="flex items-start gap-3">
        <div className="mt-0.5 rounded-lg bg-sky-50 p-2 text-sky-700">
          <FileText className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-slate-500">
            {script ? `脚本草稿 · v${script.version}` : "阶段产物"}
            {script?.reviewRequired ? " · 待确认" : ""}
            {" · "}工作区 r{revision}
          </p>
          <h2 className="truncate text-base font-semibold text-slate-900">{title}</h2>
          <p className="mt-1 truncate text-xs text-slate-500">
            {script?.artifactRef
              ?? stages.find((stage) => stage.stageId === focusStageId)?.artifactRef
              ?? stages[stages.length - 1]?.artifactRef
              ?? "等待脚本阶段产物"}
          </p>
        </div>
        {onClose ? (
          <button
            type="button"
            aria-label="收起脚本预览"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            onClick={onClose}
          >
            <X className="size-4" />
          </button>
        ) : null}
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
        {editing && script ? (
          <textarea
            aria-label="脚本 Markdown 编辑器"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="min-h-0 flex-1 resize-none bg-transparent p-3 font-sans text-[13px] leading-6 text-slate-800 outline-none"
            placeholder="在此编辑脚本 Markdown…"
          />
        ) : hasStages ? (
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
            {stages.map((stage) => {
              const focused = focusStageId === stage.stageId;
              return (
                <section
                  key={stage.stageId}
                  id={`script-stage-${stage.stageId}`}
                  ref={(node) => {
                    sectionRefs.current[stage.stageId] = node;
                  }}
                  className={`scroll-mt-3 rounded-lg border px-3 py-2.5 ${
                    focused
                      ? "border-sky-300 bg-sky-50/80 ring-2 ring-sky-200"
                      : "border-slate-200/80 bg-white/70"
                  }`}
                >
                  <header className="mb-2 flex flex-wrap items-center gap-2">
                    <h3 className="text-[13px] font-semibold text-slate-900">
                      {shortStageLabel(stage.stageId as ScriptSkillStageId, stage.title)}
                    </h3>
                    <span className="rounded-full border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] text-slate-500">
                      /{stage.stageId}
                    </span>
                    {focused ? (
                      <span className="text-[10px] font-medium text-sky-700">当前步骤</span>
                    ) : null}
                  </header>
                  {stage.changeSummary ? (
                    <p className="mb-2 text-[11px] leading-5 text-slate-500">{stage.changeSummary}</p>
                  ) : null}
                  <ScriptMarkdownView source={stage.content} />
                </section>
              );
            })}
          </div>
        ) : script ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <ScriptMarkdownView source={script.content} />
          </div>
        ) : (
          <p className="p-3 text-[13px] text-slate-400">暂无脚本产物</p>
        )}
      </div>

      {editing && script ? (
        <div className="flex flex-col gap-2">
          {error ? <p className="text-[12px] text-rose-600">{error}</p> : null}
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] text-slate-400">
              {draft.length.toLocaleString()} 字符
              {dirty ? " · 未保存" : ""}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-[12px] font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                onClick={handleCancel}
                disabled={saving || confirming}
              >
                取消
              </button>
              <button
                type="button"
                className="inline-flex min-w-[88px] items-center justify-center gap-1 rounded-lg bg-sky-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-45"
                onClick={() => void handleSave()}
                disabled={!canSave}
              >
                {saving ? <LoaderCircle className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
                {saving ? "保存中" : "保存"}
              </button>
            </div>
          </div>
        </div>
      ) : script && (onSave || onConfirmScript) ? (
        <div className="flex flex-col gap-2 border-t border-slate-100 pt-3">
          {error ? <p className="text-[12px] text-rose-600">{error}</p> : null}
          {exportReady ? (
            <p className="text-[11px] leading-5 text-slate-500">
              脚本草稿已就绪。可先编辑，确认后生成视频资产包。
            </p>
          ) : (
            <p className="text-[11px] leading-5 text-slate-400">
              可先编辑脚本草稿；完成导出或补齐设定/分镜后，即可确认并生成资产包。
            </p>
          )}
          <div className="flex gap-2">
            {onSave ? (
              <button
                type="button"
                className="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-slate-200 px-3 py-2 text-[12px] font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                onClick={() => setEditing(true)}
                disabled={saving || confirming}
              >
                <PencilLine className="size-3.5" />
                编辑
              </button>
            ) : null}
            {onConfirmScript ? (
              <button
                type="button"
                className="inline-flex flex-[1.4] items-center justify-center gap-1 rounded-lg bg-sky-600 px-3 py-2 text-[12px] font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-45"
                onClick={() => void handleConfirm()}
                disabled={!canConfirm}
              >
                {confirming ? <LoaderCircle className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
                {confirming ? "确认中…" : "确认"}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
