import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Check, ChevronUp, FilePenLine, X } from "lucide-react";

export type CreationIntent = "video" | "image";

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

export type GenParamsForm = VideoRequirementForm | ImageRequirementForm;

interface GenParamsDialogProps {
  open: boolean;
  intent: CreationIntent;
  /** 来自用户消息的初始创意诉求 */
  initialCoreMessage?: string;
  /** LLM 从用户提示词中自动抽取的表单初值 */
  initialValues?: Record<string, unknown>;
  onConfirm: (form: GenParamsForm) => void;
  onCancel: () => void;
}

const VIDEO_CATEGORIES = ["美妆护肤", "食品饮料", "数码3C", "服饰鞋包", "家居日用", "保健养生", "其他品类"];
const VIDEO_GOALS = ["直接购买", "品牌曝光", "种草引流", "引流直播间"];

const IMAGE_TYPES = ["商品广告图", "人物/场景图", "海报/封面图", "插画/概念图", "背景/素材图", "其他"];
const IMAGE_USAGES = ["广告投放", "社媒发布", "内容封面", "详情页配图", "活动宣传", "内部展示", "其他用途"];
const IMAGE_STYLES = ["真实摄影", "高级质感", "简洁干净", "小红书风", "科技感", "插画风", "自由发挥"];
const IMAGE_SIZES = ["1:1", "16:9", "9:16", "自动适配"];

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
    product_category: optionValue(values, "product_category", VIDEO_CATEGORIES, "数码3C"),
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

export function GenParamsDialog({ open, intent, initialCoreMessage, initialValues = {}, onConfirm, onCancel }: GenParamsDialogProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [video, setVideo] = useState<VideoRequirementForm>(() => videoInitialValues(initialCoreMessage, initialValues));
  const [image, setImage] = useState<ImageRequirementForm>(() => imageInitialValues(initialCoreMessage, initialValues));

  useEffect(() => {
    if (!open) return;
    setSubmitted(false);
    setCollapsed(false);
    setVideo(videoInitialValues(initialCoreMessage, initialValues));
    setImage(imageInitialValues(initialCoreMessage, initialValues));
  }, [open, intent, initialCoreMessage, initialValues]);

  if (!open) return null;

  const isVideo = intent === "video";
  const canConfirm = isVideo
    ? Boolean(video.product_info.trim() && video.product_category && video.target_audience.trim() && video.conversion_goal)
    : Boolean(image.image_goal.trim() && image.image_type && image.image_usage && image.image_style && image.image_size);

  const submit = () => {
    if (!canConfirm) return;
    setSubmitted(true);
    onConfirm(isVideo ? video : image);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/25 p-5">
      <div className="max-h-[88vh] w-full max-w-[980px] overflow-y-auto rounded-[22px] border border-line bg-[#fbfbfc] p-8 shadow-xl">
        <div className="mb-8 flex items-center justify-between">
          <div className="flex items-center gap-3 text-[22px] font-semibold text-ink">
            <FilePenLine size={26} />
            {isVideo ? "AD投放短视频需求收集" : "图片生成需求收集"}
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
                  <PillGroup options={VIDEO_CATEGORIES} value={video.product_category} onChange={(v) => setVideo((p) => ({ ...p, product_category: v }))} />
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
