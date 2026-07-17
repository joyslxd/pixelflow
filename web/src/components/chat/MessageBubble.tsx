import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent } from "react";
import { Check, ChevronDown, Download, FileArchive, FileText, FileVideo, LoaderCircle, Pencil, Presentation, RefreshCw, SlidersHorizontal, Sparkles } from "lucide-react";
import { VideoResultCard } from "@/components/canvas/VideoResultCard";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/chat";
import { canAcceptImageResult } from "@/lib/imageReview";
import { sceneAssetFailureDetails } from "@/lib/sceneAssetFailures";
import type { CreativeDirectionResponse, ImageEditModelSelection, ImageModelParamConfig, PptPageImage } from "@/lib/api";
import type { VideoResult } from "@/lib/types";
import { draftButtonState, isJianyingDraftResultRetryable, isJianyingDraftSucceededResultValid, type JianyingDraftCapability, type JianyingDraftJobResponse } from "@/lib/jianyingDraft";

interface MessageBubbleProps {
  msg: ChatMessage;
  isLatestVideoScenePackage?: boolean;
  actionsDisabled?: boolean;
  showProgressLoading?: boolean;
  onOpenArtifact?: (msg: ChatMessage) => void;
  onSelectDirection?: (msg: ChatMessage, direction: CreativeDirectionResponse) => void;
  onRegenerateDirections?: (msg: ChatMessage) => void;
  onApprovePlan?: (msg: ChatMessage) => void;
  onEditPlan?: (msg: ChatMessage) => void;
  onRevisePlan?: (msg: ChatMessage) => void;
  onRollbackPlan?: (msg: ChatMessage, version: number) => void;
  onGenerateImage?: (msg: ChatMessage) => void;
  onConfirmImageEditOptions?: (msg: ChatMessage, selection: ImageEditModelSelection) => void;
  onAcceptImageResult?: (msg: ChatMessage) => void;
  onReviseImageResult?: (msg: ChatMessage) => void;
  onGenerateVideoFromScenePackages?: (msg: ChatMessage) => void;
  onAcceptVideoResult?: (msg: ChatMessage) => void;
  onReviseVideoResult?: (msg: ChatMessage) => void;
  onOpenVideoResult?: (msg: ChatMessage, video: VideoResult, results: VideoResult[]) => void;
  onRegenerateVideoWithRevision?: (msg: ChatMessage, useQualityReview: boolean) => void;
  onRetryImageResult?: (msg: ChatMessage) => void;
  onRetrySceneAssets?: (msg: ChatMessage) => void;
  onRetryVideoMerge?: (msg: ChatMessage) => void;
  onRetryVideoAnalysis?: (msg: ChatMessage) => void;
  onApprovePptOutline?: (msg: ChatMessage) => void;
  onRevisePptOutline?: (msg: ChatMessage) => void;
  onRegeneratePptImage?: (msg: ChatMessage, pageIndex: number) => void;
  onGeneratePptFile?: (msg: ChatMessage) => void;
  onAcceptPptFile?: (msg: ChatMessage) => void;
  onRegeneratePptFile?: (msg: ChatMessage) => void;
  onGenerateJianyingDraft?: (msg: ChatMessage) => void;
  onDownloadJianyingDraft?: (msg: ChatMessage) => void;
  jianyingDraftCapability?: JianyingDraftCapability;
  jianyingDraftResult?: JianyingDraftJobResponse | null;
  jianyingDraftRunning?: boolean;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function uniqueStringArray(value: unknown): string[] {
  return Array.from(new Set(stringArray(value).map((item) => item.trim()).filter(Boolean)));
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function globalAssetRecords(globalAssets: unknown, collection: "characters" | "scenes" | "props"): Array<Record<string, unknown>> {
  return records(globalAssets && typeof globalAssets === "object" ? (globalAssets as Record<string, unknown>)[collection] : undefined);
}

function assetId(record: Record<string, unknown>): string {
  return stringValue(record.asset_id) || stringValue(record.id);
}

function assetTitle(record: Record<string, unknown>, fallback: string): string {
  return stringValue(record.name) || stringValue(record.description) || fallback;
}

function assetImage(record: Record<string, unknown>): string {
  return stringArray(record.images)[0] || stringArray(record.image_urls)[0] || stringArray(record.three_view_images)[0] || stringValue(record.url);
}

function materialUrl(record: Record<string, unknown>): string {
  return stringValue(record.url) || stringValue(record.source_image_url) || stringValue(record.path) || stringValue(record.image_url) || stringValue(record.imageUrl);
}

function materialName(record: Record<string, unknown>, index: number): string {
  return stringValue(record.name) || stringValue(record.asset_name) || stringValue(record.filename) || `附件 ${index + 1}`;
}

function previewAssets(msg: ChatMessage): Array<{ id: string; title: string; image: string }> {
  const videoScenePackages = msg.artifact?.videoScenePackages;
  const globalAssets = videoScenePackages?.global_assets;
  const globalRecords = [
    ...globalAssetRecords(globalAssets, "characters"),
    ...globalAssetRecords(globalAssets, "scenes"),
    ...globalAssetRecords(globalAssets, "props"),
  ];
  const fromGlobal = globalRecords
    .map((asset, index) => ({ id: assetId(asset) || `asset-${index}`, title: assetTitle(asset, `素材 ${index + 1}`), image: assetImage(asset) }))
    .filter((item) => item.image);
  if (fromGlobal.length > 0) return fromGlobal.slice(0, 5);
  const fromScenes = records(videoScenePackages?.scene_packages)
    .flatMap((scene) => stringArray(scene.image_urls).map((image, index) => ({ id: `${stringValue(scene.scene_id) || "scene"}-${index}`, title: stringValue(scene.title) || "场景片段", image })));
  return fromScenes.slice(0, 5);
}

function quotaInsufficient(value: unknown): boolean {
  if (!value) return false;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (record.quota_insufficient === true) return true;
    return Object.values(record).some(quotaInsufficient);
  }
  const text = String(value);
  return ["额度不足", "余额不足", "没有有效的额度", "充值", "quota insufficient", "payment required"].some((keyword) => text.includes(keyword));
}

function secondsFromMilliseconds(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.ceil(value / 1000) : undefined;
}

function mergedVideoResultForMessage(msg: ChatMessage): VideoResult | null {
  const mergedVideo = msg.artifact?.mergedVideo;
  if (!mergedVideo?.merged_video_url) return null;
  return {
    id: mergedVideo.task_id || "merged-video",
    title: "final_video.mp4",
    url: mergedVideo.merged_video_url,
    assetType: "final_video",
    durationSec: secondsFromMilliseconds(msg.artifact?.videoScenePackages?.target_duration_ms),
    status: mergedVideo.ok ? "success" : "failed",
  };
}

function sceneVideoResultsForMessage(msg: ChatMessage): VideoResult[] {
  return (msg.artifact?.generatedSceneVideos?.scene_videos || [])
    .filter((scene) => Boolean(scene.video_url))
    .map((scene, index) => ({
      id: scene.scene_id || `scene-${index + 1}`,
      title: `scene_${String(scene.scene_index || index + 1).padStart(2, "0")}.mp4`,
      url: scene.video_url,
      assetType: "generated_video",
      durationSec: secondsFromMilliseconds(scene.duration_ms),
      status: "success",
    }));
}

function pptPages(msg: ChatMessage): PptPageImage[] {
  return Array.isArray(msg.artifact?.pptImages?.pages) ? msg.artifact.pptImages.pages : [];
}

function pptPagesReady(msg: ChatMessage): boolean {
  const pages = pptPages(msg);
  return pages.length > 0 && pages.every((page) => page.status === "completed" && Boolean(page.image_url));
}

function progressDescription(content: string): string {
  if (/场景视频|分镜视频/.test(content)) return "场景视频生成中";
  if (/可编辑视频资产|可编辑场景包|场景包/.test(content)) return "可编辑视频资产生成中...";
  if (/三视图|场景图|道具图|参考图/.test(content)) return "生成角色、场景与道具参考图";
  if (/合并/.test(content)) return "合并完整视频";
  if (/PPT 大纲|SmartPPT.*大纲/.test(content)) return "生成 PPT 大纲";
  if (/页面 JSON|页面结构/.test(content)) return "生成页面结构";
  if (/PPT 图片|每页 PPT 图片|页面图片/.test(content)) return "生成 PPT 页面图片";
  if (/PPT 附件/.test(content)) return "生成 PPT 附件";
  if (/图片编辑|编辑图片/.test(content)) return "编辑图片";
  if (/生成图片|图片生成/.test(content)) return "生成图片";
  if (/视频分析|媒体链接|QAAgent QC|质检/.test(content)) return "分析视频内容";
  if (/采集 Agent 判断这是(?:图片|视频)生成需求/.test(content)) return "计划文件生成中";
  if (/采集 Agent|理解|表单/.test(content)) return "理解需求并补全参数";
  if (/plan\.md|计划文件|创作方案/.test(content)) return "生成计划文件";
  if (/创意方向/.test(content)) return "生成创意方案";
  if (/继续查询|任务状态/.test(content)) return "查询已有任务状态";
  return "处理中";
}

function imageModelType(config: ImageModelParamConfig): string {
  const record = config as unknown as Record<string, unknown>;
  return stringValue(record.modelType) || stringValue(record.model_type) || stringValue(record.model);
}

function imageModelLabel(model: string): string {
  return model === "gpt-image-2" ? "image-2" : model;
}

function imageModelParamConfig(config?: ImageModelParamConfig): Record<string, unknown> {
  const record = (config || {}) as unknown as Record<string, unknown>;
  const raw = record.paramConfig || record.param_config || {};
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

function imageModelOptions(config?: ImageModelParamConfig): { ratios: string[]; sizes: string[] } {
  const params = imageModelParamConfig(config);
  return {
    ratios: uniqueStringArray(params.aspectRatioList || params.aspect_ratio_list).length > 0
      ? uniqueStringArray(params.aspectRatioList || params.aspect_ratio_list)
      : ["1:1", "9:16", "16:9"],
    sizes: uniqueStringArray(params.sizeList || params.size_list).length > 0
      ? uniqueStringArray(params.sizeList || params.size_list)
      : ["4K"],
  };
}

function requestedImageEditParam(msg: ChatMessage, key: "ratio" | "size"): string {
  const requested = msg.artifact?.imageEditRequestedParams || {};
  if (key === "ratio") {
    return stringValue(requested.ratio) || stringValue(msg.artifact?.formValues?.image_size) || stringValue(msg.artifact?.intakeContext?.image_size);
  }
  return stringValue(requested.size) || stringValue(msg.artifact?.formValues?.image_quality) || stringValue(msg.artifact?.intakeContext?.image_quality);
}

export function MessageBubble({
  msg,
  isLatestVideoScenePackage,
  actionsDisabled,
  showProgressLoading,
  onOpenArtifact,
  onSelectDirection,
  onRegenerateDirections,
  onApprovePlan,
  onEditPlan,
  onRevisePlan,
  onRollbackPlan,
  onGenerateImage,
  onConfirmImageEditOptions,
  onAcceptImageResult,
  onReviseImageResult,
  onGenerateVideoFromScenePackages,
  onAcceptVideoResult,
  onReviseVideoResult,
  onOpenVideoResult,
  onRegenerateVideoWithRevision,
  onRetryImageResult,
  onRetrySceneAssets,
  onRetryVideoMerge,
  onRetryVideoAnalysis,
  onApprovePptOutline,
  onRevisePptOutline,
  onRegeneratePptImage,
  onGeneratePptFile,
  onAcceptPptFile,
  onRegeneratePptFile,
  onGenerateJianyingDraft,
  onDownloadJianyingDraft,
  jianyingDraftCapability,
  jianyingDraftResult: suppliedJianyingDraftResult,
  jianyingDraftRunning = false,
}: MessageBubbleProps) {
  const isUser = msg.role === "user";
  const planPreview = msg.artifact?.plan?.plan_markdown || "";
  const imagePrepareParams = msg.artifact?.imagePrepare?.params ? JSON.stringify(msg.artifact.imagePrepare.params, null, 2) : "";
  const imageEditModelConfigs = msg.artifact?.imageEditModelConfigs || [];
  const imageEditModelNames = imageEditModelConfigs.map(imageModelType).filter(Boolean);
  const confirmedImageEditSelection = msg.artifact?.imageEditConfirmedSelection;
  const requestedImageEditRatio = requestedImageEditParam(msg, "ratio");
  const requestedImageEditSize = requestedImageEditParam(msg, "size");
  const imageEditConfigSignature = JSON.stringify(
    imageEditModelConfigs.map((config) => ({
      model: imageModelType(config),
      options: imageModelOptions(config),
    })),
  );
  const scenePackages = msg.artifact?.videoScenePackages?.scene_packages || [];
  const videoAnalysisStoryboards = records(msg.artifact?.videoAnalysis?.storyboards);
  const messageMaterials = records(msg.materials);
  const sceneAssetQuotaPaused = quotaInsufficient(msg.artifact?.sceneAssetFailures);
  const sceneAssetFailureItems = sceneAssetFailureDetails(msg.artifact?.sceneAssetFailures);
  const imageQuotaPaused = quotaInsufficient(msg.artifact?.imageResult);
  const mergeQuotaPaused = quotaInsufficient(msg.artifact?.mergedVideo);
  const sceneAssetFailed = Boolean(msg.artifact?.sceneAssetFailures?.length);
  const imageGenerationFailed = Boolean(msg.artifact?.imageResult && !canAcceptImageResult(msg.artifact.imageResult));
  const videoAnalysisFailed = Boolean(msg.artifact?.videoAnalysis && !msg.artifact.videoAnalysis.ok);
  const videoGenerationFailed = Boolean(msg.artifact?.generatedSceneVideos && !msg.artifact.generatedSceneVideos.ok && msg.artifact.videoScenePackages);
  const videoMergeFailed = Boolean(msg.artifact?.mergedVideo && !msg.artifact.mergedVideo.ok && msg.artifact.generatedSceneVideos?.scene_videos.length);
  const imageAccepted = Boolean(msg.artifact?.imageAccepted);
  const sceneGlobalAssetEditReview = Boolean(msg.artifact?.sceneGlobalAssetEditReview);
  const videoAccepted = Boolean(msg.artifact?.videoAccepted);
  const mergedVideoResult = mergedVideoResultForMessage(msg);
  const sceneVideoResults = sceneVideoResultsForMessage(msg);
  const videoResults = [mergedVideoResult, ...sceneVideoResults].filter((result): result is VideoResult => Boolean(result));
  const jianyingDraftResult = msg.artifact?.type === "jianying_draft" ? msg.artifact.jianyingDraft || suppliedJianyingDraftResult : suppliedJianyingDraftResult;
  const jianyingDraftScenes = (msg.artifact?.generatedSceneVideos?.scene_videos || []).map((scene) => ({
    scene_id: scene.scene_id,
    scene_index: scene.scene_index,
    task_id: scene.task_id || null,
    video_url: scene.video_url,
  }));
  const jianyingDraftAction = draftButtonState({
    providerAvailable: Boolean(jianyingDraftCapability?.available),
    pendingJob: jianyingDraftRunning ? { status: "running" } : null,
    scenes: jianyingDraftScenes,
    failedSceneIds: msg.artifact?.generatedSceneVideos?.failed_scenes.map((scene) => String(scene.scene_id || "")) || [],
    result: jianyingDraftResult,
  });
  const jianyingDraftUnavailable = !jianyingDraftCapability?.available;
  const jianyingDraftDownloadUrl = jianyingDraftResult?.download_url?.startsWith("https://") ? jianyingDraftResult.download_url : "";
  const jianyingDraftSucceeded = isJianyingDraftSucceededResultValid(jianyingDraftResult) && Boolean(jianyingDraftDownloadUrl);
  const jianyingDraftRetryable = isJianyingDraftResultRetryable(jianyingDraftResult);
  const videoResultActionDisabled = Boolean(actionsDisabled || jianyingDraftRunning);
  const pptImagePages = pptPages(msg);
  const allPptPagesReady = pptPagesReady(msg);
  const hasRunningPptPage = pptImagePages.some((page) => page.status === "running");
  const pptFileDone = Boolean(msg.artifact?.pptDone);
  const progressText = showProgressLoading ? progressDescription(msg.content) : "";
  const [loadingDots, setLoadingDots] = useState(0);
  const [sceneAssetFailureDetailsOpen, setSceneAssetFailureDetailsOpen] = useState(false);
  const [selectedImageEditModel, setSelectedImageEditModel] = useState("");
  const [selectedImageEditRatio, setSelectedImageEditRatio] = useState("");
  const [selectedImageEditSize, setSelectedImageEditSize] = useState("");
  const [imageEditModelMenuOpen, setImageEditModelMenuOpen] = useState(false);
  const [imageEditModelMenuFocusIndex, setImageEditModelMenuFocusIndex] = useState(0);
  const imageEditModelMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hasRunningPptPage) {
      setLoadingDots(3);
      return;
    }
    const timer = window.setInterval(() => {
      setLoadingDots((current) => (current + 1) % 4);
    }, 450);
    return () => window.clearInterval(timer);
  }, [hasRunningPptPage]);

  useEffect(() => {
    if (msg.artifact?.type !== "image_edit_options") return;
    setImageEditModelMenuOpen(false);
    setImageEditModelMenuFocusIndex(0);
    const confirmedModel = confirmedImageEditSelection?.model && imageEditModelNames.includes(confirmedImageEditSelection.model) ? confirmedImageEditSelection.model : "";
    const preferredModel = confirmedModel || (imageEditModelNames.includes("gpt-image-2") ? "gpt-image-2" : imageEditModelNames[0] || "gpt-image-2");
    const preferredConfig = imageEditModelConfigs.find((config) => imageModelType(config) === preferredModel) || imageEditModelConfigs[0];
    const options = imageModelOptions(preferredConfig);
    setSelectedImageEditModel(preferredModel);
    setSelectedImageEditRatio(
      confirmedImageEditSelection?.ratio && options.ratios.includes(confirmedImageEditSelection.ratio)
        ? confirmedImageEditSelection.ratio
        : requestedImageEditRatio && options.ratios.includes(requestedImageEditRatio)
          ? requestedImageEditRatio
          : options.ratios[0] || "1:1",
    );
    setSelectedImageEditSize(
      confirmedImageEditSelection?.size && options.sizes.includes(confirmedImageEditSelection.size)
        ? confirmedImageEditSelection.size
        : requestedImageEditSize && options.sizes.includes(requestedImageEditSize)
          ? requestedImageEditSize
          : options.sizes[0] || "4K",
    );
  }, [msg.id, imageEditConfigSignature, requestedImageEditRatio, requestedImageEditSize, confirmedImageEditSelection?.model, confirmedImageEditSelection?.ratio, confirmedImageEditSelection?.size]);

  useEffect(() => {
    if (!imageEditModelMenuOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      const target = event.target instanceof Node ? event.target : null;
      if (target && imageEditModelMenuRef.current?.contains(target)) return;
      setImageEditModelMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImageEditModelMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [imageEditModelMenuOpen]);

  const currentImageEditModel = selectedImageEditModel || confirmedImageEditSelection?.model || (imageEditModelNames.includes("gpt-image-2") ? "gpt-image-2" : imageEditModelNames[0] || "gpt-image-2");
  const currentImageEditConfig = imageEditModelConfigs.find((config) => imageModelType(config) === currentImageEditModel) || imageEditModelConfigs[0];
  const currentImageEditOptions = imageModelOptions(currentImageEditConfig);
  const imageEditModelChoices = imageEditModelNames.length > 0 ? imageEditModelNames : ["gpt-image-2"];
  const imageEditRatioSupported = !requestedImageEditRatio || currentImageEditOptions.ratios.includes(requestedImageEditRatio);
  const imageEditSizeSupported = !requestedImageEditSize || currentImageEditOptions.sizes.includes(requestedImageEditSize);
  const effectiveImageEditRatio = selectedImageEditRatio || (imageEditRatioSupported ? requestedImageEditRatio : "") || currentImageEditOptions.ratios[0] || "1:1";
  const effectiveImageEditSize = selectedImageEditSize || (imageEditSizeSupported ? requestedImageEditSize : "") || currentImageEditOptions.sizes[0] || "4K";
  const imageEditUnsupportedReason = [
    requestedImageEditRatio && !imageEditRatioSupported ? `当前模型不支持需求尺寸 ${requestedImageEditRatio}，已改用 ${effectiveImageEditRatio}` : "",
    requestedImageEditSize && !imageEditSizeSupported ? `当前模型不支持需求清晰度 ${requestedImageEditSize}，已改用 ${effectiveImageEditSize}` : "",
  ].filter(Boolean).join("，");
  const imageEditSubmitDisabled = !currentImageEditModel || !effectiveImageEditRatio || !effectiveImageEditSize;
  const imageEditModelListboxId = `image-edit-model-listbox-${msg.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const currentImageEditModelIndex = Math.max(0, imageEditModelChoices.indexOf(currentImageEditModel));
  const clampImageEditModelMenuFocusIndex = (index: number) => Math.min(Math.max(index, 0), imageEditModelChoices.length - 1);
  const openImageEditModelMenu = (focusIndex = currentImageEditModelIndex) => {
    setImageEditModelMenuFocusIndex(clampImageEditModelMenuFocusIndex(focusIndex));
    setImageEditModelMenuOpen(true);
  };
  const closeImageEditModelMenu = () => setImageEditModelMenuOpen(false);
  const moveImageEditModelMenuFocus = (direction: number) => {
    setImageEditModelMenuFocusIndex((index) => (index + direction + imageEditModelChoices.length) % imageEditModelChoices.length);
  };
  const selectImageEditModel = (nextModel: string) => {
    const nextConfig = imageEditModelConfigs.find((config) => imageModelType(config) === nextModel);
    const options = imageModelOptions(nextConfig);
    setSelectedImageEditModel(nextModel);
    setSelectedImageEditRatio(requestedImageEditRatio && options.ratios.includes(requestedImageEditRatio) ? requestedImageEditRatio : options.ratios[0] || "1:1");
    setSelectedImageEditSize(requestedImageEditSize && options.sizes.includes(requestedImageEditSize) ? requestedImageEditSize : options.sizes[0] || "4K");
    setImageEditModelMenuFocusIndex(clampImageEditModelMenuFocusIndex(imageEditModelChoices.indexOf(nextModel)));
    closeImageEditModelMenu();
  };
  const handleImageEditModelMenuKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!imageEditModelMenuOpen) {
        openImageEditModelMenu(currentImageEditModelIndex);
      } else {
        moveImageEditModelMenuFocus(1);
      }
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!imageEditModelMenuOpen) {
        openImageEditModelMenu(currentImageEditModelIndex);
      } else {
        moveImageEditModelMenuFocus(-1);
      }
      return;
    }
    if (event.key === "Home" && imageEditModelMenuOpen) {
      event.preventDefault();
      setImageEditModelMenuFocusIndex(0);
      return;
    }
    if (event.key === "End" && imageEditModelMenuOpen) {
      event.preventDefault();
      setImageEditModelMenuFocusIndex(imageEditModelChoices.length - 1);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!imageEditModelMenuOpen) {
        openImageEditModelMenu(currentImageEditModelIndex);
      } else {
        selectImageEditModel(imageEditModelChoices[imageEditModelMenuFocusIndex] || currentImageEditModel);
      }
      return;
    }
    if (event.key === "Escape") {
      closeImageEditModelMenu();
    }
  };

  const blockDisabledAction = (event: MouseEvent<HTMLDivElement>) => {
    if (!actionsDisabled) return;
    const target = event.target instanceof HTMLElement ? event.target : null;
    if (!target?.closest("button")) return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold",
          isUser ? "bg-accent text-white" : "bg-accent-soft text-accent",
        )}
      >
        {isUser ? "A" : <Sparkles size={15} />}
      </div>
      <div className={cn("flex max-w-[78%] flex-col", isUser ? "items-end" : "items-start")}>
        <div
          className={cn(
            "whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed",
            isUser
              ? "bg-accent-soft text-ink"
              : "border border-line bg-surface text-ink",
          )}
        >
          {msg.content}
        </div>
        {messageMaterials.length > 0 && (
          <div className={cn("mt-2 flex max-w-[520px] flex-wrap gap-2", isUser ? "justify-end" : "justify-start")}>
            {messageMaterials.map((material, index) => {
              const url = materialUrl(material);
              const name = materialName(material, index);
              const type = stringValue(material.type).toLowerCase();
              const isImage = type === "image" || material.source === "scene_global_asset";
              return (
                <a
                  key={`${url}-${index}`}
                  href={url || undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="flex max-w-[220px] items-center gap-2 rounded-xl border border-line bg-white px-2.5 py-1.5 text-[12px] text-ink hover:bg-canvas"
                >
                  {isImage && url ? (
                    <img src={url} alt="" className="h-8 w-8 shrink-0 rounded-md object-cover" />
                  ) : (
                    <FileText size={15} className="shrink-0 text-ink-soft" />
                  )}
                  <span className="truncate">{name}</span>
                </a>
              );
            })}
          </div>
        )}
        <div
          className={cn(actionsDisabled && "opacity-60 [&_button]:cursor-not-allowed [&_button]:hover:opacity-100")}
          onClickCapture={blockDisabledAction}
          aria-disabled={actionsDisabled || undefined}
        >
        {msg.artifact?.type === "directions" && msg.artifact.directions ? (
          <div className="mt-2 w-full max-w-[520px] space-y-2 rounded-2xl border border-accent/20 bg-accent-soft/50 p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[13px] font-semibold text-ink">{msg.artifact.title}</div>
                <div className="mt-1 text-[12px] text-ink-soft">{msg.artifact.description}</div>
              </div>
              <button
                type="button"
                title="重新生成创意方向"
                onClick={() => onRegenerateDirections?.(msg)}
                className="flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 py-1.5 text-[12px] font-medium text-ink hover:bg-canvas"
              >
                <RefreshCw size={14} />
                重新生成
              </button>
            </div>
            <div className="space-y-2">
              {msg.artifact.directions.map((direction) => (
                <div key={direction.direction_id} className="rounded-xl border border-line bg-white/80 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-semibold text-ink">{direction.title}</span>
                        {direction.recommended && (
                          <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-medium text-white">
                            推荐
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => onSelectDirection?.(msg, direction)}
                      className="shrink-0 rounded-lg bg-brand px-2.5 py-1.5 text-[12px] font-medium text-white hover:opacity-90"
                    >
                      选择
                    </button>
                  </div>
                  <p className="mt-1 text-[12px] leading-relaxed text-ink-soft">{direction.description}</p>
                  {direction.tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {direction.tags.map((tag) => (
                        <span key={tag} className="rounded-full bg-canvas px-2 py-0.5 text-[11px] text-ink-soft">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : msg.artifact?.type === "plan" && msg.artifact.plan ? (
          <div className="mt-2 w-full max-w-[620px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileText size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">
                  {msg.artifact.title} v{msg.artifact.plan.plan_version || msg.artifact.planVersion || 1}
                </span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
            </div>
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
              {planPreview}
            </pre>
            {msg.artifact.plan.plan_history?.length > 1 && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-canvas px-3 py-2">
                <span className="text-[12px] text-ink-soft">历史版本</span>
                {msg.artifact.plan.plan_history
                  .filter((item) => item.version !== msg.artifact?.plan?.plan_version)
                  .map((item) => (
                    <button
                      key={item.version}
                      type="button"
                      onClick={() => onRollbackPlan?.(msg, item.version)}
                      className="rounded-lg border border-line bg-white px-2.5 py-1 text-[12px] font-medium text-ink hover:border-accent/40 hover:text-accent"
                    >
                      回退到 v{item.version}
                    </button>
                  ))}
              </div>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => onApprovePlan?.(msg)}
                className="flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Check size={15} />
                同意方案
              </button>
              <button
                type="button"
                onClick={() => onEditPlan?.(msg)}
                className="flex items-center justify-center gap-1.5 rounded-xl border border-line px-4 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
              >
                <Pencil size={15} />
                编辑
              </button>
              <button
                type="button"
                onClick={() => onRevisePlan?.(msg)}
                className="flex items-center justify-center gap-1.5 rounded-xl border border-line px-4 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
              >
                <Sparkles size={15} />
                Agent 修改
              </button>
            </div>
          </div>
        ) : msg.artifact?.type === "image_edit_options" && msg.artifact.imageEditModelConfigs ? (
          <div className="mt-2 w-full max-w-[620px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <SlidersHorizontal size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
            </div>
            <div ref={imageEditModelMenuRef} className="relative space-y-1.5 text-[12px] text-ink-soft">
              <span className="font-medium text-ink">模型</span>
              <button
                type="button"
                aria-haspopup="listbox"
                aria-expanded={imageEditModelMenuOpen}
                aria-controls={imageEditModelListboxId}
                aria-activedescendant={imageEditModelMenuOpen ? `${imageEditModelListboxId}-option-${imageEditModelMenuFocusIndex}` : undefined}
                onClick={() => (imageEditModelMenuOpen ? closeImageEditModelMenu() : openImageEditModelMenu(currentImageEditModelIndex))}
                onKeyDown={handleImageEditModelMenuKeyDown}
                className={cn(
                  "flex min-h-11 w-full items-center justify-between gap-3 rounded-xl border bg-white px-3 py-2 text-left outline-none transition-all duration-200",
                  imageEditModelMenuOpen ? "border-accent shadow-[0_10px_28px_rgba(31,111,235,0.12)]" : "border-line hover:border-accent/50 hover:bg-canvas focus:border-accent focus:ring-2 focus:ring-accent/10",
                )}
              >
                <span className="min-w-0">
                  <span className="block truncate text-[13px] font-semibold text-ink">{imageModelLabel(currentImageEditModel)}</span>
                  <span className="mt-0.5 block truncate text-[11px] text-ink-soft">
                    支持 {currentImageEditOptions.ratios.length} 个尺寸 · {currentImageEditOptions.sizes.length} 个清晰度
                  </span>
                </span>
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
                  <ChevronDown size={17} className={cn("transition-transform duration-200", imageEditModelMenuOpen ? "rotate-180" : "rotate-0")} />
                </span>
              </button>
              <div
                id={imageEditModelListboxId}
                role="listbox"
                aria-label="图片编辑模型"
                className={cn(
                  "absolute left-0 right-0 top-[calc(100%+8px)] z-30 origin-top overflow-hidden rounded-xl border border-line bg-white shadow-[0_18px_45px_rgba(15,23,42,0.16)] transition-all duration-200 ease-out",
                  imageEditModelMenuOpen ? "max-h-72 translate-y-0 scale-100 opacity-100" : "pointer-events-none max-h-0 -translate-y-1 scale-95 opacity-0",
                )}
              >
                <div className="max-h-72 overflow-y-auto p-1.5">
                  {imageEditModelChoices.map((model, index) => {
                    const active = currentImageEditModel === model;
                    const highlighted = imageEditModelMenuFocusIndex === index;
                    return (
                      <button
                        key={model}
                        id={`${imageEditModelListboxId}-option-${index}`}
                        type="button"
                        role="option"
                        aria-selected={active}
                        onMouseEnter={() => setImageEditModelMenuFocusIndex(index)}
                        onClick={() => selectImageEditModel(model)}
                        className={cn(
                          "group flex min-h-10 w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-[13px] font-semibold outline-none transition-all duration-150",
                          active
                            ? "bg-accent-soft text-accent shadow-[inset_0_0_0_1px_rgba(31,111,235,0.16)]"
                            : highlighted
                              ? "translate-x-0.5 bg-canvas text-accent"
                              : "text-ink hover:translate-x-0.5 hover:bg-canvas hover:text-accent",
                        )}
                      >
                        <span className="min-w-0 truncate">{imageModelLabel(model)}</span>
                        <Check size={15} className={cn("shrink-0 transition-all duration-150", active ? "scale-100 opacity-100" : "scale-75 opacity-0")} />
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <div className="flex items-center justify-between text-[12px]">
                  <span className="font-medium text-ink">尺寸</span>
                  {requestedImageEditRatio ? <span className="text-ink-soft">需求指定 {requestedImageEditRatio}</span> : <span className="text-ink-soft">自动选择可用尺寸</span>}
                </div>
                <div className="flex flex-wrap gap-2">
                  {currentImageEditOptions.ratios.map((ratio) => {
                    const active = effectiveImageEditRatio === ratio;
                    return (
                      <button
                        key={ratio}
                        type="button"
                        onClick={() => setSelectedImageEditRatio(ratio)}
                        className={cn(
                          "rounded-full border px-3 py-1.5 text-[12px] font-medium",
                          active ? "border-accent bg-accent-soft text-accent" : "border-line bg-white text-ink-soft hover:bg-canvas",
                        )}
                      >
                        {ratio}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between text-[12px]">
                  <span className="font-medium text-ink">清晰度</span>
                  {requestedImageEditSize ? <span className="text-ink-soft">需求指定 {requestedImageEditSize}</span> : <span className="text-ink-soft">自动选择可用清晰度</span>}
                </div>
                <div className="flex flex-wrap gap-2">
                  {currentImageEditOptions.sizes.map((size) => {
                    const active = effectiveImageEditSize === size;
                    return (
                      <button
                        key={size}
                        type="button"
                        onClick={() => setSelectedImageEditSize(size)}
                        className={cn(
                          "rounded-full border px-3 py-1.5 text-[12px] font-medium",
                          active ? "border-accent bg-accent-soft text-accent" : "border-line bg-white text-ink-soft hover:bg-canvas",
                        )}
                      >
                        {size}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
            {imageEditUnsupportedReason ? (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] leading-relaxed text-ink">
                {imageEditUnsupportedReason}。你也可以重新选择当前模型支持的参数后继续提交。
              </div>
            ) : (
              <div className="rounded-xl bg-canvas px-3 py-2 text-[12px] leading-relaxed text-ink-soft">
                将使用 {imageModelLabel(currentImageEditModel)}，尺寸 {effectiveImageEditRatio}，清晰度 {effectiveImageEditSize}。
              </div>
            )}
            <button
              type="button"
              disabled={imageEditSubmitDisabled}
              onClick={() => onConfirmImageEditOptions?.(msg, {
                model: currentImageEditModel,
                ratio: effectiveImageEditRatio,
                size: effectiveImageEditSize,
              })}
              className={cn(
                "flex w-full items-center justify-center gap-1.5 rounded-xl py-2.5 text-[13px] font-medium",
                imageEditSubmitDisabled ? "cursor-not-allowed bg-canvas text-ink-soft" : "bg-brand text-white hover:opacity-90",
              )}
            >
              <Sparkles size={15} />
              确认并编辑图片
            </button>
          </div>
        ) : msg.artifact?.type === "image_prepare" && msg.artifact.imagePrepare ? (
          <div className="mt-2 w-full max-w-[620px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileText size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.imagePrepare.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.imagePrepare.ok ? "可执行" : "需处理"}
              </span>
            </div>
            <div className="grid gap-2 text-[12px] text-ink-soft">
              <div>
                <span className="font-medium text-ink">接口：</span>
                {msg.artifact.imagePrepare.endpoint}
              </div>
              <div>
                <span className="font-medium text-ink">方式：</span>
                {msg.artifact.imagePrepare.method}
              </div>
              {msg.artifact.imagePrepare.message && (
                <div>
                  <span className="font-medium text-ink">提示：</span>
                  {msg.artifact.imagePrepare.message}
                </div>
              )}
            </div>
            <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
              {msg.artifact.imagePrepare.prompt}
            </pre>
            <pre className="max-h-[180px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
              {imagePrepareParams}
            </pre>
            {msg.artifact.imagePrepare.ok && (
              <button
                type="button"
                onClick={() => onGenerateImage?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                开始生成图片
              </button>
            )}
          </div>
        ) : msg.artifact?.type === "video_scene_packages" && msg.artifact.videoScenePackages ? (
          <div className="mt-2 w-full max-w-[560px] overflow-hidden rounded-2xl border border-line bg-surface">
            <div className="grid grid-cols-5 border-b border-line bg-canvas/60">
              {previewAssets(msg).length > 0 ? (
                previewAssets(msg).map((asset) => (
                  <div key={asset.id} className="border-r border-line last:border-r-0">
                    <img src={asset.image} alt={asset.title} className="aspect-[4/3] w-full object-cover" />
                  </div>
                ))
              ) : (
                Array.from({ length: 5 }).map((_, index) => (
                  <div key={index} className="flex aspect-[4/3] items-center justify-center border-r border-line text-[11px] text-ink-soft last:border-r-0">
                    待生成
                  </div>
                ))
              )}
            </div>
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1 py-3 pr-2">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-[14px] font-semibold text-ink">{msg.artifact.title || "创意 Storyboard"}</span>
                  <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] text-accent">故事板</span>
                </span>
                <span className="mt-1 block text-[12px] leading-relaxed text-ink-soft">
                  {scenePackages.length} 个分镜片段，点击查看分镜后可编辑故事线、镜头描述、旁白和 @参考图。
                </span>
              </span>
            </div>
            {msg.artifact.sceneAssetFailures?.length ? (
              <div className="mx-3 mb-3 overflow-hidden rounded-xl border border-amber/30 bg-amber/10 text-[12px] text-ink">
                <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <span>
                    {sceneAssetQuotaPaused ? "参考图生成因额度不足暂停，充值后可继续。" : `${msg.artifact.sceneAssetFailures.length} 个参考图生成失败，可进入分镜检查。`}
                  </span>
                  <button
                    type="button"
                    aria-expanded={sceneAssetFailureDetailsOpen}
                    onClick={() => setSceneAssetFailureDetailsOpen((open) => !open)}
                    className="flex items-center gap-1 font-medium text-amber hover:text-ink"
                  >
                    {sceneAssetFailureDetailsOpen ? "收起失败原因" : "查看失败原因"}
                    <ChevronDown size={14} className={cn("transition-transform", sceneAssetFailureDetailsOpen && "rotate-180")} />
                  </button>
                </div>
                {sceneAssetFailureDetailsOpen ? (
                  <div className="divide-y divide-amber/20 border-t border-amber/20 bg-white/50">
                    {sceneAssetFailureItems.map((failure, index) => (
                      <div key={failure.id} className="space-y-1.5 px-3 py-2.5">
                        <div className="flex flex-wrap items-center gap-1.5 font-medium text-ink">
                          <span>{index + 1}. {failure.title}</span>
                          <span className="rounded bg-amber/10 px-1.5 py-0.5 text-[11px] text-amber">{failure.typeLabel}</span>
                          <span className="text-[11px] font-normal text-ink-soft">{failure.sceneLabel}</span>
                        </div>
                        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-soft">
                          {failure.model ? <span>模型：{failure.model}</span> : null}
                          {failure.ratio ? <span>比例：{failure.ratio}</span> : null}
                          {failure.size ? <span>清晰度：{failure.size}</span> : null}
                          {failure.endpoint ? <span className="break-all">接口：{failure.endpoint}</span> : null}
                        </div>
                        <div className="break-words text-[12px] leading-relaxed text-amber">失败原因：{failure.error}</div>
                        {failure.attempts && failure.attempts.length > 1 ? (
                          <div className="space-y-1 text-[11px] leading-relaxed text-ink-soft">
                            {failure.attempts.map((attempt, attemptIndex) => (
                              <div key={`${failure.id}-attempt-${attemptIndex}`}>
                                尝试 {attemptIndex + 1}：{attempt.endpoint || "未知接口"} · {attempt.error || "未返回原因"}
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
            {isLatestVideoScenePackage ? (
              <div className="grid gap-2 border-t border-line p-3 sm:grid-cols-2">
                {msg.artifact.videoScenePackages ? (
                  <button
                    type="button"
                    onClick={() => onOpenArtifact?.(msg)}
                    className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                  >
                    <FileText size={15} />
                    查看分镜
                  </button>
                ) : null}
                {sceneAssetFailed ? (
                  <button
                    type="button"
                    onClick={() => onRetrySceneAssets?.(msg)}
                    className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                  >
                    <Sparkles size={15} />
                    {sceneAssetQuotaPaused ? "继续生成参考图" : "重新生成参考图"}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => onGenerateVideoFromScenePackages?.(msg)}
                    className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                  >
                    <Sparkles size={15} />
                    确认并生成视频
                  </button>
                )}
              </div>
            ) : null}
          </div>
        ) : msg.artifact?.type === "ppt_outline" && msg.artifact.pptSummary ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <Presentation size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.pptSummary.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.pptSummary.ok ? "待确认" : "失败"}
              </span>
            </div>
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
              {String(msg.artifact.pptSummary.summary || msg.artifact.pptSummary.message || "")}
            </pre>
            {!msg.artifact.pptSummary.ok && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {String(msg.artifact.pptSummary.error || msg.artifact.pptSummary.message || "PPT 大纲生成失败")}
              </div>
            )}
            {msg.artifact.pptSummary.ok && (
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => onApprovePptOutline?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Check size={15} />
                  同意大纲
                </button>
                <button
                  type="button"
                  onClick={() => onRevisePptOutline?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                >
                  <Pencil size={15} />
                  修改大纲
                </button>
              </div>
            )}
          </div>
        ) : msg.artifact?.type === "ppt_images" && msg.artifact.pptImages ? (
          <div className="mt-2 w-full max-w-[760px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <Presentation size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", allPptPagesReady ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {allPptPagesReady ? "已完成" : "生成中"}
              </span>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {pptImagePages.map((page) => (
                <div key={page.page_index} className="group relative overflow-hidden rounded-xl border border-line bg-canvas">
                  {page.image_url ? (
                    <a href={page.image_url} target="_blank" rel="noreferrer">
                      <img src={page.image_url} alt={page.title || `第 ${page.page_index} 页`} className="aspect-[16/9] w-full object-cover" />
                    </a>
                  ) : (
                    <div className="flex aspect-[16/9] items-center justify-center text-[12px] text-ink-soft">
                      {page.status === "failed" ? "生成失败" : `图片生成中${".".repeat(loadingDots)}`}
                    </div>
                  )}
                  {page.status !== "running" && (
                    <button
                      type="button"
                      onClick={() => onRegeneratePptImage?.(msg, page.page_index)}
                      className="absolute right-2 top-2 hidden h-8 w-8 items-center justify-center rounded-full bg-white/90 text-ink shadow-sm hover:text-accent group-hover:flex"
                      aria-label="重新生成本页"
                    >
                      <RefreshCw size={15} />
                    </button>
                  )}
                  <div className="flex items-center justify-between gap-2 px-2 py-1.5 text-[11px] text-ink-soft">
                    <span className="truncate">{page.title || `第 ${page.page_index} 页`}</span>
                    <span>第 {page.page_index} 页</span>
                  </div>
                  {page.error ? <div className="border-t border-line px-2 py-1.5 text-[11px] text-amber">{page.error}</div> : null}
                </div>
              ))}
            </div>
            {allPptPagesReady && (
              <button
                type="button"
                onClick={() => onGeneratePptFile?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                开始生成PPT附件
              </button>
            )}
          </div>
        ) : msg.artifact?.type === "ppt_file" && msg.artifact.pptFile ? (
          <div className="mt-2 w-full max-w-[560px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <Presentation size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.pptFile.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.pptFile.ok ? "已生成" : "失败"}
              </span>
            </div>
            {msg.artifact.pptFile.ppt_url ? (
              <a
                href={msg.artifact.pptFile.ppt_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-3 rounded-xl border border-line bg-canvas px-3 py-3 text-[13px] text-ink hover:bg-accent-soft"
              >
                <Download size={17} className="text-accent" />
                <span className="min-w-0 flex-1 truncate">{msg.artifact.pptFile.filename || msg.artifact.pptFile.ppt_url}</span>
                {msg.artifact.pptFile.slide_count ? <span className="text-ink-soft">{msg.artifact.pptFile.slide_count} 页</span> : null}
              </a>
            ) : (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {String(msg.artifact.pptFile.error || msg.artifact.pptFile.message || "PPT 附件生成失败")}
              </div>
            )}
            {pptFileDone ? (
              <div className="flex items-center gap-2 rounded-xl border border-emerald/20 bg-emerald/10 px-3 py-2 text-[12px] text-emerald">
                <Check size={15} />
                PPT 流程已结束
              </div>
            ) : msg.artifact.pptFile.ok ? (
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => onAcceptPptFile?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Check size={15} />
                  满意，结束
                </button>
                <button
                  type="button"
                  onClick={() => onRegeneratePptFile?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                >
                  <RefreshCw size={15} />
                  重新生成PPT附件
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => onRegeneratePptFile?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                重新生成PPT附件
              </button>
            )}
          </div>
        ) : msg.artifact?.type === "image_result" && msg.artifact.imageResult ? (
          <div className="mt-2 w-full max-w-[620px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <Sparkles size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.imageResult.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.imageResult.ok ? "已生成" : "失败"}
              </span>
            </div>
            {msg.artifact.imageResult.error && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.imageResult.error}
              </div>
            )}
            {sceneGlobalAssetEditReview && (
              <div className="rounded-xl border border-accent/20 bg-accent-soft/50 p-2 text-[12px] leading-relaxed text-ink">
                这是场景包素材候选图，点击确认后才会替换回场景包。
              </div>
            )}
            {msg.artifact.imageResult.images.length > 0 && (
              <div className="grid gap-3 sm:grid-cols-2">
                {msg.artifact.imageResult.images.map((image, index) => (
                  <a
                    key={image.asset_id || image.url || index}
                    href={image.download_url || image.url}
                    target="_blank"
                    rel="noreferrer"
                    className="overflow-hidden rounded-xl border border-line bg-canvas"
                  >
                    {image.url ? (
                      <img src={image.url} alt={`生成图片 ${index + 1}`} className="mx-auto block max-h-[420px] max-w-full object-contain" />
                    ) : (
                      <div className="flex aspect-square items-center justify-center text-[12px] text-ink-soft">无图片 URL</div>
                    )}
                    <div className="truncate px-2 py-1.5 text-[11px] text-ink-soft">{image.url || image.asset_id || `图片 ${index + 1}`}</div>
                  </a>
                ))}
              </div>
            )}
            {imageGenerationFailed && (
              <button
                type="button"
                onClick={() => onRetryImageResult?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                {imageQuotaPaused ? "充值后继续生成" : "重新生成图片"}
              </button>
            )}
            {sceneGlobalAssetEditReview && canAcceptImageResult(msg.artifact.imageResult) && imageAccepted ? (
              <div className="flex items-center gap-2 rounded-xl border border-emerald/20 bg-emerald/10 px-3 py-2 text-[12px] text-emerald">
                <Check size={15} />
                素材已替换到场景包
              </div>
            ) : sceneGlobalAssetEditReview && canAcceptImageResult(msg.artifact.imageResult) ? (
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => onAcceptImageResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Check size={15} />
                  确认并替换素材
                </button>
                <button
                  type="button"
                  onClick={() => onReviseImageResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                >
                  <Pencil size={15} />
                  重新编辑
                </button>
              </div>
            ) : canAcceptImageResult(msg.artifact.imageResult) && imageAccepted ? (
              <div className="flex items-center gap-2 rounded-xl border border-emerald/20 bg-emerald/10 px-3 py-2 text-[12px] text-emerald">
                <Check size={15} />
                图片流程已结束
              </div>
            ) : canAcceptImageResult(msg.artifact.imageResult) ? (
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => onAcceptImageResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Check size={15} />
                  满意，结束
                </button>
                <button
                  type="button"
                  onClick={() => onReviseImageResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                >
                  <Pencil size={15} />
                  重新生成
                </button>
              </div>
            ) : null}
          </div>
        ) : msg.artifact?.type === "video_analysis_result" && msg.artifact.videoAnalysis ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.videoAnalysis.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.videoAnalysis.ok ? "已完成" : "需补充"}
              </span>
            </div>
            {msg.artifact.videoAnalysis.error && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.videoAnalysis.error}
              </div>
            )}
            {msg.artifact.videoAnalysis.video_urls.length > 0 && (
              <div className="grid gap-2 text-[12px] text-ink-soft">
                {msg.artifact.videoAnalysis.video_urls.map((url, index) => (
                  <a key={`${url}-${index}`} href={url} target="_blank" rel="noreferrer" className="truncate rounded-lg bg-canvas px-2 py-1.5 text-accent">
                    {index + 1}. {url}
                  </a>
                ))}
              </div>
            )}
            {videoAnalysisStoryboards.length > 0 && (
              <div className="space-y-2">
                {videoAnalysisStoryboards.slice(0, 4).map((storyboard, index) => {
                  const shots = records(storyboard.shots);
                  return (
                    <div key={`${String(storyboard.video_url || index)}-${index}`} className="rounded-xl border border-line bg-canvas p-2 text-[12px] text-ink-soft">
                      <div className="font-medium text-ink">{String(storyboard.video_url || storyboard.video_urls || `分析结果 ${index + 1}`)}</div>
                      {storyboard.analysis_markdown ? (
                        <div className="mt-1 whitespace-pre-wrap leading-relaxed">{String(storyboard.analysis_markdown)}</div>
                      ) : null}
                      {storyboard.generation_prompt ? (
                        <div className="mt-1 whitespace-pre-wrap leading-relaxed text-ink">生成建议：{String(storyboard.generation_prompt)}</div>
                      ) : null}
                      {shots.length > 0 ? (
                        <div className="mt-1 space-y-1">
                          {shots.slice(0, 3).map((shot, shotIndex) => (
                            <div key={`${String(shot.time_range || shotIndex)}-${shotIndex}`}>
                              {String(shot.time_range || `镜头 ${shotIndex + 1}`)}：{String(shot.visual_description || shot.description || "")}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
            <div className="rounded-xl bg-canvas px-3 py-2 text-[12px] leading-relaxed text-ink-soft">
              调用链路：{msg.artifact.videoAnalysis.extract_endpoint} → {msg.artifact.videoAnalysis.endpoint || "未进入视频分析"}
            </div>
            {videoAnalysisFailed && (
              <button
                type="button"
                onClick={() => onRetryVideoAnalysis?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                重新分析视频
              </button>
            )}
          </div>
        ) : msg.artifact?.type === "video_quality_review" && msg.artifact.videoQualityReview ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.videoQualityReview.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.videoQualityReview.ok ? "已分析" : "失败"}
              </span>
            </div>
            {msg.artifact.videoQualityReview.quality_report_markdown && (
              <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
                {msg.artifact.videoQualityReview.quality_report_markdown}
              </pre>
            )}
            {msg.artifact.videoQualityReview.affected_scene_ids.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {msg.artifact.videoQualityReview.affected_scene_ids.map((sceneId) => (
                  <span key={sceneId} className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] text-accent">
                    {sceneId}
                  </span>
                ))}
              </div>
            )}
            {msg.artifact.videoQualityReview.issues.length > 0 && (
              <div className="space-y-2">
                {msg.artifact.videoQualityReview.issues.slice(0, 4).map((issue, index) => (
                  <div key={`${String(issue.scene_id || index)}-${index}`} className="rounded-xl border border-line bg-canvas p-2 text-[12px] text-ink-soft">
                    <span className="font-medium text-ink">{String(issue.scene_id || `问题 ${index + 1}`)}</span>
                    <span className="ml-2">{String(issue.observed || issue.message || issue.description || "")}</span>
                    {issue.expected ? <span className="ml-2 text-ink">应为：{String(issue.expected)}</span> : null}
                  </div>
                ))}
              </div>
            )}
            {msg.artifact.videoRevisionFeedback && (
              <div className="rounded-xl bg-canvas px-3 py-2 text-[12px] leading-relaxed text-ink-soft">
                用户意见：{msg.artifact.videoRevisionFeedback}
              </div>
            )}
            <div className="grid gap-2 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => onRegenerateVideoWithRevision?.(msg, false)}
                className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
              >
                <Pencil size={15} />
                只按我的意见修改
              </button>
              <button
                type="button"
                onClick={() => onRegenerateVideoWithRevision?.(msg, true)}
                className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                结合质检结果修改
              </button>
            </div>
          </div>
        ) : msg.artifact?.type === "video_result" && (msg.artifact.mergedVideo || msg.artifact.generatedSceneVideos) ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium",
                msg.artifact.mergedVideo?.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber",
              )}>
                {msg.artifact.mergedVideo?.ok ? "已合并" : "失败"}
              </span>
            </div>
            {msg.artifact.mergedVideo?.error && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.mergedVideo.error}
              </div>
            )}
            {mergedVideoResult && (
              <section className="space-y-2">
                <div className="text-[13px] font-semibold text-ink">成品视频</div>
                <VideoResultCard
                  result={mergedVideoResult}
                  className="max-w-[324px]"
                  onOpen={(video) => onOpenVideoResult?.(msg, video, videoResults)}
                />
              </section>
            )}
            {sceneVideoResults.length > 0 ? (
              <section className="space-y-2">
                <div className="text-[13px] font-semibold text-ink">分镜视频生成结果</div>
                <div className="grid gap-3 sm:grid-cols-3">
                  {sceneVideoResults.map((result) => (
                    <VideoResultCard
                      key={result.id}
                      result={result}
                      onOpen={(video) => onOpenVideoResult?.(msg, video, videoResults)}
                    />
                  ))}
                </div>
              </section>
            ) : null}
            {msg.artifact.generatedSceneVideos?.failed_scenes.length ? (
              <div className="space-y-2 rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                <div className="font-medium">失败场景：{msg.artifact.generatedSceneVideos.failed_scenes.length} 个</div>
                {msg.artifact.generatedSceneVideos.failed_scenes.map((scene, index) => (
                  <details key={`${String(scene.scene_id || index)}-${index}`} className="rounded-lg bg-white/70 px-2 py-1.5">
                    <summary className="cursor-pointer text-amber">
                      {String(scene.scene_index || index + 1)}. {String(scene.scene_id || "未知场景")} · 查看失败原因
                    </summary>
                    <pre className="mt-2 max-h-[180px] overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-ink-soft">
                      {JSON.stringify(scene, null, 2)}
                    </pre>
                  </details>
                ))}
              </div>
            ) : null}
            {videoGenerationFailed ? (
              <button
                type="button"
                onClick={() => onGenerateVideoFromScenePackages?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                重新生成场景视频
              </button>
            ) : null}
            {videoMergeFailed ? (
              <button
                type="button"
                onClick={() => onRetryVideoMerge?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                {mergeQuotaPaused ? "继续合并视频" : "重新合并视频"}
              </button>
            ) : null}
            {msg.artifact.mergedVideo?.ok && videoAccepted ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 rounded-xl border border-emerald/20 bg-emerald/10 px-3 py-2 text-[12px] text-emerald">
                  <Check size={15} />
                  视频流程已结束
                </div>
                {jianyingDraftSucceeded ? (
                  <a
                    href={jianyingDraftDownloadUrl}
                    target="_blank"
                    rel="noreferrer"
                    onClick={() => onDownloadJianyingDraft?.(msg)}
                    className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-xl border border-line px-3 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                  >
                    <Download size={15} />
                    下载剪映草稿
                  </a>
                ) : (
                  <button
                    type="button"
                    disabled={actionsDisabled || !jianyingDraftAction.enabled}
                    title={jianyingDraftUnavailable ? "剪映草稿服务待接入" : jianyingDraftAction.reason || undefined}
                    onClick={() => onGenerateJianyingDraft?.(msg)}
                    className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-xl border border-line px-3 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {jianyingDraftRunning ? <LoaderCircle size={15} className="animate-spin" /> : <FileArchive size={15} />}
                    {jianyingDraftRunning ? "草稿生成中" : jianyingDraftAction.label}
                  </button>
                )}
              </div>
            ) : msg.artifact.mergedVideo?.ok ? (
              <div className="grid gap-2 sm:grid-cols-3">
                <button
                  type="button"
                  disabled={videoResultActionDisabled}
                  onClick={() => onAcceptVideoResult?.(msg)}
                  className="flex min-h-11 items-center justify-center gap-1.5 rounded-xl bg-brand px-3 py-2.5 text-[13px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Check size={15} />
                  无意见，结束
                </button>
                <button
                  type="button"
                  disabled={actionsDisabled || !jianyingDraftAction.enabled}
                  title={jianyingDraftUnavailable ? "剪映草稿服务待接入" : jianyingDraftAction.reason || undefined}
                  onClick={() => onGenerateJianyingDraft?.(msg)}
                  className="flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-line px-3 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {jianyingDraftRunning ? <LoaderCircle size={15} className="animate-spin" /> : <FileArchive size={15} />}
                  {jianyingDraftRunning ? "草稿生成中" : jianyingDraftAction.label}
                </button>
                <button
                  type="button"
                  disabled={videoResultActionDisabled}
                  onClick={() => onReviseVideoResult?.(msg)}
                  className="flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-line px-3 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Pencil size={15} />
                  提出修改意见
                </button>
              </div>
            ) : null}
          </div>
        ) : msg.artifact?.type === "jianying_draft" && msg.artifact.jianyingDraft ? (
          <div className="mt-2 w-full max-w-[560px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileArchive size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">
                  {jianyingDraftSucceeded ? "剪映草稿已生成" : "剪映草稿生成失败"}
                </span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">
                  {jianyingDraftRetryable && !jianyingDraftSucceeded
                    ? "剪映草稿生成失败，请重新生成。"
                    : msg.artifact.description}
                </span>
              </span>
            </div>
            {jianyingDraftSucceeded ? (
              <div className="space-y-2 text-[12px] text-ink-soft">
                <div className="truncate">{msg.artifact.jianyingDraft.file_name || "jianying-draft.zip"}</div>
                <div>来源分镜：{msg.artifact.jianyingDraftSceneCount || 0} 个</div>
                <a
                  href={jianyingDraftDownloadUrl}
                  target="_blank"
                  rel="noreferrer"
                  onClick={() => onDownloadJianyingDraft?.(msg)}
                  className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-xl bg-brand px-3 py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Download size={15} />
                  下载剪映草稿
                </a>
              </div>
            ) : jianyingDraftRetryable ? (
              <div className="space-y-2">
                <div className="text-[12px] leading-relaxed text-ink-soft">
                  {msg.artifact.jianyingDraft.status === "succeeded"
                    ? "剪映草稿生成失败，请重新生成。"
                    : msg.artifact.jianyingDraft.message}
                </div>
                <button
                  type="button"
                  disabled={actionsDisabled || jianyingDraftRunning || jianyingDraftUnavailable}
                  title={jianyingDraftUnavailable ? "剪映草稿服务待接入" : undefined}
                  onClick={() => onGenerateJianyingDraft?.(msg)}
                  className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-xl border border-line px-3 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {jianyingDraftRunning ? <LoaderCircle size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                  {jianyingDraftRunning ? "草稿生成中" : "重新生成剪映草稿"}
                </button>
              </div>
            ) : null}
          </div>
        ) : msg.artifact ? (
          <button
            type="button"
            onClick={() => onOpenArtifact?.(msg)}
            className="mt-2 flex w-full max-w-[320px] items-center gap-3 rounded-2xl border border-accent/20 bg-accent-soft/70 px-3 py-3 text-left transition-colors hover:border-accent/40 hover:bg-accent-soft"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/70 text-accent">
              <FileVideo size={18} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
              <span className="mt-0.5 block truncate text-[12px] text-ink-soft">{msg.artifact.description}</span>
            </span>
            <span className="shrink-0 rounded-lg bg-white/70 px-2 py-1 text-[12px] font-medium text-accent">
              {msg.artifact.actionLabel}
            </span>
          </button>
        ) : null}
        </div>
        {showProgressLoading ? (
          <span
            className="mt-1 ml-1 flex items-center gap-1.5 text-[11px] text-ink-soft"
            role="status"
            aria-label="loading"
          >
            <span className="block h-3.5 w-3.5 animate-spin rounded-full bg-[conic-gradient(from_0deg,#4f46e5,#60a5fa,#a78bfa,#4f46e5)] p-[1.5px]">
              <span className="block h-full w-full rounded-full bg-canvas" />
            </span>
            <span>{progressText}</span>
          </span>
        ) : null}
        <span className="mt-1 px-1 text-[11px] text-ink-soft/60">{msg.time}</span>
      </div>
    </div>
  );
}
