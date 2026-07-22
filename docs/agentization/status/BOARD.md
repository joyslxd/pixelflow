# PixelFlow Agent 化总进度看板

> 更新时间：2026-07-22
>
> 基线：`02493711e8c9b74ec5f8e54cfadac3881297754c`
>
> 当前结论：四阶段上线方案已确认，当前目标为 R1。模块之间并行、模块内部切片严格串行；M00 首次集成手动启动一次，之后明确阶段检查点和模块最终提交由远端单槽流水线自动集成。当前无真实外部用户，各阶段获批后覆盖全部新对话100%，不使用随机百分比或用户白名单；每个下一切片、生产运行模式/intent范围和真实付费冒烟仍需人工启动/批准。中文提交、中文代码注释和配置逐项中文说明是所有切片的硬性门禁。所有实现模块尚未开工。

## 上线里程碑

| 批次 | 目标日 | 用户可见成果 | 当前状态 | 代码检查点 | 生产状态 |
| --- | ---: | --- | --- | --- | --- |
| R1 | D4 | 自动上下文压缩开始/完成提示、输入排队、刷新恢复、原任务继续 | `design_ready` | 待 M00/M01/M03/M04/M07/M12.3/M13.1 | 未发布；M13.1 后仍待人工批准 `assist+100%` |
| R2 | D9 | 视频会话 Agent：继续、修改、重生、重试、新建、切换、取消、追问 | `planned` | 待 M02/M05/M06/M11/M12.5/M13.2 | 未发布；目标 `primary(video)+100%` |
| R3 | D13 | 图片/编辑、PPT、视频分析接入同一会话 Agent | `planned` | 待 M08/M09/M10/M13.3 | 未发布；目标 `primary(四类intent)+100%` |
| R4 | D16–D18 | 五流程全量门禁、回滚、新对话全面接管验收 | `planned` | 待全部模块和 M13.4–M13.5 | 未发布；保持R3范围100% |

| 模块 | 名称 | Owner | 当前状态 | 已完成切片 | 阻塞/前置 | 合并 SHA |
| --- | --- | --- | --- | ---: | --- | --- |
| M00 | 合同、分支自动化、中文工程门禁、feature flag、测试入口 | A+B | `ready` | 0/5 | 设计评审 | — |
| M01 | 持久化、CAS、Inbox/Outbox | A | `not_started` | 0/5 | M00 | — |
| M02 | LangGraph 会话/Workflow 内核 | A | `not_started` | 0/4 | M00、M01 | — |
| M03 | 模型档案、预算、ContextEnvelope | A | `not_started` | 0/4 | M00 | — |
| M04 | 全局上下文压缩 | A | `not_started` | 0/5 | M01、M03 | — |
| M05 | Supervisor 决策与目标解析 | A | `not_started` | 0/5 | M02–M04 | — |
| M06 | 持久化 External Job Coordinator | A | `not_started` | 0/5 | M01、M02 | — |
| M07 | 前端 Supervisor 事件 Runtime | B | `not_started` | 0/5 | M00 | — |
| M08 | 图片/图片编辑 Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M09 | PPT Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M10 | 视频分析 Adapter | B | `not_started` | 0/4 | M00；联调 M03/M06 | — |
| M11 | 视频生成 Adapter | B | `not_started` | 0/5 | M00；联调 M05/M06 | — |
| M12 | 交互 UI 与 Legacy 迁移 | B | `not_started` | 0/5 | M07 | — |
| M13 | R1–R4 增量 E2E、Shadow、全量发布、回滚 | A+B | `not_started` | 0/5 | 各批次按阶段依赖；最终 M01–M12 | — |

## 当前文件所有权

尚未领取任何实现文件。设计文档由本轮设计任务创建；`scripts/__pycache__/` 是设计前已存在的未跟踪用户文件，不属于本项目任务。

## 下一步

1. 确认 A/B 人员对应关系；日常分支固定为 `feature/dev_0.8.4_boguan`，集成分支固定为 `feature/agent_0.8.4_boguan`。
2. 使用 `superpowers:writing-plans` 为 M00 生成逐文件 TDD 实施计划，计划必须写明 M00-A/M00-B 双分支、各线内部串行、共享文件所有权和首次集成引导步骤。
3. 分别用运行手册的 A/B 首次话术同时启动 `M00-A.1`、`M00-B.1`；每个 Codex 任务只执行一个切片，完成后由开发者手动说“继续下一个未完成切片”。
4. A/B 两线完成后手动启动 `M00-I.1`，验证脚本、Gitee/Jenkins 定时任务、`ready_for_phase_integration/ready_for_integration` 自动集成和每日 02:00 漂移检查；实际远端配置通过前状态不得超过 `automation_local_ready`。
5. M00 绿色后按 R1 顺序并行启动 A 线 M01/M03、B 线 M07；依赖满足后启动 M04 和 M12。每个 Codex 任务只做一个切片，M12.3 结束时自动进入 R1 阶段集成候选，但不会自动开始 M12.4，也不会自动发布生产。
6. R1 所需增量全部进入 Agent 且最新 dev→agent 绿色后，开发者手动启动 M13.1。M13.1 切片通过先写 `ready_for_phase_integration:R1`，远端候选绿色进入 Agent 后才写 `awaiting_release_approval:R1`；唯一发布负责人再使用运行手册 9.17 明确批准后，受控流水线才允许把生产从 `off+0%` 调整到 `assist+100%`。

总看板在合法阶段检查点或最终模块通过闸门并由单槽候选合入 `feature/agent_0.8.4_boguan` 后更新；`phase_integrated` 只表示该批次增量已进入 Agent，不表示模块完成。模块分支内的逐切片实时进度写对应模块状态文件。
