import { useEffect, useRef, useState } from "react";
import { ImagePlus, Loader2, Upload, X } from "lucide-react";
import { api } from "@/lib/api";

export interface GenParamsForm {
  productName: string;
  imageUrl: string;
  coreMessage: string;
  creativeStyle: string;
  platform: string;
  ratio: string;
  resolution: string;
  durationSec: number;
  count: number;
  sound: boolean;
}

interface GenParamsDialogProps {
  open: boolean;
  /** 来自用户消息的初始创意诉求 */
  initialCoreMessage?: string;
  uploadThreadId?: string;
  onConfirm: (form: GenParamsForm) => void;
  onCancel: () => void;
}

const PLATFORMS = ["douyin", "kuaishou", "taobao", "xiaohongshu"];
const RATIOS = ["9:16", "16:9", "1:1"];
const RESOLUTIONS = ["720p", "1080p"];

function Label({ children }: { children: React.ReactNode }) {
  return <div className="mb-1 text-[12px] font-medium text-ink-soft">{children}</div>;
}

const inputCls =
  "w-full rounded-lg border border-line bg-canvas px-3 py-2 text-[13px] text-ink outline-none placeholder:text-ink-soft/60 focus:border-accent/40";

export function GenParamsDialog({ open, initialCoreMessage, uploadThreadId, onConfirm, onCancel }: GenParamsDialogProps) {
  const [f, setF] = useState<GenParamsForm>({
    productName: "",
    imageUrl: "",
    coreMessage: initialCoreMessage ?? "",
    creativeStyle: "情绪种草",
    platform: "douyin",
    ratio: "9:16",
    resolution: "1080p",
    durationSec: 15,
    count: 1,
    sound: true,
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const previewObjectUrlRef = useRef<string>("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const set = <K extends keyof GenParamsForm>(k: K, v: GenParamsForm[K]) =>
    setF((p) => ({ ...p, [k]: v }));

  useEffect(
    () => () => {
      if (previewObjectUrlRef.current) URL.revokeObjectURL(previewObjectUrlRef.current);
    },
    [],
  );

  const setLocalPreview = (file: File) => {
    if (previewObjectUrlRef.current) URL.revokeObjectURL(previewObjectUrlRef.current);
    const url = URL.createObjectURL(file);
    previewObjectUrlRef.current = url;
    setPreviewUrl(url);
  };

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setUploadError("请选择图片文件。");
      return;
    }
    if (!uploadThreadId) {
      setUploadError("当前会话还未准备好,请重新打开参数面板。");
      return;
    }
    setLocalPreview(file);
    setUploadError("");
    setUploading(true);
    try {
      const uploaded = await api.uploadThreadFiles(uploadThreadId, [file]);
      const fileInfo = uploaded.files[0];
      const publicUrl = fileInfo?.tos_url || fileInfo?.public_url || fileInfo?.borgrise_url;
      if (!publicUrl) throw new Error(uploaded.message || "上传未返回可访问的图片 URL");
      set("imageUrl", publicUrl);
      setPreviewUrl(publicUrl);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  if (!open) return null;
  const canConfirm = f.productName.trim() && f.imageUrl.trim() && f.coreMessage.trim();
  const imagePreview = previewUrl || f.imageUrl;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 p-4">
      <div className="w-full max-w-lg rounded-2xl border border-line bg-surface shadow-xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div>
            <div className="text-[15px] font-semibold text-ink">视频生成参数</div>
            <div className="mt-0.5 text-[12px] text-ink-soft">补充商品与参数,Agent 据此生成 Brief</div>
          </div>
          <button onClick={onCancel} className="text-ink-soft hover:text-ink" aria-label="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-3.5 px-5 py-4">
          <div className="flex gap-3">
            <div className="shrink-0">
              <Label>商品图</Label>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="relative flex h-[58px] w-[58px] items-center justify-center overflow-hidden rounded-lg border border-dashed border-line bg-canvas text-ink-soft hover:border-accent/40 hover:text-accent"
                aria-label="上传商品图"
              >
                {imagePreview.trim() ? (
                  <img src={imagePreview} alt="商品图" className="h-full w-full object-cover" />
                ) : (
                  <ImagePlus size={18} />
                )}
                <span className="absolute inset-x-0 bottom-0 flex h-5 items-center justify-center bg-ink/60 text-white">
                  {uploading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                </span>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => void handleFile(e.target.files?.[0])}
              />
            </div>
            <div className="min-w-0 flex-1">
              <Label>商品名 *</Label>
              <input className={inputCls} value={f.productName} onChange={(e) => set("productName", e.target.value)} placeholder="如：极简不锈钢保温杯 500ml" />
              <div className="mt-2.5">
                <Label>商品图 URL *（可上传本地图片或粘贴公网 URL)</Label>
                <input
                  className={inputCls}
                  value={f.imageUrl}
                  onChange={(e) => {
                    set("imageUrl", e.target.value);
                    setPreviewUrl("");
                    setUploadError("");
                  }}
                  placeholder="上传后自动填充,或粘贴 https://…/product.jpg"
                />
              </div>
              {uploadError && <div className="mt-1.5 text-[12px] text-rose-500">{uploadError}</div>}
              {uploading && <div className="mt-1.5 text-[12px] text-ink-soft">正在上传本地图片…</div>}
            </div>
          </div>

          <div>
            <Label>核心诉求 *</Label>
            <textarea
              className={`${inputCls} h-16 resize-none`}
              value={f.coreMessage}
              onChange={(e) => set("coreMessage", e.target.value)}
              placeholder="冬天通勤路上随时喝到热水,主打 12 小时保温"
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>创意风格</Label>
              <input className={inputCls} value={f.creativeStyle} onChange={(e) => set("creativeStyle", e.target.value)} />
            </div>
            <div>
              <Label>平台</Label>
              <select className={inputCls} value={f.platform} onChange={(e) => set("platform", e.target.value)}>
                {PLATFORMS.map((p) => <option key={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <Label>比例</Label>
              <select className={inputCls} value={f.ratio} onChange={(e) => set("ratio", e.target.value)}>
                {RATIOS.map((r) => <option key={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <Label>分辨率</Label>
              <select className={inputCls} value={f.resolution} onChange={(e) => set("resolution", e.target.value)}>
                {RESOLUTIONS.map((r) => <option key={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <Label>时长(秒)</Label>
              <input type="number" min={4} max={60} className={inputCls} value={f.durationSec} onChange={(e) => set("durationSec", Number(e.target.value))} />
            </div>
            <div>
              <Label>数量</Label>
              <input type="number" min={1} max={4} className={inputCls} value={f.count} onChange={(e) => set("count", Number(e.target.value))} />
            </div>
          </div>

          <label className="flex items-center gap-2 text-[13px] text-ink">
            <input type="checkbox" checked={f.sound} onChange={(e) => set("sound", e.target.checked)} className="accent-[var(--color-accent)]" />
            输出声音
          </label>
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
          <button onClick={onCancel} className="rounded-xl border border-line px-4 py-2 text-[14px] text-ink hover:bg-canvas">
            取消
          </button>
          <button
            onClick={() => canConfirm && onConfirm(f)}
            disabled={!canConfirm || uploading}
            className="rounded-xl bg-brand px-5 py-2 text-[14px] font-medium text-white transition-opacity disabled:opacity-30"
          >
            开始生成
          </button>
        </div>
      </div>
    </div>
  );
}
