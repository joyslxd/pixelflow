# PixelFlow 后端贡献说明

新增 Gateway 接口必须以 `/agent` 开头，并只承担 Controller 职责：认证、DTO 校验、owner 隔离、稳定错误码和调用 Application Service。Provider、模型调用和业务编排不能直接写进 Router。

业务状态必须写入 PixelFlow Repository。浏览器使用 `AgentSnapshotV1`、公开 SSE 与 Workspace revision 恢复状态；禁止恢复旧任务轮询、浏览器业务副本或 Sidecar 私有 DTO。

所有 Workspace 修改必须携带 `expected_workspace_revision`。图片和视频生成必须经 Gateway GenerationJob；长期记忆写入必须经 `LongTermMemoryService` 和 WriteOutbox。Authorization 只在请求作用域内使用，不能存入日志、配置、Snapshot、测试 fixture 或 Sidecar。

提交前运行 M00 中文工程门禁、Ruff、相关 pytest、前端类型检查和 Harness 合同测试。新增人工注释、docstring、配置说明和提交信息必须使用中文。
