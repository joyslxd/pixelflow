import { useEffect, useRef, useState } from "react";
import type { ClipboardEvent, DragEvent } from "react";
import { ArrowUp, FileText, ImageIcon, Loader2, Plus, X } from "lucide-react";
import { api, type UploadedAttachment } from "@/lib/api";
import type { AgentUserMessagePayload } from "@/lib/authStorage";

interface ComposerProps {
  onSubmit?: (payload: AgentUserMessagePayload) => void;
  referencedMaterials?: Array<Record<string, unknown>>;
  onRemoveReferencedMaterial?: (key: string) => void;
  prefillRequest?: { id: string; content: string } | null;
  disabled?: boolean;
}

/** 极简对话输入框。参数不在这里填 —— 检测到视频生成意图后再弹参数面板。 */
export function Composer({ onSubmit, referencedMaterials = [], onRemoveReferencedMaterial, prefillRequest, disabled = false }: ComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<UploadedAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dragDepthRef = useRef(0);
  const hasReferencedMaterials = referencedMaterials.length > 0;
  const canSend = !disabled && !uploading && (text.trim().length > 0 || (!hasReferencedMaterials && attachments.length > 0));

  useEffect(() => {
    if (referencedMaterials.length > 0) textareaRef.current?.focus();
  }, [referencedMaterials.length]);

  useEffect(() => {
    if (!prefillRequest) return;
    setText(prefillRequest.content);
    textareaRef.current?.focus();
  }, [prefillRequest]);

  const submit = () => {
    if (!canSend) return;
    onSubmit?.({ content: text.trim(), materials: [...referencedMaterials, ...attachments] });
    setText("");
    setAttachments([]);
    setUploadError("");
  };

  const selectFiles = () => inputRef.current?.click();

  const uploadFiles = async (files: File[]) => {
    const selected = files;
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

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const imageFiles = imageFilesFromDataTransfer(event.clipboardData);
    if (imageFiles.length === 0) return;
    event.preventDefault();
    if (disabled || uploading) {
      setUploadError("当前任务处理中，暂时无法添加图片素材");
      return;
    }
    void uploadFiles(imageFiles);
  };

  const handleDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!hasFileTransfer(event.dataTransfer) || disabled || uploading) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setIsDraggingFiles(true);
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!hasFileTransfer(event.dataTransfer) || disabled || uploading) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!hasFileTransfer(event.dataTransfer)) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDraggingFiles(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!hasFileTransfer(event.dataTransfer)) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setIsDraggingFiles(false);
    if (disabled || uploading) {
      setUploadError("当前任务处理中，暂时无法添加图片素材");
      return;
    }
    const imageFiles = imageFilesFromDataTransfer(event.dataTransfer);
    if (imageFiles.length === 0) {
      setUploadError("请拖入图片素材");
      return;
    }
    void uploadFiles(imageFiles);
  };

  const removeAttachment = (url: string) => {
    setAttachments((items) => items.filter((item) => item.url !== url));
  };

  const removeReferencedMaterial = (item: Record<string, unknown>) => {
    onRemoveReferencedMaterial?.(materialKey(item));
  };

  const materialsForDisplay = [...referencedMaterials, ...attachments];

  return (
    <div
      onPaste={handlePaste}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`relative rounded-[24px] border bg-white px-3 pb-3 pt-4 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_10px_30px_rgba(16,24,40,0.08)] transition-colors focus-within:border-ink-soft/35 ${
        isDraggingFiles ? "border-accent ring-2 ring-accent/20" : "border-line"
      }`}
    >
      {isDraggingFiles ? (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center gap-2 rounded-[24px] border-2 border-dashed border-accent bg-white/90 text-sm font-medium text-accent">
          <ImageIcon size={20} />
          松开即可添加图片素材
        </div>
      ) : null}
      {materialsForDisplay.length > 0 && (
        <div className="mb-3 flex max-h-24 flex-wrap gap-2 overflow-y-auto px-1">
          {materialsForDisplay.map((item) => {
            const url = materialUrl(item);
            const isReferenced = item.source === "scene_global_asset";
            const isImage = materialType(item) === "image" || Boolean(item.source_image_url);
            return (
              <span key={materialKey(item)} className="flex max-w-[220px] items-center gap-2 rounded-xl border border-line bg-canvas/70 px-2 py-1.5 text-[12px] text-ink">
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
        rows={2}
        placeholder="说说你想做什么，例如：帮保温杯做一条冬季通勤的种草短视频"
        className="min-h-[64px] max-h-[320px] w-full resize-none overflow-y-auto bg-transparent px-2 text-[15px] leading-7 text-ink outline-none placeholder:text-ink-soft/60"
      />
      <div className="mt-2 flex min-h-10 items-center gap-2">
        <input ref={inputRef} type="file" multiple className="hidden" onChange={(event) => void uploadFiles(Array.from(event.target.files || []))} />
        <button
          type="button"
          onClick={selectFiles}
          disabled={disabled || uploading}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-soft transition-colors hover:bg-canvas hover:text-ink disabled:opacity-40"
          aria-label="添加素材"
        >
          {uploading ? <Loader2 size={18} className="animate-spin" /> : <Plus size={18} />}
        </button>
        <span className="text-[12px] text-ink-soft">
          {materialsForDisplay.length > 0 ? `已添加 ${materialsForDisplay.length} 个素材` : "支持粘贴或拖入图片"}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-white transition-all hover:scale-[1.03] disabled:scale-100 disabled:bg-ink-soft/35 disabled:text-white"
          aria-label="发送"
        >
          <ArrowUp size={18} strokeWidth={2.4} />
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

function hasFileTransfer(dataTransfer: DataTransfer): boolean {
  return Array.from(dataTransfer.types).includes("Files");
}

function imageFilesFromDataTransfer(dataTransfer: DataTransfer): File[] {
  const itemFiles = Array.from(dataTransfer.items)
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);
  return (itemFiles.length > 0 ? itemFiles : Array.from(dataTransfer.files)).filter(isImageFile);
}

function isImageFile(file: File): boolean {
  return file.type.toLowerCase().startsWith("image/") || /\.(avif|bmp|gif|heic|heif|jpe?g|png|svg|webp)$/i.test(file.name);
}
