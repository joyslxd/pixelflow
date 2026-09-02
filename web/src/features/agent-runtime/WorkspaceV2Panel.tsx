/** Workspace V2 四层只读投影；所有业务修改均通过对话交由 Agent 执行。 */

import { useMemo } from "react";

import { WorkspaceAssetThumbnail } from "./WorkspaceAssetThumbnail";
import { WorkspaceScenePreview } from "./WorkspaceScenePreview";
import { generationJobCounts, projectWorkspaceV2 } from "./workspaceV2";

type Props = {
  summary: Record<string, unknown>;
  conversationId: string;
  workspaceId: string;
  revision: number;
};

const creativeLabels: Record<string, string> = {
  brand: "品牌", product: "产品", audience: "受众", platform: "平台", aspect_ratio: "画幅",
  target_duration_sec: "目标总时长（秒）", audio: "声音方案", cta: "CTA", creative_direction: "创意方向",
  tone: "调性", visual_style: "视觉风格", delivery: "交付要求", reference_strategy: "参考素材策略",
};
const narrativeLabels: Record<string, string> = {
  concept: "创意概念", character_arc: "人物弧线", era: "时代设定", narration: "旁白",
  dialogue: "对白", sound: "声音骨架", brand_closure: "品牌收束", script: "脚本大纲", outline: "脚本大纲",
};
const primaryNarrativeKeys = ["concept", "script", "outline", "narration", "dialogue", "sound", "brand_closure"];
const optionalNarrativeKeys = ["character_arc", "era"];

function sectionTitle(title: string) {
  return <h3 className="text-sm font-semibold text-ink">{title}</h3>;
}

function orderedFieldKeys(source: Record<string, string | number>, labels: Record<string, string>): string[] {
  /** 固定字段优先，再呈现 Gateway 允许的扩展字段，防止创意/脚本细节无声丢失。 */

  return [...Object.keys(labels), ...Object.keys(source).filter((key) => !(key in labels))];
}

function statusLabel(status: string): string {
  return (({ planned: "已规划", generating: "生成中", ready: "已就绪", failed: "失败", queued: "等待调度", polling: "处理中", succeeded: "已完成", paused: "已暂停", starting: "处理中", indeterminate: "失败" } as Record<string, string>)[status.toLowerCase()] ?? status) || "未知";
}

function assetOriginLabel(origin: string): string {
  return ({ existing_material: "已有素材", planned_generation: "待生成素材", provider_output: "已生成素材" } as Record<string, string>)[origin] ?? "待生成素材";
}

function AssetStatusMark({ state }: { state: string }) {
  if (state === "generating") {
    return <div className="grid h-16 w-16 shrink-0 place-items-center rounded-lg border border-accent/40 bg-accent-soft"><span className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" aria-label="生成中" /></div>;
  }
  if (state === "failed") {
    return <div className="grid h-16 w-16 shrink-0 place-items-center rounded-lg border border-red-200 bg-red-50 text-[10px] text-red-700">失败</div>;
  }
  if (state === "ready") {
    return <div className="grid h-16 w-16 shrink-0 place-items-center rounded-lg border border-emerald-200 bg-emerald-50 text-[10px] text-emerald-800">已生成</div>;
  }
  return <div className="grid h-16 w-16 shrink-0 place-items-center rounded-lg border border-dashed border-line bg-canvas text-[10px] text-ink-soft">待生成</div>;
}

export function WorkspaceV2Panel({ summary, conversationId, workspaceId, revision }: Props) {
  const projection = useMemo(() => projectWorkspaceV2(summary), [summary]);
  const counts = generationJobCounts(projection.generationJobs);
  const creativeFieldKeys = orderedFieldKeys(projection.creativeBrief, creativeLabels).filter((key) => projection.creativeBrief[key] !== undefined);
  const narrativeFieldKeys = orderedFieldKeys(projection.narrativePlan, narrativeLabels).filter((key) => Boolean(projection.narrativePlan[key]?.trim()));
  const visiblePrimaryNarrativeKeys = narrativeFieldKeys.filter(
    (key) => primaryNarrativeKeys.includes(key),
  );
  const visibleOptionalNarrativeKeys = narrativeFieldKeys.filter(
    (key) => optionalNarrativeKeys.includes(key),
  );
  const visibleExtendedNarrativeKeys = narrativeFieldKeys.filter(
    (key) => !primaryNarrativeKeys.includes(key) && !optionalNarrativeKeys.includes(key),
  );
  const narrativeFact = (key: string) => <article key={key} className="rounded bg-surface p-3"><p className="font-medium text-ink">{narrativeLabels[key] ?? key}</p><p className="mt-1 whitespace-pre-wrap leading-6">{projection.narrativePlan[key]}</p></article>;

  return (
    <div className="mt-2 space-y-4 text-xs text-ink-soft">
      <p>Workspace V{projection.schemaVersion} · revision {revision}</p>
      <p className="rounded border border-accent/20 bg-accent-soft p-3 text-accent">工作空间仅用于查看。要修改创意、脚本、资产或分镜，请在左侧对话中直接描述你的要求。</p>
      {projection.awaitingProductionConstraints ? <p className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">存在待确认的生产约束。</p> : null}

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle("创意与生产约束")}
        <div className="grid gap-2 sm:grid-cols-2">
          {creativeFieldKeys.map((key) => (
            <article key={key} className="rounded bg-surface p-3">
              <p>{creativeLabels[key] ?? key}</p>
              <p className="mt-1 break-words font-medium text-ink">{projection.creativeBrief[key]}</p>
            </article>
          ))}
        </div>
        {creativeFieldKeys.length === 0 ? <p>暂无已确认的生产约束。</p> : null}
      </section>

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle("叙事与脚本")}
        {visiblePrimaryNarrativeKeys.map(narrativeFact)}
        {visiblePrimaryNarrativeKeys.length === 0 ? <p>尚未填写叙事约束。</p> : null}
        {visibleOptionalNarrativeKeys.length > 0 ? (
          <details className="rounded bg-surface p-2">
            <summary className="cursor-pointer text-ink">人物与世界观</summary>
            <div className="mt-2 space-y-2">{visibleOptionalNarrativeKeys.map(narrativeFact)}</div>
          </details>
        ) : null}
        {visibleExtendedNarrativeKeys.length > 0 ? (
          <details className="rounded bg-surface p-2">
            <summary className="cursor-pointer text-ink">更多叙事约束</summary>
            <div className="mt-2 space-y-2">{visibleExtendedNarrativeKeys.map(narrativeFact)}</div>
          </details>
        ) : null}
      </section>

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle(`资产注册表（${projection.assets.length}）`)}
        {projection.assets.map((asset) => (
          <article key={asset.assetId} className="flex gap-3 rounded bg-surface p-3">
            {asset.state === "ready"
              ? <WorkspaceAssetThumbnail conversationId={conversationId} workspaceId={workspaceId} assetId={asset.assetId} alt={asset.role} revision={revision} />
              : <AssetStatusMark state={asset.state} />}
            <div className="min-w-0">
              <p className="font-medium text-ink">{asset.slot} · {asset.role}</p>
              <p>{assetOriginLabel(asset.origin)} · {asset.kind} · {statusLabel(asset.state)} · {asset.usableForVideo ? "可用于视频" : "暂不可用于视频"}</p>
              {asset.generationPrompt ? <details><summary>资产生成提示词</summary><p className="mt-1 whitespace-pre-wrap">{asset.generationPrompt}</p></details> : null}
              {asset.referenceAssetIds.length > 0 ? <p>参考：{asset.referenceAssetIds.join("、")}</p> : null}
              {asset.generationStatus ? <p>生成任务：{statusLabel(asset.generationStatus)}</p> : null}
              {asset.failureReasonCode ? <p>失败码：{asset.failureReasonCode}</p> : null}
            </div>
          </article>
        ))}
        {projection.assets.length === 0 ? <p>暂无资产注册记录。</p> : null}
      </section>

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle(`Prompt Package（${projection.packages.length} 段）`)}
        <p>这是生成前的最终可执行层：继承上述叙事约束，并由 Seedance 2.5 Skill 优化。</p>
        <p>总时长：{projection.packages.reduce((total, item) => total + (item.durationSec ?? 0), 0)} 秒</p>
        <ol className="space-y-2">
          {projection.packages.map((item) => (
            <li key={item.segmentId} className="rounded bg-surface p-2">
              <p className="font-medium text-ink">{item.sequence}. {item.segmentId} · {item.durationSec ?? "—"} 秒 · {item.generationMode} · {statusLabel(item.state)}</p>
              {item.hasPreview ? (
                <WorkspaceScenePreview
                  src={item.previewUrl}
                  title={`${item.sequence}. ${item.segmentId}`}
                />
              ) : item.state === "generating" ? (
                <div className="mt-2 grid h-24 place-items-center rounded-lg border border-accent/40 bg-accent-soft">
                  <span className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" aria-label="成片生成中" />
                </div>
              ) : item.state === "failed" ? (
                <div className="mt-2 grid h-24 place-items-center rounded-lg border border-red-200 bg-red-50 text-[10px] text-red-700">成片失败</div>
              ) : null}
              {item.promptSummary ? <details><summary>已优化 Prompt{item.promptCharCount !== null ? `（${item.promptCharCount} 字）` : ""}</summary><p className="mt-1 whitespace-pre-wrap">{item.promptSummary}</p>{item.promptTruncated ? <p className="mt-1 text-amber-800">当前仅显示前 8,000 字；请拆分该段或使用单段详情读取完整文本。</p> : null}</details> : null}
              {item.referenceAssetIds.length > 0 ? <p>引用：{item.referenceAssetIds.join("、")}</p> : null}
              {[item.continuityFrom && `承接 ${item.continuityFrom}`, item.transitionOut && `转场 ${item.transitionOut}`, item.era, item.camera, item.sound].filter(Boolean).map((detail) => <p key={detail}>{detail}</p>)}
              {item.hardConstraints.length > 0 ? <p>约束：{item.hardConstraints.join("；")}</p> : null}
            </li>
          ))}
        </ol>
        {projection.packages.length === 0 ? <p>暂无 Prompt Package。</p> : null}
      </section>

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle("GenerationJob 进度")}
        <p>任务 {projection.generationJobs.length} · 等待 {counts.queued} · 处理中 {counts.polling} · 完成 {counts.succeeded} · 失败 {counts.failed} · 暂停 {counts.paused}</p>
        {projection.generationJobs.map((job) => <p key={job.jobId} className="rounded bg-surface p-2">{job.itemId || job.jobId} · {job.kind} · {statusLabel(job.status)}</p>)}
        {projection.generationJobs.length === 0 ? <p>暂无生成任务。</p> : null}
      </section>

      {projection.mergedReady ? (
        <section className="space-y-2 rounded bg-canvas p-3">
          {sectionTitle("合并成片")}
          {projection.mergedPreviewUrl ? (
            <WorkspaceScenePreview src={projection.mergedPreviewUrl} title="合并成片" />
          ) : (
            <p>成片已生成，当前预览地址不可播放。</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
