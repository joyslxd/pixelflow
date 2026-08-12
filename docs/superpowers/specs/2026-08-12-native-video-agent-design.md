# PixelFlow Native Video Agent 改造设计

## 文档状态

**状态：已确认，待拆解详细实施计划。**

**日期：2026-08-12**

**目标分支：feature/agent_0.8.5_boguan_joyce**

本文定义 PixelFlow Video Agent 从“Intake 模型 + JSON Planner + Plan Runner”改造为 DeerFlow 风格原生 Tool-calling Agent 的目标架构。本文继承 2026-08-10 Video Agent V2.1 设计中的 Workspace、Operation、确认和确定性安全边界，但取代其中对旧 frontend_v2 执行链和独立 Planner 的兼容方案。

改造后只保留一条运行链，不保留 feature flag、旧 Agent fallback 或 /agent/flows/video/* Job HTTP 编排接口。

## 1. 当前问题

当前系统存在多套“下一步”决策：

1. Intake 模型理解意图并输出中间 JSON。
2. Planner 模型再次理解并输出 JSON Plan。
3. VideoAgentRunner 按 Plan Step 顺序执行。
4. Entrypoint 仍包含路径分类、确认纠正和 fallback。
5. frontend_v2 仍能直接启动、轮询和恢复旧 HTTP Job。

因此模型不能在 Tool 结果返回后自然调整方向，多套状态机还可能同时推进任务。用户感受到的是上下文断裂、行为死板和固定 Workflow。

## 2. 产品目标

用户始终从同一对话入口表达需求，系统应支持：

- 从 idea 讨论创意和脚本；
- 导入成熟脚本并直接进入视频生产；
- 分析参考视频并替换商品、人物或背景；
- 检查指定分镜，定向修改并只重生受影响镜头；
- 根据 Tool、确认和异步 Operation 结果持续判断；
- 在对话中展示安全思考摘要、短计划、步骤耗时和结果；
- 在右侧 Canvas 持续编辑脚本、场景包、参考图、分镜视频和成片。

成功标志不是代码中出现 create_agent()，而是“下一步做什么”只由原生 Agent 根据上下文和 Tool Result 判断，其他组件只负责安全、执行和持久化。

## 3. 架构原则

1. **单一原生 Agent 控制面。** 删除 Intake Agent、JSON Planner、Plan Runner 和前端 Workflow 的决策权。
2. **模型原生选择 Tool。** Tool Call 由 create_agent() 绑定模型直接生成，不经自定义 JSON Plan 翻译。
3. **Registry 只定义能力。** 它管理 Tool、schema 和 mutation 边界，不决定调用顺序。
4. **Executor 单次执行。** 一次只执行一个 Tool Call，不遍历 Plan。
5. **Plan 只用于观察。** Plan/Step 记录目标、活动、结果和耗时，不驱动执行。
6. **确定性边界由代码裁决。** 鉴权、确认、额度、成本、幂等、revision 和恢复不交给模型。
7. **服务端拥有执行事实。** Workspace、Operation 和 Confirmation 是唯一当前事实。
8. **异步任务不占住 Agent。** Operation 启动后结束 invocation，终态再用内部 Turn 唤醒 Agent。
9. **不公开原始思维链。** 只展示用户可见摘要和安全进度。
10. **旧链路硬删除。** 不保留双轨开关、frontend_v2 执行分支或旧 Job API。

## 4. 目标架构

~~~mermaid
flowchart LR
  U["自然语言 / 工作台命令"] --> T["Thin Entrypoint"]
  T --> A["create_video_agent()"]
  W["VideoWorkspace"] --> C["Context Middleware"]
  O["Operation / Confirmation"] --> C
  H["历史 / 附件"] --> C
  C --> A
  A --> P["update_video_plan"]
  A --> TC["原生 Tool Call"]
  TC --> G["Tool Gateway"]
  G --> R["VideoToolRegistry"]
  G --> B["鉴权 / 确认 / 额度 / Revision"]
  B --> E["Executor.execute_tool_call()"]
  E --> D["领域服务 / Provider Adapter"]
  D --> W
  D --> O
  E --> TR["Tool Result"]
  TR --> A
  A --> OUT["回答 / 暂停 / 等待 Operation"]
~~~

### 4.1 唯一 Agent 构造

新增 backend/pixelflow/video_agent/agent.py：

~~~python
def create_video_agent(
    *,
    model,
    registry,
    executor,
    video_repository,
    runtime_repository,
    skill_catalog,
):
    return create_agent(
        model=model,
        system_prompt=VIDEO_AGENT_SYSTEM_PROMPT,
        tools=build_video_agent_tools(registry),
        middleware=[
            VideoWorkspaceContextMiddleware(...),
            VideoPlanMiddleware(...),
            VideoToolGatewayMiddleware(...),
            VideoProgressMiddleware(...),
            VideoLoopLimitMiddleware(max_business_tools=3),
        ],
        state_schema=VideoAgentState,
    )
~~~

打开该文件应能直接看到模型、Prompt、Tools、Middleware 和 State 的装配关系。

### 4.2 保留组件的责任

| 组件 | 处理方式 | 负责 | 禁止 |
|---|---|---|---|
| VideoToolRegistry | 保留并适配 | Tool 目录、schema、mutation 边界 | 指定业务顺序 |
| VideoAgentExecutor | 重构 | 执行单个 Tool、写 Workspace 和事件 | 遍历 Plan、解释意图 |
| VideoWorkspace | 保留 | 项目结构化事实 | 存储私有 reasoning |
| AgentPlan/Step | 保留合同 | 短计划、结果和耗时 | 驱动 Executor |
| Confirmation | 保留 | 阻止未确认操作 | 选择下一 Tool |
| Quota | 保留 | 检查和扣减 | 让模型自行判断额度 |
| Revision | 保留 | 乐观并发保护 | 静默覆盖 |
| Operation | 保留 | 异步任务和恢复事实 | 编排后续 Tool |
| 领域实现 | 保留 | 场景包、生图、生视频、合成 | 回调旧 HTTP API |
| LegacyWorkspace | 保留并拆分 | 对话、编辑和 Canvas | 启动 Job、推导流程 |

## 5. Tool 与 Executor 改造

### 5.1 Registry 转为原生 Tool

build_video_agent_tools(registry) 将每个 VideoToolSpec 映射成 StructuredTool：

- name 和 description 来自 Registry；
- args_schema 直接使用现有 Pydantic input model；
- coroutine 统一进入 VideoToolGateway；
- user_id、workspace_id、plan_id、step_id、Authorization 和 revision 由 ToolRuntime.context 注入，不暴露给模型。

Tool Result 只返回安全摘要、Artifact refs、新 revision、Confirmation 和 Operation 状态。

### 5.2 Executor 一次只执行一个调用

~~~python
async def execute_tool_call(
    self,
    *,
    context: VideoToolContext,
    tool_name: str,
    arguments: Mapping[str, object],
) -> VideoToolResult:
    ...
~~~

删除 run_plan() 和 VideoAgentRunner.notify_turn() 到 run_plan() 的链路。Tool Result 返回后，只有原生 Agent 能决定是否调用下一个 Tool。

### 5.3 业务 Tool

~~~text
inspect_video_workspace
brainstorm_script
import_script
run_script_skill_stage
confirm_script_creative
analyze_reference_video
prepare_scene_packages
generate_scene_assets
inspect_scene
patch_scene
replace_project_assets
generate_scenes
review_generated_scenes
compose_or_export_video
~~~

继续复用现有领域实现，但只允许通过 Python 领域端口或 Adapter 调用。

## 6. Prompt、上下文与 Skill

backend/pixelflow/video_agent/prompts.py 提供唯一 VIDEO_AGENT_SYSTEM_PROMPT，只定义稳定原则：

- 先读取状态，再选择最小下一步；
- 复杂 Turn 使用 1–3 步短计划；
- 只使用已注册 Tool；
- 不绕过确认、额度、权限和 revision；
- 根据 Tool Result 重新判断，不预写完整 Workflow；
- 必要时询问澄清并给出简洁最终回答。

Context Middleware 在每次模型调用前注入：

- 当前 Turn、结构化 Command、历史和附件；
- 当前脚本、版本和确认状态；
- 人物、场景、道具、分镜和参考图状态；
- dirty_scene_ids、variants 和 QC；
- 未完成 Confirmation 和 Operation；
- 权限、可用能力和适用 Skill 指引。

删除独立 Intake、IntakeThinkingResult 机器块和 Intake 到 Planner 的中间翻译。

Skill 保留为模型可读的业务指引，不是状态机。P0 不引入 Sub-agent。

## 7. Plan 生命周期

新增框架 Tool update_video_plan(goal, steps)：

- 复杂任务执行前发布 1–3 步；
- 简单问答、澄清和单次状态读取可以不发布；
- 业务 Tool Call 与当前 Step 关联；
- Tool 开始、完成、失败或等待确认时，服务端更新 Step 和耗时；
- Tool Result 改变局面后，模型可修改未完成步骤；
- 模型漏调 Plan 时，Middleware 自动建立单步记录；
- Plan 不能调用 Executor 或自动运行下一 Step。

VideoLoopLimitMiddleware 每个 invocation 最多允许 3 个业务 Tool Call。达到上限后 Agent 总结当前结果，必要时通过新内部 Turn 继续。

## 8. Confirmation 与异步 Operation

模型可选择需确认 Tool，但 Gateway 在执行前：

1. 计算范围、数量、模型和预估额度；
2. 持久化 Confirmation；
3. 生成确认卡和 awaiting_confirmation Step；
4. 停止当前 invocation。

用户确认或取消后创建结构化 Turn，再次调用 Agent。确认绑定原 Tool Call 和 Workspace revision。

异步流程：

~~~text
Agent Tool Call
  -> 校验并创建 Operation
  -> 启动 Provider Job
  -> 返回任务已启动
  -> 结束 invocation

Operation 终态
  -> 写 Workspace 和 Artifact refs
  -> 更新 Plan Step
  -> 创建内部 resume Turn
  -> 重新调用 Agent
  -> 模型读取结果后继续判断
~~~

Operation 只表达执行事实，不保存预写后续步骤。

## 9. 旧链路删除范围

### 9.1 后端

删除：

- stream_intake_thinking、Intake Prompt、IntakeThinkingResult 和解析器；
- DeepSeekVideoPlanningModel、VideoAgentPlanner 和 JSON Proposal；
- entry_path、关键词路由和 _submit_turn_after_thinking；
- 规划失败回退 inspect 的第二套链路；
- VideoAgentRunner 的 run_plan 执行；
- /agent/flows/video/* 路由；
- /agent/flows/video/jianying-draft/* 路由；
- 只服务旧 HTTP Job 启动、轮询和进程内字典的胶水代码；
- 对应旧路由和兼容测试。

不删除 V2 Tool/Operation Adapter 仍复用的纯领域服务、Provider 客户端、DTO 和结果解析器。

### 9.2 前端

删除：

- frontend_v2 执行分支和默认值；
- startPrepareScenePackagesJob、startGenerateSceneAssetsJob、startSceneVideosJob 等客户端；
- pending Job 保存、轮询、重连和恢复；
- Supervisor Workflow action 和旧 artifact 推进；
- 以聊天卡或 localStorage 作为执行真相的逻辑；
- 要求保留旧 API 的合同测试。

## 10. 历史会话自动升级

orchestration_mode=frontend_v2 的历史会话：

1. 打开时继续只读展示已有产物。
2. 首次发送消息或编辑工作台时，服务端在同一事务中：
   - 读取可验证旧产物；
   - 映射到 VideoWorkspace；
   - 建立 Artifact refs 和版本；
   - 升级为原生 Video Agent 模式；
   - 登记当前 Turn 并调用 Agent。
3. 升级必须幂等，失败时不得部分写入或回退旧 API。
4. 历史 Workflow 只能归档展示，不表达当前进度。

## 11. 前端交互设计

参考 DeerFlow 原生 UI 的信息层级、思考折叠、Tool 活动和 Canvas 交互，保留 PixelFlow 视频领域编辑体验。

### 11.1 Turn 展示顺序

~~~text
用户消息
  -> 思考摘要（流式展开，完成折叠）
  -> 执行计划（复杂任务，最多 3 步）
  -> Tool / Operation / Confirmation 活动
  -> 结果卡片
  -> Agent 最终回答
~~~

所有元素绑定同一 turn_id，按服务端 sequence 排序。

### 11.2 思考摘要

- 当前思考默认展开并显示实时耗时；
- 完成后自动折叠为“思考了 18 秒”和一句摘要；
- 用户可以展开或隐藏；
- 只展示用户可见 reasoning summary 和安全进度；
- 不展示 reasoning_content、隐藏思维链、Prompt 或内部参数；
- 刷新后从服务端事件恢复。

安全进度示例：“正在读取视频工作区”“正在校验分镜生成参数”“正在等待参考图生成”。

### 11.3 计划与步骤

计划卡显示目标、1–3 个步骤、状态、实时或最终耗时、公开结果和 Artifact refs。状态读取使用轻量进度行；产生业务变更的 Tool 使用可展开 Activity。

### 11.4 对话卡片

| 卡片 | 摘要 | 点击后 |
|---|---|---|
| 脚本 | 版本、时长、确认状态 | Script Canvas |
| 场景包 | 人物/场景/道具/分镜数 | Scene Package Canvas |
| 分镜修改 | 镜头 ID、变更、脏状态 | 定位分镜 |
| 参考图 | 缩略图、成功/失败数 | 参考图管理 |
| 分镜视频 | 缩略图、生成状态 | 分镜视频包 |
| QC | 通过状态、问题数 | 质检证据 |
| 成片 | 封面、时长、导出状态 | 播放和下载 |
| Confirmation | 范围、模型、数量、额度 | 确认或取消 |
| Operation | 阶段、进度、耗时 | 相关产物 |

对话卡只用于快速判断，不嵌套完整编辑器。

### 11.5 右侧 Canvas

~~~text
VideoCanvasShell
  |- ScriptCanvas
  |- ScenePackageCanvas
  |- SceneAssetCanvas
  |- SceneVideoCanvas
  |- QualityReviewCanvas
  |- DeliveryCanvas
~~~

Canvas 是项目产物的持续编辑面，不是单条消息的临时弹窗。顶部统一显示名称、版本、状态、关联 Step、保存状态和关闭操作。

场景包继续保留人物、场景、道具、分镜时间码、单镜头修改和重生、多版本选择、“重新生成完成”标记、预览、合成和剪映导出。

工作台精确编辑提交结构化 Turn/Command，自然语言修改提交普通 Turn；两者都不直接调用 Job API。桌面端使用对话 + 右侧 Canvas，移动端 Canvas 全屏。

### 11.6 组件拆分

~~~text
web/src/features/native-video-agent/
  chat/
    AgentTurnGroup.tsx
    AgentReasoningDisclosure.tsx
    AgentActivityTimeline.tsx
    ToolActivityItem.tsx
  cards/
    ArtifactCard.tsx
    ConfirmationCard.tsx
    OperationCard.tsx
    ErrorCard.tsx
  canvas/
    VideoCanvasShell.tsx
    ArtifactCanvasRouter.tsx
    ScriptCanvas.tsx
    ScenePackageCanvas.tsx
    SceneAssetCanvas.tsx
    SceneVideoCanvas.tsx
    QualityReviewCanvas.tsx
    DeliveryCanvas.tsx
  state/
    contracts.ts
    reducer.ts
    selectors.ts
~~~

LegacyWorkspace 最终只装配页面布局、会话、聊天区和 Canvas；MessageBubble 只渲染基础消息。

## 12. 统一服务端事件

~~~text
agent.reasoning_summary.delta
agent.reasoning_summary.completed
agent.plan.created
agent.plan.updated
agent.tool.started
agent.tool.progress
agent.tool.completed
agent.tool.failed
agent.confirmation.requested
agent.operation.updated
agent.artifact.updated
agent.response.delta
agent.response.completed
~~~

所有事件包含 conversation_id、turn_id、sequence 和服务端时间。Tool 事件含 tool_call_id，Plan 事件含 plan_id/step_id，Operation 事件含 operation_id。

Snapshot 恢复事实，事件提供实时体验。前端 reducer 必须幂等并忽略旧 sequence，不得根据文案猜状态。

## 13. 错误处理

- **模型失败：** 同一原生链内有限重试，超限后记录失败；不回退旧 Planner。
- **Tool 无效：** Registry/schema 拒绝并返回安全 ToolMessage；重复失败由 Loop Limit 停止。
- **Revision 冲突：** 不覆盖，返回最新摘要让 Agent 重新判断；破坏性调用重新确认。
- **额度不足：** 不启动 Provider，显示可恢复额度卡。
- **Operation 失败：** 保存错误和局部结果，唤醒 Agent 判断局部重试。
- **前端断线：** 不影响执行，从 Snapshot + sequence 恢复。
- **历史升级失败：** 保持只读并允许重试，不部分升级。

## 14. 实施批次

### P0-1：原生 Agent 骨架与单 Tool 执行

**业务效果：** 单一 Agent 读取 Workspace、原生选 Tool，结果回到同一模型循环。

- 新建 Agent、Prompt、State、Tool adapter 和 Middleware；
- Registry 映射 StructuredTool；
- Executor 提供 execute_tool_call；
- 重写 Thin Entrypoint 和 Gateway 装配；
- 先验证 inspect、import、brainstorm、patch；
- 新链路直接作为唯一默认。

### P0-2：计划、思考摘要与 Turn UI

**业务效果：** 展示安全思考摘要和短计划，Tool 实时显示状态、耗时和结果。

- 实现 update_video_plan 和 Plan Middleware；
- 实现 reasoning summary、response、tool 统一事件；
- 实现 AgentTurnGroup、思考折叠和 Activity Timeline；
- 保证刷新恢复和事件顺序。

### P0-3：Confirmation 与 Operation

**业务效果：** Agent 可选全部视频 Tool；费用确认后执行，长任务完成后自动继续判断。

- Gateway 接入确认、额度和 revision；
- 异步 Tool 统一 Operation；
- 终态创建 resume Turn；
- 实现 Confirmation、Operation、Quota、Error 卡；
- 验证重启、重连、幂等和局部失败。

### P0-4：Canvas 与历史升级

**业务效果：** 新旧项目在同一工作台编辑，旧项目首次操作自动升级且不丢产物。

- 实现 VideoCanvasShell 和 ArtifactCanvasRouter；
- 迁移脚本、场景包、参考图、分镜视频、QC、成片界面；
- 工作台编辑改为 Turn/Command；
- 实现历史会话幂等升级；
- 缩减 LegacyWorkspace 和 MessageBubble。

### P0-5：旧链路硬删除

**业务效果：** 生产只剩一套 Agent 链，不会重复启动计费任务。

- 删除 Intake、JSON Planner、Plan Runner 和 entry_path；
- 删除 /agent/flows/video/* 与剪映旧 Job 路由；
- 删除前端 Job client、轮询恢复和 frontend_v2 执行分支；
- 删除旧合同和不可达代码；
- 保留仍被 Tool Adapter 使用的领域实现。

## 15. 验收标准

### 15.1 后端

1. 模型只能调用 Registry Tool。
2. schema、mutation、权限和 revision 由服务端强制校验。
3. Executor 没有 Plan 遍历入口。
4. Plan 能恢复状态和耗时，但不能触发 Tool。
5. Operation 终态能幂等唤醒 Agent。
6. 确认和额度无法由 Prompt 绕过。
7. 模型失败不会进入旧 Planner 或旧 HTTP Job。
8. 历史升级失败时不部分写入。

### 15.2 前端

1. 思考摘要流式展开、完成折叠且可重开。
2. 不渲染原始 reasoning_content。
3. Plan 最多 3 步，并展示状态、耗时、结果和产物。
4. 同一 Turn 的内容顺序稳定，刷新不丢失。
5. 卡片打开正确 Canvas 和版本。
6. 单分镜修改只标记对应 dirty_scene_ids，完成后显示“重新生成完成”。
7. 不存在旧 Job API、轮询或 Workflow 推进。
8. 桌面和移动视口无重叠、溢出或遮挡。

### 15.3 Golden Journeys

1. Idea -> 创意 -> 脚本 -> 确认 -> 场景包。
2. 成熟脚本 -> 结构化 -> 参考图 -> 分镜视频 -> 合成。
3. 参考视频 -> 拆解 -> 替换商品 -> 局部重生 -> 检查 -> 合成。
4. 检查第 N 镜 -> 修改 -> 确认 -> 只重生第 N 镜 -> QC。
5. 生成中断线或重启 -> Operation 恢复 -> Agent 自动继续。
6. 旧项目 -> 只读 -> 首次修改 -> 自动升级 -> 原生 Agent 继续。

## 16. 主要文件范围

### 后端新增

~~~text
backend/pixelflow/video_agent/agent.py
backend/pixelflow/video_agent/prompts.py
backend/pixelflow/video_agent/state.py
backend/pixelflow/video_agent/tool_adapter.py
backend/pixelflow/video_agent/middleware/
~~~

### 后端主要修改

~~~text
backend/app/gateway/app.py
backend/pixelflow/video_agent/runtime.py
backend/pixelflow/video_agent/entrypoint.py
backend/pixelflow/video_agent/executor/service.py
backend/pixelflow/video_agent/tools/registry.py
backend/pixelflow/video_agent/operation_resume.py
backend/pixelflow/video_agent/workspace/repository.py
backend/pixelflow/agent_runtime/jobs/completion.py
~~~

### 前端主要范围

~~~text
web/src/features/native-video-agent/
web/src/features/legacy-workspace/LegacyWorkspace.tsx
web/src/components/chat/ChatPanel.tsx
web/src/components/chat/MessageBubble.tsx
web/src/components/canvas/CanvasPanel.tsx
web/src/lib/api.ts
web/src/lib/supervisor/
~~~

详细实施计划必须先生成完整旧 API、路由和模式引用清单，再分批删除，避免遗留不可达分支。

## 17. 完成定义

以下条件全部满足才算完成：

1. 存在唯一易定位的 create_video_agent()。
2. Tool Call 由模型原生产生，不经 JSON Planner。
3. Tool Result 回到同一 Agent 循环。
4. Registry、Executor、Plan、Operation 没有隐式 Workflow。
5. Intake、JSON Planner、Plan Runner、frontend_v2 执行分支和旧 Job API 已删除。
6. 历史项目能在首次操作时无损升级。
7. 对话稳定展示思考摘要、短计划、步骤、耗时和结果卡。
8. Canvas 保留场景包和单分镜编辑等核心体验。
9. Golden Journey、后端合同、前端状态和桌面/移动视觉验收全部通过。
