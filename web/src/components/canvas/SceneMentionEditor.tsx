import { ImageIcon } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import {
  collectMentionImageUrls,
  normalizeShotMentions,
  upsertShotMention,
  type SceneMention,
  type SceneMentionCandidate,
} from "@/lib/sceneMentions";
import { MAX_REFERENCE_IMAGE_COUNT } from "@/lib/scenePackages";
import { cn } from "@/lib/utils";

interface SceneMentionEditorProps {
  text: string;
  shotDescription: Record<string, unknown>;
  candidates: SceneMentionCandidate[];
  onChange: (next: { text: string; mentions: SceneMention[] }) => void;
}

export function SceneMentionEditor({ text, shotDescription, candidates, onChange }: SceneMentionEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [cursor, setCursor] = useState(text.length);
  const mentions = useMemo(() => normalizeShotMentions({ ...shotDescription, text }, [], undefined), [shotDescription, text]);
  const atQuery = mentionQuery(text, cursor);
  const filteredCandidates = useMemo(() => {
    const keyword = atQuery.replace(/^@/, "").trim().toLowerCase();
    return candidates.filter((candidate) => {
      if (mentions.some((mention) => mention.asset_id === candidate.asset_id)) return false;
      if (!keyword) return true;
      return [candidate.name, candidate.asset_id, candidate.type].some((value) => value.toLowerCase().includes(keyword));
    });
  }, [atQuery, candidates, mentions]);
  const canAddMore = mentions.length < MAX_REFERENCE_IMAGE_COUNT;

  const emitText = (nextText: string, nextCursor: number) => {
    const syncedMentions = syncMentionsWithText(nextText, mentions);
    setCursor(nextCursor);
    onChange({ text: nextText, mentions: syncedMentions });
  };

  const selectCandidate = (candidate: SceneMentionCandidate) => {
    if (!canAddMore) return;
    const area = textareaRef.current;
    const currentCursor = area?.selectionStart ?? cursor;
    const range = mentionRange(text, currentCursor);
    const token = `@${candidate.name}`;
    const nextText = `${text.slice(0, range.start)}${token}${text.slice(range.end)}`;
    const nextCursor = range.start + token.length;
    const shot = upsertShotMention({ text: nextText, mentions }, candidate);
    onChange(shot);
    setCursor(nextCursor);
    window.requestAnimationFrame(() => {
      area?.focus();
      area?.setSelectionRange(nextCursor, nextCursor);
    });
  };

  return (
    <div className="relative grid gap-2">
      <textarea
        ref={textareaRef}
        value={text}
        onChange={(event) => emitText(event.currentTarget.value, event.currentTarget.selectionStart)}
        onClick={(event) => setCursor(event.currentTarget.selectionStart)}
        onKeyUp={(event) => setCursor(event.currentTarget.selectionStart)}
        placeholder="0-5秒: 地点:@办公室走廊 中,角色:@赵总监 完成动作。5-12秒: 地点:@办公室走廊 中,角色:@林晓 进入近景。"
        className="min-h-44 w-full resize-none rounded-xl border border-line bg-white px-3 py-2 text-[13px] leading-relaxed text-ink outline-none focus:border-accent"
      />

      {atQuery && filteredCandidates.length > 0 ? (
        <div className="absolute left-3 top-[calc(100%-10px)] z-20 w-[min(440px,calc(100%-24px))] overflow-hidden rounded-xl border border-line bg-white shadow-xl">
          <div className="border-b border-line px-3 py-2 text-[12px] text-ink-soft">选择素材进行关联</div>
          <div className="max-h-56 overflow-y-auto p-1.5">
            {filteredCandidates.slice(0, 8).map((candidate) => (
              <button
                key={candidate.asset_id}
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectCandidate(candidate)}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-canvas"
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
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-soft">
        <span className={cn("shrink-0", mentions.length >= MAX_REFERENCE_IMAGE_COUNT && "text-amber")}>
          已关联 {mentions.length}/{MAX_REFERENCE_IMAGE_COUNT}
        </span>
        {mentions.map((mention) => (
          <span key={mention.asset_id} className="group relative inline-flex items-center rounded-full border border-line bg-white px-2 py-1 text-accent">
            @{mention.name}
            {mention.image_url ? (
              <span className="pointer-events-none absolute bottom-full left-0 z-30 mb-2 hidden w-44 overflow-hidden rounded-xl border border-line bg-white p-2 shadow-xl group-hover:block">
                <img src={mention.image_url} alt="" className="h-36 w-full rounded-lg object-cover" />
                <span className="mt-1 block truncate text-[11px] text-ink-soft">{mention.image_url}</span>
              </span>
            ) : null}
          </span>
        ))}
        {collectMentionImageUrls(mentions).length >= MAX_REFERENCE_IMAGE_COUNT ? (
          <span className="text-amber">最多只能选择 9 张图片</span>
        ) : null}
      </div>
    </div>
  );
}

function mentionQuery(text: string, cursor: number): string {
  const range = mentionRange(text, cursor);
  if (range.start < 0) return "";
  return text.slice(range.start, range.end);
}

function mentionRange(text: string, cursor: number): { start: number; end: number } {
  const left = text.slice(0, cursor);
  const start = left.lastIndexOf("@");
  if (start < 0) return { start: -1, end: cursor };
  const between = left.slice(start);
  if (/\s/.test(between.slice(1))) return { start: -1, end: cursor };
  const right = text.slice(cursor);
  const rightMatch = right.match(/^[^\s，。,.!?！？；;、]*/);
  return { start, end: cursor + (rightMatch?.[0].length || 0) };
}

function syncMentionsWithText(text: string, mentions: SceneMention[]): SceneMention[] {
  return mentions.filter((mention) => text.includes(`@${mention.name}`) || text.includes(`@${mention.asset_id}`));
}
