/** 把显式公开信息压缩为稳定 Event，拒绝 reasoning 与不安全文本。 */
interface CordisContext {
    provide(name: string, value: unknown): () => void;
}
export interface PublicEvent {
    type: "public_summary" | "response";
    text: string;
}
export declare class EventBridge {
    private disposed;
    private readonly events;
    publish(event: PublicEvent): PublicEvent;
    drain(): PublicEvent[];
    dispose(): void;
}
export declare const name = "pixelflow-event-bridge";
export declare function apply(ctx: CordisContext): () => void;
export {};
