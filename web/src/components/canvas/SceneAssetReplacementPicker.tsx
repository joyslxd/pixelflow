import { ImageIcon, Loader2, RefreshCw, Upload, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ContentAssetItem, type ContentAssetPageResponse, type DigitalHumanAssetType, type UploadedAttachment } from "@/lib/api";
import type { GlobalSceneAssetGroup, SceneGlobalAssetReplacement } from "@/lib/scenePackages";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

type PickerMode = "digital_human" | "image_asset";

interface ReplacementOption {
  key: string;
  name: string;
  description: string;
  imageUrl: string;
  replacement: SceneGlobalAssetReplacement;
}

interface SceneAssetReplacementPickerProps {
  open: boolean;
  assetGroup: GlobalSceneAssetGroup;
  assetName: string;
  onCancel: () => void;
  onConfirm: (replacement: SceneGlobalAssetReplacement) => void;
}

const DIGITAL_HUMAN_TYPES: Array<{ label: string; value: DigitalHumanAssetType }> = [
  { label: "虚拟数字人", value: "xnszr" },
  { label: "真人数字人", value: "zrszr" },
  { label: "IP素材", value: "ipsc" },
];

export function SceneAssetReplacementPicker({
  open,
  assetGroup,
  assetName,
  onCancel,
  onConfirm,
}: SceneAssetReplacementPickerProps) {
  const canUseDigitalHuman = assetGroup === "characters";
  const [mode, setMode] = useState<PickerMode>(canUseDigitalHuman ? "digital_human" : "image_asset");
  const [digitalHumanType, setDigitalHumanType] = useState<DigitalHumanAssetType>("xnszr");
  const [items, setItems] = useState<ReplacementOption[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadedImage, setUploadedImage] = useState<UploadedAttachment | null>(null);
  const loadingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  const selected = useMemo(() => items.find((item) => item.key === selectedKey), [items, selectedKey]);

  const loadPage = useCallback(
    async (pageNumber: number, replace = false) => {
      if (!open || loadingRef.current) return;
      loadingRef.current = true;
      setLoading(true);
      setError("");
      try {
        const response =
          mode === "digital_human"
            ? await api.listCharacterAssets({
                assetType: digitalHumanType,
                assetSource: "all",
                pageCurrent: pageNumber,
                pageSize: PAGE_SIZE,
              })
            : await api.listContentImageAssets({ pageCurrent: pageNumber, pageSize: PAGE_SIZE });
        const nextItems = normalizeAssetPage(response, mode, digitalHumanType);
        const totalPages = pageTotalPages(response);
        setItems((current) => (replace ? nextItems : [...current, ...nextItems]));
        setPage(pageNumber);
        setHasMore(totalPages ? pageNumber < totalPages : nextItems.length >= PAGE_SIZE);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
        loadingRef.current = false;
      }
    },
    [digitalHumanType, mode, open],
  );

  useEffect(() => {
    if (!open) return;
    const nextMode = canUseDigitalHuman ? mode : "image_asset";
    if (nextMode !== mode) {
      setMode(nextMode);
      return;
    }
    setItems([]);
    setSelectedKey("");
    setPage(1);
    setHasMore(true);
    void loadPage(1, true);
  }, [canUseDigitalHuman, digitalHumanType, loadPage, mode, open]);

  useEffect(() => {
    if (open) return;
    setUploading(false);
    setUploadError("");
    setUploadedImage(null);
    if (uploadInputRef.current) uploadInputRef.current.value = "";
  }, [open]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el || loading || !hasMore) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 36) {
      void loadPage(page + 1);
    }
  };

  const confirm = () => {
    if (!selected) return;
    onConfirm(selected.replacement);
  };

  const uploadLocalImage = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file || uploading) return;
    if (!file.type.toLowerCase().startsWith("image/")) {
      setUploadError("请选择 JPG、PNG、WebP 等图片文件。");
      if (uploadInputRef.current) uploadInputRef.current.value = "";
      return;
    }
    setUploading(true);
    setUploadError("");
    try {
      const uploaded = await api.uploadAttachment(file);
      if (uploaded.type !== "image") {
        throw new Error("上传结果不是有效图片，请重新选择。");
      }
      setUploadedImage(uploaded);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
    }
  };

  const confirmUploadedImage = () => {
    if (!uploadedImage) return;
    onConfirm({
      source: "local_upload",
      displayImageUrl: uploadedImage.url,
      generationReferenceUrl: uploadedImage.url,
      assetType: "image",
      assetName: uploadedImage.filename || uploadedImage.name,
      raw: { ...uploadedImage },
    });
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/55 px-4" role="dialog" aria-modal="true">
      <div className="relative flex max-h-[86vh] w-full max-w-[900px] flex-col rounded-[8px] bg-white shadow-[0_24px_80px_rgba(15,23,42,0.28)]">
        <div className="flex shrink-0 items-center justify-between border-b border-line px-6 py-4">
          <div>
            <div className="text-[18px] font-semibold text-ink">替换素材</div>
            <div className="mt-1 text-[12px] text-ink-soft">当前素材：{assetName}</div>
          </div>
          <div className="flex items-center gap-2">
            <input
              ref={uploadInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(event) => void uploadLocalImage(event.currentTarget.files)}
            />
            <button
              type="button"
              onClick={() => uploadInputRef.current?.click()}
              disabled={uploading}
              className="flex h-9 items-center gap-1.5 rounded-[8px] border border-accent px-3 text-[13px] font-medium text-accent hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
            >
              {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
              {uploading ? "上传中..." : "本地上传"}
            </button>
            <button type="button" onClick={onCancel} disabled={uploading} className="flex h-9 w-9 items-center justify-center rounded-full hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50" aria-label="关闭">
              <X size={20} />
            </button>
          </div>
        </div>

        {uploadError ? (
          <div className="mx-6 mt-3 rounded-[8px] border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">{uploadError}</div>
        ) : null}

        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line px-6 py-3">
          <div className="flex rounded-[8px] bg-canvas p-1">
            {canUseDigitalHuman ? (
              <button
                type="button"
                onClick={() => setMode("digital_human")}
                className={cn("flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px]", mode === "digital_human" ? "bg-white text-ink shadow-sm" : "text-ink-soft")}
              >
                <UserRound size={15} />
                数字人素材
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setMode("image_asset")}
              className={cn("flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px]", mode === "image_asset" ? "bg-white text-ink shadow-sm" : "text-ink-soft")}
            >
              <ImageIcon size={15} />
              图片素材
            </button>
          </div>
          {mode === "digital_human" ? (
            <div className="flex flex-wrap gap-2">
              {DIGITAL_HUMAN_TYPES.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  onClick={() => setDigitalHumanType(item.value)}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-[12px]",
                    digitalHumanType === item.value ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-soft hover:bg-canvas",
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <div ref={scrollRef} onScroll={handleScroll} className="min-h-[360px] flex-1 overflow-y-auto px-6 py-5">
          {error ? (
            <div className="mb-4 flex items-center justify-between rounded-[8px] border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">
              <span className="line-clamp-2">素材加载失败：{error}</span>
              <button type="button" onClick={() => loadPage(1, true)} className="ml-3 flex shrink-0 items-center gap-1 rounded-md bg-white px-2 py-1 text-red-700">
                <RefreshCw size={13} />
                重试
              </button>
            </div>
          ) : null}
          {items.length === 0 && !loading ? (
            <div className="flex h-64 items-center justify-center rounded-[8px] border border-dashed border-line text-[13px] text-ink-soft">暂无可用素材</div>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {items.map((item) => {
                const selectedItem = selectedKey === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setSelectedKey(item.key)}
                    className={cn(
                      "overflow-hidden rounded-[8px] border bg-white text-left transition-colors",
                      selectedItem ? "border-accent shadow-[0_0_0_2px_rgba(17,94,89,0.12)]" : "border-line hover:border-accent",
                    )}
                  >
                    <div className="aspect-[3/4] bg-canvas">
                      <img src={item.imageUrl} alt={item.name} className="h-full w-full object-cover" />
                    </div>
                    <div className="grid gap-1 px-2 py-2">
                      <div className="truncate text-[13px] font-medium text-ink">{item.name}</div>
                      <div className="truncate text-[11px] text-ink-soft">{item.description}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-5 text-[13px] text-ink-soft">
              <Loader2 size={15} className="animate-spin" />
              素材加载中...
            </div>
          ) : null}
          {!loading && items.length > 0 && !hasMore ? (
            <div className="py-4 text-center text-[12px] text-ink-soft">已加载全部素材</div>
          ) : null}
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t border-line px-6 py-4">
          <button type="button" onClick={onCancel} disabled={uploading} className="rounded-[8px] border border-line px-4 py-2 text-[13px] font-medium text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50">
            取消
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={!selected || uploading}
            className="rounded-[8px] bg-brand px-4 py-2 text-[13px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            确认替换
          </button>
        </div>
      </div>
      {uploadedImage ? (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 px-4" role="alertdialog" aria-modal="true" aria-labelledby="local-upload-confirm-title">
          <div className="w-full max-w-[520px] rounded-[8px] bg-white p-6 shadow-[0_24px_80px_rgba(15,23,42,0.32)]">
            <div id="local-upload-confirm-title" className="text-[18px] font-semibold text-ink">图片上传成功，是否替换当前素材？</div>
            <div className="mt-2 text-[13px] text-ink-soft">确认后将同步更新当前素材及分镜中的对应引用。</div>
            <div className="mt-5 flex max-h-[440px] min-h-[240px] items-center justify-center overflow-hidden rounded-[8px] bg-canvas">
              <img src={uploadedImage.url} alt={uploadedImage.filename || uploadedImage.name} className="max-h-[440px] w-full object-contain" />
            </div>
            <div className="mt-3 truncate text-[13px] text-ink">{uploadedImage.filename || uploadedImage.name}</div>
            <div className="mt-6 flex justify-end gap-2">
              <button type="button" onClick={() => setUploadedImage(null)} className="rounded-[8px] border border-line px-4 py-2 text-[13px] font-medium text-ink hover:bg-canvas">
                取消
              </button>
              <button type="button" onClick={confirmUploadedImage} className="rounded-[8px] bg-brand px-4 py-2 text-[13px] font-medium text-white hover:opacity-90">
                确认替换
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function normalizeAssetPage(
  response: ContentAssetPageResponse | ContentAssetItem[],
  mode: PickerMode,
  digitalHumanType: DigitalHumanAssetType,
): ReplacementOption[] {
  const rawItems = Array.isArray(response) ? response : response.history || response.records || response.list || [];
  return rawItems
    .map((item, index) => (mode === "digital_human" ? digitalHumanOption(item, index, digitalHumanType) : imageAssetOption(item, index)))
    .filter((item): item is ReplacementOption => Boolean(item));
}

function pageTotalPages(response: ContentAssetPageResponse | ContentAssetItem[]): number {
  if (Array.isArray(response)) return 0;
  const value = response.totalPages ?? response.pages;
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function digitalHumanOption(item: ContentAssetItem, index: number, assetType: DigitalHumanAssetType): ReplacementOption | null {
  const thirdAssetId = stringValue(item.thirdAssetId) || stringValue(item.third_asset_id);
  const imageUrl = firstImageFromRefrenceUrl(stringValue(item.refrenceUrl));
  if (!thirdAssetId || !imageUrl) return null;
  const name = stringValue(item.name) || `数字人${index + 1}`;
  return {
    key: `digital-${assetType}-${thirdAssetId}-${index}`,
    name,
    description: digitalHumanTypeLabel(assetType),
    imageUrl,
    replacement: {
      source: "digital_human",
      displayImageUrl: imageUrl,
      generationReferenceUrl: `asset://${thirdAssetId.replace(/^asset:\/\//, "")}`,
      thirdAssetId: thirdAssetId.replace(/^asset:\/\//, ""),
      assetType,
      contentAssetId: String(item.id ?? ""),
      assetName: name,
      raw: item,
    },
  };
}

function imageAssetOption(item: ContentAssetItem, index: number): ReplacementOption | null {
  const imageUrl = firstImageFromAsset(item);
  if (!imageUrl) return null;
  const name = stringValue(item.name) || filenameFromUrl(imageUrl) || `图片素材${index + 1}`;
  return {
    key: `image-${item.id ?? imageUrl}-${index}`,
    name,
    description: "图片素材",
    imageUrl,
    replacement: {
      source: "image_asset",
      displayImageUrl: imageUrl,
      generationReferenceUrl: imageUrl,
      assetType: "image",
      contentAssetId: String(item.id ?? ""),
      assetName: name,
      raw: item,
    },
  };
}

function firstImageFromAsset(item: ContentAssetItem): string {
  const result = item.result && typeof item.result === "object" ? item.result : {};
  const imageUrls = arrayOfStrings(result.image_url) || arrayOfStrings(result.imageUrl);
  if (imageUrls?.[0]) return imageUrls[0];
  const direct =
    stringValue(item.image_url) ||
    stringValue(item.imageUrl) ||
    stringValue(item.url) ||
    stringValue(item.download_url) ||
    stringValue(item.downloadUrl);
  if (isImageUrl(direct)) return direct;
  return firstImageFromRefrenceUrl(stringValue(item.refrenceUrl));
}

function firstImageFromRefrenceUrl(value: string): string {
  return value
    .split(",")
    .map((item) => item.trim())
    .find(isImageUrl) || "";
}

function isImageUrl(value: string): boolean {
  return Boolean(value) && /^https?:\/\//i.test(value) && /\.(png|jpe?g|webp|gif|bmp|svg)(?:[?#].*)?$/i.test(value.split("?")[0] || value);
}

function arrayOfStrings(value: unknown): string[] | null {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function filenameFromUrl(value: string): string {
  try {
    const pathname = new URL(value).pathname;
    return decodeURIComponent(pathname.split("/").filter(Boolean).pop() || "");
  } catch {
    return "";
  }
}

function digitalHumanTypeLabel(value: DigitalHumanAssetType): string {
  return DIGITAL_HUMAN_TYPES.find((item) => item.value === value)?.label || "数字人素材";
}
