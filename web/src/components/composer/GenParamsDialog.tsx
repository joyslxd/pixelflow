import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Check, ChevronUp, FilePenLine, Upload, X } from "lucide-react";
import { api, type ImageModelParamConfig, type UploadedAttachment } from "@/lib/api";
import {
  FALLBACK_IMAGE_MODEL_CONFIG,
  FALLBACK_VIDEO_MODEL_CONFIG,
  filterSeedanceConfigs,
  hasCompleteVideoModelCapabilities,
  imageModelCapabilities,
  preferredVideoSize,
  preferredVideoSound,
  resolveImageModel,
  resolveVideoModel,
  videoModelCapabilities,
  videoRatios,
  type ImageModelCapabilities,
  type VideoModelCapabilities,
} from "@/lib/videoRequirementConfig";

export type CreationIntent = "video" | "image" | "ppt";

export interface VideoRequirementForm {
  intent: "video";
  product_info: string;
  product_category: string;
  target_audience: string;
  conversion_goal: string;
  video_duration_sec: number;
  video_ratio: string;
  video_model_mode: "system_recommended" | "manual";
  video_model: string;
  video_model_capabilities: VideoModelCapabilities;
  video_size: string;
  video_sound: "on" | "off";
  image_model: string;
  image_model_capabilities: ImageModelCapabilities;
  video_usage: string;
  visual_style: string;
}

export interface ImageRequirementForm {
  intent: "image";
  image_goal: string;
  image_type: string;
  image_usage: string;
  image_style: string;
  image_size: string;
  image_count?: number;
}

export interface PptRequirementForm {
  intent: "ppt";
  ppt_topic: string;
  ppt_style: string;
  attachments: Array<Record<string, unknown>>;
}

export type GenParamsForm = VideoRequirementForm | ImageRequirementForm | PptRequirementForm;

interface GenParamsDialogProps {
  open: boolean;
  intent: CreationIntent;
  /** 来自用户消息的初始创意诉求 */
  initialCoreMessage?: string;
  /** LLM 从用户提示词中自动抽取的表单初值 */
  initialValues?: Record<string, unknown>;
  initialMaterials?: Array<Record<string, unknown>>;
  onConfirm: (form: GenParamsForm) => void;
  onCancel: () => void;
}

const VIDEO_GOALS = ["直接购买", "品牌曝光", "种草引流", "引流直播间"];
const VIDEO_DURATION_OPTIONS = ["30", "60", "90", "180", "自定义"];
const VIDEO_MODEL_MODES = ["system_recommended", "manual"] as const;
type VideoDurationMode = (typeof VIDEO_DURATION_OPTIONS)[number];

const IMAGE_TYPES = ["商品广告图", "人物/场景图", "海报/封面图", "插画/概念图", "背景/素材图", "其他"];
const IMAGE_USAGES = ["广告投放", "社媒发布", "内容封面", "详情页配图", "活动宣传", "内部展示", "其他用途"];
const IMAGE_STYLES = ["真实摄影", "高级质感", "简洁干净", "小红书风", "科技感", "插画风", "自由发挥"];
const IMAGE_SIZES = ["1:1", "16:9", "9:16", "自动适配"];
const PPT_CUSTOM_STYLE = "自定义";
const PPT_STYLES = ["极简商务", "科技数据", "教育培训", "产品发布", "投融资路演", "自定义"];
const PPT_ACCEPT = ".doc,.docx,.xls,.xlsx,.pdf";
const PPT_MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024;
const PPT_MAX_ATTACHMENT_SIZE_LABEL = "20MB";
const PPT_MAX_TOTAL_ATTACHMENT_SIZE_BYTES = 100 * 1024 * 1024;
const PPT_MAX_TOTAL_ATTACHMENT_SIZE_LABEL = "100MB";

const inputCls =
  "h-12 w-full rounded-xl border border-line bg-surface px-4 text-[14px] text-ink outline-none placeholder:text-ink-soft/55 focus:border-accent/40";
const selectCls = `${inputCls} appearance-none pr-10`;

const textValue = (values: Record<string, unknown>, key: string, fallback = "") => {
  const value = values[key];
  return typeof value === "string" && value.trim() ? value : fallback;
};

const optionValue = (values: Record<string, unknown>, key: string, options: string[], fallback: string) => {
  const value = values[key];
  return typeof value === "string" && options.includes(value) ? value : fallback;
};

const numberValue = (values: Record<string, unknown>, key: string, fallback: number) => {
  const parsed = Number(values[key]);
  return Number.isFinite(parsed) && parsed > 0 ? Math.max(1, Math.min(10, Math.round(parsed))) : fallback;
};

const naturalNumberValue = (values: Record<string, unknown>, key: string, fallback: number, min: number, max: number) => {
  const parsed = Number(values[key]);
  return Number.isInteger(parsed) && parsed >= min && parsed <= max ? parsed : fallback;
};

function initialImageModelCapabilities(values: Record<string, unknown>): ImageModelCapabilities {
  const raw = values.image_model_capabilities;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const capabilities = raw as Record<string, unknown>;
    const aspectRatios = Array.isArray(capabilities.aspect_ratios)
      ? capabilities.aspect_ratios.map(String).filter(Boolean)
      : [];
    const sizes = Array.isArray(capabilities.sizes) ? capabilities.sizes.map(String).filter(Boolean) : [];
    if (aspectRatios.length > 0 && sizes.length > 0) {
      return { aspect_ratios: aspectRatios, sizes };
    }
  }
  return imageModelCapabilities(FALLBACK_IMAGE_MODEL_CONFIG);
}

function initialVideoModelCapabilities(values: Record<string, unknown>): VideoModelCapabilities {
  const raw = values.video_model_capabilities;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const capabilities = raw as Record<string, unknown>;
    const generationTypes = Array.isArray(capabilities.generation_types)
      ? capabilities.generation_types.map(String).filter(Boolean)
      : [];
    const uploadFileTypes = Array.isArray(capabilities.upload_file_types)
      ? capabilities.upload_file_types.map(String).filter(Boolean)
      : [];
    const aspectRatios = Array.isArray(capabilities.aspect_ratios)
      ? capabilities.aspect_ratios.map(String).filter(Boolean)
      : [];
    const sizes = Array.isArray(capabilities.sizes) ? capabilities.sizes.map(String).filter(Boolean) : [];
    const soundOptions = Array.isArray(capabilities.sound_options)
      ? capabilities.sound_options.map(String).filter((value): value is "on" | "off" => value === "on" || value === "off")
      : [];
    const durationsSec = Array.isArray(capabilities.durations_sec)
      ? capabilities.durations_sec.map(Number).filter((value) => Number.isInteger(value) && value > 0)
      : [];
    if (generationTypes.length > 0) {
      return {
        generation_types: generationTypes,
        upload_file_types: uploadFileTypes,
        aspect_ratios: aspectRatios,
        sizes,
        sound_options: soundOptions,
        durations_sec: durationsSec,
      };
    }
  }
  return videoModelCapabilities(FALLBACK_VIDEO_MODEL_CONFIG);
}

function videoInitialValues(initialCoreMessage: string | undefined, values: Record<string, unknown>): VideoRequirementForm {
  const modelMode = textValue(values, "video_model_mode", "system_recommended");
  return {
    intent: "video",
    product_info: textValue(values, "product_info", initialCoreMessage ?? ""),
    product_category: textValue(values, "product_category"),
    target_audience: textValue(values, "target_audience"),
    conversion_goal: optionValue(values, "conversion_goal", VIDEO_GOALS, "引流直播间"),
    video_duration_sec: naturalNumberValue(values, "video_duration_sec", 30, 4, 300),
    video_ratio: textValue(values, "video_ratio", "9:16"),
    video_model_mode: VIDEO_MODEL_MODES.includes(modelMode as (typeof VIDEO_MODEL_MODES)[number])
      ? (modelMode as VideoRequirementForm["video_model_mode"])
      : "system_recommended",
    video_model: textValue(values, "video_model", "seedance-2.0"),
    video_model_capabilities: initialVideoModelCapabilities(values),
    video_size: textValue(values, "video_size", "1080p"),
    video_sound: textValue(values, "video_sound", "on") === "off" ? "off" : "on",
    image_model: textValue(values, "image_model", "gpt-image-2"),
    image_model_capabilities: initialImageModelCapabilities(values),
    video_usage: textValue(values, "video_usage", "宣传片"),
    visual_style: textValue(values, "visual_style"),
  };
}

function videoDurationModeValue(duration: number): VideoDurationMode {
  const value = String(duration);
  return VIDEO_DURATION_OPTIONS.includes(value) ? value : "自定义";
}

function imageInitialValues(initialCoreMessage: string | undefined, values: Record<string, unknown>): ImageRequirementForm {
  const imageSize = textValue(values, "image_size");
  return {
    intent: "image",
    image_goal: textValue(values, "image_goal", initialCoreMessage ?? ""),
    image_type: optionValue(values, "image_type", IMAGE_TYPES, "海报/封面图"),
    image_usage: optionValue(values, "image_usage", IMAGE_USAGES, "社媒发布"),
    image_style: optionValue(values, "image_style", IMAGE_STYLES, "真实摄影"),
    image_size: imageSize === "自定义" ? "自动适配" : optionValue(values, "image_size", IMAGE_SIZES, "9:16"),
    image_count: numberValue(values, "image_count", 1),
  };
}

function pptInitialValues(
  initialCoreMessage: string | undefined,
  values: Record<string, unknown>,
  initialMaterials: Array<Record<string, unknown>>,
): PptRequirementForm {
  const style = textValue(values, "ppt_style", "极简商务");
  return {
    intent: "ppt",
    ppt_topic: textValue(values, "ppt_topic", initialCoreMessage ?? ""),
    ppt_style: style === "自由发挥" ? "" : style,
    attachments: officeAttachments(records(values.attachments).concat(initialMaterials)),
  };
}

function pptStyleModeValue(style: string): string {
  return PPT_STYLES.includes(style) ? style : PPT_CUSTOM_STYLE;
}

function pptCustomStyleValue(style: string): string {
  return style && !PPT_STYLES.includes(style) ? style : "";
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function attachmentName(attachment: Record<string, unknown>): string {
  return String(attachment.name || attachment.filename || attachment.url || "附件");
}

function attachmentUrl(attachment: Record<string, unknown>): string {
  return String(attachment.url || attachment.path || attachment.fileUrl || attachment.file_url || "");
}

function attachmentSize(attachment: Record<string, unknown>): number {
  const size = Number(attachment.size || attachment.fileSize || attachment.file_size || 0);
  return Number.isFinite(size) && size > 0 ? size : 0;
}

function isOfficeAttachment(value: Record<string, unknown>): boolean {
  const target = `${attachmentName(value)} ${attachmentUrl(value)}`.toLowerCase().split("?")[0];
  return /\.(docx?|xlsx?|pdf)(?:$|#)/.test(target);
}

function officeAttachments(values: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const seen = new Set<string>();
  return values.filter((value) => {
    if (!isOfficeAttachment(value)) return false;
    const key = attachmentUrl(value) || attachmentName(value);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function PillGroup({ options, value, onChange }: { options: string[]; value: string; onChange: (value: string) => void }) {
  return (
    <div className="flex flex-wrap gap-3">
      {options.map((option) => {
        const selected = value === option;
        return (
          <button
            key={option}
            type="button"
            onClick={() => onChange(option)}
            className={`flex h-12 items-center gap-2 rounded-xl border px-4 text-[14px] transition-colors ${
              selected
                ? "border-[#ded6fb] bg-[#ebe6ff] text-ink"
                : "border-line bg-surface text-ink-soft hover:border-accent/30 hover:text-ink"
            }`}
          >
            <span className={`flex h-5 w-5 items-center justify-center rounded-full border ${selected ? "border-accent" : "border-line"}`}>
              {selected && <span className="h-2.5 w-2.5 rounded-full bg-accent" />}
            </span>
            {option}
          </button>
        );
      })}
    </div>
  );
}

function FieldBlock({ index, label, children }: { index: number; label: string; children: ReactNode }) {
  return (
    <div className="space-y-3">
      <div className="text-[18px] font-semibold leading-6 text-ink">
        {index}. {label}
      </div>
      {children}
    </div>
  );
}

export function GenParamsDialog({ open, intent, initialCoreMessage, initialValues = {}, initialMaterials = [], onConfirm, onCancel }: GenParamsDialogProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [video, setVideo] = useState<VideoRequirementForm>(() => videoInitialValues(initialCoreMessage, initialValues));
  const [videoDurationMode, setVideoDurationMode] = useState<VideoDurationMode>(() => videoDurationModeValue(video.video_duration_sec));
  const [customVideoDuration, setCustomVideoDuration] = useState(() =>
    videoDurationModeValue(video.video_duration_sec) === "自定义" ? String(video.video_duration_sec) : "",
  );
  const [videoModelConfigs, setVideoModelConfigs] = useState<ImageModelParamConfig[]>([FALLBACK_VIDEO_MODEL_CONFIG]);
  const [imageModelConfigs, setImageModelConfigs] = useState<ImageModelParamConfig[]>([FALLBACK_IMAGE_MODEL_CONFIG]);
  const [modelConfigsLoading, setModelConfigsLoading] = useState(false);
  const [modelConfigsError, setModelConfigsError] = useState("");
  const [image, setImage] = useState<ImageRequirementForm>(() => imageInitialValues(initialCoreMessage, initialValues));
  const [ppt, setPpt] = useState<PptRequirementForm>(() => pptInitialValues(initialCoreMessage, initialValues, initialMaterials));
  const [pptStyleMode, setPptStyleMode] = useState(() => pptStyleModeValue(ppt.ppt_style));
  const [pptCustomStyle, setPptCustomStyle] = useState(() => pptCustomStyleValue(ppt.ppt_style));

  useEffect(() => {
    if (!open) return;
    setSubmitted(false);
    setCollapsed(false);
    setUploadError("");
    const nextVideo = videoInitialValues(initialCoreMessage, initialValues);
    const nextDurationMode = videoDurationModeValue(nextVideo.video_duration_sec);
    setVideo(nextVideo);
    setVideoDurationMode(nextDurationMode);
    setCustomVideoDuration(nextDurationMode === "自定义" ? String(nextVideo.video_duration_sec) : "");
    setImage(imageInitialValues(initialCoreMessage, initialValues));
    const nextPpt = pptInitialValues(initialCoreMessage, initialValues, initialMaterials);
    setPpt(nextPpt);
    setPptStyleMode(pptStyleModeValue(nextPpt.ppt_style));
    setPptCustomStyle(pptCustomStyleValue(nextPpt.ppt_style));
  }, [open, intent, initialCoreMessage, initialValues, initialMaterials]);

  useEffect(() => {
    if (!open || intent !== "video") return;
    let cancelled = false;
    setModelConfigsLoading(true);
    setModelConfigsError("");
    void Promise.allSettled([api.listVideoGenerateModelConfigs(), api.listImageGenerateModelConfigs()]).then((results) => {
      if (cancelled) return;
      const rawVideoConfigs = results[0].status === "fulfilled" ? results[0].value : [];
      const rawImageConfigs = results[1].status === "fulfilled" ? results[1].value : [];
      const availableVideoConfigs = filterSeedanceConfigs(rawVideoConfigs);
      const availableImageConfigs = rawImageConfigs.filter((config) => config.isEnabled !== false);
      const nextVideoConfigs = availableVideoConfigs.length > 0 ? availableVideoConfigs : [FALLBACK_VIDEO_MODEL_CONFIG];
      const nextImageConfigs = availableImageConfigs.length > 0 ? availableImageConfigs : [FALLBACK_IMAGE_MODEL_CONFIG];
      const requestedVideoModel = textValue(initialValues, "video_model", "seedance-2.0");
      const requestedImageModel = textValue(initialValues, "image_model", "gpt-image-2");
      const selectedVideoConfig = resolveVideoModel(nextVideoConfigs, requestedVideoModel);
      const selectedImageConfig = resolveImageModel(nextImageConfigs, requestedImageModel);
      const selectedVideoCapabilities = videoModelCapabilities(selectedVideoConfig);
      const ratios = videoRatios(selectedVideoConfig);

      setVideoModelConfigs(nextVideoConfigs);
      setImageModelConfigs(nextImageConfigs);
      setVideo((current) => ({
        ...current,
        video_model: selectedVideoConfig.modelType,
        video_model_capabilities: selectedVideoCapabilities,
        video_ratio: ratios.includes(current.video_ratio) ? current.video_ratio : ratios[0],
        video_size: preferredVideoSize(selectedVideoConfig, current.video_size),
        video_sound: preferredVideoSound(selectedVideoConfig, current.video_sound),
        image_model: selectedImageConfig.modelType,
        image_model_capabilities: imageModelCapabilities(selectedImageConfig),
      }));
      if (
        results.some((result) => result.status === "rejected")
        || availableVideoConfigs.length === 0
        || !hasCompleteVideoModelCapabilities(selectedVideoCapabilities)
      ) {
        setModelConfigsError("视频模型实时能力读取失败或不完整，暂不能提交，请刷新后重试。");
      }
      setModelConfigsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open, intent, initialValues]);

  if (!open) return null;

  const isVideo = intent === "video";
  const isPpt = intent === "ppt";
  const validVideoDuration = Number.isInteger(video.video_duration_sec) && video.video_duration_sec >= 4 && video.video_duration_sec <= 300;
  const canConfirm = isVideo
    ? Boolean(
        video.product_info.trim()
          && video.product_category.trim()
          && video.target_audience.trim()
          && video.conversion_goal
          && validVideoDuration
          && video.video_ratio
          && video.video_model
          && hasCompleteVideoModelCapabilities(video.video_model_capabilities)
          && video.video_model_capabilities.aspect_ratios.includes(video.video_ratio)
          && video.video_model_capabilities.sizes.includes(video.video_size)
          && (
            video.video_model_capabilities.sound_options.length === 0
            || video.video_model_capabilities.sound_options.includes(video.video_sound)
          )
          && video.image_model
          && video.image_model_capabilities.aspect_ratios.length > 0
          && video.image_model_capabilities.sizes.length > 0
          && video.video_usage.trim(),
      )
    : isPpt
      ? Boolean(ppt.ppt_topic.trim() && ppt.ppt_style && ppt.attachments.length > 0 && !uploading)
      : Boolean(image.image_goal.trim() && image.image_type && image.image_usage && image.image_style && image.image_size);

  const submit = () => {
    if (!canConfirm) return;
    setSubmitted(true);
    onConfirm(isVideo ? video : isPpt ? ppt : image);
  };

  const updateVideoDurationMode = (value: string) => {
    const mode = value as VideoDurationMode;
    setVideoDurationMode(mode);
    if (mode === "自定义") {
      setCustomVideoDuration("");
      setVideo((current) => ({ ...current, video_duration_sec: 0 }));
      return;
    }
    setCustomVideoDuration("");
    setVideo((current) => ({ ...current, video_duration_sec: Number(mode) }));
  };

  const updateCustomVideoDuration = (value: string) => {
    if (value && !/^\d+$/.test(value)) return;
    setCustomVideoDuration(value);
    const duration = Number(value);
    setVideo((current) => ({
      ...current,
      video_duration_sec: Number.isInteger(duration) && duration >= 4 && duration <= 300 ? duration : 0,
    }));
  };

  const updateVideoModelMode = (value: string) => {
    const mode = value === "manual" ? "manual" : "system_recommended";
    if (mode === "manual") {
      setVideo((current) => ({ ...current, video_model_mode: mode }));
      return;
    }
    const selected = resolveVideoModel(videoModelConfigs, "seedance-2.0");
    const ratios = videoRatios(selected);
    setVideo((current) => ({
      ...current,
      video_model_mode: mode,
      video_model: selected.modelType,
      video_model_capabilities: videoModelCapabilities(selected),
      video_ratio: ratios.includes(current.video_ratio) ? current.video_ratio : ratios[0],
      video_size: preferredVideoSize(selected, current.video_size),
      video_sound: preferredVideoSound(selected, current.video_sound),
    }));
  };

  const updateVideoModel = (modelType: string) => {
    const selected = resolveVideoModel(videoModelConfigs, modelType);
    const ratios = videoRatios(selected);
    setVideo((current) => ({
      ...current,
      video_model_mode: "manual",
      video_model: selected.modelType,
      video_model_capabilities: videoModelCapabilities(selected),
      video_ratio: ratios.includes(current.video_ratio) ? current.video_ratio : ratios[0],
      video_size: preferredVideoSize(selected, current.video_size),
      video_sound: preferredVideoSound(selected, current.video_sound),
    }));
  };

  const updateImageModel = (modelType: string) => {
    const selected = resolveImageModel(imageModelConfigs, modelType);
    setVideo((current) => ({
      ...current,
      image_model: selected.modelType,
      image_model_capabilities: imageModelCapabilities(selected),
    }));
  };

  const updatePptStyle = (value: string) => {
    setPptStyleMode(value);
    if (value === PPT_CUSTOM_STYLE) {
      setPpt((prev) => ({ ...prev, ppt_style: pptCustomStyle.trim() }));
      return;
    }
    setPptCustomStyle("");
    setPpt((prev) => ({ ...prev, ppt_style: value }));
  };

  const updatePptCustomStyle = (value: string) => {
    setPptCustomStyle(value);
    setPpt((prev) => ({ ...prev, ppt_style: value.trim() }));
  };

  const uploadPptFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setUploadError("");
    try {
      const uploads: UploadedAttachment[] = [];
      const validationErrors: string[] = [];
      let totalSize = ppt.attachments.reduce((sum, attachment) => sum + attachmentSize(attachment), 0);
      for (const file of Array.from(files)) {
        if (!/\.(docx?|xlsx?|pdf)$/i.test(file.name)) {
          validationErrors.push(`${file.name}：附件仅支持 Word、Excel、PDF 文件`);
          continue;
        }
        if (file.size > PPT_MAX_ATTACHMENT_SIZE_BYTES) {
          validationErrors.push(`${file.name}：文件大小不能超过 ${PPT_MAX_ATTACHMENT_SIZE_LABEL}`);
          continue;
        }
        if (totalSize + file.size > PPT_MAX_TOTAL_ATTACHMENT_SIZE_BYTES) {
          validationErrors.push(`${file.name}：附件总大小不能超过 ${PPT_MAX_TOTAL_ATTACHMENT_SIZE_LABEL}`);
          continue;
        }
        const uploaded = await api.uploadAttachment(file);
        if (uploaded.size > PPT_MAX_ATTACHMENT_SIZE_BYTES) {
          validationErrors.push(`${file.name}：文件大小不能超过 ${PPT_MAX_ATTACHMENT_SIZE_LABEL}`);
          continue;
        }
        if (totalSize + uploaded.size > PPT_MAX_TOTAL_ATTACHMENT_SIZE_BYTES) {
          validationErrors.push(`${file.name}：附件总大小不能超过 ${PPT_MAX_TOTAL_ATTACHMENT_SIZE_LABEL}`);
          continue;
        }
        uploads.push(uploaded);
        totalSize += uploaded.size;
      }
      if (uploads.length) {
        setPpt((prev) => ({ ...prev, attachments: officeAttachments(prev.attachments.concat(uploads)) }));
      }
      setUploadError(validationErrors.join("；"));
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/25 p-5">
      <div className="max-h-[88vh] w-full max-w-[980px] overflow-y-auto rounded-[22px] border border-line bg-[#fbfbfc] p-8 shadow-xl">
        <div className="mb-8 flex items-center justify-between">
          <div className="flex items-center gap-3 text-[22px] font-semibold text-ink">
            <FilePenLine size={26} />
            {isVideo ? "AD投放短视频需求收集" : isPpt ? "PPT生成需求收集" : "图片生成需求收集"}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              className="flex h-9 w-9 items-center justify-center rounded-full bg-canvas text-ink-soft hover:text-ink"
              aria-label="折叠表单"
            >
              <ChevronUp size={18} className={collapsed ? "rotate-180 transition-transform" : "transition-transform"} />
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-canvas hover:text-ink"
              aria-label="关闭"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {!collapsed && (
          <div className="space-y-9">
            {isVideo ? (
              <>
                <FieldBlock index={1} label="请提供你要投放的产品信息">
                  <input
                    className={inputCls}
                    value={video.product_info}
                    onChange={(e) => setVideo((p) => ({ ...p, product_info: e.target.value }))}
                    placeholder="苹果什么什么PRO"
                  />
                </FieldBlock>
                <FieldBlock index={2} label="产品品类">
                  <input
                    className={inputCls}
                    value={video.product_category}
                    onChange={(e) => setVideo((p) => ({ ...p, product_category: e.target.value }))}
                    placeholder="例如：服饰鞋包、运动鞋、数码3C"
                  />
                </FieldBlock>
                <FieldBlock index={3} label="目标人群">
                  <input
                    className={inputCls}
                    value={video.target_audience}
                    onChange={(e) => setVideo((p) => ({ ...p, target_audience: e.target.value }))}
                    placeholder="25-35"
                  />
                </FieldBlock>
                <FieldBlock index={4} label="转化目标">
                  <PillGroup options={VIDEO_GOALS} value={video.conversion_goal} onChange={(v) => setVideo((p) => ({ ...p, conversion_goal: v }))} />
                </FieldBlock>
                <FieldBlock index={5} label="视频总时长">
                  <PillGroup options={VIDEO_DURATION_OPTIONS} value={videoDurationMode} onChange={updateVideoDurationMode} />
                  {videoDurationMode === "自定义" && (
                    <div className="space-y-2">
                      <input
                        className={inputCls}
                        type="number"
                        min={4}
                        max={300}
                        step={1}
                        inputMode="numeric"
                        value={customVideoDuration}
                        onChange={(event) => updateCustomVideoDuration(event.target.value)}
                        placeholder="请输入 4-300 之间的自然数秒"
                      />
                      {customVideoDuration && !validVideoDuration && (
                        <div className="text-[12px] text-amber">视频总时长必须是 4-300 之间的自然数。</div>
                      )}
                    </div>
                  )}
                </FieldBlock>
                <FieldBlock index={6} label="视频画幅">
                  <select
                    className={selectCls}
                    value={video.video_ratio}
                    onChange={(event) => setVideo((current) => ({ ...current, video_ratio: event.target.value }))}
                  >
                    {videoRatios(resolveVideoModel(videoModelConfigs, video.video_model)).map((ratio) => (
                      <option key={ratio} value={ratio}>
                        {ratio}
                      </option>
                    ))}
                  </select>
                </FieldBlock>
                <FieldBlock index={7} label="视频模型">
                  <PillGroup
                    options={["系统推荐模型", "手动选择"]}
                    value={video.video_model_mode === "system_recommended" ? "系统推荐模型" : "手动选择"}
                    onChange={(value) => updateVideoModelMode(value === "手动选择" ? "manual" : "system_recommended")}
                  />
                  <select className={selectCls} value={video.video_model} onChange={(event) => updateVideoModel(event.target.value)}>
                    {videoModelConfigs.map((config) => (
                      <option key={config.modelType} value={config.modelType}>
                        {config.modelType}
                      </option>
                    ))}
                  </select>
                  <div className="text-[12px] text-ink-soft">
                    {video.video_model_mode === "system_recommended" ? `系统推荐结果：${video.video_model}` : `已选择：${video.video_model}`}
                  </div>
                </FieldBlock>
                <FieldBlock index={8} label="视频清晰度">
                  <select
                    className={selectCls}
                    value={video.video_size}
                    onChange={(event) => setVideo((current) => ({ ...current, video_size: event.target.value }))}
                  >
                    {video.video_model_capabilities.sizes.map((size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ))}
                  </select>
                  <div className="text-[12px] text-ink-soft">清晰度来自当前视频模型的实时能力配置。</div>
                </FieldBlock>
                <FieldBlock index={9} label="图片模型">
                  <select className={selectCls} value={video.image_model} onChange={(event) => updateImageModel(event.target.value)}>
                    {imageModelConfigs.map((config) => (
                      <option key={config.modelType} value={config.modelType}>
                        {config.modelType}
                      </option>
                    ))}
                  </select>
                  <div className="text-[12px] text-ink-soft">角色、场景和道具图片将使用该模型；图片比例与清晰度由 plan.md 在模型支持范围内自动规划。</div>
                </FieldBlock>
                <FieldBlock index={10} label="视频用途">
                  <input
                    className={inputCls}
                    value={video.video_usage}
                    onChange={(event) => setVideo((current) => ({ ...current, video_usage: event.target.value }))}
                    placeholder="例如：品牌宣传、产品介绍、活动预热"
                  />
                </FieldBlock>
                <FieldBlock index={11} label="视觉风格">
                  <input
                    className={inputCls}
                    value={video.visual_style}
                    onChange={(event) => setVideo((current) => ({ ...current, visual_style: event.target.value }))}
                    placeholder="例如：电影光影、科技感、写实、未来感"
                  />
                </FieldBlock>
                {(modelConfigsLoading || modelConfigsError) && (
                  <div className="rounded-xl border border-line bg-surface px-4 py-3 text-[12px] text-ink-soft">
                    {modelConfigsLoading ? "正在读取可用模型配置..." : modelConfigsError}
                  </div>
                )}
              </>
            ) : isPpt ? (
              <>
                <FieldBlock index={1} label="PPT主题">
                  <input
                    className={inputCls}
                    value={ppt.ppt_topic}
                    onChange={(e) => setPpt((p) => ({ ...p, ppt_topic: e.target.value }))}
                    placeholder="例如：2026年度营销策略汇报"
                  />
                </FieldBlock>
                <FieldBlock index={2} label="PPT风格">
                  <PillGroup options={PPT_STYLES} value={pptStyleMode} onChange={updatePptStyle} />
                  {pptStyleMode === PPT_CUSTOM_STYLE && (
                    <input
                      className={inputCls}
                      value={pptCustomStyle}
                      onChange={(e) => updatePptCustomStyle(e.target.value)}
                      placeholder="输入自定义 PPT 风格"
                    />
                  )}
                </FieldBlock>
                <FieldBlock index={3} label="附件">
                  <label className="flex min-h-[96px] cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line bg-surface px-4 text-center text-[13px] text-ink-soft hover:border-accent/40 hover:text-ink">
                    <Upload size={22} />
                    {uploading ? (
                      <span>上传中...</span>
                    ) : (
                      <span className="flex flex-col gap-1">
                        <span>上传 Word、Excel、PDF，可上传多个</span>
                        <span>单个文件不超过 {PPT_MAX_ATTACHMENT_SIZE_LABEL}，总大小不超过 {PPT_MAX_TOTAL_ATTACHMENT_SIZE_LABEL}</span>
                      </span>
                    )}
                    <input
                      className="hidden"
                      type="file"
                      accept={PPT_ACCEPT}
                      multiple
                      disabled={uploading}
                      onChange={(e) => {
                        void uploadPptFiles(e.currentTarget.files);
                        e.currentTarget.value = "";
                      }}
                    />
                  </label>
                  {uploadError && <div className="rounded-xl border border-amber/30 bg-amber/10 px-3 py-2 text-[12px] text-ink">{uploadError}</div>}
                  {ppt.attachments.length > 0 ? (
                    <div className="grid gap-2 sm:grid-cols-2">
                      {ppt.attachments.map((attachment, index) => (
                        <div key={`${attachmentUrl(attachment)}-${index}`} className="flex min-w-0 items-center justify-between gap-2 rounded-xl border border-line bg-white px-3 py-2 text-[13px] text-ink">
                          <span className="truncate">{attachmentName(attachment)}</span>
                          <button
                            type="button"
                            onClick={() => setPpt((p) => ({ ...p, attachments: p.attachments.filter((_, itemIndex) => itemIndex !== index) }))}
                            className="shrink-0 text-ink-soft hover:text-ink"
                          >
                            移除
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-[12px] text-ink-soft">请上传至少一个 Word、Excel 或 PDF 附件。</div>
                  )}
                </FieldBlock>
              </>
            ) : (
              <>
                <FieldBlock index={1} label="你想生成什么图片？">
                  <input
                    className={inputCls}
                    value={image.image_goal}
                    onChange={(e) => setImage((p) => ({ ...p, image_goal: e.target.value }))}
                    placeholder="例如：科技感海报、办公室场景图、小红书封面、人物插画"
                  />
                </FieldBlock>
                <FieldBlock index={2} label="图片类型">
                  <PillGroup options={IMAGE_TYPES} value={image.image_type} onChange={(v) => setImage((p) => ({ ...p, image_type: v }))} />
                </FieldBlock>
                <FieldBlock index={3} label="图片用途">
                  <PillGroup options={IMAGE_USAGES} value={image.image_usage} onChange={(v) => setImage((p) => ({ ...p, image_usage: v }))} />
                </FieldBlock>
                <FieldBlock index={4} label="图片风格">
                  <PillGroup options={IMAGE_STYLES} value={image.image_style} onChange={(v) => setImage((p) => ({ ...p, image_style: v }))} />
                </FieldBlock>
                <FieldBlock index={5} label="图片尺寸">
                  <PillGroup options={IMAGE_SIZES} value={image.image_size} onChange={(v) => setImage((p) => ({ ...p, image_size: v }))} />
                </FieldBlock>
              </>
            )}
          </div>
        )}

        <div className="mt-8 flex justify-end">
          <button
            type="button"
            onClick={submit}
            disabled={!canConfirm || submitted}
            className="flex h-14 min-w-[150px] items-center justify-center gap-2 rounded-xl bg-brand px-5 text-[16px] font-medium text-white transition-opacity disabled:bg-line disabled:text-ink-soft disabled:opacity-70"
          >
            <Check size={20} />
            {submitted ? "已提交" : "提交"}
          </button>
        </div>
      </div>
    </div>
  );
}
