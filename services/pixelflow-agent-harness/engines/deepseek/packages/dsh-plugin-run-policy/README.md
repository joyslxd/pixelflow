# PixelFlow Run Policy Plugin

该 Plugin 只维护单个 Harness Run 的模型步数、业务 Tool 次数、截止时间和取消状态。它不访问数据库、Provider、用户凭据或工作区；达到限制时仅抛出固定错误码，最终 Run 状态仍由 Sidecar Engine 与 Gateway 控制面持久化。

`m0-safe.cordis.yml` 的 `maxModelSteps`、`maxBusinessTools` 和 `deadlineSeconds` 分别限制模型调用、业务 Tool 调用和本 Run 墙钟时间；变更仅影响新 Run。
