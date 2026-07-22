# M03 模型档案、Token 预算与 ContextEnvelope

- phase：`not_started`
- owner：A
- branch：计划 `codex/agent-0.8.4-m03-context-runtime`
- 依赖：M00
- 当前切片：M03.1

## 切片

- [ ] M03.1 ModelContextProfile 与 128K 保守降级（2h）
- [ ] M03.2 TokenMeter/usable budget（2.5h）
- [ ] M03.3 ContextEnvelope assembler（2.5h）
- [ ] M03.4 tool/artifact externalizer（2h）

## 恢复提示

256K/384K/512K 是建议上限，不是当前 AIRouter 已验证事实。缺失档案必须走 128K，并在测试中锁定。
