# PixelFlow Agent 化总进度看板

> 更新时间：2026-07-28
>
> 原始设计基线：`02493711e8c9b74ec5f8e54cfadac3881297754c`；M00-A/M00-B 共同 Agent 基线：`8e626ae232d984f14fa9954b672b4e025894d426`。M00-I.1 不固定使用本页旧快照，必须在执行时重新 fetch 四条远端引用。
>
> 当前结论：M13.1 / R1 已完成唯一负责人批准的人工生产发布，生产为 `assist + enabled_intents=[] + 100% + context_compaction=true`，现有阶段工作流继续拥有业务推进权。发布负责人确认上传、重启和启动日志正常；未提供截图，Codex 的外部未认证探测确认 `/agent/health` 到达认证边界并返回预期 `401`，不把它夸大为已认证功能 smoke。自动化状态仍为 `automation_local_ready`；没有 Jenkins 或其他远端 CI，不标记 `automation_active`。M02、M05、M11 已完成最终模块集成；M13.2/R2 和任何 `primary` 生产变化仍须分别按执行手册人工启动。

## 上线里程碑

| 批次 | 目标日 | 用户可见成果 | 当前状态 | 代码检查点 | 生产状态 |
| --- | ---: | --- | --- | --- | --- |
| R1 | D4 | 自动上下文压缩开始/完成提示、输入排队、刷新恢复、原任务继续 | `released` | M00、M01、M03、M04、M07、M12.3、M13.1 已进入 Agent；生产配置提交 `38a782b` | 已人工发布 `assist+100%`；负责人确认启动日志正常，未报告红线异常 |
| R2 | D9 | 视频会话 Agent：继续、修改、重生、重试、新建、切换、取消、追问 | `planned` | M02、M05、M11 已进入 Agent；待 M06/M12.5/M13.2 | 未发布；目标 `primary(video)+100%` |
| R3 | D13 | 图片/编辑、PPT、视频分析接入同一会话 Agent | `planned` | 待 M08/M09/M10/M13.3 | 未发布；目标 `primary(四类intent)+100%` |
| R4 | D16–D18 | 五流程全量门禁、回滚、新对话全面接管验收 | `planned` | 待全部模块和 M13.4–M13.5 | 未发布；保持R3范围100% |

| 模块 | 名称 | Owner | 当前状态 | 已完成切片 | 阻塞/前置 | 合并 SHA |
| --- | --- | --- | --- | ---: | --- | --- |
| M00 | 合同、分支自动化、中文工程门禁、feature flag、测试入口 | A+B | `merged` | 5/5 | 无；门禁基线已修复；`automation_local_ready` | `9b7a292`（验收实现）；`1aba4ae` + `4514ffe`（基线修复与审核加固） |
| M01 | 持久化、CAS、Inbox/Outbox | A | `merged` | 5/5 | 无；已进入 Agent | `337a191` |
| M02 | LangGraph 会话/Workflow 内核 | A | `merged` | 4/4 | 无；已进入 Agent | `e77bdcd` |
| M03 | 模型档案、预算、ContextEnvelope | A | `merged` | 4/4 | 无；已进入 Agent | `e43b5e9` |
| M04 | 全局上下文压缩 | A | `merged` | 5/5 | 无；已进入 Agent | `7e4f4c3` |
| M05 | Supervisor 决策与目标解析 | A | `merged` | 5/5 | 无；已进入 Agent | `2c0c0bc` |
| M06 | 持久化 External Job Coordinator | A | `not_started` | 0/5 | M01、M02 | — |
| M07 | 前端 Supervisor 事件 Runtime | B | `merged` | 5/5 | M00 | `a5a7b75` |
| M08 | 图片/图片编辑 Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M09 | PPT Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M10 | 视频分析 Adapter | B | `not_started` | 0/4 | M00；联调 M03/M06 | — |
| M11 | 视频生成 Adapter | B | `merged` | 5/5 | 无；已进入 Agent，R2 真实联调仍依赖 M06 | `5ed26af` |
| M12 | 交互 UI 与 Legacy 迁移 | B | `phase_integrated` | 0/5 | M07 | `af3f7c1` |
| M13 | R1–R4 增量 E2E、Shadow、全量发布、回滚 | A+B | `phase_integrated` | 1/5 | R1 已发布生产；R2–R4 仍按阶段依赖和独立批准执行 | `328fb53` |

## 当前文件所有权

M00-A、M00-B 和 M00-I.1 写锁均已释放。M00-I.1 使用唯一新候选 `codex/integrate-m00-20260724-0043` 完成，没有复用上一条 blocked 候选。门禁基线修复使用独立候选 `codex/integrate-m00-gate-repair-20260724-164428` 和全局单槽锁，推送确认后释放。M03 使用全新候选 `codex/integrate-m03-20260724-101526-afe4c4f6` 完成最终集成。M01 首轮候选因权威清单未固化而安全阻塞；修复后使用全新候选 `codex/integrate-m01-20260724-114004-b292f538` 完成最终集成，没有复用原阻塞候选。M04 使用全新候选 `codex/integrate-m04-20260725-011234-0f2661e4` 完成最终集成；集成前仅以状态提交规范 checkpoint 元数据，没有改写 M04.5 业务实现。M02 使用全新候选 `codex/integrate-m02-20260727-224341-c8add0a7` 完成最终集成。M05 首次执行在候选创建前因状态占位值不规范而停止，第二条候选 `codex/integrate-m05-20260728-053559-3206adb1` 因本地临时门禁 wrapper 的 PowerShell 5.1 解析错误安全阻塞；修复入口并在保留候选复跑 Final 后，使用全新候选 `codex/integrate-m05-20260728-054138-49267ca5` 完成最终集成，没有复用 blocked 候选。M11 首次候选 `codex/integrate-m11-20260728-102519-7a52afec` 因缺少全新 worktree 的前端本地依赖而安全阻塞；补齐依赖并在保留候选复跑 Final 绿色后，只恢复模块入口，再使用全新候选 `codex/integrate-m11-20260728-110448-578e18ae` 完成最终集成，没有复用 blocked 候选。M01/M02/M03/M04/M05/M11 全局单槽锁和模块写锁均已释放；根工作区及原模块 worktree 中既有用户文件未被删除或纳入提交。

## 下一步

1. M01、M02、M03、M04、M05、M11 已完成最终集成，不得重复执行其最后切片或 9.10A。本次任务不自动启动 M06、M12、M13.2 或任何其他模块切片。
2. 当前自动化状态为 `automation_local_ready`。模块开工、阶段/最终集成和 dev→agent 漂移检查均人工触发仓库脚本；只有未来实际部署并验收远端 CI 后才能提升为 `automation_active`。
3. R1 已按运行手册 9.17 完成人工发布并保持 `assist+100%`；M02、M05、M11 已进入最新 Agent，M06 仍须由独立新任务完成安全预检后启动。本记录不授权 M13.2/R2、`primary(video)`、真实付费供应商测试或 Agent→dev 合并。

总看板在合法阶段检查点或最终模块通过闸门并由单槽候选合入 `feature/agent_0.8.4_boguan` 后更新；当前单槽候选由开发者人工触发。`phase_integrated` 只表示该批次增量已进入 Agent，不表示模块完成。模块分支内的逐切片实时进度写对应模块状态文件。
