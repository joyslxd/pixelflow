# PixelFlow 调用 content-app 接口清单

本文档记录 `pixelflow` 代码中所有通过 Borgrise/content-app 基地址调用的接口。

维护规则：

- 只要新增、删除、改名或改参数任何 `content-app` 接口调用，都必须同步更新本文档。
- 新增调用前先搜索 `BORGRISE_BASE_URL`、`make_request(` 和 `make_multipart_request(`。
- `BORGRISE_BASE_URL` 当前代码默认值是 `https://test-video.borgrise.com/api`。如果联动本机同级 `content-app`，应配置成类似 `http://localhost:8082/api`。
- 下表中的接口均按 `content-app` 看到的完整 `/api/...` 路径书写。

## 鉴权和公共约定

调用代码集中在：

- `backend/pixelflow/skills/borgrise/run_generation.py`
- `backend/pixelflow/skills/borgrise/skill.py`
- 主工作流入口在 `backend/app/gateway/routers/pixelflow_*.py` 和旧 `backend/pixelflow/nodes.py`

凭据来源：

- 前端入口请求头 `Authorization`：由 content-app 登录后产生，pixelflow 网关校验通过后写入请求级 ContextVar。
- `BORGRISE_BASE_URL`：content-app/Borgrise API 根地址，必须包含 `/api`；登录态校验也复用该地址并拼接 `/auth/verify`。
- 不再传 `projectId`：content-app/Borgrise 现在按 `Authorization` 对应的登录用户识别项目、资产和扣费上下文。

下面接口都需要 `Authorization: Bearer <content-app-jwt>`。生成类接口还会附带额度相关请求头：

- `modelType` 或 `ModelType`
- `billType`
- `duration`
- `apiModelParamObj`

## 主 PixelFlow 流程实际调用

视频场景包和场景参考图没有新增 content-app 接口路径：前端现在先调用 PixelFlow 网关的 `/agent/flows/video/prepare-scene-packages/start` 或 `/agent/flows/video/generate-scene-assets/start` 获取 Python `job_id`，再轮询对应 `/jobs/{job_id}`。其中场景包主链路 job 内部仍按原能力生成可编辑场景包，并通过 `/api/picture/text_to_image` 生成角色三视图、场景图和道具图；用户离开再回来只查询已有 Python job，不会重复触发 content-app 扣费接口。场景视频生成 job 内部会并行调用下方视频生成接口，当前最大并发数为 3；所有分镜都成功、失败或额度暂停后才统一返回。全部成功后按 `scene_index` 调用 PixelFlow `/agent/flows/video/merge`；如果只有 1 个分镜，PixelFlow 直接把该分镜 URL 作为最终视频返回，不调用 content-app `/api/video/merge`。失败重试时只重新提交 `failed_scenes` 中的分镜，已成功分镜复用旧视频 URL。

| 接口 | 方法 | 调用位置 | 用途 | content-app 对应控制器 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `/api/auth/verify` | `POST` | `content_app_auth.verify_authorization_header_remote()`、SSE 生成器 | 实时校验 content-app token，禁用用户或失效 token 立即拒绝。 | `AuthController.verifyToken()` | pixelflow 本地只读取 JWT payload 里的 `sub` 作为用户名；token 真伪、过期和用户禁用状态以此接口返回为准。 |
| `/api/modelParamConfig/listByCategory/image_generate` | `GET` | `web/src/lib/api.ts` 的 `listImageGenerateModelConfigs()`，由图片编辑参数确认卡触发 | 查询图片生成/编辑可选模型，以及每个模型支持的尺寸和清晰度。 | `ModelParamConfigController.listByCategory()` | 前端在直接图片编辑分支进入生成前调用；默认优先选 `gpt-image-2`。响应里的 `modelType`、`paramConfig.aspectRatioList`、`paramConfig.sizeList` 用于校验 LLM 提取的尺寸和清晰度；不兼容时前端提示用户当前模型不支持，并自动落到当前模型可用参数，允许用户重新选择可用参数后继续提交。用户确认的模型、尺寸和清晰度会写入对话 context，切换对话或刷新恢复后仍显示用户确认过的参数。 |
| `/api/creative/decompose_video_to_storyboard` | `POST` | `skill._decompose_blocking()`，由 `nodes._decompose_reference_videos()` 或 `/agent/flows/video/analyze-storyboards` 触发 | 将用户上传/输入的参考视频拆解为 storyboard shots，供后续 Brief、分镜规划或视频分析结果展示使用。 | `CreativeController.decomposeVideoToStoryboard()` | 可能返回异步 task，随后会调用 `/api/task/{taskId}/status` 轮询；视频分析默认最多等 15 分钟。 |
| `/api/video/image-to-video` | `POST` | `run_generation.image_to_video()`，由 `BorgriseSkill.image_to_video()` 和 `nodes._generate_segment()` 触发 | 按 segment 的首图和 prompt 生成视频片段，是当前 GENERATE 阶段的主生成接口。 | `VideoController.imageToVideo()` | 不再传 `projectId`；视频生成默认最多等 1 小时。 |
| `/api/task/{taskId}/status` | `GET` | `run_generation.poll_task()` | 轮询异步生成、拆解和 SmartPPT 任务，直到完成、失败或超时。 | `TaskController.getTaskStatus()` | 被多个 wrapper 复用，但超时按入口区分：视频生成 1 小时、图片生成 10 分钟、视频分析/参考拆解 15 分钟、SmartPPT 2 小时；单次状态查询遇到可恢复网络错误时，除 `make_request` 内部重试外，还会继续状态轮询最多 3 次，避免任务已完成但状态查询短暂 SSL/网络异常导致误判失败。 |
| `/api/picture/image_edit` | `POST` | `run_generation.image_edit()`，由 `pixelflow_image.generate_image()` 和 `pixelflow_image.edit_image_asset()` 触发 | 对已有图片按 prompt 编辑；主图片流程识别 `image_operation=image_edit` 时会跳过表单/创意/plan 直接编辑上传原图，也用于视频场景包全局素材引用后编辑并替换原素材。 | `ImageController.imageEdit()` | 普通图片编辑分支会传上传原图 URL、用户编辑 prompt、`model`、`width`、`height`、`imageSize`、`size`、`max_images`、`num`；`size` 是比例字符串如 `9:16`，`imageSize` 是清晰度如 `2K/3K/4K/1080p`，不能混用。模型、比例和清晰度取值以 `/api/modelParamConfig/listByCategory/image_generate` 为准。若 content-app 参数配置允许但价格配置缺失，生成接口会返回业务失败，PixelFlow 直接展示失败原因并允许用户回到参数确认卡重选。`max_images/num` 保持一致。图片编辑失败后，前端重新打开模型/尺寸/清晰度确认卡，不直接复用失败参数盲重试。`edit-asset` 请求只传单张 `source_image_url`、用户编辑 prompt、`max_images=1`；生成后通过 `/api/task/{taskId}/status` 轮询结果，图片默认最多等 10 分钟。 |
| `/api/picture/smart-ppt/generatePptSummary` | `POST` | `run_generation.generate_ppt_summary()`，由 `pixelflow_ppt.start_ppt_summary()` 触发 | 根据 PPT 主题、风格和 Word/Excel/PDF 附件生成 PPT 大纲。 | `SmartPptController.generatePptSummary()` | 请求 body 传 `topic`、`pptStyle`、`fileUrls`、可选 `smartPptProjectId`；返回 taskId 后通过 `/api/task/{taskId}/status` 轮询，PPT 默认最多等 2 小时。 |
| `/api/picture/smart-ppt/updatePptSummary` | `POST` | `run_generation.update_ppt_summary()`，由 `pixelflow_ppt.start_update_ppt_summary()` 触发 | 根据用户修改意见更新 PPT 大纲。 | `SmartPptController.updatePptSummary()` | 请求 body 传 `originalOutline`、`smartPptProjectId`、`modificationOpinion`；返回 taskId 后轮询。 |
| `/api/picture/smart-ppt/generatePptContentToJson` | `POST` | `run_generation.generate_ppt_content_json()`，由 `pixelflow_ppt.start_ppt_content_json()` 触发 | 将确认后的 PPT 大纲转为页面 JSON。 | `SmartPptController.generatePptContentToJson()` | 请求 body 传 `originalOutline`、`smartPptProjectId`、`pptStyle`；轮询结果读取 `content_json`。 |
| `/api/picture/smart-ppt/generatePptImage` | `POST` | `run_generation.generate_ppt_image()`，由 `pixelflow_ppt.start_ppt_images()` 和 `start_regenerate_ppt_image()` 触发 | 根据单页 JSON 生成 PPT 页面图片。 | `SmartPptController.generatePptImage()` | 请求 body 传 `jsonContent`、`smartPptProjectId`；轮询结果可能直接是图片 URL 字符串。 |
| `/api/picture/smart-ppt/generatePptFile` | `POST` | `run_generation.generate_ppt_file()`，由 `pixelflow_ppt.start_ppt_file()` 触发 | 根据已生成的页面图片 URL 集合生成 PPT 文件。 | `SmartPptController.generatePptFile()` | 请求 body 传页面图片 `fileUrls`、`smartPptProjectId`；轮询结果读取 `ppt_url`、`filename`、`slide_count`。 |

## Borgrise 工具和 CLI 封装的接口

这些接口已经在 `run_generation.py` 里封装，可能被 CLI、调试脚本、后续节点或长视频辅助函数调用；当前主 PixelFlow 流程不一定直接走到。

| 接口 | 方法 | 调用位置 | 用途 | content-app 对应控制器 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `/api/upload` | `POST multipart` | `run_generation.upload_file()` | 上传本地文件，返回后续接口可引用的 URL。 | `UploadController.uploadFile()` | `content-app` 会按 content type 或扩展名识别 `image`、`video`、`audio` 或普通文件，再上传到 TOS。 |
| `/api/asset/virtual-human-asset` | `POST` | `run_generation.create_virtual_human_asset()` | 创建虚拟人第三方资产。 | `AssetLibraryController.createVirtualHumanAsset()` | 通常和 `/api/asset/create` 串联使用。 |
| `/api/asset/create` | `POST` | `run_generation.create_virtual_human_asset()` | 在 content-app 资产库创建资产记录。 | `AssetLibraryController.createAsset()` | 依赖前一步返回的第三方资产 ID。 |
| `/api/asset/refrence-urls` | `POST` | `run_generation.resolve_asset_urls()` | 根据 asset id 查询可引用的 `refrence_url`。 | `AssetLibraryController.getRefrenceUrls()` | 接口名保留了后端现有拼写 `refrence`。 |
| `/api/video/text-to-video` | `POST` | `run_generation.text_to_video()` | 纯文本生成视频。 | `VideoController.textToVideo()` | CLI/工具能力，当前主流程未直接调用；视频生成默认最多等 1 小时。 |
| `/api/video/reference-mode-video` | `POST` | `run_generation.reference_mode_video()` | 用图片、视频、音频参考素材生成视频。 | `VideoController.referenceModeVideo()` | 长参考视频、原生音频参考视频 helper 会复用该 wrapper；视频生成默认最多等 1 小时。场景视频 job 可能并行触发多个该 wrapper 调用，当前最大并发数为 3；最终视频生成后，用户从原 `video_scene_packages` 卡片进入“查看分镜”并只修改部分分镜时，PixelFlow 只会把 dirty scenes 提交到 `/agent/flows/video/generate-scenes/start`，该 wrapper 只为这些分镜实际触发；未修改分镜复用旧视频 URL。失败或额度暂停时，重试也只为 `failed_scenes` 中的分镜触发。 |
| `/api/video/extend-video` | `POST` | `run_generation.extend_video()`，`BorgriseSkill.extend_video()` | 在已有视频基础上继续延展内容。 | `VideoController.extendVideo()` | 长视频 helper 会复用；当前 `nodes.py` 主生成流程未直接调用；视频生成默认最多等 1 小时。 |
| `/api/video/merge` | `POST` | `run_generation.merge_videos()` | 合并多个视频 URL 为一个交付视频。 | `VideoController.mergeVideos()` | 请求 body 只传 `videoUrls`，项目归属由 content-app 登录态处理；当 PixelFlow 只有 1 个分镜视频时不会调用该接口，直接把单分镜 URL 作为最终视频。 |
| `/api/picture/text_to_image` | `POST` | `run_generation.text_to_image()` | 文生图。 | `ImageController.textToImage()` | 生成后通过 `/api/task/{taskId}/status` 轮询结果；图片默认最多等 10 分钟。 |
| `/api/picture/multi_reference_image_generation` | `POST` | `run_generation.reference_image()` | 多参考图生图。 | `ImageController.multiReferenceImageGeneration()` | 生成后通过 `/api/task/{taskId}/status` 轮询结果；图片默认最多等 10 分钟。 |
| `/api/picture/image_edit` | `POST` | `run_generation.image_edit()` | 对已有图片按 prompt 编辑。 | `ImageController.imageEdit()` | 请求体包含 `image_url`、`prompt`、`model`、`width`、`height`、`imageSize`、`size`、`max_images`、`num`；主 PixelFlow 图片流程的直接图片编辑分支、plan 后图片编辑分支和视频场景包全局素材编辑都会复用；生成后通过 `/api/task/{taskId}/status` 轮询结果；图片默认最多等 10 分钟。模型、比例和清晰度的可选项由 `/api/modelParamConfig/listByCategory/image_generate` 提供，Python 侧不再维护模型级清晰度白名单，避免前端可选但网关旧规则提前拦截。 |
| `/api/picture/batch_text_to_image` | `POST` | `run_generation.batch_text_to_image()` | 批量文生图。 | `ImageController.batchTextToImage()` | 可能返回多个 task id；每个图片任务默认最多等 10 分钟。 |

## 当前已知注意点

- 不要在 `pixelflow` 配置文件、IDEA Run Configuration、环境变量或代码中写死用户 token、用户名、密码。
- `run_generation.py` 会覆盖调用方误传的 `Authorization`，始终使用当前请求上下文中的 content-app token。
- `content-app` dev 配置端口是 `8082`，本地联动时 `BORGRISE_BASE_URL` 应指向 `http://localhost:8082/api`。
