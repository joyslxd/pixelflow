import { Check, ImageIcon, Loader2, RefreshCw, Upload, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ContentAssetItem, type ContentAssetPageResponse, type DigitalHumanAssetType, type UploadedAttachment } from "@/lib/api";
import type { GlobalSceneAssetGroup, SceneGlobalAssetReplacement } from "@/lib/scenePackages";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;
const ASSET_IMAGE_MAX_BYTES = 20 * 1024 * 1024;
const ASSET_REFRESH_ATTEMPTS = 3;
const ASSET_REFRESH_DELAY_MS = 1_000;
const ASSET_IMAGE_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const ASSET_IMAGE_FILE_PATTERN = /\.(jpe?g|png|webp)$/i;

type PickerMode = "digital_human" | "image_asset";
type AssetUploadStage = "idle" | "preparing" | "uploading" | "creating_asset" | "refreshing" | "completed" | "sync_delayed" | "failed";

interface UploadedAssetLocator {
  id: string;
  imageUrl: string;
}

interface ReplacementOption {
  key: string;
  name: string;
  description: string;
  imageUrl: string;
  replacement: SceneGlobalAssetReplacement;
}

interface SceneAssetReplacementPickerProps {
  open: boolean;
  operation?: "add" | "replace";
  assetGroup: GlobalSceneAssetGroup;
  assetName?: string;
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
  operation = "replace",
  assetGroup,
  assetName,
  onCancel,
  onConfirm,
}: SceneAssetReplacementPickerProps) {
  const adding = operation === "add";
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
  const [assetUploadStage, setAssetUploadStage] = useState<AssetUploadStage>("idle");
  const [assetUploadProgress, setAssetUploadProgress] = useState(0);
  const [assetUploadError, setAssetUploadError] = useState("");
  const [assetUploadDragging, setAssetUploadDragging] = useState(false);
  const [justUploadedKey, setJustUploadedKey] = useState("");
  const [uploadedAssetLocator, setUploadedAssetLocator] = useState<UploadedAssetLocator | null>(null);
  const [contentProjectId, setContentProjectId] = useState<string | number | null>(null);
  const loadingRef = useRef(false);
  const listRequestTokenRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const assetUploadInputRef = useRef<HTMLInputElement | null>(null);
  const justUploadedCardRef = useRef<HTMLButtonElement | null>(null);

  const selected = useMemo(() => items.find((item) => item.key === selectedKey), [items, selectedKey]);
  const assetUploadBusy = assetUploadStage === "preparing" || assetUploadStage === "uploading" || assetUploadStage === "creating_asset" || assetUploadStage === "refreshing";
  const interactionBusy = uploading || assetUploadBusy;

  const loadPage = useCallback(
    async (pageNumber: number, replace = false): Promise<ReplacementOption[] | null> => {
      if (!open || (loadingRef.current && !replace)) return null;
      const requestToken = replace ? ++listRequestTokenRef.current : listRequestTokenRef.current;
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
        if (requestToken !== listRequestTokenRef.current) return null;
        const totalPages = pageTotalPages(response);
        setItems((current) => (replace ? nextItems : [...current, ...nextItems]));
        setPage(pageNumber);
        setHasMore(totalPages ? pageNumber < totalPages : nextItems.length >= PAGE_SIZE);
        return nextItems;
      } catch (err) {
        if (requestToken === listRequestTokenRef.current) {
          setError(err instanceof Error ? err.message : String(err));
        }
        return null;
      } finally {
        if (requestToken === listRequestTokenRef.current) {
          setLoading(false);
          loadingRef.current = false;
        }
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
    setAssetUploadStage("idle");
    setAssetUploadProgress(0);
    setAssetUploadError("");
    setAssetUploadDragging(false);
    setJustUploadedKey("");
    setUploadedAssetLocator(null);
    listRequestTokenRef.current += 1;
    loadingRef.current = false;
    if (uploadInputRef.current) uploadInputRef.current.value = "";
    if (assetUploadInputRef.current) assetUploadInputRef.current.value = "";
  }, [open]);

  useEffect(() => {
    if (!justUploadedKey) return;
    const frame = window.requestAnimationFrame(() => {
      justUploadedCardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [justUploadedKey]);

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

  const refreshUploadedAsset = async (locator: UploadedAssetLocator): Promise<boolean> => {
    setAssetUploadStage("refreshing");
    setAssetUploadError("");
    let receivedPage = false;
    for (let attempt = 0; attempt < ASSET_REFRESH_ATTEMPTS; attempt += 1) {
      if (attempt > 0) await delay(ASSET_REFRESH_DELAY_MS);
      const refreshedItems = await loadPage(1, true);
      if (!refreshedItems) continue;
      receivedPage = true;
      const uploadedOption = refreshedItems.find((item) => uploadedAssetOptionMatches(item, locator));
      if (!uploadedOption) continue;
      setJustUploadedKey(uploadedOption.key);
      setAssetUploadStage("completed");
      setAssetUploadError("");
      return true;
    }
    if (!receivedPage) {
      setError("");
      setAssetUploadStage("failed");
      setAssetUploadError("刷新失败，请重试");
      return false;
    }
    setAssetUploadStage("sync_delayed");
    setAssetUploadError("图片已上传，资产库同步中，请稍后刷新");
    return false;
  };

  const resolveContentProjectId = async (): Promise<string | number> => {
    if (contentProjectId !== null && contentProjectId !== "") return contentProjectId;
    const projects = await api.listContentProjects();
    const projectId = projects[0]?.id;
    if (projectId === undefined || projectId === null || projectId === "") {
      throw new Error("未找到可用项目，暂时无法上传到资产库");
    }
    setContentProjectId(projectId);
    return projectId;
  };

  const uploadImageAsset = async (file: File | undefined, fileCount = 1) => {
    if (!file || interactionBusy) return;
    if (fileCount !== 1) {
      setAssetUploadStage("failed");
      setAssetUploadError("每次只能上传 1 张图片");
      return;
    }
    const validationError = validateAssetImageFile(file);
    if (validationError) {
      setAssetUploadStage("failed");
      setAssetUploadError(validationError);
      if (assetUploadInputRef.current) assetUploadInputRef.current.value = "";
      return;
    }

    let stage: "project" | "upload" | "create" | "refresh" = "project";
    setAssetUploadProgress(0);
    setAssetUploadError("");
    setJustUploadedKey("");
    setUploadedAssetLocator(null);
    setSelectedKey("");
    setAssetUploadStage("preparing");
    try {
      const projectId = await resolveContentProjectId();
      stage = "upload";
      setAssetUploadStage("uploading");
      const uploaded = await api.uploadAttachment(file, {
        onProgress: (percent) => setAssetUploadProgress(percent),
      });
      if (uploaded.type !== "image") {
        throw new Error("上传结果不是有效图片，请重新选择");
      }

      stage = "create";
      setAssetUploadStage("creating_asset");
      const createdAsset = await api.createContentImageAsset({
        projectId,
        name: filenameFromUrl(uploaded.url) || uploaded.filename || uploaded.name || file.name,
        refrenceUrl: uploaded.url,
      });
      const locator: UploadedAssetLocator = {
        id: createdAsset.id === undefined || createdAsset.id === null ? "" : String(createdAsset.id),
        imageUrl: uploaded.url,
      };
      setUploadedAssetLocator(locator);

      stage = "refresh";
      await refreshUploadedAsset(locator);
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setAssetUploadStage("failed");
      if (stage === "project") {
        setAssetUploadError(detail || "获取项目信息失败，请重试");
      } else if (stage === "upload") {
        setAssetUploadError(detail || "图片上传失败，请重试");
      } else if (stage === "create") {
        setAssetUploadError(detail || "图片保存到资产库失败，请重试");
      } else {
        setAssetUploadError(detail || "刷新失败，请重试");
      }
    } finally {
      setAssetUploadDragging(false);
      if (assetUploadInputRef.current) assetUploadInputRef.current.value = "";
    }
  };

  const retryUploadedAssetRefresh = () => {
    if (!uploadedAssetLocator || assetUploadBusy) return;
    void refreshUploadedAsset(uploadedAssetLocator);
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
            <div className="text-[18px] font-semibold text-ink">{adding ? "添加素材" : "替换素材"}</div>
            {!adding ? <div className="mt-1 text-[12px] text-ink-soft">当前素材：{assetName}</div> : null}
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
              disabled={interactionBusy}
              className="flex h-9 items-center gap-1.5 rounded-[8px] border border-accent px-3 text-[13px] font-medium text-accent hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
            >
              {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
              {uploading ? "上传中..." : "本地上传"}
            </button>
            <button type="button" onClick={onCancel} disabled={interactionBusy} className="flex h-9 w-9 items-center justify-center rounded-full hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50" aria-label="关闭">
              <X size={20} />
            </button>
          </div>
        </div>

        {uploadError ? (
          <div className="mx-6 mt-3 rounded-[8px] border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">{uploadError}</div>
        ) : null}
        {mode === "image_asset" && assetUploadError ? (
          <div className={cn(
            "mx-6 mt-3 flex items-center justify-between gap-3 rounded-[8px] border px-3 py-2 text-[13px]",
            assetUploadStage === "sync_delayed" ? "border-amber-200 bg-amber-50 text-amber-700" : "border-red-200 bg-red-50 text-red-700",
          )}>
            <span>{assetUploadError}</span>
            {uploadedAssetLocator && (assetUploadStage === "sync_delayed" || assetUploadStage === "failed") ? (
              <button type="button" onClick={retryUploadedAssetRefresh} className="flex shrink-0 items-center gap-1 rounded-md bg-white px-2 py-1">
                <RefreshCw size={13} />
                刷新
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line px-6 py-3">
          <div className="flex rounded-[8px] bg-canvas p-1">
            {canUseDigitalHuman ? (
              <button
                type="button"
                onClick={() => setMode("digital_human")}
                disabled={interactionBusy}
                className={cn("flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px]", mode === "digital_human" ? "bg-white text-ink shadow-sm" : "text-ink-soft")}
              >
                <UserRound size={15} />
                数字人素材
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setMode("image_asset")}
              disabled={interactionBusy}
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
          {items.length === 0 && !loading && mode !== "image_asset" ? (
            <div className="flex h-64 items-center justify-center rounded-[8px] border border-dashed border-line text-[13px] text-ink-soft">暂无可用素材</div>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {mode === "image_asset" ? (
                <>
                  <input
                    ref={assetUploadInputRef}
                    type="file"
                    accept=".jpg,.jpeg,.png,.webp"
                    className="hidden"
                    onChange={(event) => void uploadImageAsset(event.currentTarget.files?.[0], event.currentTarget.files?.length || 0)}
                  />
                  <button
                    type="button"
                    disabled={interactionBusy}
                    onClick={() => assetUploadInputRef.current?.click()}
                    onDragEnter={(event) => {
                      event.preventDefault();
                      if (!interactionBusy) setAssetUploadDragging(true);
                    }}
                    onDragOver={(event) => {
                      event.preventDefault();
                      if (!interactionBusy) setAssetUploadDragging(true);
                    }}
                    onDragLeave={(event) => {
                      event.preventDefault();
                      setAssetUploadDragging(false);
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      setAssetUploadDragging(false);
                      void uploadImageAsset(event.dataTransfer.files?.[0], event.dataTransfer.files?.length || 0);
                    }}
                    className={cn(
                      "flex min-h-[250px] flex-col items-center justify-center rounded-[8px] border border-dashed px-4 text-center transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                      assetUploadDragging ? "border-accent bg-accent-soft" : "border-line bg-canvas hover:border-accent hover:bg-accent-soft/40",
                    )}
                  >
                    {assetUploadBusy ? <Loader2 size={26} className="animate-spin text-accent" /> : <Upload size={26} className="text-accent" />}
                    <div className="mt-3 text-[13px] font-semibold text-ink">
                      {assetUploadStage === "uploading"
                        ? `上传中 ${assetUploadProgress}%`
                        : assetUploadStage === "preparing"
                          ? "准备上传..."
                        : assetUploadStage === "creating_asset"
                          ? "保存到资产库..."
                          : assetUploadStage === "refreshing"
                            ? "刷新资产库..."
                            : "上传到资产库"}
                    </div>
                    <div className="mt-1 text-[11px] leading-5 text-ink-soft">点击或拖拽图片到此处</div>
                    <div className="mt-2 text-[10px] leading-4 text-ink-soft">JPG / PNG / WEBP，单张不超过 20MB</div>
                  </button>
                </>
              ) : null}
              {items.map((item) => {
                const selectedItem = selectedKey === item.key;
                const justUploaded = justUploadedKey === item.key;
                return (
                  <button
                    key={item.key}
                    ref={justUploaded ? justUploadedCardRef : undefined}
                    type="button"
                    onClick={() => setSelectedKey(item.key)}
                    disabled={interactionBusy}
                    className={cn(
                      "relative overflow-hidden rounded-[8px] border bg-white text-left transition-colors disabled:cursor-not-allowed disabled:opacity-70",
                      selectedItem ? "border-accent shadow-[0_0_0_2px_rgba(17,94,89,0.12)]" : "border-line hover:border-accent",
                    )}
                  >
                    <div className="relative aspect-[3/4] bg-canvas">
                      <img src={item.imageUrl} alt={item.name} className="h-full w-full object-cover" />
                      {justUploaded ? (
                        <span className="absolute left-2 top-2 rounded-full bg-accent px-2 py-1 text-[10px] font-medium text-white">刚刚上传</span>
                      ) : null}
                      {selectedItem ? (
                        <span className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-accent text-white"><Check size={14} /></span>
                      ) : null}
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
          <button type="button" onClick={onCancel} disabled={interactionBusy} className="rounded-[8px] border border-line px-4 py-2 text-[13px] font-medium text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50">
            取消
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={!selected || interactionBusy}
            className="rounded-[8px] bg-brand px-4 py-2 text-[13px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {adding ? "确认添加" : "确认替换"}
          </button>
        </div>
      </div>
      {uploadedImage ? (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 px-4" role="alertdialog" aria-modal="true" aria-labelledby="local-upload-confirm-title">
          <div className="w-full max-w-[520px] rounded-[8px] bg-white p-6 shadow-[0_24px_80px_rgba(15,23,42,0.32)]">
            <div id="local-upload-confirm-title" className="text-[18px] font-semibold text-ink">
              {adding ? "图片上传成功，是否添加此素材？" : "图片上传成功，是否替换当前素材？"}
            </div>
            <div className="mt-2 text-[13px] text-ink-soft">
              {adding ? "确认后将添加到当前场景包，之后可在镜头描述中通过 @ 引用。" : "确认后将同步更新当前素材及分镜中的对应引用。"}
            </div>
            <div className="mt-5 flex max-h-[440px] min-h-[240px] items-center justify-center overflow-hidden rounded-[8px] bg-canvas">
              <img src={uploadedImage.url} alt={uploadedImage.filename || uploadedImage.name} className="max-h-[440px] w-full object-contain" />
            </div>
            <div className="mt-3 truncate text-[13px] text-ink">{uploadedImage.filename || uploadedImage.name}</div>
            <div className="mt-6 flex justify-end gap-2">
              <button type="button" onClick={() => setUploadedImage(null)} className="rounded-[8px] border border-line px-4 py-2 text-[13px] font-medium text-ink hover:bg-canvas">
                取消
              </button>
              <button type="button" onClick={confirmUploadedImage} className="rounded-[8px] bg-brand px-4 py-2 text-[13px] font-medium text-white hover:opacity-90">
                {adding ? "确认添加" : "确认替换"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function validateAssetImageFile(file: File): string {
  const mimeType = file.type.toLowerCase();
  if ((mimeType && !ASSET_IMAGE_MIME_TYPES.has(mimeType)) || !ASSET_IMAGE_FILE_PATTERN.test(file.name)) {
    return "暂不支持该图片格式";
  }
  if (file.size > ASSET_IMAGE_MAX_BYTES) {
    return "图片大小不能超过 20MB";
  }
  return "";
}

function uploadedAssetOptionMatches(item: ReplacementOption, locator: UploadedAssetLocator): boolean {
  if (locator.id && item.replacement.contentAssetId === locator.id) return true;
  return Boolean(locator.imageUrl) && item.imageUrl === locator.imageUrl;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
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
