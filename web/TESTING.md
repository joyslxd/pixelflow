# Web 测试命令说明

`package.json` 新增测试脚本使用 Node 统一编排 TypeScript 编译和测试执行，临时产物写入操作系统临时目录并在结束后删除，因此 Windows、macOS 和 Linux 使用同一入口。

| 配置键 | 用途 | 执行影响 |
| --- | --- | --- |
| `scripts.test` | 聚合运行 `web/tests` 下全部 `*.test.mjs`，并预先编译测试依赖的 TypeScript 模块。 | 作为 Web 非付费回归入口；失败时返回非零退出码，不修改业务文件，也不调用外部 API。 |
| `scripts.test:agent-runtime-contracts` | 只编译并验证 `contracts-v1.md` 对应的 TypeScript 镜像合同和 wire event 校验。 | 供 M00-B 定向 TDD 与后续跨端合同门禁使用；失败时返回非零退出码，不执行现有 UI 回归。 |

执行方式：

```bash
corepack pnpm test:agent-runtime-contracts
corepack pnpm test
```

两个脚本都要求先完成 `corepack pnpm install`。它们不读取 Authorization、供应商密钥或生产配置。

## F4 切换验证

`pnpm test` 包含 F4 源码门禁：禁止旧工作台、`/agent/flows`、旧 Task Client、`pending*Job` 业务状态和 Sidecar 私有 URL 回流。`pnpm build-dev` 会产生带内容哈希的 JavaScript/CSS 文件，发布时应以新 `index.html` 引用这些文件；CDN 不得将 HTML 长期缓存。

完整浏览器旅程需在已启动 Gateway、Sidecar 与隔离测试数据上执行：新建对话、连续输入、刷新恢复、切换对话、断网重连、表单提交/关闭、确认、授权恢复、GenerationJob 完成和最终下载。旧静态资源命中新 Gateway 时必须得到升级提示，且不得对已删除 API 无限重试。
