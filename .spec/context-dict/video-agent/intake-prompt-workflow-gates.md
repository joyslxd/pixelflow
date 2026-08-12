---
topic: Intake 提示词按工作流闸门规则裁决
module: video-agent
date: 2026-08-11
keywords:
  - _INTAKE_SYSTEM_PROMPT
  - 工作流闸门规则
  - Path A
  - Path B
  - script_plan_confirmed
  - confirm_script_creative
  - needs_user_reply
  - workspace_digest
---

## 结论摘要

Intake 系统提示不再写「缺画幅就追问」这类零散指令，改为把 **start/script 状态机前置条件**整理成【工作流闸门规则】交给 LLM：对照用户输入 + `workspace_digest` + `blocking_confirmation` 判断能否进入下一步。代码只校验机器块 schema（intent/missing 白名单），不做路径关键词裁决。

## 相关文件

- `backend/pixelflow/video_agent/thinking_stream.py`（`_INTAKE_SYSTEM_PROMPT`）
- `backend/pixelflow/video_agent/planner/workspace_digest.py`（补充 awaiting/missing/source 等事实字段）

## 核心逻辑

1. Path A：start → 创意确认 → plan…export；创意确认前须齐时长/画幅/CTA
2. Path B：成稿 polish；同样要生产字段；生成前须脚本方案确认
3. continue_*：硬前置 `script_plan_confirmed`
4. digest 只给事实，不给「可以推进」结论

## 注意事项

- missing 标签白名单含：视频画幅、结尾行动引导、整片时长、脚本方案确认、角色设定、产品信息、创意方向
- 示例 JSON 禁止 trailing comma
- Entrypoint 里仍有 field_followup 等降级，属失败兜底，不是 Intake 主裁决
