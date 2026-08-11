# pixelflow — overview

## 业务是什么

PixelFlow 面向电商内容创作：从自然语言 + 素材附件出发，完成图片生成、短视频生成、视频分析拆解和 PPT 制作。

主流程已从早期 LangGraph-only 任务流演进为前端工作台驱动的分段工作流：采集意图 → 补全表单 → 创意方向 → plan.md / 脚本 Skill → 人工确认 → 资产 / 分镜 / 成片（或图片、PPT 链路）。

## VideoAgent V2（当前主线）

- 统一入口：`VideoAgentEntrypoint` + Agent Runtime Turn / Snapshot / SSE
- 权威状态：`VideoWorkspace`、`AgentPlan` / Step、事务性 Outbox、受控工具注册表
- 脚本创作三路径（概要）:
  - Path A 模糊主题创作：`/start` → **确认选题创意** → `/plan`… → export
  - Path B 成稿润色：review → compliance → …
  - Path C 继续生成：跳过重规划，续跑后续步骤
- UI：`WorkspacePage` → `VideoAgentWorkspace`（当前仍返回 `LegacyWorkspace` 兼容承载）

## 架构速览

```text
Web (React+Vite)
  → FastAPI Gateway /agent/*
    → Agent Runtime (Turn / Snapshot / SSE / compaction)
    → PixelFlow (intake / creative / generate / skills)
    → VideoAgent (workspace / plan / executor / tools)
    → Borgrise / content-app / PowerMem / 剪映
```

## 关键业务约束（摘要）

- `/agent` 请求必须带 content-app `Authorization`
- 计费 Skill 必须透传入口 Authorization；token 不落配置/代码/测试
- 额度不足（402 等）必须暂停并可恢复，不得丢上下文
- `creation_contract` 确认后是后续时长/画幅/模型的权威合同
- 前端进度只展示业务摘要，不暴露 prompt / 思维链 / 密钥 / 堆栈

## 文档入口

- 总览: `README.md`、`AGENTS.md`
- Agent/Skill: `docs/pixelflow-agent-skill-flow-latest-design.md`
- VideoAgent: `docs/superpowers/specs/2026-08-04-unified-video-agent-design.md`
- 计划: `docs/superpowers/plans/2026-08-04-unified-video-agent-v2.md`
