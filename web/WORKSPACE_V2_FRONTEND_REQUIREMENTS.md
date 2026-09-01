# Workspace V2 前端改造要求

版本：V2；适用范围：Agent Workspace、Workspace Snapshot、脚本/分镜面板、资产面板和 GenerationJob 进度板。

## 1. 改造目标

前端必须把 Workspace 当作同一份带 `revision` 的权威生产状态，而不是把脚本、资产和分镜分别保存在浏览器本地。Agent 可以自主决定调用哪些 Tool，前端只展示状态、提交用户编辑和处理确认/冲突。

不得在前端实现“先写脚本、再生成资产、再生成视频”的固定调用链，也不得根据按钮点击顺序推断业务状态。

## 2. Snapshot 数据兼容

Snapshot 中新增字段均为可选，前端必须兼容旧字段。优先读取 V2 字段，缺失时回退到旧投影：

| V2 字段 | 旧字段回退 |
| --- | --- |
| `workspace_schema_version` | 缺失按 `1` 处理 |
| `creative_brief` | `product_info`、`video_ratio` 等 |
| `narrative_plan` | `script`、`script_pipeline` |
| `asset_registry` | `global_assets`、`reference_images`、`materials` |
| `prompt_packages` | `scenes`、`scene_packages` |
| `generation_jobs` | `scene_video_progress`、资产/分镜内任务记录 |
| `outputs` | `assets`、`outputs`、`merged_video` |

前端不得假设 Snapshot 包含完整 Prompt、Provider URL、授权信息或全部 GenerationJob 事件。详情应通过受控接口按需读取。

## 3. 四层工作区 UI

### 3.1 创意与生产约束

展示和编辑：

- 品牌、产品、受众、平台；
- 画幅、目标总时长、声音方案、CTA；
- 当前 Workspace revision；
- 是否存在待确认的生产约束。

保存时提交当前 `expected_revision`。收到 409 时保留本地草稿，刷新权威 Workspace 后提示用户合并，不得静默覆盖。

### 3.2 叙事与脚本

展示和编辑：

- 创意概念；
- 人物弧线；
- 时代设定；
- 旁白、对白和声音骨架；
- 品牌收束文案；
- 当前脚本版本和状态。

脚本编辑器只负责修改脚本文本和叙事字段，不自动触发任何计费 Tool。

### 3.3 资产注册表

按 `asset_id` 展示资产卡片，支持角色、场景、道具、产品、Logo、音频等类型。每张卡片展示：

- slot，例如 `@图片1`、`@音频1`；
- 资产类型和角色；
- `planned / generating / ready / failed`；
- 参考资产绑定；
- 是否允许进入视频生成；
- 安全 Artifact 引用和 GenerationJob 状态。

前端不得展示或持久化 Authorization、Provider 原始响应或带签名下载 URL。

### 3.4 Prompt Package

按 `sequence` 展示 A-Q 等分镜段，支持：

- `segment_id`；
- `duration_sec`；
- `generation_mode`：`independent`、`extend`、`reference`；
- Prompt 摘要和展开查看；
- `reference_asset_ids`；
- `continuity_from`、`transition_out`；
- 年代、机位、声音、硬约束；
- 当前视频资产和 GenerationJob 状态。

长片不得按 6 个分镜截断。Gateway 以每个资产/分镜一个 GenerationJob 的方式调度，前端不按并发槽位截断列表。

## 4. Tool 交互要求

前端不得直接调用 `create_storyboard`、`prepare_scene_packages` 或 `generate_scenes`。这些 Tool 由 Agent 通过 Tool Broker 自主选择。

前端只需要：

1. 展示 Agent 返回的规划结果；
2. 展示确认中断并提交用户确认；
3. 监听 Workspace revision 和 GenerationJob 进度；
4. 在用户明确编辑时调用公开 Workspace Command；
5. 在 409 时保留草稿并刷新后重试。

确认操作必须使用稳定 `client_response_id`，重复点击回读同一结果。

## 5. GenerationJob 进度

前端只按 GenerationJob 汇总安全状态，不建立 Batch、Child 或 Operation 层级：

```text
一次 Agent Tool Call
  ├─ GenerationJob（图片资产）
  └─ GenerationJob（视频分镜）
```

进度板至少显示 queued、polling、succeeded、failed 数量，以及对应的资产/分镜标识。
GenerationJob 进入终态时，只更新它对应的 Workspace 资产或分镜版本；前端不得把任意一个任务
终态直接显示为整条视频完成或失败。

## 6. 长视频要求

- 单段时长支持 4–30 秒；
- 总成片时长不得用 180 秒做前端硬限制；
- 17 段、约 368 秒的规划应能完整展示和保存；
- 生成时由 Gateway Worker 调度，前端只消费 GenerationJob 进度；
- 不得以“最多 6 个分镜”限制分镜列表、排序或 Prompt Package 数量。

## 7. 状态管理与刷新

- `workspace.revision` 是所有编辑的 CAS 基准；
- 每次成功写入后以服务端返回 revision 替换本地 revision；
- SSE 事件只做增量更新，遇到序号 gap 必须重新拉取 Snapshot；
- Snapshot 只返回尾部事件时，前端不得认为历史对话被删除；
- 切换会话或 Run 时清空旧 Run 的临时 GenerationJob 状态，不能串项目。

## 8. 验收测试

前端至少补充以下测试：

1. 旧 Snapshot 无 V2 字段时仍能显示脚本、资产和分镜；
2. V2 Snapshot 正确显示四层数据；
3. 17 段、368 秒规划不被截断为 6 段；
4. 多个 GenerationJob 的进度正确汇总；
5. 单个 GenerationJob 失败不会覆盖其他镜头状态；
6. revision 冲突保留用户草稿；
7. 重复确认不会创建第二个恢复 Run；
8. SSE 断线后 Snapshot 刷新能恢复进度；
9. 空输入、运行中输入和历史会话切换不会导致发送按钮失效；
10. 页面不渲染 Authorization、Provider 原始异常或签名 URL。
