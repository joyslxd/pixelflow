# pixelflow — Context Index

> 会话入口。先读本文件，再按需读其他画像文件。

## Files

| File | Content | Load when |
|------|---------|-----------|
| [overview.md](overview.md) | 业务背景与主流程 | 设计 / 系分 |
| [conventions.md](conventions.md) | 编码与协作约定 | 实现阶段 |

## Related

| Path | When |
|------|------|
| `.spec/context-dict/` | 调查前 `rg` 检索历史结论 |
| `.spec/security-checklists/` | 支付/权限/KYC/DB/API 变更对照 |
| `docs/superpowers/specs/2026-08-04-unified-video-agent-design.md` | VideoAgent 设计真源 |
| `AGENTS.md` / `README.md` | 能力表与本地联调细节 |

## Key Constraints for AI

- 不要提交 `.env` / 凭证文件
- 不要删除现有测试
- 确认只走确认/取消 API；公开事件不泄漏内部推理
- 优先 VideoAgent 链路；`LegacyWorkspace` 只做兼容承载
- 改对外 API / 额度 / 鉴权 / schema 前先做 L2/L3 风险判定

## 维护

完整重扫可再运行 `/vibe-init`，或 `bash vibe-quick-init.sh --force` 后手工合并差异。
