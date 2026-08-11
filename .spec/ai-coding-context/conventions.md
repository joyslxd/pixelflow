# pixelflow — conventions

## 实现原则

- AI 是主实现者：检索 context-dict → 澄清 → 设计（按规模）→ 写代码+测试 → 自我 Review → 沉淀知识
- 微改动（单文件 / 少于 50 行 / bugfix）直接实现；小需求一页纸确认后实现；中大需求走 Phase A/B/C
- 优先改现有文件，少新建；不主动全量重写 `LegacyWorkspace.tsx`

## VideoAgent / Runtime

- 前端以 Snapshot + SSE 投影驱动 UI，不依赖临时内部字段
- 确认 / 取消只走公开确认 API；自然语言同意仅映射到同一 API
- 确认 HTTP 不得同步拖跑长 LLM 后续步（确认步 `stop_after` + 后台 `resume_plan`）
- 计划步数：LLM 提案 ≤8；Path A 含创意确认闸门可达 9
- 工具入参先过 DTO；公开事件不泄漏内部参数 / 推理

## 后端编码

- public 方法校验入参；业务逻辑在 Service；Controller 保持薄
- 方法尽量 ≤50 行；事务只在 Service 层
- Gateway 新增接口必须以 `/agent` 开头
- 禁止直接改生产 bootstrap / 未经讨论改 CI/CD / schema

## 前端编码

- V2 状态与组件优先落在 `web/src/features/video-agent/*`
- 涉及共享状态 / 投影 / 序列化时同步更新 `web/tests/*` 与后端相关测试
- 上传走 content-app `/api/upload`，结果统一作为 `materials`

## Git

- 分支: `{stage}_{reqId}_{developer}`
- 提交: `{type}: {subject}-{reqId}`，末尾加 `AI-Assisted: yes`
- 不提交 `.env` / 凭证；不删现有测试
- 跨仓需求：各仓都要 commit + push + PR/MR 才算完成

## 知识沉淀

写入 `.spec/context-dict/{域}/{主题}.md` 的触发：读 ≥3 文件理清机制、找到 bug 根因、发现非显而易见边界、或下次易踩坑处。

格式:

```md
---
topic:
module:
date:
keywords:
---
## 结论摘要
## 关键文件
## 核心逻辑
## 注意事项
```
