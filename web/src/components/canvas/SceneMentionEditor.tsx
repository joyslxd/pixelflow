import { ImageIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import {
  normalizeShotMentions,
  type SceneMention,
  type SceneMentionCandidate,
} from "@/lib/sceneMentions";
import { MAX_REFERENCE_IMAGE_COUNT } from "@/lib/scenePackages";

interface SceneMentionEditorProps {
  text: string;
  shotDescription: Record<string, unknown>;
  candidates: SceneMentionCandidate[];
  onChange: (next: { text: string; mentions: SceneMention[] }) => void;
}

interface ActiveMentionQuery {
  text: string;
  left: number;
  top: number;
}

export function SceneMentionEditor({ text, shotDescription, candidates, onChange }: SceneMentionEditorProps) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const editorRef = useRef<HTMLDivElement | null>(null);
  const queryRangeRef = useRef<Range | null>(null);
  const lastDomKeyRef = useRef("");
  const [activeQuery, setActiveQuery] = useState<ActiveMentionQuery | null>(null);
  const mentions = useMemo(() => normalizeShotMentions({ ...shotDescription, text }, [], undefined), [shotDescription, text]);
  const mentionedAssetIds = useMemo(() => new Set(mentions.map((mention) => mention.asset_id)), [mentions]);
  const canAddNewReference = mentions.length < MAX_REFERENCE_IMAGE_COUNT;
  const filteredCandidates = useMemo(() => {
    const keyword = (activeQuery?.text || "").replace(/^@/, "").trim().toLowerCase();
    return candidates
      .filter((candidate) => {
        if (!keyword) return true;
        return [candidate.name, candidate.asset_id, candidate.type].some((value) => value.toLowerCase().includes(keyword));
      })
      .sort((a, b) => Number(mentionedAssetIds.has(b.asset_id)) - Number(mentionedAssetIds.has(a.asset_id)));
  }, [activeQuery?.text, candidates, mentionedAssetIds]);
  const canSelectCandidate = (candidate: SceneMentionCandidate) => canAddNewReference || mentionedAssetIds.has(candidate.asset_id);
  const firstSelectableCandidate = filteredCandidates.find(canSelectCandidate);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const key = editorStateKey(text, mentions);
    if (lastDomKeyRef.current === key) return;
    renderEditorContent(editor, text, mentions);
    lastDomKeyRef.current = key;
  }, [mentions, text]);

  const emitFromEditor = () => {
    const editor = editorRef.current;
    if (!editor) return;
    const next = serializeEditorContent(editor);
    lastDomKeyRef.current = editorStateKey(next.text, next.mentions);
    onChange(next);
  };

  const updateMentionQuery = () => {
    const query = readActiveMentionQuery(wrapperRef.current, editorRef.current);
    queryRangeRef.current = query?.range || null;
    setActiveQuery(query ? { text: query.text, left: query.left, top: query.top } : null);
  };

  const handleInput = () => {
    emitFromEditor();
    updateMentionQuery();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!activeQuery) return;
    if (event.key === "Escape") {
      event.preventDefault();
      queryRangeRef.current = null;
      setActiveQuery(null);
      return;
    }
    if ((event.key === "Enter" || event.key === "Tab") && firstSelectableCandidate) {
      event.preventDefault();
      selectCandidate(firstSelectableCandidate);
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    insertTextAtSelection(event.clipboardData.getData("text/plain"));
    handleInput();
  };

  const selectCandidate = (candidate: SceneMentionCandidate) => {
    if (!canSelectCandidate(candidate)) return;
    const editor = editorRef.current;
    if (!editor) return;
    editor.focus();
    const selection = window.getSelection();
    const range = queryRangeRef.current || selection?.getRangeAt(0);
    if (!range) return;
    range.deleteContents();
    const chip = createMentionChip(candidate);
    const space = document.createTextNode(" ");
    const fragment = document.createDocumentFragment();
    fragment.append(chip, space);
    range.insertNode(fragment);
    const caret = document.createRange();
    caret.setStartAfter(space);
    caret.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(caret);
    queryRangeRef.current = null;
    setActiveQuery(null);
    emitFromEditor();
  };

  return (
    <div ref={wrapperRef} className="relative grid gap-2">
      <div
        ref={editorRef}
        role="textbox"
        aria-multiline="true"
        contentEditable
        suppressContentEditableWarning
        data-placeholder="0-5秒: 地点:@办公室走廊 中,角色:@赵总监 完成动作。5-12秒: 地点:@办公室走廊 中,角色:@林晓 进入近景。"
        onInput={handleInput}
        onClick={updateMentionQuery}
        onFocus={updateMentionQuery}
        onKeyDown={handleKeyDown}
        onKeyUp={updateMentionQuery}
        onMouseUp={updateMentionQuery}
        onPaste={handlePaste}
        className="min-h-44 w-full rounded-xl border border-line bg-white px-3 py-2 text-[13px] leading-relaxed text-ink outline-none empty:before:pointer-events-none empty:before:text-ink-soft empty:before:content-[attr(data-placeholder)] focus:border-accent"
      />

      {activeQuery && filteredCandidates.length > 0 ? (
        <div
          className="absolute z-30 w-[min(440px,calc(100%-24px))] overflow-hidden rounded-xl border border-line bg-white shadow-xl"
          style={{ left: activeQuery.left, top: activeQuery.top }}
        >
          <div className="border-b border-line px-3 py-2 text-[12px] text-ink-soft">
            选择素材进行关联
            {!canAddNewReference ? <span className="ml-2 text-amber">最多 9 张不同图片，已关联素材可重复引用</span> : null}
          </div>
          <div className="max-h-56 overflow-y-auto p-1.5">
            {filteredCandidates.slice(0, 8).map((candidate) => {
              const selectable = canSelectCandidate(candidate);
              return (
                <button
                  key={candidate.asset_id}
                  type="button"
                  disabled={!selectable}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => selectCandidate(candidate)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {candidate.image_url ? (
                    <img src={candidate.image_url} alt="" className="h-9 w-9 shrink-0 rounded-md object-cover" />
                  ) : (
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-canvas text-ink-soft">
                      <ImageIcon size={15} />
                    </span>
                  )}
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] font-medium text-ink">@{candidate.name}</span>
                    <span className="block truncate text-[11px] text-ink-soft">{candidate.asset_id}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="text-[12px] text-ink-soft">
        已关联 {mentions.length}/{MAX_REFERENCE_IMAGE_COUNT}
        {mentions.length >= MAX_REFERENCE_IMAGE_COUNT ? <span className="ml-2 text-amber">最多 9 张不同图片，已关联素材可重复引用</span> : null}
      </div>
    </div>
  );
}

function readActiveMentionQuery(wrapper: HTMLDivElement | null, editor: HTMLDivElement | null) {
  if (!wrapper || !editor) return null;
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || !selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!editor.contains(range.startContainer)) return null;
  if (range.startContainer.nodeType !== Node.TEXT_NODE) return null;
  const textNode = range.startContainer;
  const source = textNode.textContent || "";
  const leftText = source.slice(0, range.startOffset);
  const atIndex = leftText.lastIndexOf("@");
  if (atIndex < 0) return null;
  const queryText = leftText.slice(atIndex);
  if (/[\s，。,.!?！？；;、]/.test(queryText.slice(1))) return null;
  const queryRange = document.createRange();
  queryRange.setStart(textNode, atIndex);
  queryRange.setEnd(textNode, range.startOffset);
  const rect = queryRange.getBoundingClientRect();
  const wrapperRect = wrapper.getBoundingClientRect();
  return {
    text: queryText,
    range: queryRange,
    left: Math.max(8, Math.min(rect.left - wrapperRect.left, wrapperRect.width - 460)),
    top: Math.max(36, rect.bottom - wrapperRect.top + 8),
  };
}

function renderEditorContent(editor: HTMLDivElement, text: string, mentions: SceneMention[]) {
  editor.replaceChildren();
  const parts = splitTextByMentions(text, mentions);
  parts.forEach((part) => {
    if (typeof part === "string") {
      editor.appendChild(document.createTextNode(part));
    } else {
      editor.appendChild(createMentionChip(part));
    }
  });
}

function splitTextByMentions(text: string, mentions: SceneMention[]): Array<string | SceneMention> {
  if (!text) return [];
  const tokens = mentions
    .flatMap((mention) => [
      { token: `@${mention.name}`, mention },
      { token: `@${mention.asset_id}`, mention },
    ])
    .filter((item) => item.token.length > 1)
    .sort((a, b) => b.token.length - a.token.length);
  const parts: Array<string | SceneMention> = [];
  let cursor = 0;
  while (cursor < text.length) {
    const match = tokens.find((item) => text.startsWith(item.token, cursor));
    if (!match) {
      parts.push(text[cursor]);
      cursor += 1;
      continue;
    }
    parts.push(match.mention);
    cursor += match.token.length;
  }
  return mergeTextParts(parts);
}

function mergeTextParts(parts: Array<string | SceneMention>): Array<string | SceneMention> {
  const merged: Array<string | SceneMention> = [];
  for (const part of parts) {
    const previous = merged[merged.length - 1];
    if (typeof part === "string" && typeof previous === "string") {
      merged[merged.length - 1] = previous + part;
    } else {
      merged.push(part);
    }
  }
  return merged;
}

function createMentionChip(mention: SceneMention): HTMLSpanElement {
  const chip = document.createElement("span");
  chip.setAttribute("contenteditable", "false");
  chip.setAttribute("data-mention-id", mention.asset_id);
  chip.setAttribute("data-mention-type", mention.type);
  chip.setAttribute("data-mention-name", mention.name);
  chip.setAttribute("data-mention-image-url", mention.image_url || "");
  chip.className =
    "scene-mention-token group relative mx-0.5 inline-flex max-w-[180px] cursor-default items-center gap-1 rounded-full border border-accent/25 bg-accent-soft px-1.5 py-0.5 align-middle text-accent";
  if (mention.image_url) {
    const thumb = document.createElement("img");
    thumb.src = mention.image_url;
    thumb.alt = "";
    thumb.className = "h-4 w-4 shrink-0 rounded-full object-cover";
    chip.appendChild(thumb);
  }
  const label = document.createElement("span");
  label.className = "truncate";
  label.textContent = `@${mention.name}`;
  chip.appendChild(label);
  if (mention.image_url) {
    const preview = document.createElement("span");
    preview.className =
      "pointer-events-none absolute left-0 top-full z-40 mt-2 hidden w-56 overflow-hidden rounded-xl border border-line bg-white p-2 shadow-xl group-hover:block";
    const image = document.createElement("img");
    image.src = mention.image_url;
    image.alt = "";
    image.className = "h-44 w-full rounded-lg object-cover";
    const url = document.createElement("span");
    url.className = "mt-1 block truncate text-[11px] text-ink-soft";
    url.textContent = mention.image_url;
    preview.append(image, url);
    chip.appendChild(preview);
  }
  return chip;
}

function serializeEditorContent(editor: HTMLDivElement): { text: string; mentions: SceneMention[] } {
  const mentions: SceneMention[] = [];
  const seen = new Set<string>();
  const text = Array.from(editor.childNodes).map((node) => serializeNode(node, mentions, seen)).join("");
  return { text: text.replace(/\u00a0/g, " "), mentions };
}

function serializeNode(node: ChildNode, mentions: SceneMention[], seen: Set<string>): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
  if (!(node instanceof HTMLElement)) return "";
  if (node.dataset.mentionId) {
    const mention = {
      asset_id: node.dataset.mentionId,
      type: mentionType(node.dataset.mentionType),
      name: node.dataset.mentionName || node.dataset.mentionId,
      image_url: node.dataset.mentionImageUrl || undefined,
    };
    if (!seen.has(mention.asset_id) && mentions.length < MAX_REFERENCE_IMAGE_COUNT) {
      seen.add(mention.asset_id);
      mentions.push(mention);
    }
    return `@${mention.name}`;
  }
  if (node.tagName === "BR") return "\n";
  return Array.from(node.childNodes).map((child) => serializeNode(child, mentions, seen)).join("");
}

function mentionType(value: string | undefined): SceneMention["type"] {
  return value === "character" || value === "scene" || value === "prop" ? value : "reference";
}

function insertTextAtSelection(text: string) {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}

function editorStateKey(text: string, mentions: SceneMention[]): string {
  return JSON.stringify({
    text,
    mentions: mentions.map((mention) => [mention.asset_id, mention.type, mention.name, mention.image_url || ""]),
  });
}
