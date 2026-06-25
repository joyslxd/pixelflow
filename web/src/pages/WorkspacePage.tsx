import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { GenParamsDialog, type CreationIntent, type GenParamsForm } from "@/components/composer/GenParamsDialog";
import {
  api,
  subscribeTaskEvents,
  type ConversationDetailResponse,
  type ConversationMessageResponse,
  type CreativeDirectionResponse,
  type PlanMarkdownResponse,
  type PrepareScenePackagesResponse,
  type TaskEvent,
} from "@/lib/api";
import type { ChatMessage, CanvasState, Brief, BriefShot } from "@/lib/chat";
import { buildImageRevisionPreparePayload, canAcceptImageResult, imageResultSummary } from "@/lib/imageReview";
import {
  collectSceneImageUrls,
  inferTargetDurationMs,
  sceneIdsForRevision,
  updateScenePackageAssetField,
  updateScenePackageField,
  type SceneAssetCollection,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "@/lib/types";

let seq = 0;
const uid = () => `m${++seq}`;
const now = () => new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

const isCreationIntent = (value: unknown): value is CreationIntent => value === "video" || value === "image";

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

const EXPLAINABLE_EVENT_NAMES = new Set<FlowTimelineEntry["event"]>([
  "step_started",
  "step_finished",
  "llm_summary",
  "vendor_call_started",
  "vendor_call_finished",
  "asset_ready",
]);

const EVENT_FALLBACK_TITLE: Record<FlowTimelineEntry["event"], string> = {
  step_started: "步骤开始",
  step_finished: "步骤完成",
  llm_summary: "思考摘要",
  vendor_call_started: "外部能力调用开始",
  vendor_call_finished: "外部能力调用完成",
  asset_ready: "资产已就绪",
};

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

const EMPTY_CANVAS: CanvasState = { phase: "idle", results: [], timeline: [] };

function toTimelineEntry(event: TaskEvent): FlowTimelineEntry | null {
  // 后端的可解释事件 payload 是面向前端展示的 VO；这里只做轻量字段适配。
  // 普通业务事件仍走 onEvent switch，不进入时间线，避免画布噪声过多。
  if (!EXPLAINABLE_EVENT_NAMES.has(event.event as FlowTimelineEntry["event"])) return null;
  const type = event.event as FlowTimelineEntry["event"];
  const data = event.data || {};
  return {
    id: event.id ? `event-${event.id}` : `${type}-${Date.now()}`,
    event: type,
    title: String(data.title || EVENT_FALLBACK_TITLE[type]),
    summary: String(data.summary || ""),
    phase: data.phase ? String(data.phase) : undefined,
    status: data.status ? String(data.status) : undefined,
    time: event.created_at
      ? new Date(event.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
      : now(),
  };
}

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

function valuesFromForm(form: GenParamsForm): Record<string, unknown> {
  return form.intent === "video"
    ? {
        product_info: form.product_info,
        product_category: form.product_category,
        target_audience: form.target_audience,
        conversion_goal: form.conversion_goal,
      }
    : {
        image_goal: form.image_goal,
        image_type: form.image_type,
        image_usage: form.image_usage,
        image_style: form.image_style,
        image_size: form.image_size,
      };
}

function revisedScenePrompt(
  scene: PrepareScenePackagesResponse["scene_packages"][number],
  feedback: string,
  flawAnalysis: NonNullable<ChatMessage["artifact"]>["videoFlawAnalysis"] | undefined,
  useFlawAnalysis: boolean,
): string {
  const parts = [scene.prompt, `用户修改意见：${feedback.trim()}`];
  if (useFlawAnalysis && flawAnalysis?.revision_prompt) {
    parts.push(`穿帮修复建议：${flawAnalysis.revision_prompt}`);
  }
  return parts.filter(Boolean).join("\n");
}

function messageFromResponse(message: ConversationMessageResponse): ChatMessage | null {
  if (message.role === "system") return null;
  const artifact = message.payload.artifact as ChatMessage["artifact"] | undefined;
  return {
    id: message.message_id,
    role: message.role,
    content: message.content,
    time: message.created_at
      ? new Date(message.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
      : String(message.payload.time || now()),
    artifact,
  };
}

export function WorkspacePage() {
  const navigate = useNavigate();
  const { conversationId } = useParams<{ conversationId?: string }>();
  // 页面可渲染状态：聊天消息、右侧画布、参数弹窗、流程 busy 态和 Brief 确认态。
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvas, setCanvas] = useState<CanvasState>(EMPTY_CANVAS);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [pendingCore, setPendingCore] = useState("");
  const [pendingIntent, setPendingIntent] = useState<CreationIntent>("video");
  const [pendingFormValues, setPendingFormValues] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [briefConfirmed, setBriefConfirmed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState("");

  // 运行中上下文：这些值主要给异步 SSE 回调读取，不需要每次变化都触发 React 重渲染。
  // 可以类比后端 Service 内部字段，保存当前 taskId、事件去重集合和取消订阅函数。
  const [currentTaskId, setCurrentTaskId] = useState("");
  const conversationIdRef = useRef<string>("");
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const processedArtifactIdsRef = useRef(new Set<string>());
  const planRevisionArtifactRef = useRef<NonNullable<ChatMessage["artifact"]> | null>(null);
  const imageRevisionArtifactRef = useRef<NonNullable<ChatMessage["artifact"]> | null>(null);
  const videoRevisionArtifactRef = useRef<NonNullable<ChatMessage["artifact"]> | null>(null);
  const briefReadyShownRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const restoringRef = useRef(false);
  const saveTimerRef = useRef<number | undefined>(undefined);
  const unsubRef = useRef<() => void>(() => {});

  const setActiveTaskId = (taskId: string) => {
    taskIdRef.current = taskId;
    setCurrentTaskId(taskId);
  };

  const setActiveConversationId = (id: string) => {
    conversationIdRef.current = id;
    setCurrentConversationId(id);
  };

  const persistChatMessage = (conversation: string, message: ChatMessage) => {
    void api
      .appendConversationMessage(conversation, {
        role: message.role,
        content: message.content,
        payload: { time: message.time, artifact: message.artifact },
      })
      .catch(() => {});
  };

  const pushAssistant = (content: string) => {
    const message: ChatMessage = { id: uid(), role: "assistant", content, time: now() };
    setMessages((m) => [...m, message]);
    if (conversationIdRef.current) persistChatMessage(conversationIdRef.current, message);
  };

  const pushArtifact = (content: string, artifact: NonNullable<ChatMessage["artifact"]>) => {
    const message: ChatMessage = { id: uid(), role: "assistant", content, time: now(), artifact };
    setMessages((m) => [...m, message]);
    if (conversationIdRef.current) persistChatMessage(conversationIdRef.current, message);
    return message;
  };

  const updateVideoScenePackagesInMessage = (
    messageId: string,
    updater: (scenePackages: ScenePackageRecord[]) => ScenePackageRecord[],
  ) => {
    setMessages((items) =>
      items.map((message) => {
        const artifact = message.artifact;
        const videoScenePackages = artifact?.videoScenePackages;
        if (message.id !== messageId || !artifact || !videoScenePackages) return message;
        return {
          ...message,
          artifact: {
            ...artifact,
            videoScenePackages: {
              ...videoScenePackages,
              scene_packages: updater(videoScenePackages.scene_packages as ScenePackageRecord[]) as typeof videoScenePackages.scene_packages,
            },
          },
        };
      }),
    );
  };

  const handleUpdateVideoScenePackage = (msg: ChatMessage, sceneId: string, patch: ScenePackagePatch) => {
    updateVideoScenePackagesInMessage(msg.id, (scenePackages) => updateScenePackageField(scenePackages, sceneId, patch));
  };

  const handleUpdateVideoSceneAssetField = (
    msg: ChatMessage,
    sceneId: string,
    collection: SceneAssetCollection,
    index: number,
    field: string,
    value: string,
  ) => {
    updateVideoScenePackagesInMessage(msg.id, (scenePackages) => updateScenePackageAssetField(scenePackages, sceneId, collection, index, field, value));
  };

  const pushDirectionsArtifact = (directions: CreativeDirectionResponse[], context: { intent: CreationIntent; formValues: Record<string, unknown>; coreMessage: string }) => {
    const message = pushArtifact("已根据表单生成 3 个创意方向，请选择一个进入 plan.md 策划。30 秒未选择将采用推荐方向。", {
      type: "directions",
      title: "创意方向",
      description: `${directions.length} 个方向，第一项为推荐方向`,
      actionLabel: "查看",
      directions,
      intent: context.intent,
      formValues: context.formValues,
      coreMessage: context.coreMessage,
    });
    const recommended = directions.find((direction) => direction.recommended) || directions[0];
    if (recommended) {
      window.setTimeout(() => {
        void handleSelectDirection(message, recommended, true);
      }, 30_000);
    }
  };

  const pushPlanArtifact = (
    plan: PlanMarkdownResponse,
    selectedDirection: CreativeDirectionResponse,
    context: { intent: CreationIntent; formValues: Record<string, unknown>; coreMessage: string },
  ) => {
    const message = pushArtifact("plan.md 创作方案已生成，请审核后继续。30 秒未操作将默认同意。", {
      type: "plan",
      title: "plan.md 创作方案",
      description: `基于「${selectedDirection.title}」生成，模板来自项目内 plan.md`,
      actionLabel: "审核",
      plan,
      selectedDirection,
      intent: context.intent,
      formValues: context.formValues,
      coreMessage: context.coreMessage,
    });
    window.setTimeout(() => {
      void handleApprovePlan(message, true);
    }, Math.max(1, plan.review_timeout_sec) * 1000);
  };

  const pushReviewArtifact = (phase: TaskPhase) => {
    const artifact = REVIEW_ARTIFACT[phase];
    if (!artifact) return;
    const key = `${phase}:artifact`;
    if (announcedPhasesRef.current.has(key)) return;
    announcedPhasesRef.current.add(key);
    pushArtifact(PHASE_MSG[phase] || "请在画布确认。", artifact);
  };

  const appendTimelineEvent = (event: TaskEvent) => {
    const entry = toTimelineEntry(event);
    if (!entry) return;
    setCanvas((c) => {
      const timeline = c.timeline || [];
      if (timeline.some((item) => item.id === entry.id)) return c;
      return { ...c, timeline: [...timeline, entry].slice(-80) };
    });
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

  const makeSnapshot = (): WorkspaceSnapshot => ({
    taskId: currentTaskId,
    messages,
    canvas,
    canvasOpen,
    briefConfirmed,
    lastEventId: lastEventIdRef.current,
    announcedPhases: Array.from(announcedPhasesRef.current),
    briefReadyShown: briefReadyShownRef.current,
  });

  const resetWorkspace = () => {
    unsubRef.current();
    setActiveConversationId("");
    setActiveTaskId("");
    setMessages([]);
    setCanvas(EMPTY_CANVAS);
    setCanvasOpen(false);
    setDialogOpen(false);
    setPendingCore("");
    setPendingIntent("video");
    setBusy(false);
    setBriefConfirmed(false);
    briefConfirmedRef.current = false;
    seenEventIdsRef.current = new Set();
    announcedPhasesRef.current = new Set();
    processedArtifactIdsRef.current = new Set();
    planRevisionArtifactRef.current = null;
    imageRevisionArtifactRef.current = null;
    videoRevisionArtifactRef.current = null;
    briefReadyShownRef.current = false;
    lastEventIdRef.current = 0;
  };

  const applyConversation = async (detail: ConversationDetailResponse) => {
    const snapshot = (detail.conversation.context || {}) as Partial<WorkspaceSnapshot>;
    const restoredMessages = detail.messages.map(messageFromResponse).filter((m): m is ChatMessage => Boolean(m));
    applySnapshot({ ...snapshot, messages: snapshot.messages?.length ? snapshot.messages : restoredMessages });
    const taskId = snapshot.taskId || detail.conversation.current_task_id || "";
    if (taskId) {
      setActiveTaskId(taskId);
      unsubRef.current = subscribeTaskEvents(taskId, onEvent, snapshot.lastEventId || undefined);
      await reconcileTaskFromServer(taskId);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const restoreConversation = async () => {
      restoringRef.current = true;
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      if (!conversationId) {
        resetWorkspace();
        restoringRef.current = false;
        return;
      }
      unsubRef.current();
      seenEventIdsRef.current = new Set();
      announcedPhasesRef.current = new Set();
      briefReadyShownRef.current = false;
      lastEventIdRef.current = 0;
      setActiveConversationId(conversationId);
      setBusy(true);
      try {
        const detail = await api.resumeConversation(conversationId);
        if (cancelled) return;
        await applyConversation(detail);
      } catch (err) {
        if (!cancelled) {
          resetWorkspace();
          pushAssistant(`历史对话恢复失败:${err instanceof Error ? err.message : String(err)}`);
        }
      } finally {
        if (!cancelled) {
          restoringRef.current = false;
          setBusy(false);
        }
      }
    };
    void restoreConversation();
    return () => {
      cancelled = true;
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    };
  }, [conversationId]);

  useEffect(() => {
    if (restoringRef.current || !currentConversationId) return;
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
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      void api
        .updateConversation(currentConversationId, {
          current_task_id: currentTaskId || null,
          last_phase: String(canvas.phase || "idle"),
          context: snapshot as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }, 400);
  }, [messages, canvas, canvasOpen, briefConfirmed, currentTaskId, currentConversationId]);

  const saveChatMessage = (conversation: string, message: ChatMessage) =>
    api.appendConversationMessage(conversation, {
      role: message.role,
      content: message.content,
      payload: { time: message.time, artifact: message.artifact },
    });

  const titleFromPrompt = (text: string) => (text.length > 18 ? `${text.slice(0, 18)}...` : text);

  const ensureConversation = async (title: string): Promise<string> => {
    if (conversationIdRef.current) return conversationIdRef.current;
    const created = await api.createConversation({
      title: titleFromPrompt(title),
      last_phase: String(canvas.phase || "idle"),
      current_task_id: currentTaskId || null,
      context: makeSnapshot() as unknown as Record<string, unknown>,
    });
    setActiveConversationId(created.conversation_id);
    window.dispatchEvent(new Event("pixelflow-conversations-updated"));
    return created.conversation_id;
  };

  const handleSend = async (text: string) => {
    const message: ChatMessage = { id: uid(), role: "user", content: text, time: now() };
    setMessages((m) => [...m, message]);
    let activeConversation = conversationIdRef.current;
    try {
      activeConversation = await ensureConversation(text);
      await saveChatMessage(activeConversation, message);
      if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
    } catch (err) {
      pushAssistant(`对话保存失败:${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    if (planRevisionArtifactRef.current?.intent && planRevisionArtifactRef.current.formValues) {
      const revisionArtifact = planRevisionArtifactRef.current;
      const revisionIntent = revisionArtifact.intent;
      const revisionFormValues = revisionArtifact.formValues;
      if (!isCreationIntent(revisionIntent) || !revisionFormValues) return;
      planRevisionArtifactRef.current = null;
      setBusy(true);
      pushAssistant("已收到修改意见，正在回到采集 Agent 重新生成 3 个创意方向…");
      try {
        const directionResult = await api.generateCreativeDirections({
          intent: revisionIntent,
          values: revisionFormValues,
          product_creative_profile: { revision_feedback: text },
        });
        if (!directionResult.validation.is_complete) {
          pushAssistant(directionResult.validation.message || "表单信息还不完整，请补充后再提交。");
          setBusy(false);
          return;
        }
        pushDirectionsArtifact(directionResult.creative_directions, {
          intent: revisionIntent,
          formValues: revisionFormValues,
          coreMessage: `${revisionArtifact.coreMessage || pendingCore}\n修改意见：${text}`,
        });
        if (conversationIdRef.current) {
          void api
            .updateConversation(conversationIdRef.current, {
              last_phase: "creative_directions_revised",
              context: {
                ...makeSnapshot(),
                revision_feedback: text,
                creative_directions: directionResult.creative_directions,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`重新生成创意方向失败:${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setBusy(false);
      }
      return;
    }
    const pendingImageRevisionArtifact = imageRevisionArtifactRef.current;
    if (pendingImageRevisionArtifact?.imagePrepare && pendingImageRevisionArtifact.imageResult) {
      imageRevisionArtifactRef.current = null;
      setBusy(true);
      pushAssistant("已收到图片修改意见，正在重新准备参数并生成图片…");
      try {
        const imagePrepare = await api.prepareImageGeneration(
          buildImageRevisionPreparePayload({
            formValues: pendingImageRevisionArtifact.formValues,
            selectedDirection: pendingImageRevisionArtifact.selectedDirection as unknown as Record<string, unknown>,
            planMarkdown: pendingImageRevisionArtifact.plan?.plan_markdown,
            feedback: text,
          }),
        );
        if (!imagePrepare.ok) {
          pushArtifact("图片重新生成准备失败，请查看提示。", {
            type: "image_prepare",
            title: "图片重新生成准备",
            description: imagePrepare.message,
            actionLabel: "查看",
            imagePrepare,
            imageRevisionFeedback: text,
            intent: "image",
            formValues: pendingImageRevisionArtifact.formValues,
            selectedDirection: pendingImageRevisionArtifact.selectedDirection,
            plan: pendingImageRevisionArtifact.plan,
          });
          setBusy(false);
          return;
        }
        const imageResult = await api.generateImage({
          method: imagePrepare.method,
          prompt: imagePrepare.prompt,
          negative_prompt: imagePrepare.negative_prompt,
          params: imagePrepare.params,
        });
        const imageResultMessage = pushArtifact(imageResult.ok ? "图片已按修改意见重新生成，请查看结果。" : "图片重新生成失败，请查看错误信息。", {
          type: "image_result",
          title: "图片重新生成结果",
          description: imageResultSummary(imageResult),
          actionLabel: "查看",
          imageResult,
          imagePrepare,
          imageRevisionFeedback: text,
          intent: "image",
          formValues: pendingImageRevisionArtifact.formValues,
          selectedDirection: pendingImageRevisionArtifact.selectedDirection,
          plan: pendingImageRevisionArtifact.plan,
        });
        if (canAcceptImageResult(imageResult)) {
          window.setTimeout(() => {
            void handleAcceptImageResult(imageResultMessage, true);
          }, 30_000);
        }
        if (conversationIdRef.current) {
          void api
            .updateConversation(conversationIdRef.current, {
              last_phase: imageResult.ok ? "image_regenerated" : "image_regeneration_failed",
              context: {
                ...makeSnapshot(),
                image_revision_feedback: text,
                image_prepare: imagePrepare,
                image_result: imageResult,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`图片重新生成失败:${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setBusy(false);
      }
      return;
    }
    const pendingVideoRevisionArtifact = videoRevisionArtifactRef.current;
    const pendingMergedVideo = pendingVideoRevisionArtifact?.mergedVideo;
    const pendingGeneratedSceneVideos = pendingVideoRevisionArtifact?.generatedSceneVideos;
    const pendingVideoScenePackages = pendingVideoRevisionArtifact?.videoScenePackages;
    if (pendingVideoRevisionArtifact && pendingMergedVideo && pendingGeneratedSceneVideos && pendingVideoScenePackages) {
      const revisionArtifact = pendingVideoRevisionArtifact;
      const mergedVideo = pendingMergedVideo;
      const generatedSceneVideos = pendingGeneratedSceneVideos;
      const videoScenePackages = pendingVideoScenePackages;
      const mergedVideoUrl = mergedVideo.merged_video_url;
      videoRevisionArtifactRef.current = null;
      if (!mergedVideoUrl) {
        pushAssistant("当前没有可分析的合并视频链接，无法进入视频修改流程。");
        return;
      }
      setBusy(true);
      pushAssistant("已收到视频修改意见，正在调用视频穿帮分析 Skill…");
      try {
        const flawAnalysis = await api.analyzeVideoFlaws({
          merged_video_url: mergedVideoUrl,
          scene_videos: generatedSceneVideos.scene_videos.map((scene) => ({
            scene_id: scene.scene_id,
            scene_index: scene.scene_index,
            video_url: scene.video_url,
          })),
          scene_packages: videoScenePackages.scene_packages as unknown as Array<Record<string, unknown>>,
          user_feedback: text,
        });
        pushArtifact(flawAnalysis.ok ? "视频穿帮分析已完成，请选择本轮修改策略。" : "视频穿帮分析失败，可选择只按用户意见继续修改。", {
          type: "video_flaw_analysis",
          title: "视频穿帮分析",
          description: flawAnalysis.ok
            ? `${flawAnalysis.affected_scene_ids.length || videoScenePackages.scene_packages.length} 个场景可能需要处理。`
            : flawAnalysis.message,
          actionLabel: "选择",
          videoFlawAnalysis: flawAnalysis,
          videoRevisionFeedback: text,
          videoScenePackages,
          generatedSceneVideos,
          mergedVideo,
          intent: "video",
          formValues: revisionArtifact.formValues,
          selectedDirection: revisionArtifact.selectedDirection,
          plan: revisionArtifact.plan,
        });
        if (conversationIdRef.current) {
          void api
            .updateConversation(conversationIdRef.current, {
              last_phase: flawAnalysis.ok ? "video_flaw_analysis_ready" : "video_flaw_analysis_failed",
              context: {
                ...makeSnapshot(),
                video_revision_feedback: text,
                video_flaw_analysis: flawAnalysis,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`视频穿帮分析失败:${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setBusy(false);
      }
      return;
    }
    setBusy(true);
    pushAssistant("正在调用采集 Agent 识别意图，并抽取可自动填充的表单字段…");
    try {
      const intake = await api.analyzeIntakeIntent({ prompt: text });
      if (intake.intent === "video_analysis") {
        pushAssistant("已识别为视频分析/拆解需求，正在识别媒体链接并调用视频分析 Skill…");
        const videoAnalysis = await api.analyzeStoryboards({ prompt: text });
        pushArtifact(videoAnalysis.ok ? "视频分析已完成，结果如下。" : "视频分析未完成，请查看原因后补充视频链接。", {
          type: "video_analysis_result",
          title: videoAnalysis.mode === "batch" ? "批量视频分析" : "视频分析",
          description: videoAnalysis.ok
            ? `${videoAnalysis.video_urls.length} 个视频，调用 ${videoAnalysis.endpoint}`
            : videoAnalysis.message,
          actionLabel: "查看",
          intent: "video_analysis",
          coreMessage: text,
          videoAnalysis,
        });
        if (conversationIdRef.current) {
          void api
            .updateConversation(conversationIdRef.current, {
              last_phase: videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed",
              context: {
                ...makeSnapshot(),
                intent: "video_analysis",
                intake_intent: intake,
                video_analysis: videoAnalysis,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        return;
      }
      if (isCreationIntent(intake.intent)) {
        setPendingCore(text);
        setPendingIntent(intake.intent);
        setPendingFormValues(intake.values || {});
        pushAssistant(`采集 Agent 判断这是${intake.intent === "video" ? "视频生成" : "图片生成"}需求，已把能识别的信息自动填进表单。请补充确认。`);
        if (conversationIdRef.current) {
          void api
            .updateConversation(conversationIdRef.current, {
              last_phase: "intake_form_pending",
              context: {
                ...makeSnapshot(),
                intent: intake.intent,
                intake_intent: intake,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        setDialogOpen(true);
        return;
      }
      pushAssistant(intake.reason || "我可以帮你生成图片、生成电商带货短视频，或分析已有视频。请再描述一下需求。");
      if (conversationIdRef.current) {
        void api
          .updateConversation(conversationIdRef.current, {
            last_phase: "intake_unknown",
            context: {
              ...makeSnapshot(),
              intake_intent: intake,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`采集 Agent 意图识别失败:${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
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
    appendTimelineEvent(e);
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
      case "auth_revoked":
        // 后端 SSE 会在长连接期间持续复查 content-app 登录态；被禁用或 token 失效时会主动断开。
        pushAssistant(`登录态已失效:${String(e.data.message ?? "请重新从 content-app 进入")}`);
        setBusy(false);
        unsubRef.current();
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
      const results: VideoResult[] = await Promise.all(
        videos.map(async (a, i) => {
          let url = a.url;
          if (a.asset_type === "final_video") {
            try {
              url = await api.assetContentBlobUrl(id, a.asset_id);
            } catch {
              url = "";
            }
          }
          return {
            id: a.asset_id || `r${i}`,
            url,
            assetType: a.asset_type,
            status: a.status === "ready" && url ? "success" : a.status === "error" ? "failed" : "pending",
          };
        }),
      );
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
  const handleConfirmParams = async (form: GenParamsForm) => {
    setDialogOpen(false);
    setPendingFormValues({});
    setBusy(true);
    const values = valuesFromForm(form);
    try {
      const directionResult = await api.generateCreativeDirections({ intent: form.intent, values });
      if (!directionResult.validation.is_complete) {
        pushAssistant(directionResult.validation.message || "表单信息还不完整，请补充后再提交。");
        setBusy(false);
        return;
      }
      pushDirectionsArtifact(directionResult.creative_directions, { intent: form.intent, formValues: values, coreMessage: pendingCore });
      if (conversationIdRef.current) {
        void api
          .updateConversation(conversationIdRef.current, {
            last_phase: `${form.intent}_directions`,
            context: {
              ...makeSnapshot(),
              [`${form.intent}_form`]: form,
              creative_directions: directionResult.creative_directions,
              form_values: values,
              intent: form.intent,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
      setBusy(false);
    } catch (err) {
      pushAssistant(`采集处理失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleSelectDirection = async (msg: ChatMessage, direction: CreativeDirectionResponse, auto = false) => {
    if (!isCreationIntent(msg.artifact?.intent) || !msg.artifact?.formValues) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    setBusy(true);
    pushAssistant(auto ? `30 秒未选择，已默认采用推荐方向「${direction.title}」。` : `已选择创意方向「${direction.title}」，正在生成 plan.md…`);
    try {
      const plan = await api.createPlanMarkdown({
        intent: msg.artifact.intent,
        form_values: msg.artifact.formValues,
        selected_direction: direction as unknown as Record<string, unknown>,
        product_creative_profile: { core_message: msg.artifact.coreMessage || pendingCore },
      });
      pushPlanArtifact(plan, direction, {
        intent: msg.artifact.intent,
        formValues: msg.artifact.formValues,
        coreMessage: msg.artifact.coreMessage || pendingCore,
      });
      if (conversationIdRef.current) {
        void api
          .updateConversation(conversationIdRef.current, {
            last_phase: "plan_review",
            context: {
              ...makeSnapshot(),
              intent: msg.artifact.intent,
              form_values: msg.artifact.formValues,
              selected_direction: direction,
              plan_markdown: plan.plan_markdown,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`plan.md 生成失败:${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const handleApprovePlan = async (msg: ChatMessage, auto = false) => {
    const artifact = msg.artifact;
    if (!artifact?.plan || !artifact.intent || !artifact.formValues || !artifact.selectedDirection) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    if (artifact.intent === "image") {
      setBusy(true);
      pushAssistant(auto ? "30 秒未操作，已默认同意图片 plan.md，正在准备图片生成参数…" : "图片 plan.md 已同意，正在准备图片生成参数…");
      try {
        const imagePrepare = await api.prepareImageGeneration({
          form_values: artifact.formValues,
          plan_markdown: artifact.plan.plan_markdown,
          selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
        });
        pushArtifact(imagePrepare.ok ? "图片生成参数已准备好，下一步可接入博观异步生成和轮询。" : "图片生成准备发现当前能力暂不可用，请按提示调整。", {
          type: "image_prepare",
          title: "图片生成准备",
          description: imagePrepare.ok ? `将调用 ${imagePrepare.endpoint}` : imagePrepare.message,
          actionLabel: "查看",
          imagePrepare,
          intent: "image",
          formValues: artifact.formValues,
          selectedDirection: artifact.selectedDirection,
          plan: artifact.plan,
        });
        if (conversationIdRef.current) {
          void api
            .updateConversation(conversationIdRef.current, {
              last_phase: imagePrepare.ok ? "image_generation_prepared" : "image_generation_blocked",
              context: {
                ...makeSnapshot(),
                plan_approved: true,
                plan_markdown: artifact.plan.plan_markdown,
                image_prepare: imagePrepare,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`图片生成参数准备失败:${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setBusy(false);
      }
      return;
    }
    setBusy(true);
    const formValues = artifact.formValues;
    const selectedDirection = artifact.selectedDirection;
    const targetDurationMs = inferTargetDurationMs([
      artifact.coreMessage,
      artifact.plan.plan_markdown,
      selectedDirection.title,
      selectedDirection.description,
    ]);
    pushAssistant(auto ? "30 秒未操作，已默认同意视频 plan.md，正在准备可编辑场景包…" : "视频 plan.md 已同意，正在准备可编辑场景包…");
    try {
      const videoScenePackages = await api.prepareVideoScenePackages({
        form_values: formValues,
        plan_markdown: artifact.plan.plan_markdown,
        selected_direction: selectedDirection as unknown as Record<string, unknown>,
        target_duration_ms: targetDurationMs,
      });
      let scenePackagesForReview = videoScenePackages;
      let sceneAssetFailures: Array<Record<string, unknown>> = [];
      if (videoScenePackages.ok) {
        pushAssistant("视频场景包已准备好，正在生成角色三视图、场景图和道具图…");
        const sceneAssets = await api.generateSceneAssets({
          scene_packages: videoScenePackages.scene_packages,
          image_size: "1080p",
        });
        scenePackagesForReview = {
          ...videoScenePackages,
          scene_packages: sceneAssets.scene_packages,
          message: sceneAssets.ok ? videoScenePackages.message : sceneAssets.message,
        };
        sceneAssetFailures = sceneAssets.failed_assets;
      }
      pushArtifact(scenePackagesForReview.ok ? "视频场景包和参考图已准备好，请确认后生成视频。" : "视频场景包准备失败，请检查提示。", {
        type: "video_scene_packages",
        title: "视频场景包",
        description: scenePackagesForReview.ok
          ? `${scenePackagesForReview.scene_packages.length} 个场景片段，生成视频前必须确认。`
          : scenePackagesForReview.message,
        actionLabel: "确认",
        videoScenePackages: scenePackagesForReview,
        sceneAssetFailures,
        intent: "video",
        formValues,
        selectedDirection,
        plan: artifact.plan,
      });
      if (conversationIdRef.current) {
        void api
          .updateConversation(conversationIdRef.current, {
            last_phase: videoScenePackages.ok ? "scene_package_ready" : "scene_package_failed",
            context: {
              ...makeSnapshot(),
              form_values: formValues,
              selected_direction: selectedDirection,
              plan_markdown: artifact.plan.plan_markdown,
              plan_approved: true,
              scene_packages: scenePackagesForReview.scene_packages,
              scene_asset_failures: sceneAssetFailures,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`视频场景包准备失败:${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const handleRevisePlan = (msg: ChatMessage) => {
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    planRevisionArtifactRef.current = msg.artifact || null;
    pushAssistant("已暂停当前 plan.md。请在输入框填写修改意见，我会回到采集 Agent 重新生成创意方向。");
    if (conversationIdRef.current) {
      void api
        .updateConversation(conversationIdRef.current, {
          last_phase: "plan_revision_requested",
          context: { ...makeSnapshot(), plan_approved: false, plan_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const handleGenerateImage = async (msg: ChatMessage) => {
    const imagePrepare = msg.artifact?.imagePrepare;
    if (!imagePrepare || !imagePrepare.ok) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    setBusy(true);
    pushAssistant(`正在调用 ${imagePrepare.endpoint} 生成图片…`);
    try {
      const imageResult = await api.generateImage({
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
      });
      const imageResultMessage = pushArtifact(imageResult.ok ? "图片生成完成，请查看结果。" : "图片生成失败，请查看错误信息。", {
        type: "image_result",
        title: "图片生成结果",
        description: imageResultSummary(imageResult),
        actionLabel: "查看",
        imageResult,
        imagePrepare,
        intent: "image",
        formValues: msg.artifact?.formValues,
        selectedDirection: msg.artifact?.selectedDirection,
        plan: msg.artifact?.plan,
      });
      if (canAcceptImageResult(imageResult)) {
        window.setTimeout(() => {
          void handleAcceptImageResult(imageResultMessage, true);
        }, 30_000);
      }
      if (conversationIdRef.current) {
        void api
          .updateConversation(conversationIdRef.current, {
            last_phase: imageResult.ok ? "image_generated" : "image_generation_failed",
            context: {
              ...makeSnapshot(),
              image_prepare: imagePrepare,
              image_result: imageResult,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`图片生成失败:${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  async function handleAcceptImageResult(msg: ChatMessage, auto = false) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    pushAssistant(auto ? "30 秒未收到图片修改意见，已默认满意并结束流程。" : "已确认图片满意，流程结束。");
    if (conversationIdRef.current) {
      void api
        .updateConversation(conversationIdRef.current, {
          last_phase: "image_accepted",
          context: { ...makeSnapshot(), image_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseImageResult(msg: ChatMessage) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    imageRevisionArtifactRef.current = msg.artifact;
    pushAssistant("请在输入框填写图片修改意见，我会基于当前 plan.md 和图片参数重新生成。");
    if (conversationIdRef.current) {
      void api
        .updateConversation(conversationIdRef.current, {
          last_phase: "image_revision_requested",
          context: { ...makeSnapshot(), image_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  const handleGenerateVideoFromScenePackages = async (msg: ChatMessage) => {
    const videoScenePackages = msg.artifact?.videoScenePackages;
    if (!videoScenePackages?.ok || videoScenePackages.scene_packages.length === 0) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    setBusy(true);
    pushAssistant("场景包已确认，正在并行生成场景视频…");
    try {
      const generatedSceneVideos = await api.generateSceneVideos({
        scenes: videoScenePackages.scene_packages.map((scene) => ({
          scene_id: scene.scene_id,
          scene_index: scene.scene_index,
          duration_ms: scene.duration_ms,
          prompt: scene.prompt,
          image_urls: collectSceneImageUrls(scene),
          video_urls: scene.video_urls || [],
          audio_urls: scene.audio_urls || [],
        })),
        ratio: "9:16",
        size: "720p",
        sound: "on",
      });
      if (!generatedSceneVideos.ok) {
        throw new Error(generatedSceneVideos.message || "部分场景视频生成失败");
      }
      pushAssistant("场景视频已生成，正在按场景顺序合并完整视频…");
      const duration = Math.max(1, Math.ceil(videoScenePackages.target_duration_ms / 1000));
      const mergedVideo = await api.mergeSceneVideos({
        scene_videos: generatedSceneVideos.scene_videos.map((scene) => ({
          scene_id: scene.scene_id,
          scene_index: scene.scene_index,
          video_url: scene.video_url,
        })),
        duration,
        size: "1080p",
      });
      const videoResultMessage = pushArtifact(mergedVideo.ok ? "视频生成完成，请查看合并视频和场景视频。" : "视频合并失败，请查看错误信息。", {
        type: "video_result",
        title: "视频生成结果",
        description: mergedVideo.ok ? "合并视频和每个场景视频已返回。" : mergedVideo.message,
        actionLabel: "查看",
        videoScenePackages,
        generatedSceneVideos,
        mergedVideo,
        intent: "video",
        formValues: msg.artifact?.formValues,
        selectedDirection: msg.artifact?.selectedDirection,
        plan: msg.artifact?.plan,
      });
      if (mergedVideo.ok) {
        window.setTimeout(() => {
          void handleAcceptVideoResult(videoResultMessage, true);
        }, 30_000);
      }
      if (mergedVideo.merged_video_url) {
        const result: VideoResult = {
          id: mergedVideo.task_id || "merged-video",
          url: mergedVideo.merged_video_url,
          assetType: "final_video",
          status: mergedVideo.ok ? "success" : "failed",
        };
        setCanvas((c) => ({ ...c, phase: mergedVideo.ok ? "done" : c.phase, results: [result] }));
        setCanvasOpen(true);
      }
      if (conversationIdRef.current) {
        void api
          .updateConversation(conversationIdRef.current, {
            last_phase: mergedVideo.ok ? "video_generated" : "video_merge_failed",
            context: {
              ...makeSnapshot(),
              scene_packages: videoScenePackages.scene_packages,
              generated_scene_videos: generatedSceneVideos.scene_videos,
              merged_video: mergedVideo,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`视频生成失败:${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  async function handleAcceptVideoResult(msg: ChatMessage, auto = false) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    pushAssistant(auto ? "30 秒未收到视频修改意见，已默认无意见并结束流程。" : "已确认视频无修改意见，流程结束。");
    if (conversationIdRef.current) {
      void api
        .updateConversation(conversationIdRef.current, {
          last_phase: "video_accepted",
          context: { ...makeSnapshot(), video_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseVideoResult(msg: ChatMessage) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    videoRevisionArtifactRef.current = msg.artifact;
    pushAssistant("请在输入框填写视频修改意见。我会先做穿帮分析，再让你选择是否结合分析结果重生成受影响场景。");
    if (conversationIdRef.current) {
      void api
        .updateConversation(conversationIdRef.current, {
          last_phase: "video_revision_requested",
          context: { ...makeSnapshot(), video_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  async function handleRegenerateVideoWithRevision(msg: ChatMessage, useFlawAnalysis: boolean) {
    const artifact = msg.artifact;
    if (!artifact?.videoScenePackages || !artifact.generatedSceneVideos || !artifact.mergedVideo || !artifact.videoRevisionFeedback) return;
    if (processedArtifactIdsRef.current.has(msg.id)) return;
    processedArtifactIdsRef.current.add(msg.id);
    setBusy(true);
    const affectedSceneIds = sceneIdsForRevision(
      artifact.videoScenePackages.scene_packages,
      artifact.videoRevisionFeedback,
      artifact.videoFlawAnalysis,
      useFlawAnalysis,
    );
    pushAssistant(`正在重生成 ${affectedSceneIds.size} 个受影响场景，并复用未受影响场景…`);
    try {
      const scenesToRegenerate = artifact.videoScenePackages.scene_packages.filter((scene) => affectedSceneIds.has(scene.scene_id));
      const regenerated = await api.generateSceneVideos({
        scenes: scenesToRegenerate.map((scene) => ({
          scene_id: scene.scene_id,
          scene_index: scene.scene_index,
          duration_ms: scene.duration_ms,
          prompt: revisedScenePrompt(scene, artifact.videoRevisionFeedback || "", artifact.videoFlawAnalysis, useFlawAnalysis),
          image_urls: collectSceneImageUrls(scene),
          video_urls: scene.video_urls || [],
          audio_urls: scene.audio_urls || [],
        })),
        ratio: "9:16",
        size: "720p",
        sound: "on",
      });
      if (!regenerated.ok) {
        throw new Error(regenerated.message || "受影响场景重生成失败");
      }
      const previousByScene = new Map(artifact.generatedSceneVideos.scene_videos.map((scene) => [scene.scene_id, scene]));
      const regeneratedByScene = new Map(regenerated.scene_videos.map((scene) => [scene.scene_id, scene]));
      const nextSceneVideos = artifact.videoScenePackages.scene_packages
        .map((scene) => regeneratedByScene.get(scene.scene_id) || previousByScene.get(scene.scene_id))
        .filter((scene): scene is NonNullable<typeof scene> => Boolean(scene));
      const duration = Math.max(1, Math.ceil(artifact.videoScenePackages.target_duration_ms / 1000));
      const mergedVideo = await api.mergeSceneVideos({
        scene_videos: nextSceneVideos.map((scene) => ({
          scene_id: scene.scene_id,
          scene_index: scene.scene_index,
          video_url: scene.video_url,
        })),
        duration,
        size: "1080p",
      });
      const generatedSceneVideos = {
        ...artifact.generatedSceneVideos,
        ok: regenerated.ok,
        scene_videos: nextSceneVideos,
        message: "已按修改意见更新受影响场景。",
      };
      const videoResultMessage = pushArtifact(mergedVideo.ok ? "视频已按修改意见重新生成，请查看新版本。" : "视频重新合并失败，请查看错误信息。", {
        type: "video_result",
        title: "视频修改结果",
        description: mergedVideo.ok ? "已复用未受影响场景，并合并新版本视频。" : mergedVideo.message,
        actionLabel: "查看",
        videoScenePackages: artifact.videoScenePackages,
        generatedSceneVideos,
        mergedVideo,
        intent: "video",
        formValues: artifact.formValues,
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
      });
      if (mergedVideo.ok) {
        window.setTimeout(() => {
          void handleAcceptVideoResult(videoResultMessage, true);
        }, 30_000);
      }
      if (mergedVideo.merged_video_url) {
        setCanvas((c) => ({
          ...c,
          phase: mergedVideo.ok ? "done" : c.phase,
          results: [
            {
              id: mergedVideo.task_id || "merged-video-revision",
              url: mergedVideo.merged_video_url || "",
              assetType: "final_video",
              status: mergedVideo.ok ? "success" : "failed",
            },
          ],
        }));
        setCanvasOpen(true);
      }
      if (conversationIdRef.current) {
        void api
          .updateConversation(conversationIdRef.current, {
            last_phase: mergedVideo.ok ? "video_regenerated" : "video_regeneration_merge_failed",
            context: {
              ...makeSnapshot(),
              video_revision_feedback: artifact.videoRevisionFeedback,
              video_revision_use_flaw_analysis: useFlawAnalysis,
              affected_scene_ids: Array.from(affectedSceneIds),
              generated_scene_videos: nextSceneVideos,
              merged_video: mergedVideo,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`视频修改重生成失败:${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

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
        onSelectDirection={handleSelectDirection}
        onApprovePlan={handleApprovePlan}
        onRevisePlan={handleRevisePlan}
        onGenerateImage={handleGenerateImage}
        onAcceptImageResult={handleAcceptImageResult}
        onReviseImageResult={handleReviseImageResult}
        onGenerateVideoFromScenePackages={handleGenerateVideoFromScenePackages}
        onAcceptVideoResult={handleAcceptVideoResult}
        onReviseVideoResult={handleReviseVideoResult}
        onRegenerateVideoWithRevision={handleRegenerateVideoWithRevision}
        onUpdateVideoScenePackage={handleUpdateVideoScenePackage}
        onUpdateVideoSceneAssetField={handleUpdateVideoSceneAssetField}
        onOpenArtifact={(msg) => {
          if (!msg.artifact) return;
          setCanvasOpen(true);
          if (msg.artifact.type === "brief") setCanvas((c) => ({ ...c, phase: "brief_review" }));
          if (msg.artifact.type === "results") setCanvas((c) => ({ ...c, phase: "done" }));
          if (msg.artifact.type === "segments") setCanvas((c) => ({ ...c, phase: "segment_review" }));
          if (msg.artifact.type === "edit") setCanvas((c) => ({ ...c, phase: "edit_review" }));
          if (msg.artifact.type === "qc") setCanvas((c) => ({ ...c, phase: "qc_review" }));
          if (msg.artifact.type === "video_result") setCanvas((c) => ({ ...c, phase: "done" }));
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
        <GenParamsDialog
          key={`${pendingIntent}:${pendingCore}`}
          open
          intent={pendingIntent}
          initialCoreMessage={pendingCore}
          initialValues={pendingFormValues}
          onConfirm={handleConfirmParams}
          onCancel={() => {
            setPendingFormValues({});
            setDialogOpen(false);
          }}
        />
      )}
    </div>
  );
}
