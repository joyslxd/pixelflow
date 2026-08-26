# PixelFlow Event Bridge Plugin

该 Plugin 只允许显式公开摘要和最终回复进入稳定事件桥。模型原始 reasoning、Prompt、HTML、链接、代码块、凭据模式和未知事件都被拒绝，不能写入 Sidecar Event Store 或 Gateway Outbox。Plugin 不直接访问 HTTP、数据库或浏览器 SSE；Sidecar Engine 消费其安全事件服务后再持久化。
