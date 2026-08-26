# PixelFlow Context Policy Plugin

该 Plugin 只验证进入 Harness 的上下文投影不含凭据、Authorization、Provider 或运行时控制字段。它不会组装业务上下文；上下文仍由 Gateway 的 Context Builder 生成。`maxStringLength` 限制单字段长度，超限或命中禁止键时拒绝新 Run。
