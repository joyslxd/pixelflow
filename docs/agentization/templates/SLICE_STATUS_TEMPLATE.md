# Mxx.yy 切片状态

- phase：`not_started | ready | in_progress | blocked | review | merged`
- development line：`A | B`
- module：
- slice：
- execution mode：固定 `sequential`
- owner/Codex task：
- base Agent SHA：
- previous slice commit SHA：
- required freeze/dependency SHA：
- branch：
- worktree absolute path：
- locked files：
- started at：
- updated at：

## 允许修改

按运行手册和工作拆分写精确路径。

## 禁止修改

列出模块分支共享文件、其他切片锁定路径和两个 feature 分支。

## TDD 与实现记录

- 失败测试命令/证据：
- 最小实现：
- 通过测试命令/证据：
- 独立 reviewer：
- review 发现与处理：

## Git 交付

- commit SHA：
- pushed remote branch：
- module branch push 状态：
- 是否检测到同分支/同 worktree 的其他写入者：`no`（如为 `yes` 必须停止）

## 下一步

- 第一条恢复命令：
- 尚未运行测试及原因：
- 硬阻塞：

禁止记录凭据、Authorization、完整供应商 URL 查询参数、用户原始长 prompt 或隐藏推理。

禁止创建切片子分支或切片 worktree；后续切片在上一切片 commit/push 完成并释放写入权后，恢复同一个模块分支/worktree。
