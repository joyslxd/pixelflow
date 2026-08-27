/** 基于 Gateway Snapshot 的视频业务面板；编辑命令仍回到 Gateway。 */

import { useEffect, useState } from "react";

type Summary = Record<string, unknown>;

type VideoWorkspaceSnapshotPanelProps = {
  summary: Summary;
  revision: number;
  onCancelQuotaInterrupt: () => void;
  onUpdateScript: (content: string) => Promise<void>;
  onUpdatePlanPublicGoal: (
    planId: string,
    expectedRevision: number,
    publicGoal: string | null,
  ) => Promise<void>;
};

function text(value: unknown, fallback = "—"): string {
  /** 将公开摘要中的标量安全显示为文本，未知结构不参与界面渲染。 */

  return typeof value === "string" && value.trim() ? value : fallback;
}

function count(value: unknown): number {
  /** 只接受非负整数的公开计数，防止异常 Snapshot 伪造业务进度。 */

  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : 0;
}

function records(value: unknown): Array<Record<string, unknown>> {
  /** 仅接受数组内普通对象，浏览器不解释未冻结的业务 payload。 */

  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    : [];
}

function PanelTitle({ children }: { children: string }) {
  return <h3 className="text-xs font-semibold text-ink">{children}</h3>;
}

/** 将同一 revision 的脚本、计划、素材与分镜摘要投影为首批业务面板。 */
export function VideoWorkspaceSnapshotPanel({
  summary,
  revision,
  onCancelQuotaInterrupt,
  onUpdateScript,
  onUpdatePlanPublicGoal,
}: VideoWorkspaceSnapshotPanelProps) {
  const plan = summary.active_plan;
  const planRecord = typeof plan === "object" && plan !== null ? plan as Record<string, unknown> : null;
  const scriptMissing = Array.isArray(summary.script_missing_requirements)
    ? summary.script_missing_requirements.filter((item): item is string => typeof item === "string")
    : [];
  const quotaInterruptId = typeof summary.quota_interrupt_id === "string" ? summary.quota_interrupt_id : "";
  const scriptContent = typeof summary.script_editor_content === "string" ? summary.script_editor_content : "";
  const planId = typeof planRecord?.plan_id === "string" ? planRecord.plan_id : "";
  const planRevision = typeof planRecord?.revision === "number" && Number.isInteger(planRecord.revision)
    ? planRecord.revision
    : 0;
  const planGoal = typeof planRecord?.goal === "string" ? planRecord.goal : "";
  const [scriptDraft, setScriptDraft] = useState(scriptContent);
  const [planGoalDraft, setPlanGoalDraft] = useState(planGoal);
  const [savingScript, setSavingScript] = useState(false);
  const [savingPlan, setSavingPlan] = useState(false);

  useEffect(() => setScriptDraft(scriptContent), [scriptContent, revision]);
  useEffect(() => setPlanGoalDraft(planGoal), [planGoal, planRevision]);

  const saveScript = async () => {
    if (!scriptDraft.trim() || savingScript) return;
    setSavingScript(true);
    try {
      await onUpdateScript(scriptDraft);
    } finally {
      setSavingScript(false);
    }
  };

  const savePlan = async () => {
    if (!planId || planRevision < 1 || savingPlan) return;
    setSavingPlan(true);
    try {
      await onUpdatePlanPublicGoal(planId, planRevision, planGoalDraft.trim() || null);
    } finally {
      setSavingPlan(false);
    }
  };

  return (
    <div className="mt-2 space-y-4 text-xs text-ink-soft">
      <p>Workspace revision：{revision}</p>

      {quotaInterruptId ? (
        <section className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">
          <PanelTitle>额度中断</PanelTitle>
          <p className="mt-1">原因：{text(summary.quota_interrupt_reason_code, "需要人工处理")}</p>
          <button className="mt-2 rounded border border-amber-300 px-2 py-1" onClick={onCancelQuotaInterrupt}>
            取消该任务
          </button>
        </section>
      ) : null}

      <section className="space-y-2 rounded bg-canvas p-2">
        <PanelTitle>创作脚本</PanelTitle>
        <p>状态：{text(summary.script_status, summary.has_script === true ? "已生成" : "尚未生成")}</p>
        <p>版本：{text(summary.script_version)}</p>
        {typeof summary.script_preview === "string" ? (
          <p className="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 text-ink">
            {summary.script_preview}
          </p>
        ) : null}
        <label className="block">
          <span className="sr-only">编辑脚本</span>
          <textarea
            className="mt-1 min-h-32 w-full rounded border border-line bg-surface p-2 text-ink"
            value={scriptDraft}
            maxLength={8000}
            onChange={(event) => setScriptDraft(event.target.value)}
          />
        </label>
        <button
          className="rounded border border-line px-2 py-1 disabled:opacity-50"
          disabled={savingScript || !scriptDraft.trim() || scriptDraft === scriptContent}
          onClick={() => void saveScript().catch(() => undefined)}
        >
          {savingScript ? "保存中…" : `保存脚本（Workspace r${revision}）`}
        </button>
        {scriptMissing.length > 0 ? <p>待补充：{scriptMissing.join("、")}</p> : null}
      </section>

      <section className="space-y-2 rounded bg-canvas p-2">
        <PanelTitle>执行计划</PanelTitle>
        {planRecord ? (
          <>
            <p>状态：{text(planRecord.status)}</p>
            <p>版本：{planRevision || "—"}</p>
            {typeof planRecord.goal === "string" ? <p>{planRecord.goal}</p> : null}
            <label className="block">
              <span className="sr-only">编辑计划目标</span>
              <textarea
                className="mt-1 min-h-20 w-full rounded border border-line bg-surface p-2 text-ink"
                value={planGoalDraft}
                maxLength={2000}
                onChange={(event) => setPlanGoalDraft(event.target.value)}
              />
            </label>
            <button
              className="rounded border border-line px-2 py-1 disabled:opacity-50"
              disabled={savingPlan || planRevision < 1 || planGoalDraft === planGoal}
              onClick={() => void savePlan().catch(() => undefined)}
            >
              {savingPlan ? "保存中…" : `保存计划（Plan r${planRevision || "?"}）`}
            </button>
            <ol className="space-y-1">
              {records(planRecord.steps).map((step) => (
                <li key={text(step.step_id, String(step.sequence))} className="rounded bg-surface p-1">
                  {count(step.sequence)}. {text(step.title)} · {text(step.status)}
                  {typeof step.summary === "string" ? <p className="mt-1">{step.summary}</p> : null}
                </li>
              ))}
            </ol>
          </>
        ) : <p>暂无可恢复的执行计划。</p>}
      </section>

      <section className="space-y-2 rounded bg-canvas p-2">
        <PanelTitle>素材包</PanelTitle>
        <p>角色 {count(summary.character_count)} · 场景 {count(summary.scene_asset_count)} · 道具 {count(summary.prop_count)}</p>
        <p>参考图：{count(summary.scene_asset_ready_count)}/{count(summary.scene_asset_required_count)}</p>
        {["character_summaries", "scene_asset_summaries", "prop_summaries"].flatMap((key) => records(summary[key])).map((asset) => (
          <p key={`${text(asset.asset_id)}-${text(asset.name)}`} className="rounded bg-surface px-1 py-0.5">{text(asset.name)}</p>
        ))}
      </section>

      <section className="space-y-2 rounded bg-canvas p-2">
        <PanelTitle>分镜</PanelTitle>
        <p>完成 {count(summary.scene_videos_ready_count)} · 处理中 {count(summary.scene_videos_polling_count)} · 失败 {count(summary.scene_videos_failed_count)}</p>
        <ol className="space-y-1">
          {records(summary.scene_summaries).map((scene) => (
            <li key={text(scene.scene_id)} className="rounded bg-surface p-1">
              {count(scene.scene_index)}. {text(scene.title)} · {text(scene.state)}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
