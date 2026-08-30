/** F1 输入框：不接手填 workspace_id；旧对话只读，运行中新输入显示已排队。 */

import { ChangeEvent, FormEvent, useRef, useState } from "react";

import type { TurnMaterialV1 } from "@/api/contracts";
import { uploadContentAppFile } from "@/api/contentAppAssets";
import { createClientUuid } from "@/lib/uuid";
import type { InputStatus } from "@/features/agent-runtime/state";

type ComposerProps = {
  canSend: boolean;
  sending: boolean;
  inputStatus: InputStatus;
  disabledReason?: string;
  onSubmit: (content: string, materials: TurnMaterialV1[]) => Promise<void>;
};

export function Composer({
  canSend,
  sending,
  inputStatus,
  disabledReason,
  onSubmit,
}: ComposerProps) {
  const [input, setInput] = useState("");
  const [materials, setMaterials] = useState<TurnMaterialV1[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const submittingRef = useRef(false);

  const displayName = (file: File, referenceLabel: string): string => {
    /** 浏览器下载图片常把查询串当文件名；界面与资产库应保留用户可理解的名称。 */

    const name = file.name.trim();
    return !name || /^u=/iu.test(name) || /[?&=]/u.test(name) ? `${referenceLabel}图片` : name;
  };

  const submitContent = async (content: string) => {
    /** 发送只依赖受控输入值，避免原生 submit 监听在重渲染时丢失。 */

    if (!canSend || sending || uploading || submittingRef.current || !content) return;
    submittingRef.current = true;
    const submittedMaterials = materials;
    setInput("");
    setMaterials([]);
    try {
      await onSubmit(content, submittedMaterials);
    } catch {
      setInput((current) => current || content);
      setMaterials((current) => current.length > 0 ? current : submittedMaterials);
    } finally {
      submittingRef.current = false;
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitContent(input.trim());
  };

  const uploadFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0 || uploading) return;
    if (materials.length + files.length > 9) {
      setUploadError("单次对话最多保留 9 个文件引用。");
      return;
    }
    setUploading(true);
    setUploadError("");
    try {
      const next: TurnMaterialV1[] = [];
      for (const file of files) {
        const kind = file.type.startsWith("image/") ? "image"
          : file.type.startsWith("video/") ? "video"
            : file.type.startsWith("audio/") ? "audio" : "file";
        const sameKindCount = materials.filter((item) => item.kind === kind).length
          + next.filter((item) => item.kind === kind).length + 1;
        const kindLabel = kind === "image" ? "参考图" : kind === "video" ? "参考视频" : kind === "audio" ? "参考音频" : "附件";
        const referenceLabel = `${kindLabel}${sameKindCount}`;
        const uploaded = await uploadContentAppFile(file, displayName(file, referenceLabel));
        next.push({
          material_id: createClientUuid(),
          kind,
          name: uploaded.name,
          reference_label: referenceLabel,
          content_type: uploaded.contentType,
          url: uploaded.url,
          ...(uploaded.assetId ? { asset_id: uploaded.assetId } : {}),
        });
      }
      setMaterials((current) => [...current, ...next]);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "文件上传失败，请重试。");
    } finally {
      setUploading(false);
    }
  };

  if (!canSend) {
    return (
      <p className="rounded border border-line px-3 py-2 text-sm text-ink-soft">
        {disabledReason || "旧对话仅供查看，请基于产物创建新对话。"}
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      {inputStatus === "queued" ? (
        <p className="mb-2 text-xs text-ink-soft" aria-live="polite">新输入已排队</p>
      ) : null}
      {materials.some((material) => material.kind === "image") ? (
        <div className="mb-3 flex flex-wrap gap-3" aria-label="已上传参考图">
          {materials.filter((material) => material.kind === "image").map((material) => (
            <figure key={material.material_id} className="relative h-20 w-20 overflow-hidden rounded-xl border border-line bg-canvas">
              <img
                src={material.url}
                alt={material.reference_label}
                className="h-full w-full object-cover"
              />
              <figcaption className="absolute inset-x-0 bottom-0 truncate bg-black/60 px-1 py-0.5 text-center text-[10px] text-white">
                {material.reference_label}
              </figcaption>
              <button
                type="button"
                aria-label={`移除 ${material.reference_label}`}
                className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-black/60 text-xs text-white"
                onClick={() => setMaterials((current) => current.filter((item) => item.material_id !== material.material_id))}
              >
                ×
              </button>
            </figure>
          ))}
        </div>
      ) : null}
      <div className="flex gap-2">
        <input
          name="content"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入给 Agent"
          className="min-w-0 flex-1 rounded border border-line px-3 py-2"
        />
        <input ref={fileInputRef} className="sr-only" type="file" multiple onChange={(event) => void uploadFiles(event)} />
        <button type="button" className="rounded border border-line px-3 text-ink disabled:opacity-50" disabled={uploading || sending} onClick={() => fileInputRef.current?.click()}>
          {uploading ? "上传中…" : "上传文件"}
        </button>
        <button
          type="submit"
          disabled={sending}
          className="rounded bg-accent px-4 text-white disabled:opacity-50"
        >
          发送
        </button>
      </div>
      {materials.some((material) => material.kind !== "image") ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {materials.filter((material) => material.kind !== "image").map((material) => (
            <span key={material.material_id} className="inline-flex items-center gap-1 rounded bg-canvas px-2 py-1 text-xs text-ink">
              {material.reference_label}：{material.name}
              <button type="button" aria-label={`移除 ${material.reference_label}`} className="text-ink-soft" onClick={() => setMaterials((current) => current.filter((item) => item.material_id !== material.material_id))}>×</button>
            </span>
          ))}
        </div>
      ) : null}
      {uploadError ? <p className="mt-2 text-xs text-red-600" role="alert">{uploadError}</p> : null}
    </form>
  );
}
