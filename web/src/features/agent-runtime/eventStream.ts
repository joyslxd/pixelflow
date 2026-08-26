/** 可重连的公开 SSE 生命周期；不把 cursor、Authorization 或错误正文写进业务状态。 */

import { streamHarnessEvents } from "@/api/agentRuntime";
import type { PublicAgentEventV1 } from "@/api/contracts";

const RETRY_DELAYS_MS = [300, 800, 1_500, 3_000, 5_000] as const;

export type EventStreamCallbacks = {
  getAfterSequence: () => number;
  shouldContinue: () => boolean;
  onConnecting: (reconnecting: boolean) => void;
  onEvent: (event: PublicAgentEventV1) => Promise<"continue" | "reload">;
  onDisconnected: () => void;
};

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  /** 在取消时立即结束重连等待，避免旧会话延迟回写新工作台。 */

  return new Promise((resolve) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      resolve();
    }, { once: true });
  });
}

export async function reconnectingEventStream(
  conversationId: string,
  runId: string,
  signal: AbortSignal,
  callbacks: EventStreamCallbacks,
): Promise<void> {
  /** 断线始终从最近已提交 sequence 继续；gap 由 Snapshot 重载收敛。 */

  let retry = 0;
  while (!signal.aborted && callbacks.shouldContinue()) {
    callbacks.onConnecting(retry > 0);
    try {
      let reloadRequired = false;
      for await (const frame of streamHarnessEvents(
        conversationId,
        runId,
        callbacks.getAfterSequence(),
        signal,
      )) {
        if (signal.aborted) return;
        const outcome = await callbacks.onEvent(frame.event);
        if (outcome === "reload") {
          reloadRequired = true;
          break;
        }
      }
      if (reloadRequired) {
        retry = 0;
        continue;
      }
      if (!callbacks.shouldContinue()) return;
    } catch {
      if (signal.aborted) return;
    }
    await delay(RETRY_DELAYS_MS[Math.min(retry, RETRY_DELAYS_MS.length - 1)], signal);
    retry += 1;
  }
  if (!signal.aborted) callbacks.onDisconnected();
}
