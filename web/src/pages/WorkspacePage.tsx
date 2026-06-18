import { useRef, useState } from "react";
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
  qc: "正在质检…",
  done: "全部完成 🎉",
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
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const unsubRef = useRef<() => void>(() => {});

  const pushAssistant = (content: string) =>
    setMessages((m) => [...m, { id: uid(), role: "assistant", content, time: now() }]);

  const pushArtifact = (content: string, artifact: NonNullable<ChatMessage["artifact"]>) =>
    setMessages((m) => [...m, { id: uid(), role: "assistant", content, time: now(), artifact }]);

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
    if (e.id) seenEventIdsRef.current.add(e.id);
    const phase = (e.data.phase as string) || "";
    switch (e.event) {
      case "phase_change":
        if (phase) {
          // Brief 未人工确认前，忽略 generate/edit/qc/done 阶段回放，避免旧 run 的 pending
          // 事件把画布提前推进到生成结果态。
          if (["generate", "edit", "qc", "done"].includes(phase) && !briefConfirmedRef.current) return;
          setCanvas((c) => ({ ...c, phase: phase as TaskPhase }));
          if (PHASE_MSG[phase] && !announcedPhasesRef.current.has(phase)) {
            announcedPhasesRef.current.add(phase);
            pushAssistant(PHASE_MSG[phase]);
          }
        }
        break;
      case "brief_ready":
        // brief_ready 表示后端在 LangGraph interrupt 前已经准备好 Brief，前端需要展示确认卡。
        if (briefConfirmedRef.current) return;
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

  async function loadResults() {
    // 从 /assets 拉取画布可展示的视频资产。当前只展示 final_video 和 generated_video；
    // jianying_draft 是本地草稿路径，浏览器通常不能直接播放。
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const assets = await api.listAssets(id);
      const videos = assets.filter((a) => a.asset_type === "final_video" || a.asset_type === "generated_video");
      const results: VideoResult[] = videos.map((a, i) => ({
        id: a.asset_id || `r${i}`,
        url: a.url,
        status: a.status === "ready" ? "success" : a.status === "error" ? "failed" : "pending",
      }));
      setCanvas((c) => ({ ...c, phase: "done", results }));
      pushArtifact("生成完成,素材已就绪。点击下方素材卡打开画布查看。", {
        type: "results",
        title: "生成素材",
        description: `${results.length} 条视频结果`,
        actionLabel: "打开",
      });
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
      const confirmed = task.phase !== "brief_review" && task.status !== "pending";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase: (task.phase as TaskPhase) || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
      }));
      if (task.status === "done") {
        await loadResults();
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
      taskIdRef.current = task.task_id;
      briefConfirmedRef.current = false;
      setBriefConfirmed(false);
      seenEventIdsRef.current = new Set();
      announcedPhasesRef.current = new Set();
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

  return (
    <div className="flex h-full min-h-0">
      <ChatPanel
        messages={messages}
        onSubmit={handleSend}
        busy={busy || dialogOpen}
        onOpenArtifact={(msg) => {
          if (!msg.artifact) return;
          setCanvasOpen(true);
          if (msg.artifact.type === "brief" && !briefConfirmedRef.current) setCanvas((c) => ({ ...c, phase: "brief_review" }));
          if (msg.artifact.type === "results") setCanvas((c) => ({ ...c, phase: "done" }));
        }}
      />
      {canvasOpen && <CanvasPanel state={canvas} onApprove={handleApprove} onRevise={handleRevise} onClose={() => setCanvasOpen(false)} briefConfirmed={briefConfirmed} />}
      {dialogOpen && (
        <GenParamsDialog key={pendingCore} open initialCoreMessage={pendingCore} onConfirm={handleConfirmParams} onCancel={() => setDialogOpen(false)} />
      )}
    </div>
  );
}
