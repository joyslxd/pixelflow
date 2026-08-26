/** 把显式公开信息压缩为稳定 Event，拒绝 reasoning 与不安全文本。 */

interface CordisContext { provide(name: string, value: unknown): () => void; }

export interface PublicEvent {
  type: "public_summary" | "response";
  text: string;
}

export class EventBridge {
  private disposed = false;
  private readonly events: PublicEvent[] = [];

  publish(event: PublicEvent): PublicEvent {
    if (this.disposed) throw new Error("event_bridge_disposed");
    if (!isSafeText(event.text)) throw new Error("public_event_unsafe");
    const safe = { type: event.type, text: event.text.trim() };
    this.events.push(safe);
    return safe;
  }

  drain(): PublicEvent[] {
    if (this.disposed) return [];
    return this.events.splice(0);
  }

  dispose(): void { this.disposed = true; this.events.splice(0); }
}

export const name = "pixelflow-event-bridge";
export function apply(ctx: CordisContext): () => void {
  const bridge = new EventBridge();
  const release = ctx.provide("pixelflowEventBridge", bridge);
  return () => { bridge.dispose(); release(); };
}

function isSafeText(value: string): boolean {
  const text = value.trim();
  return text.length > 0 && text.length <= 512
    && !/(https?:\/\/|<[^>]+>|```|authorization|api[_-]?key|bearer\s)/iu.test(text);
}
