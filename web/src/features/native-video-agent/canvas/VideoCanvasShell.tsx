import type { ReactNode } from "react";

import type { NativeCanvasHeader } from "./types";

interface VideoCanvasShellProps {
  header: NativeCanvasHeader;
  onClose?(): void;
  children: ReactNode;
  /** 移动端全屏；桌面由外层布局决定宽度。 */
  mobileFullscreen?: boolean;
  /** 为 false 时仅保留布局容器，避免与既有面板双层标题。 */
  showHeader?: boolean;
}

/** 统一右侧 Canvas 壳：名称/版本/状态/关联 Step/保存态/关闭。 */
export function VideoCanvasShell({
  header,
  onClose,
  children,
  mobileFullscreen = true,
  showHeader = true,
}: VideoCanvasShellProps) {
  const saveLabel =
    header.saveStatus === "saving"
      ? "保存中…"
      : header.saveStatus === "saved"
        ? "已保存"
        : header.saveStatus === "error"
          ? "保存失败"
          : null;

  return (
    <aside
      className={[
        "flex h-full min-w-0 flex-col border-l border-line bg-[#f8fafc]",
        mobileFullscreen
          ? "fixed inset-0 z-50 w-full xl:static xl:z-auto xl:w-[52vw] xl:min-w-[680px]"
          : "w-full xl:w-[52vw] xl:min-w-[680px]",
      ].join(" ")}
      data-native-canvas-shell="true"
      data-canvas-kind-title={header.title}
    >
      {showHeader ? (
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
          <div className="min-w-0 space-y-1">
            <h2 className="truncate text-[15px] font-semibold text-slate-900">{header.title}</h2>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
              {header.versionLabel ? <span>{header.versionLabel}</span> : null}
              {header.statusLabel ? <span>{header.statusLabel}</span> : null}
              {header.stepLabel ? <span>关联：{header.stepLabel}</span> : null}
              {saveLabel ? <span>{saveLabel}</span> : null}
              {(header.dirtySceneCount ?? 0) > 0 ? (
                <span className="text-amber-700">待重生 {header.dirtySceneCount} 镜</span>
              ) : null}
              {header.regenerateComplete ? (
                <span className="text-emerald-700">重新生成完成</span>
              ) : null}
            </div>
          </div>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded-lg border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 hover:bg-slate-50"
            >
              关闭
            </button>
          ) : null}
        </header>
      ) : null}
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </aside>
  );
}
