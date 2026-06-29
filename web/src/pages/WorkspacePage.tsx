import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { StoryboardPanel } from "@/components/canvas/StoryboardPanel";
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
import type { AgentUserMessagePayload } from "@/lib/authStorage";
import {
  appendVisibleConversationMessage,
  messageConversationId,
  replaceMessageById,
  restoredConversationMessages,
  shouldApplyVisibleConversationSideEffect,
} from "@/lib/conversationRouting";
import { buildImageRevisionPreparePayload, canAcceptImageResult, imageResultSummary } from "@/lib/imageReview";
import {
  collectSceneImageUrls,
  durationMsForSubmit,
  inferTargetDurationMs,
  sceneIdsForRevision,
  updateScenePackageField,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import { formatClockTime } from "@/lib/time";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "@/lib/types";

let seq = 0;
const uid = () => `m${++seq}`;
const now = () => formatClockTime(new Date().toISOString());

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
    time: formatClockTime(event.created_at, "zh-CN", undefined, now()),
  };
}

interface WorkspaceSnapshot {
  taskId: string;
  messages?: ChatMessage[];
  pendingMaterials: Array<Record<string, unknown>>;
  canvas: CanvasState;
  canvasOpen: boolean;
  briefConfirmed: boolean;
  lastEventId: number;
  announcedPhases: string[];
  briefReadyShown: boolean;
}

type ChatArtifact = NonNullable<ChatMessage["artifact"]>;

interface PendingConversationArtifact {
  conversationId: string;
  artifact: ChatArtifact;
}

interface PendingDialogContext {
  conversationId: string;
  coreMessage: string;
  materials: Array<Record<string, unknown>>;
  intakeContext?: Record<string, unknown>;
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
        image_count: form.image_count,
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

function mergeMaterials(...groups: Array<Array<Record<string, unknown>> | undefined>): Array<Record<string, unknown>> {
  const seen = new Set<string>();
  const merged: Array<Record<string, unknown>> = [];
  for (const group of groups) {
    for (const item of group || []) {
      const key = String(item.url || item.path || item.image_url || item.imageUrl || item.filename || JSON.stringify(item));
      if (!key || seen.has(key)) continue;
      seen.add(key);
      merged.push(item);
    }
  }
  return merged;
}

function isQuotaInsufficientPayload(value: unknown): boolean {
  if (!value) return false;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (record.quota_insufficient === true) return true;
    if (record.status_code === 402) return true;
    return Object.values(record).some((item) => isQuotaInsufficientPayload(item));
  }
  const text = String(value).toLowerCase();
  return ["额度不足", "余额不足", "没有有效的额度", "有效的额度", "剩余额度", "充值", "quota insufficient", "insufficient quota", "payment required"].some((keyword) =>
    text.includes(keyword.toLowerCase()),
  );
}

function quotaMessage(fallback: string) {
  return `${fallback} 当前操作已暂停，充值后回到本对话可以继续执行。`;
}

function processedArtifactKey(message: Pick<ChatMessage, "id">, conversationId: string): string {
  return `${conversationId || "local"}:${message.id}`;
}

function messageFromResponse(message: ConversationMessageResponse, conversationId: string): ChatMessage | null {
  if (message.role === "system") return null;
  const artifact = message.payload.artifact as ChatMessage["artifact"] | undefined;
  const materials = Array.isArray(message.payload.materials) ? (message.payload.materials as Array<Record<string, unknown>>) : undefined;
  const clientMessageId = typeof message.payload.client_message_id === "string" ? message.payload.client_message_id : "";
  return {
    id: clientMessageId || message.message_id,
    conversationId,
    role: message.role,
    content: message.content,
    materials,
    time: formatClockTime(message.created_at),
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
  const [selectedStoryboardMessageId, setSelectedStoryboardMessageId] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [pendingCore, setPendingCore] = useState("");
  const [pendingIntent, setPendingIntent] = useState<CreationIntent>("video");
  const [pendingFormValues, setPendingFormValues] = useState<Record<string, unknown>>({});
  const [pendingMaterials, setPendingMaterials] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(false);
  const [briefConfirmed, setBriefConfirmed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState("");

  // 接收来自 content-app 的用户消息（通过 postMessage + CustomEvent）
  useEffect(() => {
    // 先检查是否已有等待消费的消息
    if (window.__CONTENT_APP_USER_MESSAGE__) {
      const msg = window.__CONTENT_APP_USER_MESSAGE__;
      window.__CONTENT_APP_USER_MESSAGE__ = undefined;
      handleSend(msg);
      return;
    }
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string | AgentUserMessagePayload>).detail;
      if (detail) handleSend(detail);
    };
    window.addEventListener("contentAppUserMessage", handler);
    return () => window.removeEventListener("contentAppUserMessage", handler);
  }, []);

  // 运行中上下文：这些值主要给异步 SSE 回调读取，不需要每次变化都触发 React 重渲染。
  // 可以类比后端 Service 内部字段，保存当前 taskId、事件去重集合和取消订阅函数。
  const [currentTaskId, setCurrentTaskId] = useState("");
  const conversationIdRef = useRef<string>("");
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const processedArtifactIdsRef = useRef(new Set<string>());
  const pendingDialogContextRef = useRef<PendingDialogContext | null>(null);
  const planRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const imageRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const videoRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
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

  const isVisibleConversation = (targetConversationId: string) =>
    shouldApplyVisibleConversationSideEffect(conversationIdRef.current, targetConversationId);

  const setBusyForConversation = (targetConversationId: string, value: boolean) => {
    if (isVisibleConversation(targetConversationId)) setBusy(value);
  };

  const setCanvasOpenForConversation = (targetConversationId: string, value: boolean) => {
    if (isVisibleConversation(targetConversationId)) {
      setCanvasOpen(value);
      if (!value) setSelectedStoryboardMessageId("");
    }
  };

  const setCanvasForConversation = (
    targetConversationId: string,
    updater: CanvasState | ((current: CanvasState) => CanvasState),
  ) => {
    if (!isVisibleConversation(targetConversationId)) return;
    setCanvas(updater);
  };

  const beginArtifactAction = (msg: ChatMessage, targetConversationId: string): string => {
    const key = processedArtifactKey(msg, targetConversationId);
    if (processedArtifactIdsRef.current.has(key)) return "";
    processedArtifactIdsRef.current.add(key);
    return key;
  };

  const releaseArtifactAction = (key: string) => {
    if (key) processedArtifactIdsRef.current.delete(key);
  };

  const persistChatMessage = async (conversation: string, message: ChatMessage): Promise<ChatMessage> => {
    const saved = await api.appendConversationMessage(conversation, {
      role: message.role,
      content: message.content,
      payload: { artifact: message.artifact, materials: message.materials || [], client_message_id: message.id },
    });
    return {
      ...message,
      id: message.id,
      conversationId: conversation,
      time: formatClockTime(saved.created_at),
    };
  };

  const appendMessageForConversation = async (message: ChatMessage, targetConversationId: string): Promise<ChatMessage> => {
    if (targetConversationId) {
      const optimisticMessage = { ...message, conversationId: targetConversationId, time: message.time || now() };
      setMessages((items) =>
        appendVisibleConversationMessage(items, {
          activeConversationId: conversationIdRef.current,
          targetConversationId,
          message: optimisticMessage,
        }),
      );
      try {
        const savedMessage = await persistChatMessage(targetConversationId, optimisticMessage);
        setMessages((items) => replaceMessageById(items, optimisticMessage.id, savedMessage));
        return savedMessage;
      } catch {
        return optimisticMessage;
      }
    }
    const localMessage = { ...message, time: message.time || now() };
    setMessages((items) => [...items, localMessage]);
    return localMessage;
  };

  const pushAssistant = (content: string, targetConversationId = conversationIdRef.current) => {
    const message: ChatMessage = { id: uid(), conversationId: targetConversationId || undefined, role: "assistant", content, time: "" };
    void appendMessageForConversation(message, targetConversationId);
  };

  const pushArtifact = (content: string, artifact: ChatArtifact, targetConversationId = conversationIdRef.current) => {
    const message: ChatMessage = { id: uid(), conversationId: targetConversationId || undefined, role: "assistant", content, time: "", artifact };
    void appendMessageForConversation(message, targetConversationId);
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

  const pushDirectionsArtifact = (
    directions: CreativeDirectionResponse[],
    context: {
      intent: CreationIntent;
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
    },
    targetConversationId = conversationIdRef.current,
  ) => {
    const message = pushArtifact("已根据表单生成 3 个创意方向，请选择一个进入 plan.md 策划。30 秒未选择将采用推荐方向。", {
      type: "directions",
      title: "创意方向",
      description: `${directions.length} 个方向，第一项为推荐方向`,
      actionLabel: "查看",
      directions,
      intent: context.intent,
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
    }, targetConversationId);
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
    context: {
      intent: CreationIntent;
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
    },
    targetConversationId = conversationIdRef.current,
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
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
    }, targetConversationId);
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
    if (Array.isArray(snapshot.pendingMaterials)) setPendingMaterials(snapshot.pendingMaterials);
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
    pendingMaterials,
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
    setSelectedStoryboardMessageId("");
    setDialogOpen(false);
    setPendingCore("");
    setPendingIntent("video");
    setPendingMaterials([]);
    setBusy(false);
    setBriefConfirmed(false);
    briefConfirmedRef.current = false;
    seenEventIdsRef.current = new Set();
    announcedPhasesRef.current = new Set();
    processedArtifactIdsRef.current = new Set();
    pendingDialogContextRef.current = null;
    planRevisionArtifactRef.current = null;
    imageRevisionArtifactRef.current = null;
    videoRevisionArtifactRef.current = null;
    briefReadyShownRef.current = false;
    lastEventIdRef.current = 0;
  };

  const applyConversation = async (detail: ConversationDetailResponse) => {
    const snapshot = (detail.conversation.context || {}) as Partial<WorkspaceSnapshot>;
    const restoredMessages = detail.messages
      .map((message) => messageFromResponse(message, detail.conversation.conversation_id))
      .filter((m): m is ChatMessage => Boolean(m));
    applySnapshot({ ...snapshot, messages: restoredConversationMessages(undefined, restoredMessages) });
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
      pendingMaterials,
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
  }, [pendingMaterials, canvas, canvasOpen, briefConfirmed, currentTaskId, currentConversationId]);

  const titleFromPrompt = (text: string) => {
    const normalized = text.trim() || "带附件对话";
    return normalized.length > 18 ? `${normalized.slice(0, 18)}...` : normalized;
  };

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

  const normalizeSendInput = (input: string | AgentUserMessagePayload): AgentUserMessagePayload => {
    if (typeof input === "string") return { content: input, materials: [] };
    return { content: input.content, materials: Array.isArray(input.materials) ? input.materials : [] };
  };

  const handleSend = async (input: string | AgentUserMessagePayload) => {
    const { content: text, materials = [] } = normalizeSendInput(input);
    let activeConversation = conversationIdRef.current;
    const message: ChatMessage = { id: uid(), conversationId: activeConversation || undefined, role: "user", content: text, materials, time: "" };
    try {
      activeConversation = await ensureConversation(text);
      await appendMessageForConversation(message, activeConversation);
      if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
    } catch (err) {
      pushAssistant(`对话保存失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      return;
    }
    const pendingPlanRevision = planRevisionArtifactRef.current;
    if (pendingPlanRevision?.conversationId === activeConversation && pendingPlanRevision.artifact.intent && pendingPlanRevision.artifact.formValues) {
      const revisionArtifact = pendingPlanRevision.artifact;
      const revisionIntent = revisionArtifact.intent;
      const revisionFormValues = revisionArtifact.formValues;
      const flowMaterials = mergeMaterials(revisionArtifact.materials, materials);
      if (!isCreationIntent(revisionIntent) || !revisionFormValues) return;
      planRevisionArtifactRef.current = null;
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到修改意见，正在回到采集 Agent 重新生成 3 个创意方向…", activeConversation);
      try {
        const directionResult = await api.generateCreativeDirections({
          intent: revisionIntent,
          values: revisionFormValues,
          materials: flowMaterials,
          product_creative_profile: { revision_feedback: text },
          intake_context: revisionArtifact.intakeContext,
        });
        if (!directionResult.validation.is_complete) {
          pushAssistant(directionResult.validation.message || "表单信息还不完整，请补充后再提交。", activeConversation);
          setBusyForConversation(activeConversation, false);
          return;
        }
        pushDirectionsArtifact(directionResult.creative_directions, {
          intent: revisionIntent,
          formValues: revisionFormValues,
          materials: flowMaterials,
          coreMessage: `${revisionArtifact.coreMessage || pendingCore}\n修改意见：${text}`,
          intakeContext: directionResult.intake_context || revisionArtifact.intakeContext,
        }, activeConversation);
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: "creative_directions_revised",
              context: {
                ...makeSnapshot(),
                revision_feedback: text,
                materials: flowMaterials,
                intake_context: directionResult.intake_context || revisionArtifact.intakeContext,
                creative_directions: directionResult.creative_directions,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`重新生成创意方向失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    const pendingImageRevision = imageRevisionArtifactRef.current;
    const pendingImageRevisionArtifact = pendingImageRevision?.artifact;
    if (pendingImageRevision?.conversationId === activeConversation && pendingImageRevisionArtifact?.imagePrepare && pendingImageRevisionArtifact.imageResult) {
      const flowMaterials = mergeMaterials(pendingImageRevisionArtifact.materials, materials);
      imageRevisionArtifactRef.current = null;
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到图片修改意见，正在重新准备参数并生成图片…", activeConversation);
      try {
        const imagePrepare = await api.prepareImageGeneration(
          {
            ...buildImageRevisionPreparePayload({
              formValues: pendingImageRevisionArtifact.formValues,
              selectedDirection: pendingImageRevisionArtifact.selectedDirection as unknown as Record<string, unknown>,
              planMarkdown: pendingImageRevisionArtifact.plan?.plan_markdown,
              feedback: text,
            }),
            materials: flowMaterials,
            intake_context: pendingImageRevisionArtifact.intakeContext,
          },
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
            intakeContext: pendingImageRevisionArtifact.intakeContext,
            materials: flowMaterials,
            selectedDirection: pendingImageRevisionArtifact.selectedDirection,
            plan: pendingImageRevisionArtifact.plan,
          }, activeConversation);
          setBusyForConversation(activeConversation, false);
          return;
        }
        const imageResult = await api.generateImage({
          method: imagePrepare.method,
          prompt: imagePrepare.prompt,
          negative_prompt: imagePrepare.negative_prompt,
          params: imagePrepare.params,
        });
        const imageQuotaInsufficient = isQuotaInsufficientPayload(imageResult);
        const imageResultMessage = pushArtifact(imageResult.ok ? "图片已按修改意见重新生成，请查看结果。" : "图片重新生成失败，请查看错误信息。", {
          type: "image_result",
          title: "图片重新生成结果",
          description: imageQuotaInsufficient ? quotaMessage(imageResult.message || "图片重新生成额度不足。") : imageResultSummary(imageResult),
          actionLabel: "查看",
          imageResult,
          imagePrepare,
          imageRevisionFeedback: text,
          intent: "image",
          formValues: pendingImageRevisionArtifact.formValues,
          intakeContext: pendingImageRevisionArtifact.intakeContext,
          materials: flowMaterials,
          selectedDirection: pendingImageRevisionArtifact.selectedDirection,
          plan: pendingImageRevisionArtifact.plan,
        }, activeConversation);
        if (canAcceptImageResult(imageResult)) {
          window.setTimeout(() => {
            void handleAcceptImageResult(imageResultMessage, true);
          }, 30_000);
        }
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: imageResult.ok ? "image_regenerated" : "image_regeneration_failed",
              context: {
                ...makeSnapshot(),
                image_revision_feedback: text,
                intake_context: pendingImageRevisionArtifact.intakeContext,
                materials: flowMaterials,
                image_prepare: imagePrepare,
                image_result: imageResult,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`图片重新生成失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    const pendingVideoRevision = videoRevisionArtifactRef.current;
    const pendingVideoRevisionArtifact = pendingVideoRevision?.artifact;
    const pendingMergedVideo = pendingVideoRevisionArtifact?.mergedVideo;
    const pendingGeneratedSceneVideos = pendingVideoRevisionArtifact?.generatedSceneVideos;
    const pendingVideoScenePackages = pendingVideoRevisionArtifact?.videoScenePackages;
    if (pendingVideoRevision?.conversationId === activeConversation && pendingVideoRevisionArtifact && pendingMergedVideo && pendingGeneratedSceneVideos && pendingVideoScenePackages) {
      const revisionArtifact = pendingVideoRevisionArtifact;
      const flowMaterials = mergeMaterials(revisionArtifact.materials, materials);
      const mergedVideo = pendingMergedVideo;
      const generatedSceneVideos = pendingGeneratedSceneVideos;
      const videoScenePackages = pendingVideoScenePackages;
      const mergedVideoUrl = mergedVideo.merged_video_url;
      videoRevisionArtifactRef.current = null;
      if (!mergedVideoUrl) {
        pushAssistant("当前没有可分析的合并视频链接，无法进入视频修改流程。", activeConversation);
        return;
      }
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到视频修改意见，正在调用视频穿帮分析 Skill…", activeConversation);
      try {
        const flawAnalysis = await api.analyzeVideoFlaws({
          merged_video_url: mergedVideoUrl,
          scene_videos: generatedSceneVideos.scene_videos.map((scene) => ({
            scene_id: scene.scene_id,
            scene_index: scene.scene_index,
            video_url: scene.video_url,
          })),
          scene_packages: videoScenePackages.scene_packages as unknown as Array<Record<string, unknown>>,
          materials: flowMaterials,
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
          intakeContext: revisionArtifact.intakeContext,
          materials: flowMaterials,
          selectedDirection: revisionArtifact.selectedDirection,
          plan: revisionArtifact.plan,
        }, activeConversation);
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: flawAnalysis.ok ? "video_flaw_analysis_ready" : "video_flaw_analysis_failed",
              context: {
                ...makeSnapshot(),
                video_revision_feedback: text,
                intake_context: revisionArtifact.intakeContext,
                materials: flowMaterials,
                video_flaw_analysis: flawAnalysis,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`视频穿帮分析失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    setBusyForConversation(activeConversation, true);
    pushAssistant("正在调用采集 Agent 识别意图，并抽取可自动填充的表单字段…", activeConversation);
    try {
      const intake = await api.analyzeIntakeIntent({ prompt: text, materials });
      if (intake.intent === "video_analysis") {
        pushAssistant("已识别为视频分析/拆解需求，正在识别媒体链接并调用视频分析 Skill…", activeConversation);
        const videoAnalysis = await api.analyzeStoryboards({ prompt: text, materials });
        pushArtifact(videoAnalysis.ok ? "视频分析已完成，结果如下。" : "视频分析未完成，请查看原因后补充视频链接。", {
          type: "video_analysis_result",
          title: videoAnalysis.mode === "batch" ? "批量视频分析" : "视频分析",
          description: videoAnalysis.ok
            ? `${videoAnalysis.video_urls.length} 个视频，调用 ${videoAnalysis.endpoint}`
            : videoAnalysis.message,
          actionLabel: "查看",
          intent: "video_analysis",
          coreMessage: text,
          materials,
          videoAnalysis,
        }, activeConversation);
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed",
              context: {
                ...makeSnapshot(),
                intent: "video_analysis",
                materials,
                intake_intent: intake,
                video_analysis: videoAnalysis,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        return;
      }
      if (isCreationIntent(intake.intent)) {
        if (isVisibleConversation(activeConversation)) {
          setPendingCore(text);
          setPendingIntent(intake.intent);
          setPendingFormValues(intake.values || {});
          setPendingMaterials(materials);
          pendingDialogContextRef.current = {
            conversationId: activeConversation,
            coreMessage: text,
            materials,
            intakeContext: intake.intake_context,
          };
        }
        pushAssistant(`采集 Agent 判断这是${intake.intent === "video" ? "视频生成" : "图片生成"}需求，已把能识别的信息自动填进表单。请补充确认。`, activeConversation);
        if (activeConversation) {
          void api
            .updateConversation(activeConversation, {
              last_phase: "intake_form_pending",
              context: {
                ...makeSnapshot(),
                intent: intake.intent,
                materials,
                intake_intent: intake,
                intake_context: intake.intake_context,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        if (isVisibleConversation(activeConversation)) setDialogOpen(true);
        return;
      }
      pushAssistant(intake.reason || "我可以帮你生成图片、生成电商带货短视频，或分析已有视频。请再描述一下需求。", activeConversation);
      if (activeConversation) {
        void api
          .updateConversation(activeConversation, {
            last_phase: "intake_unknown",
            context: {
              ...makeSnapshot(),
              intake_intent: intake,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      pushAssistant(`采集 Agent 意图识别失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
    } finally {
      setBusyForConversation(activeConversation, false);
    }
  };

  const handleRetryVideoAnalysis = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.videoAnalysis || artifact.videoAnalysis.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const prompt = artifact.coreMessage || msg.content;
    const materials = artifact.materials || msg.materials || [];
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在重新调用视频分析 Skill…", targetConversationId);
    try {
      const videoAnalysis = await api.analyzeStoryboards({ prompt, materials });
      if (!videoAnalysis.ok) releaseArtifactAction(processedKey);
      pushArtifact(videoAnalysis.ok ? "视频分析已重新完成，结果如下。" : "视频分析仍未完成，请查看原因后补充视频链接。", {
        type: "video_analysis_result",
        title: videoAnalysis.mode === "batch" ? "批量视频分析" : "视频分析",
        description: videoAnalysis.ok
          ? `${videoAnalysis.video_urls.length} 个视频，调用 ${videoAnalysis.endpoint}`
          : videoAnalysis.message,
        actionLabel: "查看",
        intent: "video_analysis",
        coreMessage: prompt,
        materials,
        videoAnalysis,
      }, targetConversationId);
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed",
            context: {
              ...makeSnapshot(),
              intent: "video_analysis",
              materials,
              video_analysis: videoAnalysis,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频分析重试失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
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
    const dialogContext = pendingDialogContextRef.current;
    const targetConversationId = dialogContext?.conversationId || conversationIdRef.current;
    const flowMaterials = dialogContext?.materials || pendingMaterials;
    const flowCoreMessage = dialogContext?.coreMessage || pendingCore;
    const flowIntakeContext = dialogContext?.intakeContext || {};
    setDialogOpen(false);
    setPendingFormValues({});
    setBusyForConversation(targetConversationId, true);
    const values = valuesFromForm(form);
    try {
      const directionResult = await api.generateCreativeDirections({
        intent: form.intent,
        values,
        materials: flowMaterials,
        intake_context: flowIntakeContext,
      });
      if (!directionResult.validation.is_complete) {
        pushAssistant(directionResult.validation.message || "表单信息还不完整，请补充后再提交。", targetConversationId);
        setBusyForConversation(targetConversationId, false);
        return;
      }
      pushDirectionsArtifact(directionResult.creative_directions, {
        intent: form.intent,
        formValues: values,
        materials: flowMaterials,
        coreMessage: flowCoreMessage,
        intakeContext: directionResult.intake_context || flowIntakeContext,
      }, targetConversationId);
      pendingDialogContextRef.current = null;
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: `${form.intent}_directions`,
            context: {
              ...makeSnapshot(),
              [`${form.intent}_form`]: form,
              creative_directions: directionResult.creative_directions,
              form_values: values,
              intake_context: directionResult.intake_context || flowIntakeContext,
              materials: flowMaterials,
              intent: form.intent,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
      setBusyForConversation(targetConversationId, false);
    } catch (err) {
      pushAssistant(`采集处理失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleSelectDirection = async (msg: ChatMessage, direction: CreativeDirectionResponse, auto = false) => {
    if (!isCreationIntent(msg.artifact?.intent) || !msg.artifact?.formValues) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(auto ? `30 秒未选择，已默认采用推荐方向「${direction.title}」。` : `已选择创意方向「${direction.title}」，正在生成 plan.md…`, targetConversationId);
    try {
      const plan = await api.createPlanMarkdown({
        intent: msg.artifact.intent,
        form_values: msg.artifact.formValues,
        selected_direction: direction as unknown as Record<string, unknown>,
        product_creative_profile: { core_message: msg.artifact.coreMessage || pendingCore },
        intake_context: msg.artifact.intakeContext,
        materials: msg.artifact.materials || [],
      });
      pushPlanArtifact(plan, direction, {
        intent: msg.artifact.intent,
        formValues: msg.artifact.formValues,
        materials: msg.artifact.materials || [],
        coreMessage: msg.artifact.coreMessage || pendingCore,
        intakeContext: msg.artifact.intakeContext,
      }, targetConversationId);
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: "plan_review",
            context: {
              ...makeSnapshot(),
              intent: msg.artifact.intent,
              form_values: msg.artifact.formValues,
              intake_context: msg.artifact.intakeContext,
              materials: msg.artifact.materials || [],
              selected_direction: direction,
              plan_markdown: plan.plan_markdown,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleApprovePlan = async (msg: ChatMessage, auto = false) => {
    const artifact = msg.artifact;
    if (!artifact?.plan || !artifact.intent || !artifact.formValues || !artifact.selectedDirection) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    if (artifact.intent === "image") {
      setBusyForConversation(targetConversationId, true);
      pushAssistant(auto ? "30 秒未操作，已默认同意图片 plan.md，正在准备图片生成参数…" : "图片 plan.md 已同意，正在准备图片生成参数…", targetConversationId);
      try {
        const imagePrepare = await api.prepareImageGeneration({
          form_values: artifact.formValues,
          plan_markdown: artifact.plan.plan_markdown,
          selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
          materials: artifact.materials || [],
          intake_context: artifact.intakeContext,
        });
        if (!imagePrepare.ok) {
          releaseArtifactAction(processedKey);
          pushArtifact("图片生成准备发现当前能力暂不可用，请按提示调整。", {
            type: "image_prepare",
            title: "图片生成准备",
            description: imagePrepare.message,
            actionLabel: "查看",
            imagePrepare,
            intent: "image",
            formValues: artifact.formValues,
            intakeContext: artifact.intakeContext,
            materials: artifact.materials || [],
            selectedDirection: artifact.selectedDirection,
            plan: artifact.plan,
          }, targetConversationId);
          if (targetConversationId) {
            void api
              .updateConversation(targetConversationId, {
                last_phase: "image_generation_blocked",
                context: {
                  ...makeSnapshot(),
                  plan_approved: true,
                  plan_markdown: artifact.plan.plan_markdown,
                  intake_context: artifact.intakeContext,
                  materials: artifact.materials || [],
                  image_prepare: imagePrepare,
                } as unknown as Record<string, unknown>,
              })
              .catch(() => {});
          }
          return;
        }
        pushAssistant(`正在调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
        const imageResult = await api.generateImage({
          method: imagePrepare.method,
          prompt: imagePrepare.prompt,
          negative_prompt: imagePrepare.negative_prompt,
          params: imagePrepare.params,
        });
        const imageQuotaInsufficient = isQuotaInsufficientPayload(imageResult);
        if (!imageResult.ok) releaseArtifactAction(processedKey);
        const imageResultMessage = pushArtifact(imageResult.ok ? "图片生成完成，请查看结果。" : "图片生成失败，请查看错误信息。", {
          type: "image_result",
          title: "图片生成结果",
          description: imageQuotaInsufficient ? quotaMessage(imageResult.message || "图片生成额度不足。") : imageResultSummary(imageResult),
          actionLabel: "查看",
          imageResult,
          imagePrepare,
          intent: "image",
          formValues: artifact.formValues,
          intakeContext: artifact.intakeContext,
          materials: artifact.materials || [],
          selectedDirection: artifact.selectedDirection,
          plan: artifact.plan,
        }, targetConversationId);
        if (canAcceptImageResult(imageResult)) {
          window.setTimeout(() => {
            void handleAcceptImageResult(imageResultMessage, true);
          }, 30_000);
        }
        if (targetConversationId) {
          void api
            .updateConversation(targetConversationId, {
              last_phase: imageResult.ok ? "image_generated" : imageQuotaInsufficient ? "image_generation_quota_paused" : "image_generation_failed",
              context: {
                ...makeSnapshot(),
                plan_approved: true,
                plan_markdown: artifact.plan.plan_markdown,
                intake_context: artifact.intakeContext,
                materials: artifact.materials || [],
                image_prepare: imagePrepare,
                image_result: imageResult,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        releaseArtifactAction(processedKey);
        pushAssistant(`图片生成参数准备失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      } finally {
        setBusyForConversation(targetConversationId, false);
      }
      return;
    }
    setBusyForConversation(targetConversationId, true);
    const formValues = artifact.formValues;
    const selectedDirection = artifact.selectedDirection;
    const targetDurationMs = inferTargetDurationMs([
      artifact.coreMessage,
      artifact.plan.plan_markdown,
      selectedDirection.title,
      selectedDirection.description,
    ]);
    pushAssistant(auto ? "30 秒未操作，已默认同意视频 plan.md，正在准备可编辑场景包…" : "视频 plan.md 已同意，正在准备可编辑场景包…", targetConversationId);
    try {
      const videoScenePackages = await api.prepareVideoScenePackages({
        form_values: formValues,
        plan_markdown: artifact.plan.plan_markdown,
        selected_direction: selectedDirection as unknown as Record<string, unknown>,
        materials: artifact.materials || [],
        target_duration_ms: targetDurationMs,
      });
      let scenePackagesForReview = videoScenePackages;
      let sceneAssetFailures: Array<Record<string, unknown>> = [];
      if (videoScenePackages.ok) {
        pushAssistant("视频场景包已准备好，正在生成角色三视图、场景图和道具图…", targetConversationId);
        const sceneAssets = await api.generateSceneAssets({
          global_assets: videoScenePackages.global_assets,
          scene_packages: videoScenePackages.scene_packages,
          image_size: "1080p",
        });
        scenePackagesForReview = {
          ...videoScenePackages,
          global_assets: sceneAssets.global_assets || videoScenePackages.global_assets,
          scene_packages: sceneAssets.scene_packages,
          message: sceneAssets.ok ? videoScenePackages.message : sceneAssets.message,
        };
        sceneAssetFailures = sceneAssets.failed_assets;
        if (sceneAssets.quota_insufficient) {
          releaseArtifactAction(processedKey);
          pushArtifact("场景参考图生成因额度不足暂停，充值后可从本卡片继续。", {
            type: "video_scene_packages",
            title: "视频场景包",
            description: quotaMessage(sceneAssets.message || "场景参考图生成额度不足。"),
            actionLabel: "继续",
            videoScenePackages: scenePackagesForReview,
            sceneAssetFailures,
            intent: "video",
            formValues,
            intakeContext: artifact.intakeContext,
            materials: artifact.materials || [],
            selectedDirection,
            plan: artifact.plan,
          }, targetConversationId);
          if (targetConversationId) {
            void api
              .updateConversation(targetConversationId, {
                last_phase: "scene_asset_quota_paused",
                context: {
                  ...makeSnapshot(),
                  form_values: formValues,
                  intake_context: artifact.intakeContext,
                  materials: artifact.materials || [],
                  selected_direction: selectedDirection,
                  plan_markdown: artifact.plan.plan_markdown,
                  plan_approved: true,
                  global_assets: scenePackagesForReview.global_assets,
                  scene_packages: scenePackagesForReview.scene_packages,
                  scene_asset_failures: sceneAssetFailures,
                } as unknown as Record<string, unknown>,
              })
              .catch(() => {});
          }
          return;
        }
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
        intakeContext: artifact.intakeContext,
        materials: artifact.materials || [],
        selectedDirection,
        plan: artifact.plan,
      }, targetConversationId);
      if (!scenePackagesForReview.ok) releaseArtifactAction(processedKey);
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: videoScenePackages.ok ? "scene_package_ready" : "scene_package_failed",
            context: {
              ...makeSnapshot(),
              form_values: formValues,
              intake_context: artifact.intakeContext,
              materials: artifact.materials || [],
              selected_direction: selectedDirection,
              plan_markdown: artifact.plan.plan_markdown,
              plan_approved: true,
              global_assets: scenePackagesForReview.global_assets,
              scene_packages: scenePackagesForReview.scene_packages,
              scene_asset_failures: sceneAssetFailures,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频场景包准备失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetrySceneAssets = async (msg: ChatMessage) => {
    const videoScenePackages = msg.artifact?.videoScenePackages;
    const hasSceneAssetFailures = Boolean(msg.artifact?.sceneAssetFailures?.length);
    if (!videoScenePackages?.scene_packages.length || !hasSceneAssetFailures) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在继续生成场景参考图…", targetConversationId);
    try {
      const sceneAssets = await api.generateSceneAssets({
        global_assets: videoScenePackages.global_assets,
        scene_packages: videoScenePackages.scene_packages,
        image_size: "1080p",
      });
      const nextPackages = {
        ...videoScenePackages,
        global_assets: sceneAssets.global_assets || videoScenePackages.global_assets,
        scene_packages: sceneAssets.scene_packages,
        message: sceneAssets.ok ? videoScenePackages.message : sceneAssets.message,
      };
      if (sceneAssets.quota_insufficient) {
        releaseArtifactAction(processedKey);
      }
      if (!sceneAssets.ok) releaseArtifactAction(processedKey);
      pushArtifact(sceneAssets.ok ? "场景参考图已继续生成完成，请确认后生成视频。" : "场景参考图继续生成失败，请查看失败项。", {
        type: "video_scene_packages",
        title: "视频场景包",
        description: sceneAssets.quota_insufficient
          ? quotaMessage(sceneAssets.message || "场景参考图生成额度不足。")
          : `${nextPackages.scene_packages.length} 个场景片段，生成视频前必须确认。`,
        actionLabel: "确认",
        videoScenePackages: nextPackages,
        sceneAssetFailures: sceneAssets.failed_assets,
        intent: "video",
        formValues: msg.artifact?.formValues,
        intakeContext: msg.artifact?.intakeContext,
        materials: msg.artifact?.materials || [],
        selectedDirection: msg.artifact?.selectedDirection,
        plan: msg.artifact?.plan,
      }, targetConversationId);
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: sceneAssets.ok ? "scene_package_ready" : sceneAssets.quota_insufficient ? "scene_asset_quota_paused" : "scene_asset_failed",
            context: {
              ...makeSnapshot(),
              global_assets: nextPackages.global_assets,
              intake_context: msg.artifact?.intakeContext,
              scene_packages: nextPackages.scene_packages,
              scene_asset_failures: sceneAssets.failed_assets,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`场景参考图继续生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRevisePlan = (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    planRevisionArtifactRef.current = msg.artifact ? { conversationId: targetConversationId, artifact: msg.artifact } : null;
    pushAssistant("已暂停当前 plan.md。请在输入框填写修改意见，我会回到采集 Agent 重新生成创意方向。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "plan_revision_requested",
          context: { ...makeSnapshot(), plan_approved: false, plan_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const handleGenerateImage = async (msg: ChatMessage) => {
    const imagePrepare = msg.artifact?.imagePrepare;
    if (!imagePrepare || !imagePrepare.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`正在调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
    try {
      const imageResult = await api.generateImage({
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
      });
      const imageQuotaInsufficient = isQuotaInsufficientPayload(imageResult);
      if (!imageResult.ok) releaseArtifactAction(processedKey);
      const imageResultMessage = pushArtifact(imageResult.ok ? "图片生成完成，请查看结果。" : "图片生成失败，请查看错误信息。", {
        type: "image_result",
        title: "图片生成结果",
        description: imageQuotaInsufficient ? quotaMessage(imageResult.message || "图片生成额度不足。") : imageResultSummary(imageResult),
        actionLabel: "查看",
        imageResult,
        imagePrepare,
        intent: "image",
        formValues: msg.artifact?.formValues,
        materials: msg.artifact?.materials || [],
        selectedDirection: msg.artifact?.selectedDirection,
        plan: msg.artifact?.plan,
      }, targetConversationId);
      if (canAcceptImageResult(imageResult)) {
        window.setTimeout(() => {
          void handleAcceptImageResult(imageResultMessage, true);
        }, 30_000);
      }
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: imageResult.ok ? "image_generated" : imageQuotaInsufficient ? "image_generation_quota_paused" : "image_generation_failed",
            context: {
              ...makeSnapshot(),
              image_prepare: imagePrepare,
              image_result: imageResult,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`图片生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetryImageResult = async (msg: ChatMessage) => {
    const imagePrepare = msg.artifact?.imagePrepare;
    if (!imagePrepare || !msg.artifact?.imageResult || canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`已继续调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
    try {
      const imageResult = await api.generateImage({
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
      });
      const imageQuotaInsufficient = isQuotaInsufficientPayload(imageResult);
      if (!imageResult.ok) releaseArtifactAction(processedKey);
      const imageResultMessage = pushArtifact(imageResult.ok ? "图片生成完成，请查看结果。" : "图片生成失败，请查看错误信息。", {
        type: "image_result",
        title: "图片生成结果",
        description: imageQuotaInsufficient ? quotaMessage(imageResult.message || "图片生成额度不足。") : imageResultSummary(imageResult),
        actionLabel: "查看",
        imageResult,
        imagePrepare,
        intent: "image",
        formValues: msg.artifact?.formValues,
        materials: msg.artifact?.materials || [],
        selectedDirection: msg.artifact?.selectedDirection,
        plan: msg.artifact?.plan,
      }, targetConversationId);
      if (canAcceptImageResult(imageResult)) {
        window.setTimeout(() => {
          void handleAcceptImageResult(imageResultMessage, true);
        }, 30_000);
      }
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: imageResult.ok ? "image_generated" : imageQuotaInsufficient ? "image_generation_quota_paused" : "image_generation_failed",
            context: { ...makeSnapshot(), image_prepare: imagePrepare, image_result: imageResult } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`图片继续生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  async function handleAcceptImageResult(msg: ChatMessage, auto = false) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    pushAssistant(auto ? "30 秒未收到图片修改意见，已默认满意并结束流程。" : "已确认图片满意，流程结束。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "image_accepted",
          context: { ...makeSnapshot(), image_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseImageResult(msg: ChatMessage) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    imageRevisionArtifactRef.current = { conversationId: targetConversationId, artifact: msg.artifact };
    pushAssistant("请在输入框填写图片修改意见，我会基于当前 plan.md 和图片参数重新生成。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "image_revision_requested",
          context: { ...makeSnapshot(), image_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  const handleGenerateVideoFromScenePackages = async (msg: ChatMessage) => {
    const videoScenePackages = msg.artifact?.videoScenePackages;
    if (!videoScenePackages?.ok || videoScenePackages.scene_packages.length === 0) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant("场景包已确认，正在生成场景视频…", targetConversationId);
    try {
      const generatedSceneVideos = await api.generateSceneVideos({
        scenes: videoScenePackages.scene_packages.map((scene) => ({
          scene_id: scene.scene_id,
          scene_index: scene.scene_index,
          duration_ms: durationMsForSubmit(scene.duration_ms),
          prompt: scene.prompt,
          storyline: scene.storyline,
          shot_description: scene.shot_description,
          narration: scene.narration,
          generation_mode: scene.generation_mode,
          image_urls: collectSceneImageUrls(scene, videoScenePackages.global_assets),
          video_urls: scene.video_urls || [],
          audio_urls: scene.audio_urls || [],
        })),
        ratio: "9:16",
        size: "720p",
        sound: "on",
      });
      if (!generatedSceneVideos.ok) {
        const videoQuotaInsufficient = isQuotaInsufficientPayload(generatedSceneVideos);
        releaseArtifactAction(processedKey);
        pushArtifact("视频生成失败：部分场景视频生成失败，请展开失败场景查看原因。", {
          type: "video_result",
          title: "视频生成结果",
          description: videoQuotaInsufficient ? quotaMessage(generatedSceneVideos.message || "场景视频生成额度不足。") : (generatedSceneVideos.message || "部分场景视频生成失败，请查看失败场景。"),
          actionLabel: "查看",
          videoScenePackages,
          generatedSceneVideos,
          intent: "video",
          formValues: msg.artifact?.formValues,
          intakeContext: msg.artifact?.intakeContext,
          materials: msg.artifact?.materials || [],
          selectedDirection: msg.artifact?.selectedDirection,
          plan: msg.artifact?.plan,
        }, targetConversationId);
        if (targetConversationId) {
          void api
            .updateConversation(targetConversationId, {
              last_phase: "video_generation_failed",
              context: {
                ...makeSnapshot(),
                global_assets: videoScenePackages.global_assets,
                intake_context: msg.artifact?.intakeContext,
                scene_packages: videoScenePackages.scene_packages,
                generated_scene_videos: generatedSceneVideos.scene_videos,
                failed_scenes: generatedSceneVideos.failed_scenes,
                video_quota_insufficient: videoQuotaInsufficient,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
        return;
      }
      pushAssistant("场景视频已生成，正在按场景顺序合并完整视频…", targetConversationId);
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
      const mergeQuotaInsufficient = isQuotaInsufficientPayload(mergedVideo);
      const videoResultMessage = pushArtifact(mergedVideo.ok ? "视频生成完成，请查看合并视频和场景视频。" : "视频合并失败，请查看错误信息。", {
        type: "video_result",
        title: "视频生成结果",
        description: mergedVideo.ok ? "合并视频和每个场景视频已返回。" : mergeQuotaInsufficient ? quotaMessage(mergedVideo.message || "视频合并额度不足。") : mergedVideo.message,
        actionLabel: "查看",
        videoScenePackages,
        generatedSceneVideos,
        mergedVideo,
        intent: "video",
        formValues: msg.artifact?.formValues,
        intakeContext: msg.artifact?.intakeContext,
        materials: msg.artifact?.materials || [],
        selectedDirection: msg.artifact?.selectedDirection,
        plan: msg.artifact?.plan,
      }, targetConversationId);
      if (!mergedVideo.ok) releaseArtifactAction(processedKey);
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
        setCanvasForConversation(targetConversationId, (c) => ({ ...c, phase: mergedVideo.ok ? "done" : c.phase, results: [result] }));
        setCanvasOpenForConversation(targetConversationId, true);
      }
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
              last_phase: mergedVideo.ok ? "video_generated" : mergeQuotaInsufficient ? "video_merge_quota_paused" : "video_merge_failed",
            context: {
              ...makeSnapshot(),
              global_assets: videoScenePackages.global_assets,
              intake_context: msg.artifact?.intakeContext,
              scene_packages: videoScenePackages.scene_packages,
              generated_scene_videos: generatedSceneVideos.scene_videos,
              merged_video: mergedVideo,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetryVideoMerge = async (msg: ChatMessage) => {
    const generatedSceneVideos = msg.artifact?.generatedSceneVideos;
    const videoScenePackages = msg.artifact?.videoScenePackages;
    if (!generatedSceneVideos?.scene_videos.length || !videoScenePackages || msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在继续合并已生成的场景视频…", targetConversationId);
    try {
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
      const mergeQuotaInsufficient = isQuotaInsufficientPayload(mergedVideo);
      if (!mergedVideo.ok) releaseArtifactAction(processedKey);
      const videoResultMessage = pushArtifact(mergedVideo.ok ? "视频合并完成，请查看完整视频。" : "视频合并失败，请查看错误信息。", {
        type: "video_result",
        title: "视频生成结果",
        description: mergedVideo.ok ? "合并视频和每个场景视频已返回。" : mergeQuotaInsufficient ? quotaMessage(mergedVideo.message || "视频合并额度不足。") : mergedVideo.message,
        actionLabel: "查看",
        videoScenePackages,
        generatedSceneVideos,
        mergedVideo,
        intent: "video",
        formValues: msg.artifact?.formValues,
        materials: msg.artifact?.materials || [],
        selectedDirection: msg.artifact?.selectedDirection,
        plan: msg.artifact?.plan,
      }, targetConversationId);
      if (mergedVideo.ok) {
        window.setTimeout(() => {
          void handleAcceptVideoResult(videoResultMessage, true);
        }, 30_000);
      }
      if (mergedVideo.merged_video_url) {
        setCanvasForConversation(targetConversationId, (c) => ({
          ...c,
          phase: mergedVideo.ok ? "done" : c.phase,
          results: [{ id: mergedVideo.task_id || "merged-video", url: mergedVideo.merged_video_url || "", assetType: "final_video", status: mergedVideo.ok ? "success" : "failed" }],
        }));
        setCanvasOpenForConversation(targetConversationId, true);
      }
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
            last_phase: mergedVideo.ok ? "video_generated" : mergeQuotaInsufficient ? "video_merge_quota_paused" : "video_merge_failed",
            context: { ...makeSnapshot(), generated_scene_videos: generatedSceneVideos.scene_videos, merged_video: mergedVideo } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频继续合并失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  async function handleAcceptVideoResult(msg: ChatMessage, auto = false) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    pushAssistant(auto ? "30 秒未收到视频修改意见，已默认无意见并结束流程。" : "已确认视频无修改意见，流程结束。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "video_accepted",
          context: { ...makeSnapshot(), video_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseVideoResult(msg: ChatMessage) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    videoRevisionArtifactRef.current = { conversationId: targetConversationId, artifact: msg.artifact };
    pushAssistant("请在输入框填写视频修改意见。我会先做穿帮分析，再让你选择是否结合分析结果重生成受影响场景。", targetConversationId);
    if (targetConversationId) {
      void api
        .updateConversation(targetConversationId, {
          last_phase: "video_revision_requested",
          context: { ...makeSnapshot(), video_revision_requested: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  async function handleRegenerateVideoWithRevision(msg: ChatMessage, useFlawAnalysis: boolean) {
    const artifact = msg.artifact;
    if (!artifact?.videoScenePackages || !artifact.generatedSceneVideos || !artifact.mergedVideo || !artifact.videoRevisionFeedback) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    const affectedSceneIds = sceneIdsForRevision(
      artifact.videoScenePackages.scene_packages,
      artifact.videoRevisionFeedback,
      artifact.videoFlawAnalysis,
      useFlawAnalysis,
    );
    pushAssistant(`正在重生成 ${affectedSceneIds.size} 个受影响场景，并复用未受影响场景…`, targetConversationId);
    try {
      const scenesToRegenerate = artifact.videoScenePackages.scene_packages.filter((scene) => affectedSceneIds.has(scene.scene_id));
      const regenerated = await api.generateSceneVideos({
        scenes: scenesToRegenerate.map((scene) => ({
          scene_id: scene.scene_id,
          scene_index: scene.scene_index,
          duration_ms: durationMsForSubmit(scene.duration_ms),
          prompt: revisedScenePrompt(scene, artifact.videoRevisionFeedback || "", artifact.videoFlawAnalysis, useFlawAnalysis),
          storyline: scene.storyline,
          shot_description: scene.shot_description,
          narration: scene.narration,
          generation_mode: scene.generation_mode,
          image_urls: collectSceneImageUrls(scene, artifact.videoScenePackages?.global_assets),
          video_urls: scene.video_urls || [],
          audio_urls: scene.audio_urls || [],
        })),
        ratio: "9:16",
        size: "720p",
        sound: "on",
      });
      if (!regenerated.ok) {
        releaseArtifactAction(processedKey);
        pushArtifact("视频修改重生成失败：部分受影响场景生成失败，请展开失败场景查看原因。", {
          type: "video_result",
          title: "视频修改结果",
          description: regenerated.message || "受影响场景重生成失败，请查看失败场景。",
          actionLabel: "查看",
          videoScenePackages: artifact.videoScenePackages,
          generatedSceneVideos: regenerated,
          mergedVideo: artifact.mergedVideo,
          intent: "video",
          formValues: artifact.formValues,
          intakeContext: artifact.intakeContext,
          materials: artifact.materials || [],
          selectedDirection: artifact.selectedDirection,
          plan: artifact.plan,
        }, targetConversationId);
        return;
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
        materials: artifact.materials || [],
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
      }, targetConversationId);
      if (!mergedVideo.ok) releaseArtifactAction(processedKey);
      if (mergedVideo.ok) {
        window.setTimeout(() => {
          void handleAcceptVideoResult(videoResultMessage, true);
        }, 30_000);
      }
      if (mergedVideo.merged_video_url) {
        setCanvasForConversation(targetConversationId, (c) => ({
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
        setCanvasOpenForConversation(targetConversationId, true);
      }
      if (targetConversationId) {
        void api
          .updateConversation(targetConversationId, {
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
      releaseArtifactAction(processedKey);
      pushAssistant(`视频修改重生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
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

  const selectedStoryboardMessage = selectedStoryboardMessageId
    ? messages.find((message) => message.id === selectedStoryboardMessageId && message.artifact?.type === "video_scene_packages")
    : undefined;

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
        onRetryImageResult={handleRetryImageResult}
        onRetrySceneAssets={handleRetrySceneAssets}
        onRetryVideoMerge={handleRetryVideoMerge}
        onRetryVideoAnalysis={handleRetryVideoAnalysis}
        onOpenArtifact={(msg) => {
          if (!msg.artifact) return;
          setCanvasOpen(true);
          if (msg.artifact.type === "video_scene_packages") {
            setSelectedStoryboardMessageId(msg.id);
            return;
          }
          setSelectedStoryboardMessageId("");
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
      {canvasOpen && selectedStoryboardMessage?.artifact?.videoScenePackages ? (
        <StoryboardPanel
          msg={selectedStoryboardMessage}
          onUpdateVideoScenePackage={(sceneId, patch) => handleUpdateVideoScenePackage(selectedStoryboardMessage, sceneId, patch)}
          onGenerateVideo={() => handleGenerateVideoFromScenePackages(selectedStoryboardMessage)}
          onRetrySceneAssets={() => handleRetrySceneAssets(selectedStoryboardMessage)}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
          }}
        />
      ) : canvasOpen && (
        <CanvasPanel
          state={canvas}
          onApprove={handleApprove}
          onRevise={handleRevise}
          onConfirmStage={handleConfirmStage}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
          }}
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
