# M13 集成、Shadow、全量发布、回滚与交付

- phase：`not_started`
- owner：A+B；当周单一集成人
- branch：计划 `codex/agent-0.8.4-m13-integration`
- 依赖：按 R1–R4 增量满足；最终收口依赖 M01–M12
- 当前切片：M13.1
- 当前发布门禁：`not_eligible`；M13.1 切片通过后先写 `ready_for_phase_integration:R1`，人工触发的单槽候选绿色进入 Agent 后再写 `awaiting_release_approval:R1`
- 生产配置：未变更；切片通过不等于生产上线

## 切片

- [ ] M13.1 / R1 assist、压缩 UI/恢复、旧流程等价、全部新对话100%（2.5h）
- [ ] M13.2 / R2 视频 replay/shadow/黄金对话/mock E2E、`primary(video)+100%`（3h）
- [ ] M13.3 / R3 图片/编辑、PPT、视频分析 mock E2E、`primary(四类intent)+100%`（3h）
- [ ] M13.4 / R4 五流程全量、保持100%、kill switch/回滚（2.5h）
- [ ] M13.5 / R4 经批准真实冒烟/文档/发布签字（2h）

## 启动与发布规则

- M13.1→M13.5 在同一分支/worktree 严格串行，每个切片都由开发者手动启动一次；直接复制[执行手册第9节](../branch-and-codex-runbook.md#codex-prompts)对应话术。
- M13.1 最早在 M00-I.1，以及 M01/M03/M04/M07/M12.3 的 R1 增量进入 Agent、最新 dev→agent 绿色后启动。
- M13.1–M13.4 默认只生成候选和执行非付费门禁，切片通过后写 `ready_for_phase_integration:R*` 并停止；当前由开发者按执行手册人工启动单槽集成，候选绿色进入 Agent 后写 `phase_integrated:R*` 和 `awaiting_release_approval:R*`，但不自动修改生产运行模式、`enabled_intents` 或 Feature Flag。当前各阶段比例固定100%，无用户白名单。
- R1 生产上线需要唯一发布负责人另行复制执行手册 9.17 的明确批准话术；以后每个批次和每次比例变化同样单独批准。
- “手动批准”不要求发布负责人亲自编辑配置文件；Codex/受控流水线获批后执行配置、部署、验证、记录和异常回滚。生产平台强制二次认证或人工审批按钮除外。

## 发布记录

| 批次 | 候选状态 | 人工批准 | 生产值/比例 | 发布证据 |
| --- | --- | --- | --- | --- |
| R1 | `not_eligible` | 未批准 | 保持发布前原值 | — |
| R2 | `not_eligible` | 未批准 | 保持发布前原值 | — |
| R3 | `not_eligible` | 未批准 | 保持发布前原值 | — |
| R4 | `not_eligible` | 未批准 | 保持发布前原值 | — |

## 恢复提示

Shadow 不能调用付费 API，也不能写 PowerMem 经验。回滚只影响新对话；运行中的 Supervisor 对话继续排空或人工处理，不能强切 owner。
