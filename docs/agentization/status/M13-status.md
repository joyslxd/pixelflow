# M13 集成、Shadow、灰度、回滚与交付

- phase：`not_started`
- owner：A+B；当周单一集成人
- branch：计划 `codex/agent-0.8.4-m13-integration`
- 依赖：按 R1–R4 增量满足；最终收口依赖 M01–M12
- 当前切片：M13.1

## 切片

- [ ] M13.1 / R1 assist、压缩 UI/恢复、旧流程等价、10% 灰度（2.5h）
- [ ] M13.2 / R2 视频 replay/shadow/黄金对话/mock E2E、10%→30%（3h）
- [ ] M13.3 / R3 图片/编辑、PPT、视频分析 mock E2E、四 intent 30%（3h）
- [ ] M13.4 / R4 五流程全量、10%→30%→50%→100%、kill switch/回滚（2.5h）
- [ ] M13.5 / R4 经批准真实冒烟/文档/发布签字（2h）

## 恢复提示

Shadow 不能调用付费 API，也不能写 PowerMem 经验。回滚只影响新对话；运行中的 Supervisor 对话继续排空或人工处理，不能强切 owner。
