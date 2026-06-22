import { useEffect, useRef, useState } from "react";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { GenParamsDialog, type GenParamsForm } from "@/components/composer/GenParamsDialog";
import { api, subscribeTaskEvents, type TaskEvent } from "@/lib/api";
import type { ChatMessage, CanvasState, Brief, BriefShot } from "@/lib/chat";
import type { TaskPhase, VideoResult } from "@/lib/types";

let seq = 0;
const uid = () => `m${++seq}`;
const now = () => new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

const VIDEO_HINTS = ["视频", "短视频", "成片", "带货", "种草", "分镜", "广告", "拍", "生成", "seedance"];
// 前端临时意图识别：只用来判断是否弹出参数表单，不代表后端 Agent 的真实理解。
const looksLikeVideoIntent = (t: string) => VIDEO_HINTS.some((k) => t.includes(k));

const PHASE_MSG: Record<string, string> = {
  intake: "正在理解商品与需求…",
  creative: "正在策划分镜 Brief…",
  brief_review: "Brief 已就绪,请在右侧确认或修改。",
  generate: "正在生成分镜片段…",
  edit: "正在剪辑合成…",
  segment_review: "分镜片段已生成,请在画布确认。",
  edit_review: "剪辑结果已生成,请在画布确认。",
  qc: "正在质检…",
  qc_review: "质检完成,请在画布确认。",
  done: "全部完成 🎉",
};

const REVIEW_ARTIFACT: Partial<Record<TaskPhase, NonNullable<ChatMessage["artifact"]>>> = {
  segment_review: {
    type: "segments",
    title: "分镜片段",
    description: "查看生成片段并确认是否进入剪辑",
    actionLabel: "查看",
  },
  edit_review: {
    type: "edit",
    title: "剪辑结果",
    description: "查看剪辑成片并确认是否质检",
    actionLabel: "查看",
  },
  qc_review: {
    type: "qc",
    title: "质检结果",
    description: "查看质检结果并确认是否完成",
    actionLabel: "查看",
  },
};

function sizeFor(ratio: string, resolution: string): string {
  // 把弹窗里的比例/清晰度转换成后端 video_params.size，例如 9:16 + 1080p -> 1080x1920。
  const r = resolution === "720p" ? 720 : 1080;
  if (ratio === "16:9") return `${Math.round((r * 16) / 9)}x${r}`;
  if (ratio === "1:1") return `${r}x${r}`;
  return `${r}x${Math.round((r * 16) / 9)}`; // 9:16
}

function toBrief(raw: Record<string, unknown>): Brief {
  // 后端 Brief DTO 使用 snake_case；前端画布组件使用 camelCase 展示模型。
  // 这个函数就是两者之间的适配器，类似 Java 里 DO/DTO -> VO 的转换。
  const shots = Array.isArray(raw.shots) ? (raw.shots as Record<string, unknown>[]) : [];
  return {
    title: String(raw.brief_id ?? "视频 Brief"),
    platform: String(raw.platform ?? ""),
    durationSec: Number(raw.duration_sec ?? 0),
    ratio: String(raw.ratio ?? "9:16"),
    shots: shots.map(
      (s, i): BriefShot => ({
        shotId: String(s.shot_id ?? `s${i}`),
        timeRange: String(s.time_range ?? ""),
        sceneType: String(s.scene_type ?? ""),
        durationSec: Number(s.duration ?? 0),
        narration: String(s.narration_text ?? ""),
        onscreen: String(s.onscreen_text ?? ""),
      }),
    ),
  };
}

const EMPTY_CANVAS: CanvasState = { phase: "idle", results: [] };
const SESSION_KEY = "pixelflow.workspace.session.v1";

interface WorkspaceSnapshot {
  taskId: string;
  messages: ChatMessage[];
  canvas: CanvasState;
  canvasOpen: boolean;
  briefConfirmed: boolean;
  lastEventId: number;
  announcedPhases: string[];
  briefReadyShown: boolean;
}

export function WorkspacePage() {
  // 页面可渲染状态：聊天消息、右侧画布、参数弹窗、流程 busy 态和 Brief 确认态。
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvas, setCanvas] = useState<CanvasState>(EMPTY_CANVAS);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [pendingCore, setPendingCore] = useState("");
  const [busy, setBusy] = useState(false);
  const [briefConfirmed, setBriefConfirmed] = useState(false);

  // 运行中上下文：这些值主要给异步 SSE 回调读取，不需要每次变化都触发 React 重渲染。
  // 可以类比后端 Service 内部字段，保存当前 taskId、事件去重集合和取消订阅函数。
  const [currentTaskId, setCurrentTaskId] = useState("");
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const briefReadyShownRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const restoredRef = useRef(false);
  const saveTimerRef = useRef<number | undefined>(undefined);
  const unsubRef = useRef<() => void>(() => {});

  const setActiveTaskId = (taskId: string) => {
    taskIdRef.current = taskId;
    setCurrentTaskId(taskId);
  };

  const pushAssistant = (content: string) =>
    setMessages((m) => [...m, { id: uid(), role: "assistant", content, time: now() }]);

  const pushArtifact = (content: string, artifact: NonNullable<ChatMessage["artifact"]>) =>
    setMessages((m) => [...m, { id: uid(), role: "assistant", content, time: now(), artifact }]);

  const pushReviewArtifact = (phase: TaskPhase) => {
    const artifact = REVIEW_ARTIFACT[phase];
    if (!artifact) return;
    const key = `${phase}:artifact`;
    if (announcedPhasesRef.current.has(key)) return;
    announcedPhasesRef.current.add(key);
    pushArtifact(PHASE_MSG[phase] || "请在画布确认。", artifact);
  };

  async function reconcileTaskFromServer(taskId: string) {
    try {
      const task = await api.getTask(taskId);
      setActiveTaskId(task.task_id);
      const phase = task.phase as TaskPhase;
      const confirmed = task.phase !== "brief_review";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase: phase || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
      }));

      if (["segment_review", "edit_review", "qc_review"].includes(task.phase)) {
        setCanvasOpen(true);
        await loadResults(phase);
        pushReviewArtifact(phase);
        if (PHASE_MSG[task.phase] && !announcedPhasesRef.current.has(task.phase)) {
          announcedPhasesRef.current.add(task.phase);
        }
        return;
      }

      if (task.phase === "brief_review") {
        setCanvasOpen(true);
        if (!announcedPhasesRef.current.has("brief_review")) {
          announcedPhasesRef.current.add("brief_review");
          pushAssistant(PHASE_MSG.brief_review);
        }
        return;
      }

      if (task.status === "done") {
        await loadResults("done");
        return;
      }

      if (task.status === "error") {
        pushAssistant(`生成失败:${task.error || "未知错误"}`);
        setBusy(false);
      }
    } catch {
      /* keep restored snapshot if server reconciliation fails */
    }
  }

  const applySnapshot = (snapshot: Partial<WorkspaceSnapshot>) => {
    if (Array.isArray(snapshot.messages)) setMessages(snapshot.messages);
    if (snapshot.canvas) setCanvas(snapshot.canvas);
    if (typeof snapshot.canvasOpen === "boolean") setCanvasOpen(snapshot.canvasOpen);
    if (typeof snapshot.briefConfirmed === "boolean") {
      setBriefConfirmed(snapshot.briefConfirmed);
      briefConfirmedRef.current = snapshot.briefConfirmed;
    }
    if (snapshot.taskId) setActiveTaskId(snapshot.taskId);
    if (typeof snapshot.lastEventId === "number") {
      lastEventIdRef.current = snapshot.lastEventId;
      seenEventIdsRef.current = new Set(Array.from({ length: snapshot.lastEventId }, (_, i) => i + 1));
    }
    if (Array.isArray(snapshot.announcedPhases)) announcedPhasesRef.current = new Set(snapshot.announcedPhases);
    if (typeof snapshot.briefReadyShown === "boolean") briefReadyShownRef.current = snapshot.briefReadyShown;
  };

  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      let snapshot: Partial<WorkspaceSnapshot> | null = null;
      try {
        const server = await api.getSessionContext();
        if (server?.context) snapshot = server.context as Partial<WorkspaceSnapshot>;
      } catch {
        /* fall back to local snapshot */
      }
      if (!snapshot) {
        try {
          const raw = localStorage.getItem(SESSION_KEY);
          snapshot = raw ? (JSON.parse(raw) as Partial<WorkspaceSnapshot>) : null;
        } catch {
          localStorage.removeItem(SESSION_KEY);
        }
      }
      if (cancelled) return;
      if (snapshot) {
        applySnapshot(snapshot);
        if (snapshot.taskId) {
          unsubRef.current = subscribeTaskEvents(snapshot.taskId, onEvent, snapshot.lastEventId || undefined);
          await reconcileTaskFromServer(snapshot.taskId);
        }
      }
      restoredRef.current = true;
    };
    void restore();
    return () => {
      cancelled = true;
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    };
  }, []);

  useEffect(() => {
    try {
    if (!restoredRef.current) return;
    const snapshot: WorkspaceSnapshot = {
      taskId: currentTaskId,
      messages,
      canvas,
      canvasOpen,
      briefConfirmed,
      lastEventId: lastEventIdRef.current,
      announcedPhases: Array.from(announcedPhasesRef.current),
      briefReadyShown: briefReadyShownRef.current,
    };
    localStorage.setItem(SESSION_KEY, JSON.stringify(snapshot));
    if (currentTaskId) {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => {
        void api.saveSessionContext(currentTaskId, snapshot as unknown as Record<string, unknown>).catch(() => {});
      }, 400);
    }
    } catch {
      /* ignore persistence errors in the UI path */
    }
  }, [messages, canvas, canvasOpen, briefConfirmed, currentTaskId]);

  const handleSend = (text: string) => {
    setMessages((m) => [...m, { id: uid(), role: "user", content: text, time: now() }]);
    if (looksLikeVideoIntent(text)) {
      setPendingCore(text);
      setTimeout(() => {
        pushAssistant("好的,帮你做带货短视频。请补充商品与参数 👇");
        setDialogOpen(true);
      }, 300);
    } else {
      setTimeout(() => pushAssistant("我可以帮你生成电商带货短视频。描述一下商品和你想要的效果?"), 300);
    }
  };

  async function onEvent(e: TaskEvent) {
    // SSE 事件分发器：后端事件表可能因为断线重连/afterId 被重复消费，这里先按 id 去重。
    if (e.id && seenEventIdsRef.current.has(e.id)) return;
    if (e.id) {
      seenEventIdsRef.current.add(e.id);
      lastEventIdRef.current = Math.max(lastEventIdRef.current, e.id);
    }
    const phase = (e.data.phase as string) || "";
    switch (e.event) {
      case "phase_change":
        if (phase) {
          // Brief 未人工确认前，忽略 generate/edit/qc/done 阶段回放，避免旧 run 的 pending
          // 事件把画布提前推进到生成结果态。
          if (["generate", "edit", "qc", "done"].includes(phase) && !briefConfirmedRef.current) return;
          setCanvas((c) => ({ ...c, phase: phase as TaskPhase }));
          if (["segment_review", "edit_review", "qc_review", "done"].includes(phase)) {
            void loadResults(phase as TaskPhase);
            pushReviewArtifact(phase as TaskPhase);
          }
          if (PHASE_MSG[phase] && !announcedPhasesRef.current.has(phase)) {
            announcedPhasesRef.current.add(phase);
            if (!(phase in REVIEW_ARTIFACT)) pushAssistant(PHASE_MSG[phase]);
          }
        }
        break;
      case "brief_ready":
        // brief_ready 表示后端在 LangGraph interrupt 前已经准备好 Brief，前端需要展示确认卡。
        if (briefConfirmedRef.current || briefReadyShownRef.current) return;
        briefReadyShownRef.current = true;
        setCanvas((c) => ({ ...c, phase: "brief_review", brief: toBrief((e.data.brief as Record<string, unknown>) || {}) }));
        setBusy(false);
        pushArtifact("Brief 已生成。点击下方素材卡打开画布查看和确认。", {
          type: "brief",
          title: "视频 Brief",
          description: "分镜、旁白与投放参数",
          actionLabel: "查看",
        });
        break;
      case "task_done":
        // task_done 代表业务任务已完成，可以从 /assets 拉取最终视频或生成片段。
        await loadResults();
        break;
      case "brief_confirmed":
        // brief_confirmed 是业务事件，表示用户确认动作已被后端接收。
        briefConfirmedRef.current = true;
        setBriefConfirmed(true);
        break;
      case "run_finished":
        // run_finished 只表示某个 LangGraph run 结束，不一定等于业务任务 done；
        // 需要再查任务详情，同步 checkpoint 后的 phase/status/brief。
        await refreshTaskAfterRun();
        break;
      case "task_failed":
        pushAssistant(`生成失败:${String(e.data.error ?? "未知错误")}`);
        setBusy(false);
        break;
    }
  }

  async function loadResults(nextPhase: TaskPhase = "done") {
    // 从 /assets 拉取画布可展示的视频资产。当前只展示 final_video 和 generated_video；
    // jianying_draft 是本地草稿路径，浏览器通常不能直接播放。
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const [assets, taskResult] = await Promise.all([api.listAssets(id), api.getResult(id).catch(() => null)]);
      const videos = assets.filter((a) => a.asset_type === "final_video" || a.asset_type === "generated_video");
      const results: VideoResult[] = videos.map((a, i) => ({
        id: a.asset_id || `r${i}`,
        url: a.asset_type === "final_video" ? api.assetContentUrl(id, a.asset_id) : a.url,
        assetType: a.asset_type,
        status: a.status === "ready" ? "success" : a.status === "error" ? "failed" : "pending",
      }));
      const qcReport = taskResult?.result?.qc_report;
      setCanvas((c) => ({
        ...c,
        phase: nextPhase,
        results,
        qcReport: qcReport && typeof qcReport === "object" ? c.qcReport || (qcReport as CanvasState["qcReport"]) : c.qcReport,
      }));
      if (nextPhase === "done") {
        pushArtifact("生成完成,素材已就绪。点击下方素材卡打开画布查看。", {
          type: "results",
          title: "生成素材",
          description: `${results.length} 条视频结果`,
          actionLabel: "打开",
        });
      }
    } catch {
      pushAssistant("结果拉取失败,请稍后在历史中查看。");
    } finally {
      setBusy(false);
    }
  }

  async function refreshTaskAfterRun() {
    // LangGraph run 结束后重新查询业务任务。后端 getTask 会先同步 checkpoint，
    // 因此前端能拿到最新 phase、brief、error 和 result。
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const task = await api.getTask(id);
      const confirmed = task.phase !== "brief_review";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase: (task.phase as TaskPhase) || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
      }));
      if (task.status === "done") {
        await loadResults("done");
        return;
      }
      if (["segment_review", "edit_review", "qc_review"].includes(task.phase)) {
        await loadResults(task.phase as TaskPhase);
        if (PHASE_MSG[task.phase] && !announcedPhasesRef.current.has(task.phase)) {
          announcedPhasesRef.current.add(task.phase);
          pushAssistant(PHASE_MSG[task.phase]);
        }
        return;
      }
      if (task.phase === "brief_review") {
        setBusy(false);
        pushAssistant("Brief 已就绪,请打开素材卡确认后再生成视频。");
      }
      if (task.status === "error") {
        setBusy(false);
        pushAssistant(`生成失败:${task.error || "未知错误"}`);
      }
    } catch {
      pushAssistant("任务状态同步失败,请稍后重试。");
      setBusy(false);
    }
  }

  // 参数弹窗确认后的主链路：创建业务任务 -> 记录 taskId -> 重置事件缓存 -> 订阅 SSE。
  // 注意：GenParamsForm 里的 count/sound/reference_videos 当前还没有透传到后端主链路。
  const handleConfirmParams = async (form: GenParamsForm) => {
    setDialogOpen(false);
    setBusy(true);
    pushAssistant(`已收到「${form.productName}」,正在创建任务…`);
    try {
      const task = await api.createTask({
        product_info: { product_name: form.productName, main_image_url: form.imageUrl },
        video_params: { platform: form.platform, duration_sec: form.durationSec, ratio: form.ratio, size: sizeFor(form.ratio, form.resolution) },
        creative_direction: { core_message: form.coreMessage, creative_style: form.creativeStyle },
        user_message: form.coreMessage,
        auto_start: true,
      });
      setActiveTaskId(task.task_id);
      briefConfirmedRef.current = false;
      setBriefConfirmed(false);
      seenEventIdsRef.current = new Set();
      lastEventIdRef.current = 0;
      announcedPhasesRef.current = new Set();
      briefReadyShownRef.current = false;
      setCanvas({ phase: (task.phase as TaskPhase) || "intake", results: [] });
      unsubRef.current();
      unsubRef.current = subscribeTaskEvents(task.task_id, onEvent);
    } catch (err) {
      pushAssistant(`创建任务失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleApprove = async () => {
    // 对应后端 /brief/confirm：恢复 LangGraph 的 Brief interrupt，批准后进入 GENERATE。
    pushAssistant("Brief 已确认,开始生成…");
    setBusy(true);
    briefConfirmedRef.current = true;
    setBriefConfirmed(true);
    try {
      await api.confirmBrief(taskIdRef.current, true);
      setCanvas((c) => ({ ...c, phase: "generate" }));
    } catch (err) {
      pushAssistant(`确认失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleRevise = async () => {
    // 对应后端 /brief/revise：当前只写业务 brief/反馈/偏好，不会直接恢复 LangGraph run。
    const fb = "请优化分镜节奏与卖点表达";
    pushAssistant("已请求修改 Brief。");
    try {
      await api.reviseBrief(taskIdRef.current, {}, fb);
      briefConfirmedRef.current = false;
      setBriefConfirmed(false);
      setCanvas((c) => ({ ...c, phase: "brief_review" }));
    } catch (err) {
      pushAssistant(`修改失败:${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleConfirmStage = async (stage: "segments" | "edit" | "qc", approved: boolean) => {
    setBusy(true);
    try {
      const task = await api.confirmStage(taskIdRef.current, stage, approved);
      setCanvas((c) => ({ ...c, phase: (task.phase as TaskPhase) || c.phase }));
      pushAssistant(approved ? "已确认,继续下一步。" : "已退回,重新处理。");
    } catch (err) {
      pushAssistant(`确认失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0">
      <ChatPanel
        messages={messages}
        onSubmit={handleSend}
        busy={busy || dialogOpen}
        onOpenArtifact={(msg) => {
          if (!msg.artifact) return;
          setCanvasOpen(true);
          if (msg.artifact.type === "brief") setCanvas((c) => ({ ...c, phase: "brief_review" }));
          if (msg.artifact.type === "results") setCanvas((c) => ({ ...c, phase: "done" }));
          if (msg.artifact.type === "segments") setCanvas((c) => ({ ...c, phase: "segment_review" }));
          if (msg.artifact.type === "edit") setCanvas((c) => ({ ...c, phase: "edit_review" }));
          if (msg.artifact.type === "qc") setCanvas((c) => ({ ...c, phase: "qc_review" }));
          if (["segments", "edit", "qc"].includes(msg.artifact.type)) {
            const phaseByType = { segments: "segment_review", edit: "edit_review", qc: "qc_review" } as const;
            void loadResults(phaseByType[msg.artifact.type as "segments" | "edit" | "qc"]);
          }
        }}
      />
      {canvasOpen && (
        <CanvasPanel
          state={canvas}
          onApprove={handleApprove}
          onRevise={handleRevise}
          onConfirmStage={handleConfirmStage}
          onClose={() => setCanvasOpen(false)}
          briefConfirmed={briefConfirmed}
        />
      )}
      {dialogOpen && (
        <GenParamsDialog key={pendingCore} open initialCoreMessage={pendingCore} onConfirm={handleConfirmParams} onCancel={() => setDialogOpen(false)} />
      )}
    </div>
  );
}
