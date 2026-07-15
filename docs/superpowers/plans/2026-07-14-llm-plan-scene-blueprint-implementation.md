# PixelFlow 视频 Plan 分镜蓝图实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Plan LLM 应用 Seedance Skill 自主完成总分总叙事、精确时长调度和逐分镜蓝图，并让后续场景包严格消费同一蓝图。

**Architecture:** 在 `creative` 层新增可校验的分镜蓝图合同，Plan LLM 返回结构化蓝图和 Markdown；Plan 结果、历史版本和 API 持久化蓝图。场景包层只把语义资产要求落成 `@asset_id` 和图片 URL，不再重新决定镜头数量、时长和故事。

**Tech Stack:** Python 3.12、Pydantic/FastAPI、DeepSeek `deepseek-v4-pro`、React/TypeScript、Pytest、Vitest/Node test。

## Global Constraints

- 视频总时长为 4-300 秒整数；每个分镜为 4-15 秒整数；总和必须精确相等。
- Plan 和场景包都应用 `backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/SKILL.md`。
- 新字段向后兼容旧对话；新 Plan 不得缺少结构化蓝图。
- 用户确认的模型、画幅、清晰度和创作合同不得被后续阶段覆盖。
- 代码注释和 Git 提交说明使用中文；Token 不写入代码、文档或日志。

---

### Task 1: 定义并校验视频 Plan 分镜蓝图

**Files:**
- Create: `backend/pixelflow/creative/scene_blueprint.py`
- Test: `backend/tests/test_plan_scene_blueprint.py`

**Interfaces:**
- Produces: `normalize_scene_blueprints(raw, total_duration_sec) -> list[dict[str, Any]]`
- Produces: `fallback_scene_blueprints(total_duration_sec, ...) -> list[dict[str, Any]]`
- Produces: `scene_blueprint_durations(blueprints) -> list[int]`

- [x] 写失败测试，覆盖非等分时长、连续时间线、秒级镜头描述、4/300 秒边界和总分总结构。
- [x] 运行测试，确认因模块或接口缺失而失败。
- [x] 实现纯函数校验、语义保留式时间线修复与内容权重兜底。
- [x] 运行测试并保持通过。

### Task 2: 让 Plan LLM 加载 Seedance Skill 并返回蓝图

**Files:**
- Modify: `backend/pixelflow/creative/plan_llm.py`
- Modify: `backend/pixelflow/creative/plan_markdown.py`
- Test: `backend/tests/test_creative_plan_markdown.py`
- Test: `backend/tests/test_seedance_prompt_skill.py`

**Interfaces:**
- `generate_plan_payload(...)` 的视频输出新增 `scene_blueprints`。
- `PlanMarkdownResult` 新增 `scene_blueprints: list[dict[str, Any]]`。

- [x] 写失败测试，断言 Plan Prompt 包含 Seedance Skill、总分总、自主时长分配和完整蓝图 JSON 合同。
- [x] 写失败测试，让假 LLM 返回 `6/12/8`，断言 Plan 使用该时长而不是 10 秒切分。
- [x] 运行测试并确认预期失败。
- [x] 注入 Seedance Skill，解析和校验蓝图；失败时先修复时长，再走结构化兜底。
- [x] 将蓝图渲染进执行合同并保存到 Plan 历史。
- [x] 运行 Plan 测试并保持通过。

### Task 3: 让修订、回退和 API 保存蓝图

**Files:**
- Modify: `backend/app/gateway/routers/pixelflow_planning.py`
- Modify: `backend/pixelflow/creative/plan_markdown.py`
- Test: `backend/tests/test_pixelflow_planning_router.py`
- Test: `backend/tests/test_creative_plan_markdown.py`

**Interfaces:**
- `PlanMarkdownResponse.scene_blueprints`。
- `PlanRestoreRequest.scene_blueprints`。
- `plan_history[*].scene_blueprints`。

- [x] 写失败测试，覆盖创建、修订、回退和旧历史兼容。
- [x] 运行测试并确认预期失败。
- [x] 实现 DTO 与深拷贝快照。
- [x] 运行测试并保持通过。

### Task 4: 场景包严格消费 Plan 蓝图

**Files:**
- Modify: `backend/pixelflow/generate/scene_packages.py`
- Modify: `backend/app/gateway/routers/pixelflow_video.py`
- Test: `backend/tests/test_video_scene_packages.py`
- Test: `backend/tests/test_pixelflow_video_router.py`

**Interfaces:**
- `prepare_video_scene_packages_with_llm(..., scene_blueprints=None)`。
- `PrepareScenePackagesRequest.scene_blueprints`。

- [x] 写失败测试，传入 `6/12/8` 蓝图并断言场景包顺序、时长、故事线和旁白不被重算。
- [x] 运行测试并确认预期失败。
- [x] 让场景包 Prompt 将蓝图标为权威输入，规范化时锁定蓝图内容和时间线。
- [x] 保留无蓝图历史请求的旧兼容路径。
- [x] 运行场景包和视频 Router 测试。

### Task 5: 前端持久化和传递蓝图

**Files:**
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/planMessageRecovery.ts`
- Modify: `web/src/lib/activePlanSnapshot.ts`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Test: `web/src/lib/planMessageRecovery.test.ts`
- Test: `web/src/lib/activePlanSnapshot.test.ts`

**Interfaces:**
- `PlanMarkdownResponse.scene_blueprints`。
- `PrepareScenePackagesJobRequest.scene_blueprints`。

- [x] 写失败测试，覆盖消息恢复和 Plan 回退快照中的蓝图。
- [x] 运行测试并确认预期失败。
- [x] 更新类型、快照和场景包启动请求。
- [x] 运行前端测试、lint 和 build。

### Task 6: 文档、回归与真实视频链路

**Files:**
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Produces: 当前实现事实和真实验证记录。

- [x] 更新 Agent/Skill 调用关系、Plan 蓝图字段、版本恢复和场景包消费规则。
- [x] 运行定向测试、完整后端测试、ruff、前端测试、lint 和 build。
- [x] 启动本地测试环境配置，查询实时模型能力。
- [x] 从采集需求开始跑通表单、3 个创意、Plan、场景资产、场景视频和合并视频。
- [x] 核验最终 Plan 分镜时长不是机械 10 秒，总时长精确，最终视频可访问且内容符合需求。
- [x] 提交前再次 `git fetch`；当前分支与远端同提交，无需合并冲突。
- [x] 使用中文提交说明提交本次改动。
