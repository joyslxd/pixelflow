# Mxx / Mxx.x 交接记录

- from：
- to：
- timestamp：
- base SHA：
- working SHA：
- module branch：
- worktree：
- latest dev SHA included：
- latest agent SHA included：
- dev-sync guard：`passed | not_required | blocked`
- automation state：`design_only | automation_local_ready | automation_active`
- 当前 feature flag：

## 已完成

逐项写清已完成的代码、合同和测试，不写“基本完成”。

## 决策与理由

记录实现中新增的判断；如偏离冻结合同，必须同时链接 `integration/DECISIONS.md` 的决策编号。

## 当前状态快照

- 工作区是否干净：
- 修改/新增文件：
- 未提交或半成品：
- 当前失败测试及预期原因：
- migration/config 状态：
- 文件锁/locked paths：
- 已 push 的远端分支：
- 当前唯一模块写入者已释放：`yes | no`

## 剩余切片

按执行顺序列出，每项仍控制在 1–3 小时。

## 精确恢复动作

1. 第一条需要读取的文件：
2. 第一条需要执行的命令：
3. 第一个预期失败的测试：
4. 完成判断：

## 验证记录

- 已运行命令/exit code：
- 尚未运行及原因：
- mock/live：
- 是否调用付费 API：否
- 独立 reviewer / 结论：
- commit SHA：

## 风险和回滚

写明外部依赖、已知 race/crash window、数据迁移和回滚注意事项。

禁止写入 Authorization、token、API key、完整供应商 URL 查询参数或用户原始长 prompt。
