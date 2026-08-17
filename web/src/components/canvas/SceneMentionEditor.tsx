import { ImageIcon } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import {
  filterMentionCandidates,
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
  /** 表格单元格内联编辑：更矮、无外框，默认不单独展示关联计数。 */
  compact?: boolean;
  showAssociationCount?: boolean;
  className?: string;
  placeholder?: string;
}

interface ActiveMentionQuery {
  text: string;
  left: number;
  top: number;
  width: number;
  placement: "above" | "below";
  listMaxHeight: number;
}

const MENTION_MENU_MARGIN = 8;
const MENTION_MENU_MAX_WIDTH = 440;
const MENTION_MENU_MAX_LIST_HEIGHT = 224;
const MENTION_MENU_HEADER_HEIGHT = 38;

const MENTION_CANDIDATE_GROUPS: Array<{ group: SceneMentionCandidate["group"]; label: string }> = [
  { group: "characters", label: "角色" },
  { group: "scenes", label: "场景" },
  { group: "props", label: "道具" },
];

export function SceneMentionEditor({
  text,
  shotDescription,
  candidates,
  onChange,
  compact = false,
  showAssociationCount,
  className,
  placeholder,
}: SceneMentionEditorProps) {
  const showCount = showAssociationCount ?? !compact;
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const editorRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const queryRangeRef = useRef<Range | null>(null);
  const lastDomKeyRef = useRef("");
  const [activeQuery, setActiveQuery] = useState<ActiveMentionQuery | null>(null);
  const mentions = useMemo(
    () => normalizeShotMentions({ ...shotDescription, text }, [], candidates),
    [candidates, shotDescription, text],
  );
  const mentionedAssetIds = useMemo(() => new Set(mentions.map((mention) => mention.asset_id)), [mentions]);
  const canAddNewReference = mentions.length < MAX_REFERENCE_IMAGE_COUNT;
  const filteredCandidates = useMemo(() => {
    return filterMentionCandidates(candidates, activeQuery?.text || "")
      .sort((a, b) => Number(mentionedAssetIds.has(b.asset_id)) - Number(mentionedAssetIds.has(a.asset_id)));
  }, [activeQuery?.text, candidates, mentionedAssetIds]);
  const groupedCandidates = MENTION_CANDIDATE_GROUPS
    .map(({ group, label }) => ({
      group,
      label,
      candidates: filteredCandidates.filter((candidate) => candidate.group === group),
    }))
    .filter(({ candidates: groupCandidates }) => groupCandidates.length > 0);
  const canSelectCandidate = (candidate: SceneMentionCandidate) => canAddNewReference || mentionedAssetIds.has(candidate.asset_id);
  const firstSelectableCandidate = groupedCandidates.flatMap(({ candidates: groupCandidates }) => groupCandidates).find(canSelectCandidate);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const key = editorStateKey(text, mentions);
    if (lastDomKeyRef.current === key) return;
    // 聚焦编辑中：若正文与当前 DOM 一致，只同步 key，禁止 replaceChildren（否则光标乱跳）。
    if (document.activeElement === editor) {
      const live = serializeEditorContent(editor);
      if (live.text === text) {
        lastDomKeyRef.current = key;
        return;
      }
    }
    renderEditorContent(editor, text, mentions);
    lastDomKeyRef.current = key;
  }, [mentions, text]);

  useEffect(() => {
    if (!activeQuery) return;
    const closeMenu = () => {
      queryRangeRef.current = null;
      setActiveQuery(null);
    };
    const closeOnExternalScroll = (event: Event) => {
      if (event.target instanceof Node && menuRef.current?.contains(event.target)) return;
      closeMenu();
    };
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", closeOnExternalScroll, true);
    return () => {
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", closeOnExternalScroll, true);
    };
  }, [activeQuery]);

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
    setActiveQuery(query ? {
      text: query.text,
      left: query.left,
      top: query.top,
      width: query.width,
      placement: query.placement,
      listMaxHeight: query.listMaxHeight,
    } : null);
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

  const mentionMenu = activeQuery && filteredCandidates.length > 0 && typeof document !== "undefined"
    ? createPortal(
        <div
          ref={menuRef}
          data-scene-mention-menu
          className="fixed z-[100] overflow-hidden rounded-xl border border-line bg-white shadow-xl"
          style={{
            left: activeQuery.left,
            top: activeQuery.top,
            width: activeQuery.width,
            transform: activeQuery.placement === "above" ? "translateY(-100%)" : undefined,
          }}
        >
          <div className="border-b border-line px-3 py-2 text-[12px] text-ink-soft">
            选择素材进行关联
            {!canAddNewReference ? <span className="ml-2 text-amber">最多 9 张不同图片，已关联素材可重复引用</span> : null}
          </div>
          <div className="overflow-y-auto px-1.5 pb-1.5" style={{ maxHeight: activeQuery.listMaxHeight }}>
            {groupedCandidates.map(({ group, label, candidates: groupCandidates }) => (
              <Fragment key={group}>
                <div className="sticky top-0 z-10 bg-white px-2 py-1 text-[11px] font-medium text-ink-soft">{label} ({groupCandidates.length})</div>
                {groupCandidates.map((candidate) => {
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
              </Fragment>
            ))}
          </div>
        </div>,
        document.body,
      )
    : null;

  return (
    <div ref={wrapperRef} className={compact ? "relative" : "relative grid gap-2"}>
      <div
        ref={editorRef}
        role="textbox"
        aria-multiline="true"
        contentEditable
        suppressContentEditableWarning
        data-placeholder={
          placeholder
          || (compact
            ? "点击编辑，输入 @ 关联参考图"
            : "0-5秒: 地点:@办公室走廊 中,角色:@赵总监 完成动作。5-12秒: 地点:@办公室走廊 中,角色:@林晓 进入近景。")
        }
        onInput={handleInput}
        onClick={updateMentionQuery}
        onFocus={updateMentionQuery}
        onKeyDown={handleKeyDown}
        onKeyUp={updateMentionQuery}
        onMouseUp={updateMentionQuery}
        onPaste={handlePaste}
        className={[
          "w-full text-[13px] leading-relaxed text-ink outline-none empty:before:pointer-events-none empty:before:text-ink-soft empty:before:content-[attr(data-placeholder)]",
          compact
            ? "min-h-[2.25rem] rounded-md bg-transparent px-0 py-0.5 focus:bg-white"
            : "min-h-44 rounded-xl border border-line bg-white px-3 py-2 focus:border-accent",
          className || "",
        ].join(" ")}
      />

      {mentionMenu}

      {showCount ? (
        <div className="text-[12px] text-ink-soft">
          已关联 {mentions.length}/{MAX_REFERENCE_IMAGE_COUNT}
          {mentions.length >= MAX_REFERENCE_IMAGE_COUNT ? <span className="ml-2 text-amber">最多 9 张不同图片，已关联素材可重复引用</span> : null}
        </div>
      ) : null}
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
  const viewportWidth = Math.max(160, window.innerWidth - MENTION_MENU_MARGIN * 2);
  const width = Math.min(
    MENTION_MENU_MAX_WIDTH,
    Math.max(280, wrapperRect.width - MENTION_MENU_MARGIN * 2),
    viewportWidth,
  );
  const left = Math.max(
    MENTION_MENU_MARGIN,
    Math.min(wrapperRect.left + MENTION_MENU_MARGIN, window.innerWidth - width - MENTION_MENU_MARGIN),
  );
  const availableBelow = Math.max(0, window.innerHeight - rect.bottom - MENTION_MENU_MARGIN);
  const availableAbove = Math.max(0, rect.top - MENTION_MENU_MARGIN);
  const desiredHeight = MENTION_MENU_HEADER_HEIGHT + MENTION_MENU_MAX_LIST_HEIGHT;
  const placement: ActiveMentionQuery["placement"] =
    availableBelow >= desiredHeight || availableBelow >= availableAbove ? "below" : "above";
  const availableHeight = placement === "below" ? availableBelow : availableAbove;
  return {
    text: queryText,
    range: queryRange,
    left,
    top: placement === "below" ? rect.bottom + MENTION_MENU_MARGIN : rect.top - MENTION_MENU_MARGIN,
    width,
    placement,
    listMaxHeight: Math.max(80, Math.min(MENTION_MENU_MAX_LIST_HEIGHT, availableHeight - MENTION_MENU_HEADER_HEIGHT)),
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
  chip.setAttribute("data-mention-generation-reference-url", mention.generation_reference_url || "");
  chip.setAttribute("data-mention-third-asset-id", mention.third_asset_id || "");
  chip.setAttribute("data-mention-replacement-source", mention.replacement_source || "");
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
      generation_reference_url: node.dataset.mentionGenerationReferenceUrl || undefined,
      third_asset_id: node.dataset.mentionThirdAssetId || undefined,
      replacement_source: node.dataset.mentionReplacementSource || undefined,
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
    mentions: mentions.map((mention) => [
      mention.asset_id,
      mention.type,
      mention.name,
      mention.image_url || "",
      mention.generation_reference_url || "",
      mention.third_asset_id || "",
      mention.replacement_source || "",
    ]),
  });
}
