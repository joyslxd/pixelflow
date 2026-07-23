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
