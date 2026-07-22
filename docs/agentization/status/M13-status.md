# M13 集成、Shadow、灰度、回滚与交付

- phase：`not_started`
- owner：A+B；当周单一集成人
- branch：计划 `codex/agent-0.8.4-m13-integration`
- 依赖：M01–M12
- 当前切片：M13.1

## 切片

- [ ] M13.1 integration/migration/config/OpenAPI（2.5h）
- [ ] M13.2 replay/shadow/无副作用对比（3h）
- [ ] M13.3 五流程 + 图片编辑 mock E2E（3h）
- [ ] M13.4 白名单/1%/5%/kill switch/回滚（2.5h）
- [ ] M13.5 经批准真实冒烟/文档/发布签字（2h）

## 恢复提示

Shadow 不能调用付费 API，也不能写 PowerMem 经验。回滚只影响新对话；运行中的 Supervisor 对话继续排空或人工处理，不能强切 owner。
