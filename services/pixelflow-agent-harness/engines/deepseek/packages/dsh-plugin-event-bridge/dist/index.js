/** 把显式公开信息压缩为稳定 Event，拒绝 reasoning 与不安全文本。 */
export class EventBridge {
    disposed = false;
    events = [];
    publish(event) {
        if (this.disposed)
            throw new Error("event_bridge_disposed");
        if (!isSafeText(event.text))
            throw new Error("public_event_unsafe");
        const safe = { type: event.type, text: event.text.trim() };
        this.events.push(safe);
        return safe;
    }
    drain() {
        if (this.disposed)
            return [];
        return this.events.splice(0);
    }
    dispose() { this.disposed = true; this.events.splice(0); }
}
export const name = "pixelflow-event-bridge";
export function apply(ctx) {
    const bridge = new EventBridge();
    const release = ctx.provide("pixelflowEventBridge", bridge);
    return () => { bridge.dispose(); release(); };
}
function isSafeText(value) {
    const text = value.trim();
    return text.length > 0 && text.length <= 512
        && !/(https?:\/\/|<[^>]+>|```|authorization|api[_-]?key|bearer\s)/iu.test(text);
}
