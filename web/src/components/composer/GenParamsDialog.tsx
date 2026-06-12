import { useState } from "react";
import { ImagePlus, X } from "lucide-react";

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

export function GenParamsDialog({ open, initialCoreMessage, onConfirm, onCancel }: GenParamsDialogProps) {
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
  const set = <K extends keyof GenParamsForm>(k: K, v: GenParamsForm[K]) =>
    setF((p) => ({ ...p, [k]: v }));

  if (!open) return null;
  const canConfirm = f.productName.trim() && f.imageUrl.trim() && f.coreMessage.trim();

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
              {f.imageUrl.trim() ? (
                <img src={f.imageUrl} alt="商品图" className="h-[58px] w-[58px] rounded-lg border border-line object-cover" />
              ) : (
                <div className="flex h-[58px] w-[58px] items-center justify-center rounded-lg border border-dashed border-line text-ink-soft">
                  <ImagePlus size={18} />
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <Label>商品名 *</Label>
              <input className={inputCls} value={f.productName} onChange={(e) => set("productName", e.target.value)} placeholder="如：极简不锈钢保温杯 500ml" />
              <div className="mt-2.5">
                <Label>商品图公网 URL *（供生成调用)</Label>
                <input className={inputCls} value={f.imageUrl} onChange={(e) => set("imageUrl", e.target.value)} placeholder="https://…/product.jpg" />
              </div>
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
            disabled={!canConfirm}
            className="rounded-xl bg-brand px-5 py-2 text-[14px] font-medium text-white transition-opacity disabled:opacity-30"
          >
            开始生成
          </button>
        </div>
      </div>
    </div>
  );
}
