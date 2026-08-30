# Agent 门禁基线修复单槽集成报告

## 结论

- 日期：2026-07-24
- 集成源：`origin/codex/agent-0.8.4-m00-gate-baseline-repair@1aba4ae9e4670930fd456519d8ecc7d4cef39880`
- 目标基线：`origin/feature/agent_0.8.4_boguan@5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- 冻结 dev：`origin/feature/dev_0.8.4_boguan@fb7450775a227d891372c19eae1b308045c51e68`
- 候选：`codex/integrate-m00-gate-repair-20260724-164428`
- 集成合并提交：`a82df1c7b1410a75623db5800f6ffaa0035e05ba`
- 审核加固提交：`4514ffe`
- Python：项目虚拟环境 Python 3.12.13
- 自动化状态：`automation_local_ready`
- 独立终审：`/root/m00_gate_repair_integration_review`，`Ready to merge: Yes`

开发者明确授权本次一次性 M00 维护单槽集成。候选从冻结的最新 Agent 创建，冻结 dev 已是 Agent 祖先；没有把 Agent 反向合入 dev，没有修改 M03 模块状态，也没有调用真实图片、视频、PPT、剪映、LLM 或其他付费 API。

## 安全预检与候选拓扑

- 全局单槽锁在候选创建前获取，推送复读完成前持续持有。
- 根工作区已有未跟踪 `scripts/__pycache__/` 被识别为用户文件，未删除、未移动、未纳入提交。
- M03 模块 worktree 的既有行尾状态和缓存文件未被写入。
- 修复源相对冻结 Agent 领先 10 个提交；候选在审核加固前的 tree 与修复源 tree 完全一致。
- `origin/feature/dev_0.8.4_boguan@fb7450775a227d891372c19eae1b308045c51e68` 已是冻结 Agent 的祖先，因此候选无需产生额外 dev 合并提交。
- 修复源、冻结 Agent 和冻结 dev 均在最终推送前重新 fetch 并执行精确 SHA 防漂移检查；任一引用变化都必须中止推送并重建候选。

## TDD 与独立审核闭环

候选首轮验证已达到：

```text
Agentization Pester
45 passed, 0 failed

M13 Final
Passed=True
CommandCount=8
```

独立 reviewer 首轮没有 Critical，提出 2 个 Important 和 1 个 Minor：

1. M01 Final 清单尚缺 M01.5 Event Outbox 和 owner isolation，不应在模块完成前放行；
2. M07–M12 没有统一执行前端全量测试，且 M08–M11 缺少后端权威测试范围；
3. Pester 没有精确锁定 Python 3.12 版本检查命令。

先修改测试得到预期红灯：

```text
44 passed, 3 failed
```

三个失败分别对应 M01 提前放行、M07/M12 缺少 `pnpm test`、M08–M11 提前放行。随后完成最小实现：

- M01 在 M01.5 Event Outbox 权威清单冻结前 fail-closed；
- M07、M12 固定执行 `pnpm test`、`pnpm lint`、`pnpm build-prod`；
- M08–M11 在包含后端范围的权威清单冻结前 fail-closed；
- M03 Pester 精确断言唯一 Python 3.12 版本检查命令、项目解释器路径和完整参数。

修复后回归：

```text
Agentization Pester
47 passed, 0 failed

M13 Final
Passed=True
ModuleId=M13
GateType=Final
CommandCount=8
```

M13 的八项命令包括差异检查、项目 Python 3.12 检查、后端全量 pytest、后端全量 Ruff、前端 Agent Runtime 合同测试、前端全量测试、前端 lint 和前端生产构建。独立增量复审确认上一轮 2 个 Important 和 1 个 Minor 均已关闭，没有新的 Critical、Important 或 Minor。

## 产品能力边界

- 产品已确认不保留缺失的旧 Docker/provisioner/Sandbox memory profile 能力，对应过期合同测试已退役，不恢复从未存在于当前 Git 历史的文件。
- 当前 Gateway Dockerfile、Harness 活动 Skill 根 `backend/skills/skills` 和 `/mnt/...` 虚拟路径能力仍然保留。
- 网关鉴权统一使用 content-app `Authorization`，缺少 Header 时 fail-closed；测试在 content-app Client 边界替换外部鉴权。
- 没有 migration、配置键、content-app API、生产运行模式、`enabled_intents` 或 Feature Flag 变化。
- 本次只合入代码和门禁基线，不构成发布批准，也不把自动化状态提升为 `automation_active`。

## 回滚与下一步

- 如需回滚，基于本报告和 `MERGE_LOG.md` 使用带中文说明的 `git revert` 撤销本次候选进入 Agent 的提交，禁止改写共享分支历史。
- 修复进入长期 Agent 后，恢复 `codex/agent-0.8.4-m03-context-runtime` 的 M03.4，重新执行真实 M03 Final 门禁。
- M03 Final 绿色后，由 M03.4 写 `ready_for_integration`、独立提交并 push；随后开发者复制执行手册 9.10A 话术，手动启动 M03 单槽集成。
- 不得跳过 M03.4 直接执行 M03 的 9.10A，也不得由本任务自动进入 M03.4。
