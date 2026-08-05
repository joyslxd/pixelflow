# 统一视频智能体架构设计方案
## 文档状态
本设计基线已通过审批，对应分支：`feature/agent_0.8.5_boguan_joyce`
本文档是**统一视频智能体迁移改造**的唯一权威基准文档。后续若需对架构进行实质性改动，必须先更新本文档。

### P0 已实现基线（2026-08-05）
当前代码已经建立了统一入口的可恢复骨架：
- `VideoWorkspace`、`AgentPlan`、`AgentPlanStep` 及其 SQL 迁移和用户隔离仓储；
- `agent.plan.created`、`agent.step.*`、`agent.confirmation.requested` 公开事件契约，以及前端时间线状态投影；
- `VideoAgentEntrypoint`：primary 视频 Turn 在 `/turns/start` 登记后创建或幂等复用工作区与首个计划，写入 `agent.plan.created`，且不再唤醒旧 live executor；
- Gateway 在 SQL/内存任务存储下均装配该入口；Provider 未就绪不会阻止首个 VideoAgent 计划落库。

本阶段尚未完成工具循环、真实 Skill 匹配、计费确认、异步 Job DAG 编排、步骤状态与事件 Outbox 的同事务写入，以及 V2 工作台页面接入。为兼容已有会话归属合同，当前 primary 视频会话仍沿用 `supervisor_v1` 作为持久化编排标记；其视频 Turn 已由 V2 `VideoAgentEntrypoint` 接管。该兼容标记将在 V1 物理下线阶段移除。

## 现存问题
PixelFlow 已具备完整视频基础能力：脚本与分镜规划、参考视频拆解、素材替换、生成、质检、合成、剪映导出。但当前产品流程以固定工作流驱动，存在诸多痛点：
1. 用户已有完整脚本，仍必须强制走方案评审流程；
2. 用户仅有创意想法时，无法在生成前自然发散、探索创作方向；
3. 用户上传参考视频后，无法以「替换产品」作为单一目标一键完成全流程；
4. 画面质检能定位问题，但无法自动生成对应镜头修复、重生成任务；
5. 页面文件 `WorkspacePage.tsx` 承载了大量老旧状态与业务逻辑，臃肿难维护。

新版系统要求：**单一对话式统一入口**。智能体自动识别用户视频创作目标，加载对应能力指引、调用受控工具、生成简短可执行方案，每轮工具执行完成后持续推进流程；同时针对成本高昂的异步视频任务做好安全管控。

## 设计目标
1. 统一视频创作入口，覆盖脚本成片、创意头脑风暴、参考视频二次改编、成片审核、镜头修复、视频合成、导出全场景；
2. 由智能体自主匹配创作能力与受控工具，废除强制 `Plan.md` 固定流程；
3. 持久化项目工作区，存储脚本、参考素材、资产文件、镜头、多版成片、质检报告、生成结果；
4. 完整留存执行方案与步骤记录，页面刷新、重试、服务重启后数据不丢失；
5. 可视化执行时间线，展示每一步状态、结果、时间戳、耗时，不展示模型内部推理过程；
6. 默认沿用V1版本现有DeepSeek模型，架构预留模型切换、A/B实验能力；
7. 保留原有能力：用户数据隔离、幂等执行、额度校验、外部任务故障恢复、SSE消息推送、全链路审计。

## 非设计目标（不实现功能）
1. 不向大模型暴露底层厂商原始接口，不允许任意Shell脚本执行；
2. 不把每一条用户请求硬编码为独立流程状态；
3. 首版迭代不删除V1原有工作流，不迁移正在运行的V1任务；
4. 不在 `WorkspacePage.tsx`、V1智能体流程编排层新增业务功能。

## 目标整体架构
```
统一对话输入
  → 轻量化对话路由分发器
  → 视频智能体 VideoAgent
       → 项目工作区 VideoWorkspace（全流程数据载体）
       → 能力清单 + 受控工具规范
       → 执行方案 / 单步任务
       → 循环执行工具
  → 智能体运行时可靠性底座
       → 持久化存储、幂等性、任务租约、轮询、额度校验、任务中断、SSE推送
  → 现有视频业务底层能力与厂商服务
```

### 轻量化路由分发器
原有调度器 Supervisor 简化为跨场景分发模块：识别请求属于视频/图片/PPT/普通对话，统一鉴权、全局并发限制、基础安全校验；随后将原始用户输入、附件、项目标识完整转发至对应智能体。
路由层禁止将视频需求翻译成固定动作（如修改流程、重绘镜头、重试失败任务）。视频相关意图统一由 `VideoAgent` 全权处理。

### 视频智能体 VideoAgent
`VideoAgent` 是视频领域唯一负责流程规划、工具选择的核心模块，每一轮交互执行逻辑：
1. 仅加载当前项目相关数据；
2. 匹配适用的创作能力说明；
3. 生成结构化精简执行方案，信息不足时主动向用户精准提问；
4. 在可控循环内调用已授权工具；
5. 持久化每一步输出结果，工具返回数据发生变化时自动修正剩余执行方案；
6. 涉及计费、批量任务、破坏性操作前，必须向用户发起确认。

#### 参考：参考视频改编完整执行方案示例
```
分析参考视频
→ 提取分镜与素材清单
→ 替换产品素材
→ 生成镜头修复补丁
→ 确认生成消耗额度
→ 重绘受影响镜头
→ 校验成片画面质量
→ 合成完整视频
```

## 项目与执行数据存储设计
### 视频工作区 VideoWorkspace
项目持久化黑板，支持版本管理，存储内容包含：
- 产品素材、物料资源；
- 原生脚本、导入脚本、对话生成的脚本草稿；
- 参考视频及拆解结果；
- 全局公共素材、镜头专属素材；
- 镜头定义、提示词、多版生成结果、最终选用版本；
- 单镜头质检报告、问题截图、修复建议；
- 合成成片、导出安装包。

`Plan.md` 改为可选附属文件：用户上传成熟脚本可直接使用；仅提供创意则只生成草稿；上传参考视频会自动提取画面约束与素材信息。

### 执行方案与单步任务 AgentPlan / AgentPlanStep
每一轮交互生成一份持久化执行方案，每个执行步骤记录字段：
步骤ID、方案ID、执行序号
能力ID、工具名称
步骤标题、输入摘要、结果摘要
状态：待执行 / 运行中 / 等待用户确认 / 已完成 / 执行失败 / 跳过
关联资源ID、后台任务ID、错误码
开始时间、完成时间

数据恢复仅依赖持久化步骤记录，而非SSE消息流。步骤耗时通过时间戳计算：运行中步骤展示已运行时长；页面刷新/重连后，已完成步骤展示总耗时。

### 任务有向无环图 Job DAG
仅存放异步、计费类任务：参考视频拆解、镜头生成、质检、视频合成、导出。复用智能体运行时能力：任务唯一标识、幂等保障、租约锁、厂商轮询、额度鉴权、故障恢复。

## 能力与工具体系
能力（Skill）仅提供指引，无执行权限：描述适用场景、入参出参、约束条件、示例、推荐执行顺序。
工具（Tool）是服务端注册的强类型代码接口，所有入参强制校验。

首期工具清单（精简可控）：
- `inspect_video_workspace` 查看项目工作区
- `import_script` 导入脚本
- `brainstorm_script` 脚本创意生成
- `analyze_reference_video` 解析参考视频
- `replace_project_assets` 替换项目素材
- `inspect_scene` 镜头质检
- `patch_scene` 镜头局部修复
- `generate_scenes` 批量生成镜头
- `review_generated_scenes` 成片审核
- `compose_or_export_video` 视频合成与导出

每款工具明确定义：入参出参规范、消耗等级、是否需要用户确认、幂等策略、故障恢复逻辑、允许修改的项目数据范围。大模型禁止直接调用底层渲染、FFmpeg、剪映、数据库、厂商接口。

## 原有代码边界划分
### 保留不动模块
- `backend/pixelflow/agent_runtime/jobs/`：任务标识、租约、轮询、故障恢复、额度校验；
- `backend/pixelflow/agent_runtime/persistence/`：数据仓储、事件队列、用户隔离、持久状态；
- `backend/pixelflow/agent_runtime/context/`：上下文内存管控与压缩，新增镜头素材数据包；
- `原有视频业务底层能力：agent_workflows/video、generate、qc、skills`；
- 现有智能体事件SSE推送框架、前端调度事件映射逻辑（用于迁移对照）。

### 冻结、仅适配兼容的模块
V1流程编排文件仅做兼容兜底，**不再新增V2功能**：
- `agent_workflows/video/live_handler.py`
- `agent_workflows/video/live_operations.py`
- `agent_workflows/video/live_quota.py`
- `agent_workflows/video/state_codec.py`

V2通过适配器封装复用底层可复用逻辑（规划、镜头包、视频生成、后期制作、交付模块）。稳定后底层代码可迁移至 `video_domain/`，首期上线无需迁移。

### 后端新增代码目录
```
backend/pixelflow/video_agent/
  contracts/       方案、步骤、工作区、工具调用/返回数据结构定义
  workspace/       工作区持久化、素材数据筛选
  skills/          能力描述清单、场景匹配逻辑
  tools/           受控工具实现代码
  adapters/        对接原有视频底层能力的适配层
  planner/         模型+工具循环调度、执行方案修正
  executor/        方案转异步任务DAG、用户确认拦截
  context/         镜头级素材数据包
```

`agent_runtime/supervisor/` 仅保留V1调度逻辑，V2仅用作轻量化路由分发。

## 前端事件时间线与页面改造
前端仅展示执行过程叙事，隐藏模型内部推理内容。每条时间线条目包含简洁标题、可见输入、结果摘要、关联素材、状态、时间戳、耗时；示例文案：「识别6个镜头」「镜头3需重新生成」。

新增持久化事件类型：
- `agent.plan.created` 执行方案创建
- `agent.step.started` 步骤开始执行
- `agent.step.progressed` 步骤进度更新
- `agent.step.completed` 步骤执行完成
- `agent.step.failed` 步骤执行失败
- `agent.confirmation.requested` 等待用户确认

复用原有事件输出队列、有序SSE消息流。`message.upserted` 作为展示给用户的最终消息，新增事件用于渲染步骤时间线。

现有 `WorkspacePage.tsx` 混杂老旧状态、V1调度逻辑、任务轮询、页面交互。**禁止在此文件新增V2功能**。
首期迁移将原有页面逻辑拆分迁移至：
```
web/src/features/legacy-workspace/LegacyWorkspace.tsx
web/src/features/video-agent/
  VideoAgentWorkspace.tsx      V2智能体项目主页面
  AgentPlanTimeline.tsx        执行步骤时间线组件
  AgentConfirmationCard.tsx    用户确认弹窗组件
  SceneEvidencePanel.tsx       镜头素材展示面板
  hooks/useVideoAgent.ts       智能体通用逻辑钩子
  state/reducer.ts             页面状态管理
```

最终 `web/src/pages/WorkspacePage.tsx` 仅作为路由布局外壳（代码量100–200行），根据流程模式渲染旧版页面或V2智能体工作区。

## 大模型选型策略
V2首期沿用现有 `deepseek-v4-pro` 配置。智能体执行链路依赖统一模型厂商接口，支持结构化输出、工具调用、工具结果回读、图片素材输入、能力分级。
首期不依赖Kimi K3；后续完善厂商回读能力、完成标准用例评测后，可注册为高复杂度流程规划备选模型。
视频画面拆解、视觉质检仍使用专用视觉大模型，不依赖流程规划大模型。

## 灰度上线策略
新增流程标识 `video_agent_v2`。新对话可通过白名单或流量百分比路由至V2；未白名单、存量对话仍走 `supervisor_v1`。正在运行的V1任务不会迁移至新流程。若V2出现故障，可切回全量V1实现快速回滚。

## 迭代里程碑
1. 拆分前端旧页面逻辑，`WorkspacePage.tsx` 简化为路由外壳，保障原有功能测试全量通过；
2. 实现工作区、执行方案、步骤持久化，配套数据迁移脚本、仓储、事件规范、故障恢复用例；
3. 完成视频智能体规划器、能力清单、工具注册、DeepSeek模型适配、可视化执行时间线；
4. 首批统一入口工具落地：脚本导入、创意头脑风暴、参考视频解析；
5. 完成镜头质检、镜头修复、定向重绘、质检、合成、导出全工具适配；
6. 新增V2路由分发、灰度放量、观测指标、回滚控制能力。

## 验收验证标准
1. 用户上传完整脚本，无需强制创意评审即可直接生成视频；
2. 用户仅提供创意想法时，支持多轮发散沟通，用户确认后才发起计费生成任务；
3. 上传参考视频自动拆解，素材替换精准定位受影响镜头，计费确认后才执行生成；
4. 指令「检查镜头3，有问题就重绘」可自动读取镜头素材、生成修复方案，仅执行局部任务，完整记录执行时间线；
5. 页面刷新、重连、重复提交、服务重启后，执行步骤、耗时、后台任务、计费安全不受影响；
6. V1原有回归测试全部通过；V2新增接口契约、规划器、工具、仓储、SSE、端到端全量测试；
7. 标准测试用例覆盖30–50种真实创作场景，校验意图识别、工具选择、澄清提问、镜头定位、多步骤闭环、生成失败、重复计费、响应耗时、资源消耗等指标。

## 改造工作量预估
V2整体迁移预计新增/大幅修改文件45–65个，前后端、数据库迁移、测试代码合计新增代码7000–11000行。各里程碑可独立分批上线，无需一次性全量重构。

# Unified Video Agent Design

## Status

Approved design baseline for `feature/agent_0.8.5_boguan_joyce`.

This document is the durable source of truth for the unified video-agent migration. Future work must update this document before changing the architecture materially.

## Problem

PixelFlow owns the necessary video primitives, including script and scene planning, reference-video decomposition, asset replacement, generation, QC, composition, and Jianying export. The current experience remains workflow-led:

- A user with a finished script is forced through Plan review.
- A user with only an idea cannot naturally explore creative directions before generation.
- A user with a reference video cannot ask for product replacement as one goal-oriented task.
- Scene inspection can identify a problem without reliably creating the scene-level repair and regeneration work.
- `WorkspacePage.tsx` owns too much legacy state and behavior.

The new system must use one conversational entry point. It infers the user's video goal, loads the relevant Skill guidance, chooses controlled tools, forms a short executable plan, and continues after each tool result. It must remain safe for expensive asynchronous video operations.

## Goals

- One video entry point for script-to-video, creative discussion, reference remix, review, repair, composition, and export.
- Agent-selected Skills and controlled Tools rather than a mandatory Plan.md workflow.
- A persistent project workspace containing scripts, references, assets, scenes, variants, QC evidence, and generated outputs.
- A persistent plan and step history that survive reloads, retries, and worker recovery.
- A visible execution timeline that reports each step's status, result, timestamps, and duration without exposing hidden model reasoning.
- Default to the existing DeepSeek model in V1. The architecture must allow a later model switch or A/B evaluation.
- Preserve user isolation, idempotency, quota confirmation, external-job recovery, SSE delivery, and auditability.

## Non-goals

- Do not expose raw provider APIs or arbitrary shell execution to the model.
- Do not turn every user request into a newly hardcoded workflow state.
- Do not delete V1 workflows or migrate running V1 tasks during the first release.
- Do not add new feature logic to `WorkspacePage.tsx` or the V1 `agent_workflows` orchestration layer.

## Target Architecture

```text
Unified chat input
  -> thin conversation router
  -> VideoAgent
       -> VideoWorkspace evidence pack
       -> Skill catalog and controlled tool contracts
       -> AgentPlan / AgentPlanStep
       -> tool execution loop
  -> Agent Runtime reliability layer
       -> persistence, idempotency, leases, polling, quota, interrupt, SSE
  -> existing domain capabilities and provider skills
```

### Thin Router

The existing Supervisor is reduced to a cross-domain router. It may identify that a request is video, image, PPT, or general conversation; enforce authentication, global concurrency, and hard safety checks; then hand the unmodified user input, attachments, and project reference to the target agent.

It must not translate a video request into a fixed `modify_workflow`, `regenerate_stage`, or `retry_failed` action. Video intent has one owner: `VideoAgent`.

### VideoAgent

`VideoAgent` is the video domain's sole planning and tool-selection agent. For each turn it:

1. Loads only the relevant project evidence.
2. Selects the applicable Skill manifests.
3. Produces a short structured `AgentPlan`, or asks a precise question when necessary.
4. Invokes approved tools in a bounded loop.
5. Persists every result and revises the remaining plan when tool output changes the situation.
6. Requests confirmation before billable, batch, or destructive work.

Example reference-remix plan:

```text
analyze_reference_video
-> extract_storyboard_and_assets
-> replace_product_asset
-> build_scene_patch
-> confirm_generation_cost
-> generate_affected_scenes
-> inspect_scene_results
-> compose_video
```

## Project and Execution Data

### VideoWorkspace

`VideoWorkspace` is the persistent project blackboard. It is versioned and holds:

- product and materials;
- source scripts, imported scripts, and conversational script drafts;
- reference videos and decomposition results;
- global assets and scene-local assets;
- scene definitions, prompts, generation variants, and selected variants;
- scene-level QC reports, visual evidence, and repair suggestions;
- composed outputs and export packages.

`Plan.md` becomes an optional script artifact. A supplied mature script can be used directly; an idea creates drafts only; a reference video contributes evidence and scene constraints.

### AgentPlan and AgentPlanStep

Each meaningful turn creates a durable `AgentPlan`. Each step records:

```text
step_id, plan_id, sequence
skill_id, tool_name
title, input_summary, result_summary
status: pending | running | awaiting_confirmation | completed | failed | skipped
artifact_refs, job_ids, error_code
started_at, completed_at
```

The durable step record, not the SSE stream, is the recovery source. Step duration is calculated from persisted timestamps. A running step displays elapsed time from `started_at`; a completed step displays `completed_at - started_at` after reload or reconnect.

### Job DAG

`Job DAG` holds only asynchronous or billable execution work, such as reference decomposition, scene generation, QC, composition, and export. It reuses the Agent Runtime Operation identity, idempotency, leases, provider polling, quota authorization, and recovery model.

## Skill and Tool Model

Skills are guidance, not executable authority. A Skill describes applicability, inputs, outputs, constraints, examples, and recommended order. Tools are typed code contracts that are registered server-side and validate all inputs.

The initial tool catalog is intentionally small:

- `inspect_video_workspace`
- `import_script`
- `brainstorm_script`
- `analyze_reference_video`
- `replace_project_assets`
- `inspect_scene`
- `patch_scene`
- `generate_scenes`
- `review_generated_scenes`
- `compose_or_export_video`

Every tool declares input and output schemas, cost level, confirmation policy, idempotency policy, recovery behavior, and permitted project mutations. The model never calls Borgrise, FFmpeg, Jianying, database, or provider endpoints directly.

## Existing Code Boundaries

### Keep

- `backend/pixelflow/agent_runtime/jobs/`: operation identity, leases, polling, recovery, quota.
- `backend/pixelflow/agent_runtime/persistence/`: repositories, event outbox, user isolation, durable state.
- `backend/pixelflow/agent_runtime/context/`: context budgeting and compaction, extended with scene evidence packs.
- Existing video domain capabilities in `agent_workflows/video`, `generate`, `qc`, and `skills`.
- Existing AgentEvent SSE infrastructure and frontend Supervisor event projections as migration references.

### Freeze and Adapt

The V1 flow orchestration files remain only for compatibility:

- `agent_workflows/video/live_handler.py`
- `agent_workflows/video/live_operations.py`
- `agent_workflows/video/live_quota.py`
- `agent_workflows/video/state_codec.py`

No V2 feature is added to those files. V2 wraps reusable deterministic capabilities from `planning.py`, `scene_packages.py`, `video_generation.py`, `postproduction.py`, and `delivery.py` behind adapters. Later, stable capability code may move to `video_domain/`; this move is not required for the V2 launch.

### New Backend Package

```text
backend/pixelflow/video_agent/
  contracts/       # plans, steps, workspace, tool calls, tool results
  workspace/       # workspace persistence and evidence selection
  skills/          # Skill manifests and applicability selection
  tools/           # controlled tool implementations
  adapters/        # bridges to existing video domain capabilities
  planner/         # bounded model/tool loop and plan repair
  executor/        # plan-to-job-DAG and confirmation enforcement
  context/         # scene-level evidence packs
```

`agent_runtime/supervisor/` is retained for `SUPERVISOR_V1` only and reduced to thin-routing responsibilities for V2.

## Event Timeline and Frontend

The frontend shows an execution narrative, not model chain-of-thought. Each timeline item contains a plain title, visible inputs and result summary, linked artifacts, status, timestamp, and duration. It may display examples such as "Identified 6 scenes" or "Scene 3 needs regeneration". It must never display hidden reasoning.

New persisted event types:

- `agent.plan.created`
- `agent.step.started`
- `agent.step.progressed`
- `agent.step.completed`
- `agent.step.failed`
- `agent.confirmation.requested`

The existing event outbox and monotonic SSE sequence are reused. `message.upserted` remains the user-facing conclusion, while the new events project the step timeline.

`WorkspacePage.tsx` currently contains legacy state, V1 supervisor behavior, task polling, and UI behavior. New functionality must not be added there. The first migration milestone moves its existing body to:

```text
web/src/features/legacy-workspace/LegacyWorkspace.tsx
web/src/features/video-agent/
  VideoAgentWorkspace.tsx
  AgentPlanTimeline.tsx
  AgentConfirmationCard.tsx
  SceneEvidencePanel.tsx
  hooks/useVideoAgent.ts
  state/reducer.ts
```

The final `web/src/pages/WorkspacePage.tsx` is a 100-200 line route/layout shell that renders the legacy feature or `VideoAgentWorkspace` based on orchestration mode.

## Model Strategy

V1 uses the existing `deepseek-v4-pro` configuration. The agent loop must depend on a model-provider interface supporting structured output, tool calls, tool-result replay, image evidence where required, and capability profiles.

Kimi K3 is not a V1 dependency. It may later be registered as a controlled, high-complexity planner after provider-specific replay support and golden-case evaluation. Video decomposition and visual QC remain dedicated VLM capabilities rather than depending on a planning model.

## Rollout

Add a `video_agent_v2` orchestration mode. New conversations can be allowlisted or percentage-routed to V2. Existing and non-allowlisted conversations remain `supervisor_v1`. Running V1 workflows are never migrated in place. A V2 failure can be rolled back by routing subsequent new conversations to V1.

## Milestones

1. Extract the legacy frontend feature and reduce `WorkspacePage.tsx` to a shell with behavior-preserving tests.
2. Add workspace, plan, and step persistence with migrations, repositories, event contracts, and recovery tests.
3. Add the VideoAgent planner, Skill catalog, tool registry, DeepSeek provider boundary, and visible plan/step timeline.
4. Adapt script import, creative discussion, and reference-video analysis as the first unified-entry tools.
5. Adapt scene inspection, patching, selective regeneration, QC, composition, and export tools.
6. Add `video_agent_v2` routing, canary rollout, observability, and rollback controls.

## Verification and Acceptance

- A mature script reaches generation without a mandatory creative Plan review.
- An idea supports multi-turn creative discussion and creates no generation job until explicit confirmation.
- A reference video is decomposed, product replacement identifies affected scenes, and generation cost is confirmed before jobs start.
- "Inspect scene 3 and regenerate it if wrong" reads scene evidence, records a repair decision, performs only scoped work, and reports the full step timeline.
- Reload, reconnect, retry, duplicate submission, and worker restart preserve plan steps, durations, external jobs, and cost safety.
- V1 regression tests remain green; V2 adds contract, planner, tool, repository, SSE/reducer, and end-to-end tests.
- Golden cases cover 30-50 realistic requests and measure intent/tool selection correctness, clarification correctness, scene targeting, multi-step completion, erroneous generation, duplicate billing, latency, and cost.

## Size Estimate

The V2 migration is expected to add or materially modify approximately 45-65 files and 7,000-11,000 lines across backend, frontend, migrations, and tests. The milestones are separately releasable and must not require a big-bang rewrite.
