import { useEffect, useRef, useState } from "react";
import { FileText, ImageIcon, Loader2, Plus, SendHorizontal, X } from "lucide-react";
import { api, type UploadedAttachment } from "@/lib/api";
import type { AgentUserMessagePayload } from "@/lib/authStorage";

interface ComposerProps {
  onSubmit?: (payload: AgentUserMessagePayload) => void;
  referencedMaterials?: Array<Record<string, unknown>>;
  onRemoveReferencedMaterial?: (key: string) => void;
  busy?: boolean;
}

/** 极简对话输入框。参数不在这里填 —— 检测到视频生成意图后再弹参数面板。 */
export function Composer({ onSubmit, referencedMaterials = [], onRemoveReferencedMaterial, busy }: ComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<UploadedAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const hasReferencedMaterials = referencedMaterials.length > 0;
  const canSend = !busy && !uploading && (text.trim().length > 0 || (!hasReferencedMaterials && attachments.length > 0));

  useEffect(() => {
    if (referencedMaterials.length > 0) textareaRef.current?.focus();
  }, [referencedMaterials.length]);

  const submit = () => {
    if (!canSend) return;
    onSubmit?.({ content: text.trim(), materials: [...referencedMaterials, ...attachments] });
    setText("");
    setAttachments([]);
    setUploadError("");
  };

  const selectFiles = () => inputRef.current?.click();

  const uploadFiles = async (files: FileList | null) => {
    const selected = Array.from(files || []);
    if (selected.length === 0) return;
    setUploading(true);
    setUploadError("");
    try {
      const uploaded = await Promise.all(selected.map((file) => api.uploadAttachment(file)));
      setAttachments((items) => [...items, ...uploaded]);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const removeAttachment = (url: string) => {
    setAttachments((items) => items.filter((item) => item.url !== url));
  };

  const removeReferencedMaterial = (item: Record<string, unknown>) => {
    onRemoveReferencedMaterial?.(materialKey(item));
  };

  const materialsForDisplay = [...referencedMaterials, ...attachments];

  return (
    <div className="rounded-[18px] border border-line bg-surface p-2 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_8px_24px_rgba(16,24,40,0.05)]">
      {materialsForDisplay.length > 0 && (
        <div className="mb-2 flex max-h-24 flex-wrap gap-2 overflow-y-auto px-1">
          {materialsForDisplay.map((item) => {
            const url = materialUrl(item);
            const isReferenced = item.source === "scene_global_asset";
            const isImage = materialType(item) === "image" || Boolean(item.source_image_url);
            return (
              <span key={materialKey(item)} className="flex max-w-[220px] items-center gap-2 rounded-xl border border-line bg-white px-2 py-1.5 text-[12px] text-ink">
                {isImage && url ? (
                  <img src={url} alt="" className="h-7 w-7 shrink-0 rounded-full object-cover" />
                ) : isImage ? (
                  <ImageIcon size={14} className="shrink-0 text-accent" />
                ) : (
                  <FileText size={14} className="shrink-0 text-ink-soft" />
                )}
                <span className="truncate">{materialName(item)}</span>
                <button
                  type="button"
                  onClick={() => (isReferenced ? removeReferencedMaterial(item) : removeAttachment(url))}
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-ink-soft hover:bg-canvas hover:text-ink"
                  aria-label="移除附件"
                >
                  <X size={13} />
                </button>
              </span>
            );
          })}
        </div>
      )}
      {uploadError && <div className="mb-2 rounded-xl border border-amber/30 bg-amber/10 px-3 py-2 text-[12px] text-ink">{uploadError}</div>}
      <div className="flex items-end gap-2 pl-1">
        <input ref={inputRef} type="file" multiple className="hidden" onChange={(event) => void uploadFiles(event.target.files)} />
        <button
          type="button"
          onClick={selectFiles}
          disabled={busy || uploading}
          className="mb-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-soft hover:bg-canvas hover:text-ink disabled:opacity-40"
          aria-label="添加素材"
        >
          {uploading ? <Loader2 size={18} className="animate-spin" /> : <Plus size={18} />}
        </button>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder="说说你想做什么，例如：帮保温杯做一条冬季通勤的种草短视频"
          className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent py-2 text-[15px] leading-relaxed text-ink outline-none placeholder:text-ink-soft/60"
        />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="mb-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand text-white transition-opacity disabled:opacity-30"
          aria-label="发送"
        >
          <SendHorizontal size={17} />
        </button>
      </div>
    </div>
  );
}

function materialKey(item: Record<string, unknown>): string {
  return String(item.asset_id || item.url || item.path || item.source_image_url || item.filename || JSON.stringify(item));
}

function materialUrl(item: Record<string, unknown>): string {
  return String(item.url || item.source_image_url || item.image_url || item.imageUrl || item.path || "");
}

function materialType(item: Record<string, unknown>): string {
  return String(item.type || item.media_type || item.mediaType || "").toLowerCase();
}

function materialName(item: Record<string, unknown>): string {
  return String(item.name || item.filename || item.asset_name || "引用素材");
}
