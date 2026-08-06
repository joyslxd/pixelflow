import { Clock3, Film, PencilLine } from "lucide-react";

import type { VideoAgentSceneEvidence } from "./state/workspace";

interface SceneEvidencePanelProps {
  revision: number;
  scene: VideoAgentSceneEvidence | null;
  scenes?: readonly VideoAgentSceneEvidence[];
  selectedSceneId?: string | null;
  onSelectScene?(sceneId: string): void;
  onEditScene?(sceneId: string): void;
}

export function SceneEvidencePanel({
  revision,
  scene,
  scenes = [],
  selectedSceneId = scene?.sceneId ?? null,
  onSelectScene,
  onEditScene,
}: SceneEvidencePanelProps) {
  if (!scene) {
    return (
      <aside aria-label="镜头证据" className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-500">
        选择一个分镜后查看画面、质检问题和关联素材。
      </aside>
    );
  }

  return (
    <aside aria-label="镜头证据" data-workspace-revision={revision} className="flex min-h-0 w-[440px] shrink-0 flex-col gap-4 overflow-y-auto border-l border-slate-200 bg-white p-4">
      {scenes.length > 1 ? (
        <nav aria-label="选择分镜" className="grid grid-cols-2 gap-2">
          {scenes.map((item) => (
            <button
              key={item.sceneId}
              type="button"
              aria-pressed={item.sceneId === selectedSceneId}
              onClick={() => onSelectScene?.(item.sceneId)}
              disabled={!onSelectScene}
              className="min-w-0 rounded-lg border border-slate-200 px-3 py-2 text-left text-sm enabled:hover:bg-slate-50 aria-pressed:border-sky-500 aria-pressed:bg-sky-50 disabled:cursor-default"
            >
              <span className="block text-xs text-slate-500">分镜 {item.sceneIndex}</span>
              <span className="block truncate font-medium text-slate-800">{item.title}</span>
            </button>
          ))}
        </nav>
      ) : null}
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-slate-500">分镜 {scene.sceneIndex} · revision {revision}</p>
          <h2 className="truncate text-base font-semibold text-slate-900">{scene.title}</h2>
        </div>
        {onEditScene ? (
          <button
            type="button"
            onClick={() => onEditScene(scene.sceneId)}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          >
            <PencilLine className="size-4" />
            编辑此镜头
          </button>
        ) : null}
      </header>

      <div className="overflow-hidden rounded-lg bg-slate-950">
        {scene.mediaUrl ? (
          <video src={scene.mediaUrl} controls preload="metadata" className="aspect-video w-full object-contain" />
        ) : (
          <div className="flex aspect-video items-center justify-center text-slate-400">
            <Film className="mr-2 size-5" />暂无已选视频
          </div>
        )}
      </div>

      {scene.editStatus === "重新生成完成" ? (
        <div className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          <Clock3 className="size-4" />
          <span>重新生成完成{scene.regeneratedAt ? ` · ${scene.regeneratedAt}` : ""}</span>
        </div>
      ) : null}

      <section>
        <h3 className="text-sm font-medium text-slate-800">质检问题</h3>
        {scene.issues.length > 0 ? (
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">
            {scene.issues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        ) : <p className="mt-2 text-sm text-slate-500">当前没有未展示的质检问题。</p>}
        {scene.repairSuggestion ? (
          <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
            修复建议：{scene.repairSuggestion}
          </p>
        ) : null}
      </section>

      <section>
        <h3 className="text-sm font-medium text-slate-800">历史版本与关联素材</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          {scene.variants.map((variant) => (
            <span key={variant.variantId} className="rounded-full border border-slate-200 px-2.5 py-1 text-xs text-slate-600">
              {variant.variantId}{variant.selected ? " · 当前选用" : ""}
            </span>
          ))}
          {scene.artifactRefs.map((reference) => (
            <code key={reference} className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">{reference}</code>
          ))}
        </div>
      </section>
    </aside>
  );
}
