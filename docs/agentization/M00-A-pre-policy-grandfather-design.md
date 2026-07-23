# M00-A 中文门禁启用前历史豁免设计

## 背景

M00-A.1 提交 `0af72ff6993e9e67636f21e8e16d641411702d67` 产生时，仓库尚未启用中文工程门禁。该提交使用英文标题，并在 `backend/tests/test_agent_runtime_legacy_invariants.py` 中加入英文 docstring。当前中文门禁从共同 Agent 基线检查完整 M00-A 历史时会拒绝这两类内容。

该提交已经推送，后续 M00-A.2 和 M00-A.3 都建立在其上。通过 rebase 修改标题会改变全部后续提交 SHA，并要求 force-push，因此不采用历史重写。

## 目标

- 只放过完整 SHA 为 `0af72ff6993e9e67636f21e8e16d641411702d67` 的门禁启用前历史。
- 只放过当前仍可由 `git blame` 证明来自该提交的英文注释或 docstring 行。
- 后续新增英文提交、人工注释、docstring 或配置说明继续 fail-closed。
- 不修改 M00-A.1 业务测试内容，不 force-push，不改变 A.2/A.3 SHA。
- 全历史门禁通过后，把 M00-A 模块状态从 `blocked` 更新为 `ready_for_integration`。

## 非目标

- 不提供调用方可任意传入的豁免参数。
- 不使用提交时间、作者、分支名或短 SHA 进行宽泛判断。
- 不放宽 YAML、JSON、注释、docstring 或提交信息的通用中文规则。
- 不启动 M00-I.1，不修改 M00-B 或两个长期 feature 分支。

## 设计

### 精确提交白名单

`Test-ChineseEngineeringPolicy.ps1` 内部维护最小只读映射，键为完整 40 位提交 SHA，值为中文审核原因。首个且当前唯一条目是：

```text
0af72ff6993e9e67636f21e8e16d641411702d67
```

提交标题和正文检查只跳过该精确 SHA。短 SHA、相同标题、相同作者或相邻提交均不匹配。

### 行级来源校验

累计 diff 中发现英文人工注释或 docstring 时，门禁读取该行在 `HeadRef` 下的 `git blame --porcelain` 结果：

- 行来源精确等于白名单 SHA：允许该行继续存在。
- 行由后续提交新增或修改：继续按现有中文规则拒绝。
- blame 无法解析、提交不存在或结果不唯一：fail-closed。
- 门禁显式使用空 `--ignore-revs-file=` 清除仓库或全局 `blame.ignoreRevsFile` 影响，避免后续修改被重新归因到白名单提交。

这样既能保留门禁启用前的英文 docstring，也不会允许开发者复制相同英文文本到新位置，或在后续提交中修改旧行后继续借用豁免。

### 配置与其他规则

配置逐项说明、机器指令最小例外和其他代码注释规则保持不变。历史豁免只作用于提交信息及能精确定位到白名单提交的行，不扩大到整个文件或目录。

### 状态恢复

全历史中文门禁和 M00-A 完整模块门禁通过后：

- `docs/agentization/status/M00-A-status.md` 写为 `ready_for_integration`；
- 移除 `0af72ff` 中文门禁硬阻塞，保留“等待 M00-B.1 后由开发者手动启动 M00-I.1”的下一步；
- `docs/agentization/status/M00-A.3-status.md` 与测试报告追加本次历史兼容修订证据；
- 不把远端自动化标记为 `automation_active`，仍保持 `automation_local_ready`。

## 测试设计

### RED

先增加以下测试并确认当前实现失败：

1. 共同基线 `8e626ae...` 到 `0af72ff...` 的中文策略应通过，但当前会因英文标题和 docstring 失败。
2. 临时仓库中的新英文 commit 仍应失败。
3. 后续提交修改原豁免行后，该英文行应重新失败。
4. 即使仓库配置忽略后续修订，修改原行仍应失败；把原英文文本复制到新位置也应失败。

### GREEN

最小实现完整 SHA 白名单和 blame 行级来源判断，使上述测试同时通过。

### 回归

- Pester 中文门禁与分支自动化全量测试。
- `d15c064..HEAD` 和共同 Agent 基线 `8e626ae..HEAD` 两个范围的中文策略。
- M00-A 完整模块门禁。
- 后端 65 项扩展回归、Ruff、PowerShell AST 和 `git diff --check`。
- 独立 reviewer 只读审核白名单范围是否最小、是否存在绕过路径。

## 安全性与回滚

- 白名单仅在仓库代码中维护，修改必须经过中文 commit、测试和审核。
- 任何无法证明来源的行都拒绝，不根据文本相同放行。
- 回滚本修订只需 revert 后续兼容提交，不需要重写 `0af72ff` 或后续历史。
- 不记录凭据、Authorization、用户内容或供应商 URL。
