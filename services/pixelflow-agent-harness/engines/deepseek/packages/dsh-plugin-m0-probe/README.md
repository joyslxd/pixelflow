# M0 Probe Tool Plugin

此包只用于验证 DeepSeek Harness 自定义 Tool Plugin 的装配、参数校验与结构化结果。它不访问 PixelFlow Gateway、数据库、Provider、网络或用户 Authorization；真实 Capability Tool Plugin 必须在 M3 使用 Tool Broker HTTP 回调替换它。

`m0-safe.cordis.yml` 默认禁用本包；只有 `PIXELFLOW_M0_FAKE_TOOL=1` 的专门 schema/装配测试才会加载。真实 Sidecar Run 不得开启此变量，也不得把本包作为阶段准入证据。

`package.json` 字段映射：

| 字段 | 用途与影响 |
| --- | --- |
| `name` | 标识 M0 私有 Plugin 包，不暴露给最终用户。 |
| `version` | 标识 M0 测试版本；变更后需要重新构建并重跑 Runtime 测试。 |
| `private` | 阻止误发布到 npm，取值为 `true`。 |
| `type` | 启用 ESM 模块解析，取值为 `module`。 |
| `scripts.build` | 编译 TypeScript 到 `dist/`；运行时加载编译产物。 |
| `scripts.test` | 直接验证官方 `defineTool()` 的参数拒绝与稳定 JSON 输出；不连接模型或外部服务。 |
| `dependencies.@deepseek-ai/dsh-tools` | 固定与 Python SDK `0.1.1rc1` 对应的官方 Tool API 版本。 |
| `devDependencies.typescript` | 固定本地编译器版本，仅影响构建环境。 |

`tsconfig.json` 字段映射：

| 字段 | 用途与影响 |
| --- | --- |
| `target` | 生成 ES2022 语法，Runtime 需要支持该语法。 |
| `module`、`moduleResolution` | 使用 NodeNext ESM 解析，错误配置会导致 Runtime 无法加载 Plugin。 |
| `outDir`、`rootDir` | 规定源码和编译产物位置，修改后需同步更新 Cordis 配置。 |
| `strict` | 开启严格类型检查，防止 Tool 参数或结果类型静默漂移。 |
| `declaration` | 生成类型声明，便于后续正式 Plugin 复用合同。 |
| `skipLibCheck` | 跳过第三方声明文件检查，只影响编译速度，不放宽本包源码检查。 |
| `include` | 限定参与编译的 TypeScript 源文件范围。 |
