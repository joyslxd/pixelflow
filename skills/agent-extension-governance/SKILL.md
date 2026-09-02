---
name: agent-extension-governance
description: "Use when adding or modifying PixelFlow Agent capabilities, system instructions, Harness plugins, Skill files, Tool manifests, Tool Broker handlers, run/resume behavior, or domain Workspace integration. Classifies the change into the correct layer and requires boundary and regression checks."
---

# Agent Extension Governance

保持一个可复用的 `pixelflow-agent`。把领域差异放在 Skill、Tool、Workspace 和 Provider；把安全和权威写入留在 Gateway；把 Harness Plugin 限定为 Engine 适配与策略。

## 先分类，再修改

| 需求 | 应修改的位置 | 不应修改的位置 |
| --- | --- | --- |
| 通用事实来源、受控调用、公开沟通、提示注入防护 | `agent_harness` 通用系统指令或 Run Policy | 任一领域 Skill |
| 视频、PPT、表格、搜索等领域的判断、创作规范、检查清单与选 Tool 建议 | 对应 `SKILL.md` | 通用系统指令、Harness Plugin |
| 一个稳定、用户可感知的业务动作 | Tool DTO、Manifest、Handler 与领域 Service | Skill、Plugin 中的执行代码 |
| 供应商或 SDK/HTTP/MCP 的差异 | Capability Provider Port/Adapter 与能力档案 | Harness Tool 名、Skill 业务语义 |
| 领域对象、版本、投影和编辑状态 | 领域 Workspace、Repository、Service、前端 Projection | Sidecar Session |
| Tool 注册、上下文注入、挂起、事件映射 | Sidecar Plugin | 数据库、Provider、业务 Service |

若无法明确归类，先检查现有 Tool Manifest、Workspace 合同和同类 Provider，再提出设计；不要把不确定性通过新增固定 Workflow、Supervisor 或领域专用 Agent 掩盖掉。

## 不可突破的边界

- 通用系统指令只能表达跨领域边界：权威事实来源、受控 Tool、用户沟通、注入防护和非固定工作流。不得写入领域 Tool 名、Provider 名、模型名、DTO 字段、Prompt 结构或业务阶段。
- 领域 Skill 只能承载方法、知识、质量标准和 Tool 选择建议；不得当作权限、确认、计费、revision、幂等或数据访问的安全边界。
- Gateway 是 Workspace、Tool、GenerationJob 与业务终态的唯一权威写入方。Sidecar 只能经 Tool Broker 调用业务能力，不能连接数据库、Provider 或宿主文件系统。
- Tool Broker/Handler 必须强制校验 owner、会话、Run binding、Manifest、revision、幂等和确认。不要依赖模型“记住”这些限制。
- Harness Plugin 只能注册稳定 Manifest、注入安全投影、处理挂起或映射事件；不得持有 Authorization、Provider Secret、数据库连接，也不得直接产生领域副作用。
- 付费异步生成由 Gateway GenerationJob Worker 调度、轮询并回写 Workspace；不得把 Provider 任务迁入 Sidecar 或恢复旧 Batch/Operation 生成编排。

## 实施流程

1. 阅读 `AGENTS.md`、`DEEPSEEK_HARNESS_SIDECAR_IMPLEMENTATION_PLAN.md`、目标 Workspace、Tool 与相邻测试。
2. 用上表为每项改动标注层次；新增领域时补齐 Workspace、Service、Repository、Projection、Tool 与 Skill，而不是只加 Prompt。
3. 将可硬性验证的前置条件写进 Tool/Service；将模型选择策略写进领域 Skill；将跨领域边界写进通用系统指令或 Policy。
4. 所有 `user_turn`、`confirmation_resume`、`form_resume`、`authorization_resume` 与 `run_recovery` 都必须叠加同一通用系统指令，再附加本次触发的最小补充。不得让恢复 Run 丢失事实来源、受控调用和保密边界。
5. 新增 Tool 时保持业务语义稳定、与厂商无关；Provider 更换只改 Adapter/能力档案。若用户可感知动作变化，新增 Tool 与对应领域 Skill。
6. 不复制启动、发布或部署流程；需要启动、重启、健康检查或部署时改用 `start-local-stack` Skill 与仓库部署文档。

## 必做验证

- 为新的硬约束写失败后转绿的回归测试；不要只断言提示词文案。
- 修改通用指令或恢复入口时，覆盖用户 Turn 与每种恢复 Run，验证均注入通用边界。
- 修改 Skill/Tool 路由时，验证 Skill 不包含敏感信息或执行权限，Tool 在缺少前置状态、确认或 revision 时拒绝。
- 新增 Provider 时，验证稳定 DTO 映射、脱敏错误码和 GenerationJob 的 start/poll/Workspace 回写；不得用数据库伪造终态。
- 运行目标测试、中文工程门禁和 `git diff --check`。真实模型或 Provider 请求必须另获用户明确确认。

## 交付检查

- 说明改动属于哪些层、为何不修改其他层。
- 说明新增或更新了哪些 Skill、Tool、Workspace 或 Provider 合同。
- 报告执行过的测试、未执行的真实外部请求及原因。
- 不在交接、日志、测试或提交中包含 Secret、Authorization、用户正文或 Provider 原始异常。
