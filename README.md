# PixelFlow

> ⚠️ **项目状态:开发中(Work in Progress)**
>
> 这不是一个完整可交付的项目。核心流水线已经跑通,但仍有关键环节(成片渲染仅 v1、P1 语义记忆、前端等)未完成,接口与数据结构可能随时调整。请勿用于生产环境。

PixelFlow 是一个电商带货短视频生成 AI Agent:输入商品信息,经过「采集 → 策划 → 人工确认 → 生成 → 剪辑 → 质检」的阶段化流水线,产出可剪辑的短视频草稿。

## 架构

后端基于 [DeerFlow](https://github.com/bytedance/deer-flow) 精简提取的 harness(FastAPI 网关、LangGraph 运行时/checkpointer、持久化等基础设施,移除了 IM 渠道集成),其上是 PixelFlow 自己的业务包。版权说明见 [`NOTICE`](NOTICE)。

```
backend/
├── pixelflow/                  # 业务包(本项目核心)
│   ├── graph.py / nodes.py     # LangGraph 状态机:阶段编排
│   ├── state.py                # TaskState:贯穿全图的单一状态
│   ├── intake/                 # 采集:商品信息提取、参数归一、需求完整性门控
│   ├── creative/               # 策划:Brief 生成(LLM)+ 校验修复(纯逻辑)
│   ├── generate/               # 生成:PromptEngine(Seedance 结构化提示词)
│   ├── edit/                   # 剪辑:Timeline IR + DraftPlan(纯逻辑)
│   ├── qc/                     # 质检:覆盖率/时长检查(纯逻辑)
│   ├── skills/                 # 能力边界(Protocol):borgrise 视频生成、jianying 草稿渲染
│   ├── tasks/                  # 业务任务持久化(Memory / SQL / MySQL 三种后端)
│   └── preferences/            # P0 结构化用户偏好
├── app/gateway/                # FastAPI 网关(含 /api/tasks、/api/users 偏好路由)
└── packages/harness/deerflow/  # DeerFlow 基础设施
```

设计原则:**创意交给 LLM,机械逻辑用纯函数**。所有纯逻辑模块(校验、完整性检查、Timeline、QC、PromptEngine)可离线测试;外部依赖(Borgrise、剪映)收敛在 `skills/` 的 Protocol 边界后面,通过工厂函数 + 环境变量切换实现,缺失时优雅降级而不是崩溃。

### 流水线

```
采集 intake → 策划 creative → [Brief 人工确认 interrupt] → 生成 generate → 剪辑 edit → 质检 qc → done
                    ↑__________ revise __________|                ↑_____ QC 不通过(≤2 次)_____|
```

- **Brief 确认**是 human-in-the-loop 的 `interrupt()` 门控:approve 进入生成,revise 回到策划。
- **QC** 失败回到生成重试,上限 `MAX_QC_ATTEMPTS = 2`;采集补充信息上限 `MAX_INTAKE_ROUNDS = 3`。
- 图注册在 [`backend/langgraph.json`](backend/langgraph.json),入口 `pixelflow`,checkpointer 由平台层注入。

## 当前进度

| 阶段 / 模块 | 状态 | 说明 |
|---|---|---|
| 采集 INTAKE | ✅ 已完成 | LLM 提取商品信息 + 参数归一 + 需求完整性门控(信息不足时中断补充,≤3 轮) |
| 策划 CREATIVE | ✅ 已完成 | LLM 生成分镜 Brief + 纯逻辑校验修复(validator) |
| Brief 人工确认 | ✅ 已完成 | `interrupt()` 门控,支持 approve / revise |
| 生成 GENERATE | ✅ 已完成 | 按总时长分段生成(seedance 单次 ≤15s):≤15s 融合所有分镜提示词**一次出整条**,>15s 拆多段**并行**生成后拼接;商品主图锚定每段。**真机已验证**:≤15s 单段、>15s(30s→3 段)多段并行均跑通真出片 |
| 剪辑 EDIT | ✅ 已完成 | Timeline IR + DraftPlan 纯逻辑;两条渲染路径:剪映草稿(pyJianYingDraft,精修用)或 FFmpeg 无头渲染直出 mp4(`PIXELFLOW_EDIT_SKILL=ffmpeg`),保留源音轨;单段直通、多段 concat 均真机验证 |
| 质检 QC | ✅ 已完成 | 片段完整性(阻断)+ 时长达标(警告),不通过回 GENERATE |
| 任务 API | ✅ 已完成 | `/api/tasks`:建任务、查询、结果/资产、Brief 确认/修订、SSE 进度事件;Memory/SQL/MySQL 三种存储 |
| 用户偏好 P0 | ✅ 已完成 | `/api/users/{id}/preferences`:结构化偏好(正则确定性抽取),建任务时注入初始状态 |
| 参考视频拆解 | ✅ 已完成 | INTAKE 调用博观 decompose_video_to_storyboard(视觉模型 gemini-3-flash-preview)拆分镜,纯逻辑摘要后注入 Brief 提示词;按参考数量切换创意模式(original / reference / attribution),拆解失败仅警告不阻断。**真机已验证**(小红书链接 → 分镜) |
| 最终视频渲染 | 🚧 v1 可用 | FFmpeg 直出 mp4(裁时长、缩放/填充、保留源音轨、可选花字烧录),已端到端验证产出真实成片(1080×1920 / 30fps / H.264 + AAC,≤15s 与 30s 多段拼接均验证)。暂不支持转场、TTS 旁白与 BGM;1080p 原生生成待博观接口修复(当前 720p 生成 + 上采样) |
| P1 语义记忆 | ❌ 未开始 | mem0/Qdrant 预留位,P0 只有结构化偏好 |
| P1 PPT / 图片生成 | ❌ 未开始 | 规划中 |
| 前端 | ❌ 未开始 | 当前仓库仅后端 |

测试:各纯逻辑模块与关键节点均有离线单测(`backend/tests/test_pixelflow_*`、`test_intake_*`、`test_creative_*`、`test_generate_*`、`test_edit_*`、`test_qc_*`、`test_borgrise_*`、`test_reference_video_nodes.py`、`test_prompt_engine.py`),不依赖外部服务。

## 本地开发

```bash
cd backend
uv sync                          # 安装依赖(Python 3.12)
uv run langgraph dev             # 本地运行图
uv run ruff check                # lint
uv run pytest tests/ -k pixelflow  # 跑 PixelFlow 相关测试
```

### 环境变量

参见 [`backend/.env.example`](backend/.env.example)。常用项:

| 变量 | 默认 | 说明 |
|---|---|---|
| `PIXELFLOW_MYSQL_URL` | 空 | 业务数据 MySQL 连接串;不设则回退 SQL/内存存储 |
| `PIXELFLOW_VIDEO_SKILL` | `borgrise` | 视频生成实现 |
| `PIXELFLOW_EDIT_SKILL` | `jianying` | 剪辑实现:`jianying`(剪映草稿)或 `ffmpeg`(直出 mp4) |
| `PIXELFLOW_DECOMPOSE_SKILL` | `borgrise` | 参考视频拆解实现 |
| `PIXELFLOW_DRAFT_ROOT` | 系统临时目录 | 剪映草稿输出根目录 |
| `PIXELFLOW_RENDER_ROOT` | 系统临时目录 | FFmpeg 成片输出根目录 |
| `PIXELFLOW_CAPTION_FONT` | 空 | 字体文件路径;设置后 FFmpeg 渲染才烧录花字 |
| `BORGRISE_API_TOKEN` | 空 | Borgrise 生成所需 |

> 剪映草稿生成依赖 `pyJianYingDraft`(及原生 `pymediainfo`),未安装时 EDIT 阶段会优雅降级:草稿生成失败记入 `edit_notes`,流水线继续推进。

## License

见 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)(DeerFlow 归属说明)。
