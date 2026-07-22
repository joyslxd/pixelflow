# M01 持久化、CAS、Turn Inbox 与 Event Outbox

- phase：`not_started`
- owner：A
- base SHA：待 M00 合入后填写
- branch：计划 `codex/agent-0.8.4-m01-runtime-store`
- 依赖：M00
- 当前切片：M01.1

## 切片

- [ ] M01.1 数据模型与 additive migration（2.5h）
- [ ] M01.2 SQL/Memory Repository（3h）
- [ ] M01.3 revision/CAS/服务端保留 namespace（2.5h）
- [ ] M01.4 Turn Inbox 幂等和顺序领取（2h）
- [ ] M01.5 Event Outbox/sequence/cursor（2h）

## 恢复提示

先读取现有 `tasks/store.py`、conversation context 全量替换和剪映原子 patch 测试。下一步第一条验证应是新增 migration/Store 合同失败测试。
