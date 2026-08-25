# Sidecar 第三方组件通知

本文件记录 M0 已直接引入的第三方运行时组件。完整传递依赖、文件哈希和平台 wheel 列表以同目录 `uv.lock` 为准；发布镜像构建必须使用 `uv sync --locked`，不得改用浮动版本。

| 组件 | 固定版本 | 许可证 | 用途 | 来源 |
| --- | --- | --- | --- | --- |
| `deepseek-harness-sdk` | `0.1.1rc1` | MIT | 通过 stdio JSON-RPC 驱动 DeepSeek Harness Runtime | PyPI 官方发布包 |
| `deepseek-harness-runtime-bin` | `0.1.1rc1` | MIT | SDK 自动安装的同版本平台运行时二进制 | PyPI 官方发布包 |
| `pydantic` | 以 `uv.lock` 为准 | MIT | Sidecar 稳定 DTO 校验 | PyPI 官方发布包 |

平台规则：本地 macOS ARM64 使用 `macosx_14_0_arm64` wheel；Linux 部署使用同一 `0.1.1rc1` 的 `manylinux_2_28_x86_64` 或 `manylinux_2_28_aarch64` wheel。平台二进制不同不构成版本漂移，SDK、Runtime 与 lockfile 版本必须一致。

M0 已确认官方默认 Composition 会挂载 Bash/PTY，并在当前 macOS ARM64 wheel 上因缺少 `pty.node` 无法启动。PixelFlow 只能使用 `engines/deepseek/cordis/m0-safe.cordis.yml` 或后续经同等安全审计的自定义 Composition，禁止回退到 SDK 默认 Composition。
