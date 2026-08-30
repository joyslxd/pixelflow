/** Workspace V2 四层面板：只展示安全投影，用户编辑统一走 revision CAS Command。 */

import { useEffect, useMemo, useState } from "react";

import type { PublicOperationV1 } from "@/api/contracts";

import { operationCounts, projectWorkspaceV2 } from "./workspaceV2";

type Props = {
  summary: Record<string, unknown>;
  revision: number;
  operations: PublicOperationV1[];
  onApplyPatch: (patch: Record<string, unknown>) => Promise<void>;
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

function fields(source: Record<string, string | number>): Record<string, string> {
  return Object.fromEntries(Object.entries(source).map(([key, value]) => [key, String(value)]));
}

function sectionTitle(title: string) {
  return <h3 className="text-sm font-semibold text-ink">{title}</h3>;
}

function orderedFieldKeys(source: Record<string, string | number>, labels: Record<string, string>): string[] {
  /** 固定字段优先，再呈现 Gateway 允许的扩展字段，防止创意/脚本细节无声丢失。 */

  return [...Object.keys(labels), ...Object.keys(source).filter((key) => !(key in labels))];
}

function statusLabel(status: string): string {
  return (({ planned: "已规划", generating: "生成中", ready: "已就绪", failed: "失败", queued: "等待调度", polling: "处理中", succeeded: "已完成", paused: "已暂停" } as Record<string, string>)[status.toLowerCase()] ?? status) || "未知";
}

function assetOriginLabel(origin: string): string {
  return ({ existing_material: "已有素材", planned_generation: "待生成素材", provider_output: "已生成素材" } as Record<string, string>)[origin] ?? "待生成素材";
}

export function WorkspaceV2Panel({ summary, revision, operations, onApplyPatch }: Props) {
  const projection = useMemo(() => projectWorkspaceV2(summary), [summary]);
  const [creativeDraft, setCreativeDraft] = useState(() => fields(projection.creativeBrief));
  const [narrativeDraft, setNarrativeDraft] = useState(() => projection.narrativePlan);
  const [creativeDirty, setCreativeDirty] = useState(false);
  const [narrativeDirty, setNarrativeDirty] = useState(false);
  const [saving, setSaving] = useState<"creative" | "narrative" | null>(null);
  const [conflict, setConflict] = useState("");
  const [addingNarrative, setAddingNarrative] = useState(false);
  const [narrativeKeyToAdd, setNarrativeKeyToAdd] = useState("concept");

  useEffect(() => {
    if (!creativeDirty) setCreativeDraft(fields(projection.creativeBrief));
  }, [creativeDirty, projection.creativeBrief, revision]);
  useEffect(() => {
    if (!narrativeDirty) setNarrativeDraft(projection.narrativePlan);
  }, [narrativeDirty, projection.narrativePlan, revision]);

  const save = async (kind: "creative" | "narrative") => {
    if (saving !== null) return;
    setSaving(kind);
    setConflict("");
    try {
      if (kind === "creative") {
        const targetDuration = Number(creativeDraft.target_duration_sec);
        await onApplyPatch({
          creative_brief: {
            ...creativeDraft,
            ...(Number.isInteger(targetDuration) && targetDuration > 0 ? { target_duration_sec: targetDuration } : {}),
          },
        });
        setCreativeDirty(false);
      } else {
        await onApplyPatch({ narrative_plan: narrativeDraft });
        setNarrativeDirty(false);
      }
    } catch {
      // Hook 会刷新权威 revision；此处保留草稿供用户合并后再次提交。
      setConflict("Workspace 已被更新，本地草稿已保留。请对照最新内容合并后重试。");
    } finally {
      setSaving(null);
    }
  };

  const counts = operationCounts(projection.batches);
  const childTotal = Object.values(counts).reduce((total, value) => total + value, 0);
  const creativeFieldKeys = orderedFieldKeys(creativeDraft, creativeLabels);
  const narrativeFieldKeys = orderedFieldKeys(narrativeDraft, narrativeLabels);
  const visiblePrimaryNarrativeKeys = narrativeFieldKeys.filter(
    (key) => primaryNarrativeKeys.includes(key) && Boolean(narrativeDraft[key]?.trim()),
  );
  const visibleOptionalNarrativeKeys = narrativeFieldKeys.filter(
    (key) => optionalNarrativeKeys.includes(key) && Boolean(narrativeDraft[key]?.trim()),
  );
  const visibleExtendedNarrativeKeys = narrativeFieldKeys.filter(
    (key) => !primaryNarrativeKeys.includes(key) && !optionalNarrativeKeys.includes(key) && Boolean(narrativeDraft[key]?.trim()),
  );
  const editNarrativeField = (key: string, value: string) => {
    setNarrativeDirty(true);
    setNarrativeDraft((current) => ({ ...current, [key]: value }));
  };
  const narrativeEditor = (key: string) => (
    <label key={key} className="block space-y-1">
      <span>{narrativeLabels[key] ?? key}</span>
      <textarea
        className={`w-full rounded border border-line bg-surface p-2 text-ink ${key === "script" || key === "outline" ? "min-h-32" : "min-h-16"}`}
        maxLength={key === "script" || key === "outline" ? 8_000 : 2_000}
        value={narrativeDraft[key] ?? ""}
        onChange={(event) => editNarrativeField(key, event.target.value)}
      />
    </label>
  );

  return (
    <div className="mt-2 space-y-4 text-xs text-ink-soft">
      <p>Workspace V{projection.schemaVersion} · revision {revision}</p>
      {projection.awaitingProductionConstraints ? <p className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">存在待确认的生产约束。</p> : null}
      {conflict ? <p className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900" role="alert">{conflict}</p> : null}

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle("创意与生产约束")}
        <div className="grid gap-2 sm:grid-cols-2">
          {creativeFieldKeys.map((key) => (
            <label key={key} className="space-y-1">
              <span>{creativeLabels[key] ?? key}</span>
              <input
                className="w-full rounded border border-line bg-surface px-2 py-1 text-ink"
                value={creativeDraft[key] ?? ""}
                onChange={(event) => { setCreativeDirty(true); setCreativeDraft((current) => ({ ...current, [key]: event.target.value })); }}
              />
            </label>
          ))}
        </div>
        <button className="rounded border border-line px-2 py-1 disabled:opacity-50" disabled={saving !== null || !creativeDirty} onClick={() => void save("creative")}>
          {saving === "creative" ? "保存中…" : `保存约束（r${revision}）`}
        </button>
      </section>

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle("叙事与脚本")}
        {visiblePrimaryNarrativeKeys.map(narrativeEditor)}
        {visiblePrimaryNarrativeKeys.length === 0 ? <p>尚未填写叙事约束。</p> : null}
        {visibleOptionalNarrativeKeys.length > 0 ? (
          <details className="rounded bg-surface p-2">
            <summary className="cursor-pointer text-ink">人物与世界观</summary>
            <div className="mt-2 space-y-2">{visibleOptionalNarrativeKeys.map(narrativeEditor)}</div>
          </details>
        ) : null}
        {visibleExtendedNarrativeKeys.length > 0 ? (
          <details className="rounded bg-surface p-2">
            <summary className="cursor-pointer text-ink">更多叙事约束</summary>
            <div className="mt-2 space-y-2">{visibleExtendedNarrativeKeys.map(narrativeEditor)}</div>
          </details>
        ) : null}
        {addingNarrative ? (
          <div className="space-y-2 rounded border border-line bg-surface p-2">
            <label className="block space-y-1">
              <span>选择要添加的叙事约束</span>
              <select className="w-full rounded border border-line bg-surface px-2 py-1 text-ink" value={narrativeKeyToAdd} onChange={(event) => setNarrativeKeyToAdd(event.target.value)}>
                {Object.entries(narrativeLabels).filter(([key]) => !narrativeDraft[key]?.trim()).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
            </label>
            {narrativeEditor(narrativeKeyToAdd)}
            <button className="rounded border border-line px-2 py-1" onClick={() => setAddingNarrative(false)}>完成添加</button>
          </div>
        ) : (
          <button className="rounded border border-line px-2 py-1" onClick={() => setAddingNarrative(true)}>添加叙事约束</button>
        )}
        <button className="rounded border border-line px-2 py-1 disabled:opacity-50" disabled={saving !== null || !narrativeDirty} onClick={() => void save("narrative")}>
          {saving === "narrative" ? "保存中…" : `保存叙事与脚本（r${revision}）`}
        </button>
      </section>

      <section className="space-y-2 rounded bg-canvas p-3">
        {sectionTitle(`资产注册表（${projection.assets.length}）`)}
        {projection.assets.map((asset) => (
          <article key={asset.assetId} className="rounded bg-surface p-2">
            <p className="font-medium text-ink">{asset.slot} · {asset.role}</p>
            <p>{assetOriginLabel(asset.origin)} · {asset.kind} · {statusLabel(asset.state)} · {asset.usableForVideo ? "可用于视频" : "暂不可用于视频"}</p>
            {asset.generationPrompt ? <details><summary>资产生成提示词</summary><p className="mt-1 whitespace-pre-wrap">{asset.generationPrompt}</p></details> : null}
            {asset.referenceAssetIds.length > 0 ? <p>参考：{asset.referenceAssetIds.join("、")}</p> : null}
            {asset.operationStatus ? <p>生成任务：{statusLabel(asset.operationStatus)}</p> : null}
            {asset.artifactRef ? <p>Artifact：{asset.artifactRef}</p> : null}
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
        {sectionTitle("M06 多批次进度")}
        <p>Batch {projection.batches.length} · 子 Operation {childTotal} · 等待 {counts.queued} · 处理中 {counts.polling} · 完成 {counts.succeeded} · 失败 {counts.failed} · 暂停 {counts.paused}</p>
        {projection.batches.map((batch) => <p key={batch.batchId} className="rounded bg-surface p-2">{batch.batchId} · {statusLabel(batch.status)} · {batch.children.length} 个子 Operation</p>)}
        {operations.map((operation) => <p key={operation.operation_id} className="rounded bg-surface p-2">实时 Operation {operation.operation_id} · {statusLabel(operation.status)}{operation.completed !== null && operation.total !== null ? ` · ${operation.completed}/${operation.total}` : ""}</p>)}
        {projection.batches.length === 0 && operations.length === 0 ? <p>暂无 Operation。</p> : null}
      </section>

      {projection.outputs.length > 0 ? <section className="space-y-2 rounded bg-canvas p-3">{sectionTitle("输出")} {projection.outputs.map((output) => <p key={output.outputId} className="rounded bg-surface p-2">{output.title} · {output.kind} · {statusLabel(output.status)}</p>)}</section> : null}
    </div>
  );
}
