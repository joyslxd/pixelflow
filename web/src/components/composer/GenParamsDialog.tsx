import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Check, ChevronUp, FilePenLine, Upload, X } from "lucide-react";
import { api, type UploadedAttachment } from "@/lib/api";

export type CreationIntent = "video" | "image" | "ppt";

export interface VideoRequirementForm {
  intent: "video";
  product_info: string;
  product_category: string;
  target_audience: string;
  conversion_goal: string;
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

const IMAGE_TYPES = ["商品广告图", "人物/场景图", "海报/封面图", "插画/概念图", "背景/素材图", "其他"];
const IMAGE_USAGES = ["广告投放", "社媒发布", "内容封面", "详情页配图", "活动宣传", "内部展示", "其他用途"];
const IMAGE_STYLES = ["真实摄影", "高级质感", "简洁干净", "小红书风", "科技感", "插画风", "自由发挥"];
const IMAGE_SIZES = ["1:1", "16:9", "9:16", "自动适配"];
const PPT_CUSTOM_STYLE = "自定义";
const PPT_STYLES = ["极简商务", "科技数据", "教育培训", "产品发布", "投融资路演", "自定义"];
const PPT_ACCEPT = ".doc,.docx,.xls,.xlsx,.pdf";

const inputCls =
  "h-12 w-full rounded-xl border border-line bg-surface px-4 text-[14px] text-ink outline-none placeholder:text-ink-soft/55 focus:border-accent/40";

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

function videoInitialValues(initialCoreMessage: string | undefined, values: Record<string, unknown>): VideoRequirementForm {
  return {
    intent: "video",
    product_info: textValue(values, "product_info", initialCoreMessage ?? ""),
    product_category: textValue(values, "product_category"),
    target_audience: textValue(values, "target_audience"),
    conversion_goal: optionValue(values, "conversion_goal", VIDEO_GOALS, "引流直播间"),
  };
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
  const [image, setImage] = useState<ImageRequirementForm>(() => imageInitialValues(initialCoreMessage, initialValues));
  const [ppt, setPpt] = useState<PptRequirementForm>(() => pptInitialValues(initialCoreMessage, initialValues, initialMaterials));
  const [pptStyleMode, setPptStyleMode] = useState(() => pptStyleModeValue(ppt.ppt_style));
  const [pptCustomStyle, setPptCustomStyle] = useState(() => pptCustomStyleValue(ppt.ppt_style));

  useEffect(() => {
    if (!open) return;
    setSubmitted(false);
    setCollapsed(false);
    setUploadError("");
    setVideo(videoInitialValues(initialCoreMessage, initialValues));
    setImage(imageInitialValues(initialCoreMessage, initialValues));
    const nextPpt = pptInitialValues(initialCoreMessage, initialValues, initialMaterials);
    setPpt(nextPpt);
    setPptStyleMode(pptStyleModeValue(nextPpt.ppt_style));
    setPptCustomStyle(pptCustomStyleValue(nextPpt.ppt_style));
  }, [open, intent, initialCoreMessage, initialValues, initialMaterials]);

  if (!open) return null;

  const isVideo = intent === "video";
  const isPpt = intent === "ppt";
  const canConfirm = isVideo
    ? Boolean(video.product_info.trim() && video.product_category.trim() && video.target_audience.trim() && video.conversion_goal)
    : isPpt
      ? Boolean(ppt.ppt_topic.trim() && ppt.ppt_style && ppt.attachments.length > 0 && !uploading)
      : Boolean(image.image_goal.trim() && image.image_type && image.image_usage && image.image_style && image.image_size);

  const submit = () => {
    if (!canConfirm) return;
    setSubmitted(true);
    onConfirm(isVideo ? video : isPpt ? ppt : image);
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
      for (const file of Array.from(files)) {
        if (!/\.(docx?|xlsx?|pdf)$/i.test(file.name)) {
          setUploadError("附件仅支持 Word、Excel、PDF 文件。");
          continue;
        }
        uploads.push(await api.uploadAttachment(file));
      }
      if (uploads.length) {
        setPpt((prev) => ({ ...prev, attachments: officeAttachments(prev.attachments.concat(uploads)) }));
      }
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
                    <span>{uploading ? "上传中..." : "上传 Word、Excel、PDF，可上传多个"}</span>
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
