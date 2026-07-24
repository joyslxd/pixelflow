# M07 前端 Supervisor 事件 Runtime

- phase：`in_progress`
- owner：B
- branch：`codex/agent-0.8.4-m07-web-runtime`
- 依赖：M00
- 当前切片：M07.2
- base SHA：`5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- 当前唯一写入者：已释放（M07.1 完成）
- 开始时间：`2026-07-24 07:19:26 +0800`
- M07.1 完成时间：`2026-07-24 08:30:24 +0800`
- M07.1 已释放文件：`web/src/lib/supervisor/api.ts`、`web/tests/supervisorApi.test.mjs`、`web/scripts/run-tests.mjs`、`docs/agentization/status/M07-status.md`、`docs/agentization/test-reports/M07.1.md`

## 开工检查

- 依赖验证：远端 `feature/agent_0.8.4_boguan` 为 `5826c74`，已包含 `M00-status phase=merged`。
- dev→agent 预检：远端 dev `fb74507` 已是远端 Agent 的祖先，无需修改长期分支。
- 环境说明：本机没有可执行的 PowerShell；已按 `Start-AgentModule.ps1` 的 fail-closed 条件逐项验证干净工作区、远端基线、分支不存在、worktree 路径不存在和唯一写入者后，创建并推送模块分支。

## 切片

- [x] M07.1 API transport（2h）
- [ ] M07.2 SSE/cursor/gap/reconnect（2.5h）
- [ ] M07.3 reducer 四维状态机（2.5h）
- [ ] M07.4 conversation hook/Abort 隔离（2h）
- [ ] M07.5 legacy snapshot adapter（2h）

## 恢复提示

本模块不改 `WorkspacePage.tsx`。全部使用 fixture/mock server 开发，先证明重复/乱序事件和切换对话安全。

## M07.1 完成记录

- 实现：新增 snapshot、Turn 启动、Interrupt 响应和运行状态 API transport；统一 `/agent` 路径、动态 ID 编码、Authorization、AbortSignal 与安全协议错误。
- TDD：经历模块缺失、reviewer 边界用例、非 2xx 正文安全和非法 JSON 安全映射四阶段 RED/GREEN，最终 Web 聚合测试 `226/226` 通过。
- 验证：`npm test`、`npm run lint`、`npm run build-prod`、`git diff --check` 全部通过；生产构建只有既存 chunk 体积提醒。
- 审核：独立 reviewer `/root/m07_1_review_retry` 最终确认无未解决 Important 或 Minor。
- 环境：Corepack 因签名 keyid 错误不可用，按仓库兜底流程使用 npm；未修改锁文件。
- 外部调用：未调用任何真实付费 API。
- 下一步：后续开发者需重新执行安全预检并取得唯一写入权后，才能串行开始 `M07.2 SSE/cursor/gap/reconnect`。
