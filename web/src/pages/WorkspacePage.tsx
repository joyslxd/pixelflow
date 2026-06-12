import { useState } from "react";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import type { ChatMessage, CanvasState, Brief } from "@/lib/chat";
import type { GenParams, VideoResult } from "@/lib/types";

let seq = 0;
const uid = () => `m${++seq}`;
const now = () =>
  new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

function mockBrief(prompt: string): Brief {
  return {
    title: prompt.slice(0, 18) || "新视频",
    platform: "douyin",
    durationSec: 15,
    ratio: "9:16",
    shots: [
      { shotId: "s1", timeRange: "0-3s", sceneType: "hook", durationSec: 3, narration: "开场抓眼球的痛点提问", onscreen: "通勤也要热乎" },
      { shotId: "s2", timeRange: "3-7s", sceneType: "solution", durationSec: 4, narration: "亮出产品核心卖点", onscreen: "316 不锈钢内胆" },
      { shotId: "s3", timeRange: "7-12s", sceneType: "demo", durationSec: 5, narration: "使用演示,真实质感", onscreen: "一键弹盖单手开" },
      { shotId: "s4", timeRange: "12-15s", sceneType: "cta", durationSec: 3, narration: "结尾引导下单", onscreen: "到手 89 元" },
    ],
  };
}

const EMPTY_CANVAS: CanvasState = { phase: "idle", results: [] };

export function WorkspacePage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvas, setCanvas] = useState<CanvasState>(EMPTY_CANVAS);

  const pushAssistant = (content: string) =>
    setMessages((m) => [...m, { id: uid(), role: "assistant", content, time: now() }]);

  // TODO: 接 POST /api/tasks + SSE。当前为前端 mock 流程,便于联调前预览交互。
  const handleSubmit = (prompt: string, _params: GenParams) => {
    setMessages((m) => [...m, { id: uid(), role: "user", content: prompt, time: now() }]);
    setTimeout(() => {
      pushAssistant("已根据你的描述生成分镜 Brief,请在右侧画布确认或修改。");
      setCanvas({ phase: "brief_review", brief: mockBrief(prompt), results: [], estCost: 33.88 });
    }, 500);
  };

  const handleApprove = () => {
    pushAssistant("Brief 已确认,开始生成 4 个分镜片段…");
    const pending: VideoResult[] = Array.from({ length: 4 }, (_, i) => ({
      id: `r${i}`,
      url: "",
      durationSec: 4,
      status: "pending",
    }));
    setCanvas((c) => ({ ...c, phase: "generate", results: pending }));
    setTimeout(() => {
      setCanvas((c) => ({
        ...c,
        phase: "done",
        results: c.results.map((r) => ({ ...r, status: "success" })),
        actualCost: 33.88,
      }));
      pushAssistant("生成完成,4 条成片已就绪。");
    }, 1800);
  };

  const handleRevise = () => {
    pushAssistant("好的,想调整哪部分?(节奏 / 卖点 / 花字 / 时长…)");
    setCanvas((c) => ({ ...c, phase: "creative" }));
  };

  return (
    <div className="flex h-full min-h-0">
      <ChatPanel messages={messages} onSubmit={handleSubmit} />
      <CanvasPanel state={canvas} onApprove={handleApprove} onRevise={handleRevise} />
    </div>
  );
}
