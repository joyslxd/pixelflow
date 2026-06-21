# PixelFlow 调用 content-app 接口清单

本文档记录 `pixelflow` 代码中所有通过 Borgrise/content-app 基地址调用的接口。

维护规则：

- 只要新增、删除、改名或改参数任何 `content-app` 接口调用，都必须同步更新本文档。
- 新增调用前先搜索 `BORGRISE_BASE_URL`、`make_request(`、`make_multipart_request(` 和 `with_project(`。
- `BORGRISE_BASE_URL` 当前代码默认值是 `https://test-video.borgrise.com/api`。如果联动本机同级 `content-app`，应配置成类似 `http://localhost:8082/api`。
- 下表中的接口均按 `content-app` 看到的完整 `/api/...` 路径书写。

## 鉴权和公共约定

调用代码集中在：

- `backend/pixelflow/skills/borgrise/run_generation.py`
- `backend/pixelflow/skills/borgrise/skill.py`
- 主工作流入口在 `backend/pixelflow/nodes.py`

凭据来源：

- `BORGRISE_API_TOKEN`：直接作为 `Authorization: Bearer <token>` 使用。
- `BORGRISE_USERNAME`、`BORGRISE_PASSWORD`：没有 token 或 token 过期时，调用 `/api/auth/login` 换取 token。
- `BORGRISE_BASE_URL`：content-app/Borgrise API 根地址，必须包含 `/api`。
- `BORGRISE_PROJECT_ID`：追加到部分生成接口的 `projectId` query 参数，默认 `1`。

除 `/api/auth/login` 外，下面接口都需要 `Authorization: Bearer <token>`。生成类接口还会附带额度相关请求头：

- `modelType` 或 `ModelType`
- `billType`
- `duration`
- `apiModelParamObj`

## 主 PixelFlow 流程实际调用

| 接口 | 方法 | 调用位置 | 用途 | content-app 对应控制器 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `/api/auth/login` | `POST` | `run_generation.login_and_refresh_token()` | 用 `BORGRISE_USERNAME`、`BORGRISE_PASSWORD` 登录，换取 JWT token。 | `AuthController.login()` | 登录响应的 `data.token` 会写回当前进程的 `BORGRISE_API_TOKEN`。 |
| `/api/creative/decompose_video_to_storyboard?projectId=1` | `POST` | `skill._decompose_blocking()`，由 `nodes._decompose_reference_videos()` 触发 | 将用户上传/输入的参考视频拆解为 storyboard shots，供后续 Brief 和分镜规划使用。 | `CreativeController.decomposeVideoToStoryboard()` | 可能返回异步 task，随后会调用 `/api/task/{taskId}/status` 轮询。 |
| `/api/video/image-to-video?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.image_to_video()`，由 `BorgriseSkill.image_to_video()` 和 `nodes._generate_segment()` 触发 | 按 segment 的首图和 prompt 生成视频片段，是当前 GENERATE 阶段的主生成接口。 | `VideoController.imageToVideo()` | `projectId` 默认 `1`，由 `with_project()` 追加。 |
| `/api/task/{taskId}/status` | `GET` | `run_generation.poll_task()` | 轮询异步生成、拆解任务，直到完成、失败或超时。 | `TaskController.getTaskStatus()` | 被视频生成、图片生成、参考拆解等多个 wrapper 复用。 |

## Borgrise 工具和 CLI 封装的接口

这些接口已经在 `run_generation.py` 里封装，可能被 CLI、调试脚本、后续节点或长视频辅助函数调用；当前主 PixelFlow 流程不一定直接走到。

| 接口 | 方法 | 调用位置 | 用途 | content-app 对应控制器 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `/api/upload` | `POST multipart` | `run_generation.upload_file()` | 上传本地文件，返回后续接口可引用的 URL。 | `UploadController.uploadFile()` | `content-app` 会按 content type 或扩展名识别 `image`、`video`、`audio` 或普通文件，再上传到 TOS。 |
| `/api/asset/virtual-human-asset` | `POST` | `run_generation.create_virtual_human_asset()` | 创建虚拟人第三方资产。 | `AssetLibraryController.createVirtualHumanAsset()` | 通常和 `/api/asset/create` 串联使用。 |
| `/api/asset/create` | `POST` | `run_generation.create_virtual_human_asset()` | 在 content-app 资产库创建资产记录。 | `AssetLibraryController.createAsset()` | 依赖前一步返回的第三方资产 ID。 |
| `/api/asset/refrence-urls` | `POST` | `run_generation.resolve_asset_urls()` | 根据 asset id 查询可引用的 `refrence_url`。 | `AssetLibraryController.getRefrenceUrls()` | 接口名保留了后端现有拼写 `refrence`。 |
| `/api/video/text-to-video?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.text_to_video()` | 纯文本生成视频。 | `VideoController.textToVideo()` | CLI/工具能力，当前主流程未直接调用。 |
| `/api/video/reference-mode-video?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.reference_mode_video()` | 用图片、视频、音频参考素材生成视频。 | `VideoController.referenceModeVideo()` | 长参考视频、原生音频参考视频 helper 会复用该 wrapper。 |
| `/api/video/extend-video?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.extend_video()`，`BorgriseSkill.extend_video()` | 在已有视频基础上继续延展内容。 | `VideoController.extendVideo()` | 长视频 helper 会复用；当前 `nodes.py` 主生成流程未直接调用。 |
| `/api/video/merge` | `POST` | `run_generation.merge_videos()` | 合并多个视频 URL 为一个交付视频。 | `VideoController.mergeVideos()` | 该请求 body 自带 `projectId`，没有通过 `with_project()` 追加 query。 |
| `/api/picture/text_to_image?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.text_to_image()` | 文生图。 | `ImageController.textToImage()` | 生成后通过 `/api/task/{taskId}/status` 轮询结果。 |
| `/api/picture/multi_reference_image_generation?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.reference_image()` | 多参考图生图。 | `ImageController.multiReferenceImageGeneration()` | 生成后通过 `/api/task/{taskId}/status` 轮询结果。 |
| `/api/picture/image_edit?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.image_edit()` | 对已有图片按 prompt 编辑。 | `ImageController.imageEdit()` | 生成后通过 `/api/task/{taskId}/status` 轮询结果。 |
| `/api/picture/batch_text_to_image?projectId=<BORGRISE_PROJECT_ID>` | `POST` | `run_generation.batch_text_to_image()` | 批量文生图。 | `ImageController.batchTextToImage()` | 可能返回多个 task id。 |

## 当前已知注意点

- `pixelflow` 源码和当前 `.idea` 配置中没有找到真实 `BORGRISE_USERNAME`、`BORGRISE_PASSWORD` 或 `BORGRISE_API_TOKEN`；这些值应来自运行环境、IDEA Run Configuration、Windows 环境变量或本地 `.env`。
- `content-app` dev 配置端口是 `8082`，本地联动时 `BORGRISE_BASE_URL` 应指向 `http://localhost:8082/api`。
