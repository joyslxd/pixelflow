import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { GenParamsDialog, type GenParamsForm } from "@/components/composer/GenParamsDialog";
import { api, subscribeTaskEvents, type TaskEvent } from "@/lib/api";
import type { ChatMessage, CanvasState, Brief, BriefShot } from "@/lib/chat";
import type { TaskPhase, VideoResult } from "@/lib/types";

let seq = 0;
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `m${Date.now()}-${++seq}`);
const now = () => new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

const VIDEO_HINTS = ["视频", "短视频", "成片", "带货", "种草", "分镜", "广告", "拍", "生成", "seedance"];
const looksLikeVideoIntent = (t: string) => VIDEO_HINTS.some((k) => t.includes(k));
const REVIEW_CONTINUE_HINTS = ["确认", "继续", "下一步", "开始", "进入", "质检", "剪辑", "通过"];
const REVIEW_REJECT_HINTS = ["重来", "重新生成", "重新剪辑", "不通过", "退回", "修改"];
const looksLikeReviewContinue = (t: string) => REVIEW_CONTINUE_HINTS.some((k) => t.includes(k));
const looksLikeReviewReject = (t: string) => REVIEW_REJECT_HINTS.some((k) => t.includes(k));

const PHASE_MSG: Record<string, string> = {
  intake: "正在理解商品与需求…",
  creative: "正在策划分镜 Brief…",
  brief_review: "Brief 已就绪,请在右侧确认或修改。",
  storyboard_review: "视频场景包已准备好,请确认后生成视频。",
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

const BRIEF_ARTIFACT: NonNullable<ChatMessage["artifact"]> = {
  type: "brief",
  title: "视频 Brief",
  description: "分镜、旁白与投放参数",
  actionLabel: "查看",
};

const STORYBOARD_ARTIFACT: NonNullable<ChatMessage["artifact"]> = {
  type: "storyboard",
  title: "视频场景包",
  description: "查看分镜、镜头描述、旁白和参考图",
  actionLabel: "查看分镜",
};

function sizeFor(ratio: string, resolution: string): string {
  const r = resolution === "720p" ? 720 : 1080;
  if (ratio === "16:9") return `${Math.round((r * 16) / 9)}x${r}`;
  if (ratio === "1:1") return `${r}x${r}`;
  return `${r}x${Math.round((r * 16) / 9)}`; // 9:16
}

function toBrief(raw: Record<string, unknown>): Brief {
  const shots = Array.isArray(raw.shots) ? (raw.shots as Record<string, unknown>[]) : [];
  const globalVisual = raw.global_visual && typeof raw.global_visual === "object" ? (raw.global_visual as Record<string, unknown>) : {};
  return {
    title: String(raw.brief_id ?? "视频 Brief"),
    platform: String(raw.platform ?? ""),
    durationSec: Number(raw.duration_sec ?? 0),
    ratio: String(raw.ratio ?? "9:16"),
    size: String(raw.size ?? ""),
    globalVisual: {
      subjectType: String(globalVisual.subject_type ?? ""),
      environment: String(globalVisual.environment ?? ""),
      lighting: String(globalVisual.lighting ?? ""),
      characterStyle: String(globalVisual.character_style ?? ""),
      overallStyle: String(globalVisual.overall_style ?? ""),
      forbiddenElements: String(globalVisual.forbidden_elements ?? ""),
    },
    shots: shots.map(
      (s, i): BriefShot => ({
        shotId: String(s.shot_id ?? `s${i}`),
        timeRange: String(s.time_range ?? ""),
        sceneType: String(s.scene_type ?? ""),
        durationSec: Number(s.duration ?? 0),
        shotType: String(s.shot_type ?? ""),
        cameraMovement: String(s.camera_movement ?? ""),
        visualDescription: String(s.visual_description ?? ""),
        generationPrompt: String(s.generation_prompt ?? ""),
        narration: String(s.narration_text ?? ""),
        onscreen: String(s.onscreen_text ?? ""),
        assetStrategy: String(s.asset_strategy ?? ""),
        transitionIn: String(s.transition_in ?? ""),
        transitionOut: String(s.transition_out ?? ""),
        audio:
          s.audio && typeof s.audio === "object"
            ? {
                bgmVibe: String((s.audio as Record<string, unknown>).bgm_vibe ?? ""),
                sfx: String((s.audio as Record<string, unknown>).sfx ?? ""),
                ttsVoice: String((s.audio as Record<string, unknown>).tts_voice ?? ""),
              }
            : undefined,
      }),
    ),
  };
}

const EMPTY_CANVAS: CanvasState = { phase: "idle", results: [] };
const SESSION_KEY = "pixelflow.workspace.session.v1";
const createSessionId = () => `chat-${uid()}`;

function normalizeMessages(items: ChatMessage[]): ChatMessage[] {
  const seen = new Set<string>();
  return items.map((message) => {
    const id = message.id && !seen.has(message.id) ? message.id : uid();
    seen.add(id);
    return { ...message, id };
  });
}

interface WorkspaceSnapshot {
  taskId: string;
  messages: ChatMessage[];
  canvas: CanvasState;
  canvasOpen: boolean;
  dialogOpen: boolean;
  pendingCore: string;
  dialogDraft: GenParamsForm | null;
  briefConfirmed: boolean;
  lastEventId: number;
  announcedPhases: string[];
  briefReadyShown: boolean;
}

export function WorkspacePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvas, setCanvas] = useState<CanvasState>(EMPTY_CANVAS);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [pendingCore, setPendingCore] = useState("");
  const [dialogDraft, setDialogDraft] = useState<GenParamsForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [briefConfirmed, setBriefConfirmed] = useState(false);
  const [currentTaskId, setCurrentTaskId] = useState("");
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const briefReadyShownRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const restoredRef = useRef(false);
  const skipNextRestoreRef = useRef(false);
  const saveTimerRef = useRef<number | undefined>(undefined);
  const replyTimersRef = useRef<number[]>([]);
  const unsubRef = useRef<() => void>(() => {});

  const setActiveTaskId = (taskId: string) => {
    taskIdRef.current = taskId;
    setCurrentTaskId(taskId);
  };

  const resetWorkspace = () => {
    unsubRef.current();
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    replyTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    replyTimersRef.current = [];
    taskIdRef.current = "";
    briefConfirmedRef.current = false;
    seenEventIdsRef.current = new Set();
    announcedPhasesRef.current = new Set();
    briefReadyShownRef.current = false;
    lastEventIdRef.current = 0;
    setCurrentTaskId("");
    setMessages([]);
    setCanvas(EMPTY_CANVAS);
    setCanvasOpen(false);
    setDialogOpen(false);
    setPendingCore("");
    setDialogDraft(null);
    setBusy(false);
    setBriefConfirmed(false);
    localStorage.removeItem(SESSION_KEY);
  };

  const pushAssistant = (content: string) =>
    setMessages((m) => [...m, { id: uid(), role: "assistant", content, time: now() }]);

  const pushArtifact = (content: string, artifact: NonNullable<ChatMessage["artifact"]>) =>
    setMessages((m) => [...m, { id: uid(), role: "assistant", content, time: now(), artifact }]);

  const pushBriefArtifact = (content = "Brief 已生成。点击下方素材卡打开画布查看和确认。") => {
    briefReadyShownRef.current = true;
    setMessages((m) => {
      if (m.some((message) => message.artifact?.type === "brief")) return m;
      return [...m, { id: uid(), role: "assistant", content, time: now(), artifact: BRIEF_ARTIFACT }];
    });
  };

  const pushStoryboardArtifact = (thumbnails: string[] = []) => {
    setMessages((m) => {
      if (m.some((message) => message.artifact?.type === "storyboard")) return m;
      return [
        ...m,
        {
          id: uid(),
          role: "assistant",
          content: "视频场景包已准备好，请确认后生成视频。",
          time: now(),
          artifact: {
            ...STORYBOARD_ARTIFACT,
            thumbnails,
          },
        },
      ];
    });
  };

  const pushReviewArtifact = (phase: TaskPhase) => {
    const artifact = REVIEW_ARTIFACT[phase];
    if (!artifact) return;
    const key = `${phase}:artifact`;
    if (announcedPhasesRef.current.has(key)) return;
    announcedPhasesRef.current.add(key);
    pushArtifact(PHASE_MSG[phase] || "请在画布确认。", artifact);
  };

  const pushDoneArtifact = (results: VideoResult[], qcReport?: CanvasState["qcReport"]) => {
    const key = "done:artifact";
    if (announcedPhasesRef.current.has(key)) return;
    announcedPhasesRef.current.add(key);
    const passed = qcReport?.passed;
    const verdict = passed == null ? "质检已完成" : passed ? "质检通过" : "质检未通过";
    const finalCount = results.filter((result) => result.assetType === "final_video").length;
    const description = finalCount > 0 ? `${verdict} · ${finalCount} 条成片` : `${verdict} · ${results.length} 条片段素材`;
    pushArtifact("任务已完成，点击下方结果卡打开画布查看质检报告和视频结果。", {
      type: "results",
      title: "任务完成",
      description,
      actionLabel: "打开",
    });
  };

  const handleReviewChatIntent = (text: string, phase: string): boolean => {
    const reviewStage = {
      segment_review: "segments",
      edit_review: "edit",
      qc_review: "qc",
    } as const;
    const stage = reviewStage[phase as keyof typeof reviewStage];
    if (!stage || !taskIdRef.current) return false;

    if (phase === "qc_review" && (text.includes("质检") || text.includes("结果") || text.includes("报告"))) {
      setCanvasOpen(true);
      void loadResults("qc_review");
      pushAssistant("质检结果已在右侧画布展示，可以在那里确认通过或退回重生成。");
      return true;
    }

    if (looksLikeReviewReject(text)) {
      void handleConfirmStage(stage, false);
      return true;
    }

    if (looksLikeReviewContinue(text)) {
      void handleConfirmStage(stage, true);
      return true;
    }

    setCanvasOpen(true);
    void loadResults(phase as TaskPhase);
    pushAssistant(PHASE_MSG[phase] || "当前步骤需要先在右侧画布确认。");
    return true;
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
        phase: c.phase === "storyboard_review" && phase === "brief_review" && c.brief ? "storyboard_review" : phase || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
        productName: typeof task.product_info?.product_name === "string" ? task.product_info.product_name : c.productName,
        productImageUrl: typeof task.product_info?.main_image_url === "string" ? task.product_info.main_image_url : c.productImageUrl,
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
        if (!announcedPhasesRef.current.has("brief_review")) {
          announcedPhasesRef.current.add("brief_review");
          pushBriefArtifact("Brief 已就绪,请打开素材卡确认后再生成视频。");
        } else {
          pushBriefArtifact("Brief 已就绪,请打开素材卡确认后再生成视频。");
        }
        setBusy(false);
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
    if (Array.isArray(snapshot.messages)) setMessages(normalizeMessages(snapshot.messages));
    if (snapshot.canvas) setCanvas(snapshot.canvas);
    if (typeof snapshot.canvasOpen === "boolean") setCanvasOpen(snapshot.canvasOpen);
    if (typeof snapshot.dialogOpen === "boolean") setDialogOpen(snapshot.dialogOpen);
    if (typeof snapshot.pendingCore === "string") setPendingCore(snapshot.pendingCore);
    if (snapshot.dialogDraft && typeof snapshot.dialogDraft === "object") setDialogDraft(snapshot.dialogDraft);
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
      const fresh = new URLSearchParams(location.search).get("new") === "1";
      if (fresh) {
        resetWorkspace();
        restoredRef.current = true;
        skipNextRestoreRef.current = true;
        navigate("/", { replace: true });
        return;
      }
      if (skipNextRestoreRef.current) {
        skipNextRestoreRef.current = false;
        restoredRef.current = true;
        return;
      }
      let snapshot: Partial<WorkspaceSnapshot> | null = null;
      const sessionId = new URLSearchParams(location.search).get("session");
      try {
        const server = await api.getSessionContext(sessionId || undefined);
        if (server?.context) snapshot = server.context as Partial<WorkspaceSnapshot>;
      } catch {
        /* fall back to local snapshot */
      }
      if (!snapshot) {
        try {
          const raw = localStorage.getItem(SESSION_KEY);
          const localSnapshot = raw ? (JSON.parse(raw) as Partial<WorkspaceSnapshot>) : null;
          if (localSnapshot && (!sessionId || localSnapshot.taskId === sessionId)) snapshot = localSnapshot;
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
      replyTimersRef.current.forEach((timer) => window.clearTimeout(timer));
      replyTimersRef.current = [];
    };
  }, [location.search, navigate]);

  useEffect(() => {
    try {
    if (!restoredRef.current) return;
    const safeMessages = normalizeMessages(messages);
    if (safeMessages.some((message, index) => message.id !== messages[index]?.id)) {
      setMessages(safeMessages);
      return;
    }
    const snapshot: WorkspaceSnapshot = {
      taskId: currentTaskId,
      messages: safeMessages,
      canvas,
      canvasOpen,
      dialogOpen,
      pendingCore,
      dialogDraft,
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
  }, [messages, canvas, canvasOpen, dialogOpen, pendingCore, dialogDraft, briefConfirmed, currentTaskId]);

  const handleSend = (text: string) => {
    if (!taskIdRef.current) setActiveTaskId(createSessionId());
    setMessages((m) => [...m, { id: uid(), role: "user", content: text, time: now() }]);
    if (handleReviewChatIntent(text, canvas.phase)) return;
    if (looksLikeVideoIntent(text)) {
      setPendingCore(text);
      setDialogDraft(null);
      const timer = window.setTimeout(() => {
        pushAssistant("好的,帮你做带货短视频。请补充商品与参数 👇");
        setDialogOpen(true);
      }, 300);
      replyTimersRef.current.push(timer);
    } else {
      const timer = window.setTimeout(() => pushAssistant("我可以帮你生成电商带货短视频。描述一下商品和你想要的效果?"), 300);
      replyTimersRef.current.push(timer);
    }
  };

  async function onEvent(e: TaskEvent) {
    if (e.id && seenEventIdsRef.current.has(e.id)) return;
    if (e.id) {
      seenEventIdsRef.current.add(e.id);
      lastEventIdRef.current = Math.max(lastEventIdRef.current, e.id);
    }
    const phase = (e.data.phase as string) || "";
    switch (e.event) {
      case "phase_change":
        if (phase) {
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
        if (briefConfirmedRef.current) return;
        setCanvas((c) => ({ ...c, phase: "brief_review", brief: toBrief((e.data.brief as Record<string, unknown>) || {}) }));
        setBusy(false);
        pushBriefArtifact();
        break;
      case "task_done":
        await loadResults();
        break;
      case "brief_confirmed":
        briefConfirmedRef.current = true;
        setBriefConfirmed(true);
        break;
      case "run_finished":
        await refreshTaskAfterRun();
        break;
      case "task_failed":
        pushAssistant(`生成失败:${String(e.data.error ?? "未知错误")}`);
        setBusy(false);
        break;
    }
  }

  async function loadResults(nextPhase: TaskPhase = "done") {
    const id = taskIdRef.current;
    if (!id) return;
    try {
      if (["segment_review", "edit_review", "qc_review", "done"].includes(nextPhase)) {
        setCanvasOpen(true);
      }
      const [assets, taskResult] = await Promise.all([api.listAssets(id), api.getResult(id).catch(() => null)]);
      const finalVideos = assets.filter((a) => a.asset_type === "final_video");
      const generatedVideos = assets.filter((a) => a.asset_type === "generated_video");
      const videos = finalVideos.length > 0 ? finalVideos : generatedVideos;
      const results: VideoResult[] = videos.map((a, i) => ({
        id: a.asset_id || `r${i}`,
        url: a.asset_type === "final_video" ? api.assetContentUrl(id, a.asset_id) : a.url,
        assetType: a.asset_type,
        status: a.status === "ready" ? "success" : a.status === "error" ? "failed" : "pending",
      }));
      const qcReport = taskResult?.result?.qc_report;
      const nextQcReport = qcReport && typeof qcReport === "object" ? (qcReport as CanvasState["qcReport"]) : undefined;
      setCanvas((c) => ({
        ...c,
        phase: nextPhase,
        results,
        qcReport: nextQcReport || c.qcReport,
      }));
      if (nextPhase === "done") {
        pushDoneArtifact(results, nextQcReport);
      }
    } catch {
      pushAssistant("结果拉取失败,请稍后在历史中查看。");
    } finally {
      setBusy(false);
    }
  }

  async function refreshTaskAfterRun() {
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const task = await api.getTask(id);
      const confirmed = task.phase !== "brief_review";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase:
          c.phase === "storyboard_review" && task.phase === "brief_review" && c.brief
            ? "storyboard_review"
            : (task.phase as TaskPhase) || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
        productName: typeof task.product_info?.product_name === "string" ? task.product_info.product_name : c.productName,
        productImageUrl: typeof task.product_info?.main_image_url === "string" ? task.product_info.main_image_url : c.productImageUrl,
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
        pushBriefArtifact("Brief 已就绪,请打开素材卡确认后再生成视频。");
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

  // 弹窗确认 → 真实建任务 + 订阅 SSE。
  const handleConfirmParams = async (form: GenParamsForm) => {
    setDialogOpen(false);
    setDialogDraft(null);
    setBusy(true);
    pushAssistant(`已收到「${form.productName}」,正在创建任务…`);
    try {
      const task = await api.createTask({
        product_info: {
          product_name: form.productName,
          main_image_url: form.imageUrl,
          ...(form.imageArtifactUrl ? { main_image_artifact_url: form.imageArtifactUrl } : {}),
        },
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
      setCanvas({
        phase: (task.phase as TaskPhase) || "intake",
        productName: form.productName,
        productImageUrl: form.imageUrl,
        results: [],
      });
      navigate(`/?session=${encodeURIComponent(task.task_id)}`, { replace: true });
      unsubRef.current();
      unsubRef.current = subscribeTaskEvents(task.task_id, onEvent);
    } catch (err) {
      pushAssistant(`创建任务失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleApprove = () => {
    const thumbnails = canvas.productImageUrl && canvas.brief ? canvas.brief.shots.map(() => canvas.productImageUrl as string) : [];
    pushAssistant("Brief 已确认,正在准备可编辑分镜场景包…");
    briefConfirmedRef.current = true;
    setBriefConfirmed(true);
    setCanvas((c) => ({ ...c, phase: "storyboard_review" }));
    setCanvasOpen(true);
    setBusy(false);
    pushStoryboardArtifact(thumbnails);
  };

  const handleConfirmStoryboard = async () => {
    pushAssistant("分镜场景包已确认,开始生成视频…");
    setBusy(true);
    try {
      await api.confirmBrief(taskIdRef.current, true);
      setCanvas((c) => ({ ...c, phase: "generate" }));
    } catch (err) {
      pushAssistant(`确认失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleRevise = async () => {
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
          if (msg.artifact.type === "storyboard") setCanvas((c) => ({ ...c, phase: "storyboard_review" }));
          if (msg.artifact.type === "results") {
            setCanvas((c) => ({ ...c, phase: "done" }));
            void loadResults("done");
          }
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
          onConfirmStoryboard={handleConfirmStoryboard}
          onRevise={handleRevise}
          onConfirmStage={handleConfirmStage}
          onClose={() => setCanvasOpen(false)}
          briefConfirmed={briefConfirmed}
        />
      )}
      {dialogOpen && (
        <GenParamsDialog
          key={pendingCore}
          open
          initialCoreMessage={pendingCore}
          initialForm={dialogDraft ?? undefined}
          uploadThreadId={currentTaskId || taskIdRef.current}
          onDraftChange={setDialogDraft}
          onConfirm={handleConfirmParams}
          onCancel={() => {
            setDialogOpen(false);
            setDialogDraft(null);
          }}
        />
      )}
    </div>
  );
}
