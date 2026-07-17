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

PowerMem 调用边界：

- PixelFlow 进程内所有 PowerMem search、record、health HTTP 请求共用同一请求闸门，避免 OceanBase `OB_SESSION_ENTRY_EXIST`。
- search/health 的锁等待和 HTTP 共用短总预算，超时直接 fail-open，不绕过闸门并发请求；record 使用独立长预算。
- 只有幂等的 search/health 对 `OB_SESSION_ENTRY_EXIST` 最多尝试 3 次，record 不自动重试。
- 该闸门不跨进程；多 worker、多容器或多副本部署仍需要 PowerMem 服务端正确管理数据库 Session。

## 主 PixelFlow 流程实际调用

视频场景包、场景参考图、场景视频和最终视频合并都通过 PixelFlow Python job 包一层：前端先调用 `/agent/flows/video/prepare-scene-packages/start`、`/agent/flows/video/generate-scene-assets/start`、`/agent/flows/video/generate-scenes/start` 或 `/agent/flows/video/merge/start` 获取 Python `job_id`，再轮询对应 `/jobs/{job_id}`。视频需求表单先从 `video_generate` 读取 Seedance 模型、画幅、`modelGenerateTypeList/uploadFileTypeList`，从 `image_generate` 读取场景资产图片模型能力；用户确认后形成创作合同。场景包主链路通过 `/api/picture/text_to_image` 严格使用 Plan 合同中的 `image_model/scene_image_ratio/scene_image_size` 生成角色三视图、场景图和道具图；场景资产失败时 `sceneAssetFailures` 逐张保留素材名称、所属分镜、端点、模型参数、尝试链和 content-app 原始原因，前端可展开查看，不能只返回失败数量。素材缺少生图提示词、调用失败、生成响应无 URL 均必须生成失败条目。场景包 Prompt 显式携带用户确认的 `video_model`，场景视频严格使用合同中的 `video_model/video_model_capabilities/video_ratio/video_size/video_sound`。有实时能力快照时，后端只在“全能参考”可用时调用 r2v；否则自动场景仅在“文生视频”可用时使用同一 Seedance Skill 提示词降级到 t2v，绝不把角色/场景/道具图片冒充首尾帧。旧合同能力 unknown 时保留 legacy 首次选择，但供应商明确拒绝 `task_type` 后只改试一次 t2v。用户离开再回来只查询已有 Python job，不会重复触发 content-app 计费接口。场景视频生成 job 内部可以并发调度多个分镜，但所有会创建 content-app 计费生成任务的 POST 都经 `run_generation.py` 进程内串行闸门提交；前一个创建接口返回 taskId 并完成 content-app 扣费确认后，才创建下一个图片或视频任务，后续 `/api/task/{taskId}/status` 轮询可以并行等待。所有分镜都成功、失败或额度暂停后才统一返回。全部成功后按 `scene_index` 启动 PixelFlow `/agent/flows/video/merge/start`；如果只有 1 个分镜，PixelFlow merge job 直接把该分镜 URL 作为最终视频返回，不调用 content-app `/api/video/merge`。失败重试时只重新提交 `failed_scenes` 中的分镜，已成功分镜复用旧视频 URL。

项目内 `skills/seedance-prompt/SKILL.md` 对所有启用的 Seedance 系列模型通用，不以 2.0 型号作为调用开关。模型特有的画幅、清晰度、声音和参考素材能力以 content-app 实时配置与实际生成 API 为准。相邻的 `THIRD_PARTY_NOTICE.md` 记录两个输入来源、哈希和授权边界，具有来源审计价值，不能删除。

视频需求表单会完整保存 `aspectRatioList/sizeList/onSoundList/videoDurationList/modelGenerateTypeList/uploadFileTypeList` 的实时快照。场景视频调用只使用已确认合同里的 `video_model/video_ratio/video_size/video_sound`；切换模型时必须同步修正不受支持的旧清晰度，不能让当前只支持 `480p/720p` 的 `seedance-2.0-mini` 或 `seedance-2.0-fast` 继续携带 `1080p`。

Plan 版本状态由 PixelFlow 自身维护，不调用 content-app：

- `/agent/flows/planning/plan/restore` 直接激活所选历史版本，不追加重复版本。
- 回退后再次“继续修改”时，`/agent/flows/planning/plan/revise` 以历史最大版本号加一创建新版本；例如 v2 回退到 v1 后修订生成 v3，v2 仍保留。
- 新版本历史条目保存 `creation_contract` 与 `scene_durations_sec` 快照；旧对话的历史条目缺少快照时，沿用当前权威创作合同与分镜时长，确保后续 content-app 图片、视频调用仍使用正确参数。

| 接口 | 方法 | 调用位置 | 用途 | content-app 对应控制器 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `/api/auth/verify` | `POST` | `content_app_auth.verify_authorization_header_remote()`、SSE 生成器 | 实时校验 content-app token，禁用用户或失效 token 立即拒绝。 | `AuthController.verifyToken()` | pixelflow 本地只读取 JWT payload 里的 `sub` 作为用户名；token 真伪、过期和用户禁用状态以此接口返回为准。 |
| `/api/modelParamConfig/listByCategory/image_generate` | `GET` | `web/src/lib/api.ts` 的 `listImageGenerateModelConfigs()`，由图片编辑参数确认卡、视频需求清洗表单和视频场景包全局素材编辑/融合分支触发 | 查询图片生成/编辑可选模型，以及每个模型支持的尺寸和清晰度。 | `ModelParamConfigController.listByCategory()` | 图片编辑分支由用户选择模型、比例和清晰度；视频表单只让用户选择场景资产图片模型，默认 `gpt-image-2`，并把 `paramConfig.aspectRatioList/sizeList` 作为 `image_model_capabilities` 提交给 Plan Agent。Plan LLM 从该范围选择 `scene_image_ratio/scene_image_size`，后端校验后写入最终创作合同，角色/场景/道具图片严格使用该合同生成；全局素材编辑/融合分支进入生成前也读取该接口。用户确认的模型、尺寸和清晰度会写入对话 context，切换对话或刷新恢复后仍显示用户确认过的参数。 |
| `/api/modelParamConfig/listByCategory/video_generate` | `GET` | `web/src/lib/api.ts` 的 `listVideoGenerateModelConfigs()`，由视频需求清洗表单触发 | 查询可用视频模型、画幅、清晰度、声音、时长和端点能力。 | `ModelParamConfigController.listByCategory()` | 前端展示所有启用 Seedance；将 `aspectRatioList/sizeList/onSoundList/videoDurationList/modelGenerateTypeList/uploadFileTypeList` 规范化写入 `video_model_capabilities`。用户选择的画幅、清晰度和声音必须落在快照内；切换到当前仅支持 `480p/720p` 的 `seedance-2.0-mini` 或 `seedance-2.0-fast` 时会把旧 `1080p` 自动修正为 `720p`，避免价格配置无法命中。 |
| `/api/upload` | `POST multipart` | `web/src/lib/api.ts` 的 `uploadAttachment()`，由普通附件、临时本地替换和“上传到资产库”入口触发 | 上传本地文件并返回可引用 URL。 | `UploadController.uploadFile()` | 默认无进度回调时沿用 fetch；资产库入口传 `onProgress` 时 Client 内部改用 `XMLHttpRequest.upload.onprogress` 上报真实进度。上传到资产库只校验 JPG/JPEG/PNG/WEBP 和单张不超过 20MB，不校验宽高。 |
| `/api/projects` | `GET` | `web/src/lib/api.ts` 的 `listContentProjects()`，由“上传到资产库”入口在创建资产前触发 | 查询当前用户可用项目，取第一项 `id` 作为图片资产 `projectId`。 | 项目查询 Controller | 获取失败时不继续上传或创建资产，不影响原临时“本地上传”入口。 |
| `/api/asset/create` | `POST` | `web/src/lib/api.ts` 的 `createContentImageAsset()`，由“上传到资产库”入口触发 | 将 `/api/upload` 返回的图片 URL 创建为当前用户长期图片资产。 | `AssetLibraryController.createAsset()` | 固定传 `assetType=image`、`assetSource=upload`、`projectId/name/refrenceUrl`；响应 `data.id` 只用于当前弹窗定位“刚刚上传”和回查同步，不能用创建响应临时插入列表，随后必须重新查询资产库第一页。 |
| `/api/asset/character-assets` | `POST` | `web/src/lib/api.ts` 的 `listCharacterAssets()`，由分镜全局角色素材“替换素材”弹层触发 | 查询数字人素材列表，支持 `xnszr` 虚拟数字人、`zrszr` 真人数字人、`ipsc` IP素材。 | `AssetLibraryController.getCharacterAssets()` | 前端直连 content-app，POST JSON 传 `assetSource`、`assetType`、`pageCurrent`、`pageSize`。选中数字人后，展示图取 `refrenceUrl` 的首个图片 URL，模型引用写入 `generation_reference_url=asset://thirdAssetId`，并同步到场景包 mentions；保留原场景包 `asset_id`。 |
| `/api/asset/assets` | `POST` | `web/src/lib/api.ts` 的 `listContentImageAssets()`，由分镜全局素材“替换素材”弹层触发 | 查询资产库图片素材列表。 | `AssetLibraryController.getAssets()` | 前端直连 content-app，固定传 `assetSource=all`、`assetType=image`，并分页传 `pageCurrent`、`pageSize`。选中图片素材后，展示图和模型引用都使用图片 URL，并同步到场景包 global_assets 和 mentions。 |
| `/api/creative/decompose_video_to_storyboard` | `POST` | `skill._decompose_blocking()`，由 `nodes._decompose_reference_videos()` 或 `/agent/flows/video/analyze-storyboards` 触发 | 将用户上传/输入的参考视频拆解为 storyboard shots，供后续 Brief、分镜规划或视频分析结果展示使用。 | `CreativeController.decomposeVideoToStoryboard()` | 可能返回异步 task，随后会调用 `/api/task/{taskId}/status` 轮询；视频分析默认最多等 15 分钟。 |
| `/api/creative/video_quality_review` | `POST` | `run_generation.review_video_quality()`，由 `/agent/flows/video/quality-review/start` 的 Python job 触发；旧同步 `/agent/flows/video/quality-review` 仅保留兼容 | 对合并视频和各分镜视频执行 QAAgent QC 质检，返回问题、受影响分镜和修复建议。 | `CreativeController.reviewVideoQuality()` | 覆盖画面缺陷、商品清晰与露出、Prompt 跑偏、字幕正确性、Brief 一致性、黑屏/卡顿和约束合规；前端必须通过 `/agent/flows/video/quality-review/start` 获取 `job_id`，再轮询 `/agent/flows/video/quality-review/jobs/{job_id}`，避免浏览器或网关长连接超时。PixelFlow 只消费该接口的结构化 QC 结果，不再在本地执行视频质检或 ffmpeg/ffprobe 分析。content-app 侧会把长视频压成完整时序的低码率质检预览再送入模型，避免 300 秒级成片直接 base64 后超过模型请求体限制；若接口业务失败，PixelFlow job 必须返回 `status=failed` 并保留 `result.error/message/raw.details`。 |
| `/api/video/text-to-video` | `POST` | `run_generation.text_to_video()`，由 `BorgriseSkill.text_to_video()` 和场景视频 job 触发 | 无参考素材时按镜头 prompt 生成视频。 | `VideoController.textToVideo()` | 请求体精确为 `prompt/model/ratio/size/duration/videoCount/sound`；不传 `projectId`；视频生成默认最多等 1 小时。 |
| `/api/video/image-to-video` | `POST` | `run_generation.image_to_video()`，由 `BorgriseSkill.image_to_video()` 和场景视频 job 触发 | 按首帧图和镜头 prompt 生成视频片段。 | `VideoController.imageToVideo()` | 请求体精确为 `image_url/prompt/duration/ratio/model/size/sound/videoCount`；已删除旧 `negative_prompt/seed`；不传 `projectId`。 |
| `/api/video/two-image-to-video` | `POST` | `run_generation.two_image_to_video()`，由 `BorgriseSkill.two_image_to_video()` 和场景视频 job 触发 | 按首尾帧和镜头 prompt 生成视频片段。 | `VideoController.twoImageToVideo()` | 请求体精确为 `first_frame_image_url/last_frame_image_url/prompt/ratio/duration/model/size/videoCount/sound`。 |
| `/api/video/reference-mode-video` | `POST` | `run_generation.reference_mode_video()`，由 `BorgriseSkill.reference_mode_video()` 和场景视频 job 触发 | Seedance 用最多 9 张图片及可选视频、音频参考生成分镜视频。 | `VideoController.referenceModeVideo()` | 请求体精确为 `prompt/imageUrls/videoUrls/audioUrls/duration/ratio/sound/model/size/videoCount`；PixelFlow 调用前校验最多 9 张图片、3 个视频、3 个音频、2500 字提示词和实时模型参数。content-app Bean Validation 失败返回 `data` 字段错误；HTTP 4xx 不做三次业务重试。场景任务可并发调度，但创建 content-app 计费任务的 POST 由 `run_generation.py` 串行提交，状态轮询不加锁。 |
| `/api/video/edit-video` | `POST` | `run_generation.edit_video()`，由 `BorgriseSkill.edit_video()` 和场景视频 job 触发 | 按参考视频、可选参考图和镜头 prompt 编辑视频。 | `VideoController.editVideo()` | 请求体精确为 `prompt/refImage/refVideo/model/duration/size/ratio/videoCount/sound`。 |
| `/api/task/{taskId}/status` | `GET` | `run_generation.poll_task()` | 轮询异步生成、拆解和 SmartPPT 任务，直到完成、失败或超时。 | `TaskController.getTaskStatus()` | 被多个 wrapper 复用，但超时按入口区分：视频生成 1 小时、图片生成 10 分钟、视频分析/参考拆解 15 分钟、SmartPPT 2 小时；单次状态查询遇到可恢复网络错误时，除 `make_request` 内部重试外，还会继续状态轮询最多 3 次，避免任务已完成但状态查询短暂 SSL/网络异常导致误判失败。 |
| `/api/picture/image_edit` | `POST` | `run_generation.image_edit()`，由 `pixelflow_image.generate_image()` 和 `pixelflow_image.edit_image_asset()` 触发 | 对已有图片按 prompt 编辑；主图片流程识别 `image_operation=image_edit` 时会跳过表单/创意/plan 直接编辑上传原图，也用于视频场景包全局素材引用后生成候选素材图。 | `ImageController.imageEdit()` | 普通图片编辑分支会传上传原图 URL、用户编辑 prompt、`model`、`width`、`height`、`imageSize`、`size`、`max_images`、`num`；`size` 是比例字符串如 `9:16`，`imageSize` 是清晰度如 `2K/3K/4K/1080p`，不能混用。模型、比例和清晰度取值以 `/api/modelParamConfig/listByCategory/image_generate` 为准。若 content-app 参数配置允许但价格配置缺失，生成接口会返回业务失败，PixelFlow 直接展示失败原因并允许用户回到参数确认卡重选。`max_images/num` 保持一致。图片编辑失败后，前端重新打开模型/尺寸/清晰度确认卡，不直接复用失败参数盲重试。`edit-asset` 请求传单张 `source_image_url`、用户编辑 prompt、用户确认的 `model/ratio/size`、`max_images=1`；生成后通过 `/api/task/{taskId}/status` 轮询结果，图片默认最多等 10 分钟；成功后前端先展示候选图，用户确认后才替换场景包素材。 |
| `/api/picture/smart-ppt/generatePptSummary` | `POST` | `run_generation.generate_ppt_summary()`，由 `pixelflow_ppt.start_ppt_summary()` 触发 | 根据 PPT 主题、风格和 Word/Excel/PDF 附件生成 PPT 大纲。 | `SmartPptController.generatePptSummary()` | 请求 body 传 `topic`、`pptStyle`、`fileUrls`、可选 `smartPptProjectId`；返回 taskId 后通过 `/api/task/{taskId}/status` 轮询，PPT 默认最多等 2 小时。 |
| `/api/picture/smart-ppt/updatePptSummary` | `POST` | `run_generation.update_ppt_summary()`，由 `pixelflow_ppt.start_update_ppt_summary()` 触发 | 根据用户修改意见更新 PPT 大纲。 | `SmartPptController.updatePptSummary()` | 请求 body 传 `originalOutline`、`smartPptProjectId`、`modificationOpinion`；返回 taskId 后轮询。 |
| `/api/picture/smart-ppt/generatePptContentToJson` | `POST` | `run_generation.generate_ppt_content_json()`，由 `pixelflow_ppt.start_ppt_content_json()` 触发 | 将确认后的 PPT 大纲转为页面 JSON。 | `SmartPptController.generatePptContentToJson()` | 请求 body 传 `originalOutline`、`smartPptProjectId`、`pptStyle`；轮询结果读取 `content_json`。 |
| `/api/picture/smart-ppt/generatePptImage` | `POST` | `run_generation.generate_ppt_image()`，由 `pixelflow_ppt.start_ppt_images()` 和 `start_regenerate_ppt_image()` 触发 | 根据单页 JSON 生成 PPT 页面图片。 | `SmartPptController.generatePptImage()` | 请求 body 传 `jsonContent`、`smartPptProjectId`；轮询结果可能直接是图片 URL 字符串。多页图片可并发调度，但创建 content-app 计费任务的 POST 由 `run_generation.py` 串行提交。 |
| `/api/picture/smart-ppt/generatePptFile` | `POST` | `run_generation.generate_ppt_file()`，由 `pixelflow_ppt.start_ppt_file()` 触发 | 根据已生成的页面图片 URL 集合生成 PPT 文件。 | `SmartPptController.generatePptFile()` | 请求 body 传页面图片 `fileUrls`、`smartPptProjectId`；轮询结果读取 `ppt_url`、`filename`、`slide_count`。 |

## Borgrise 工具和 CLI 封装的接口

这些接口已经在 `run_generation.py` 里封装，可能被 CLI、调试脚本、后续节点或长视频辅助函数调用；当前主 PixelFlow 流程不一定直接走到。

| 接口 | 方法 | 调用位置 | 用途 | content-app 对应控制器 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `/api/upload` | `POST multipart` | `run_generation.upload_file()`；剪映草稿 `HttpJianyingDraftSkill` 也通过该封装上传最终 ZIP | 上传本地文件，返回后续接口可引用的 URL。 | `UploadController.uploadFile()` | `content-app` 会按 content type 或扩展名识别 `image`、`video`、`audio` 或普通文件，再上传到 TOS；前端资产库调用说明见上方主流程表。剪映流程下载第三方返回的单个 ZIP（真实响应可能用单元素数组包装 URL），限制 200 MiB 并校验非空 ZIP 后原样携带当前用户 Authorization 调用本接口，不解压也不重新打包，最终把自有 TOS HTTPS 地址返回前端。 |
| `/api/asset/virtual-human-asset` | `POST` | `run_generation.create_virtual_human_asset()` | 创建虚拟人第三方资产。 | `AssetLibraryController.createVirtualHumanAsset()` | 通常和 `/api/asset/create` 串联使用。 |
| `/api/asset/create` | `POST` | `run_generation.create_virtual_human_asset()` | 后端工具在 content-app 资产库创建数字人资产记录。 | `AssetLibraryController.createAsset()` | 依赖前一步返回的第三方资产 ID；前端创建普通图片资产的调用说明见上方主流程表。 |
| `/api/asset/refrence-urls` | `POST` | `run_generation.resolve_asset_urls()` | 根据 asset id 查询可引用的 `refrence_url`。 | `AssetLibraryController.getRefrenceUrls()` | 接口名保留了后端现有拼写 `refrence`。 |
| `/api/video/text-to-video` | `POST` | `run_generation.text_to_video()` | 纯文本生成视频。 | `VideoController.textToVideo()` | CLI、旧任务流和当前场景视频 job 共用同一 wrapper；精确 DTO 见上方主流程表。 |
| `/api/video/reference-mode-video` | `POST` | `run_generation.reference_mode_video()` | 用图片、视频、音频参考素材生成视频。 | `VideoController.referenceModeVideo()` | 长参考视频、原生音频参考视频 helper 和当前场景视频 job 共用；最终视频生成后只为 dirty/failed scenes 重新触发，未修改分镜复用旧 URL。 |
| `/api/video/extend-video` | `POST` | `run_generation.extend_video()`，`BorgriseSkill.extend_video()` | 在已有视频基础上继续延展内容。 | `VideoController.extendVideo()` | 长视频 helper 会复用；当前 `nodes.py` 主生成流程未直接调用；视频生成默认最多等 1 小时。 |
| `/api/video/merge` | `POST` | `run_generation.merge_videos()` | 合并多个视频 URL 为一个交付视频。 | `VideoController.mergeVideos()` | 请求 body 只传 `videoUrls`，项目归属由 content-app 登录态处理；该接口由 content-app 同步完成下载、ffmpeg 合并和上传，但 PixelFlow 前端不能直接长连接等待，必须通过 `/agent/flows/video/merge/start` 和 `/agent/flows/video/merge/jobs/{job_id}` 轮询 Python job。Python job 使用 `BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT` 控制读等待，默认 1 小时，避免总时长超过 180 秒的视频合并被普通 30 秒 HTTP 读超时截断；当 PixelFlow 只有 1 个分镜视频时不会调用该接口，直接把单分镜 URL 作为最终视频。若 content-app 返回 HTTP 500/业务失败，`run_generation` 会提取 JSON `message` 并把原始响应放入 `details`，PixelFlow merge job 返回 `status=failed` 且保留 `result.raw.details` 供前端展示。 |
| `/api/picture/text_to_image` | `POST` | `run_generation.text_to_image()` | 文生图。 | `ImageController.textToImage()` | 生成后通过 `/api/task/{taskId}/status` 轮询结果；图片默认最多等 10 分钟。 |
| `/api/picture/multi_reference_image_generation` | `POST` | `run_generation.reference_image()` | 多参考图生图。 | `ImageController.multiReferenceImageGeneration()` | 生成后通过 `/api/task/{taskId}/status` 轮询结果；图片默认最多等 10 分钟。 |
| `/api/picture/image_edit` | `POST` | `run_generation.image_edit()` | 对已有图片按 prompt 编辑。 | `ImageController.imageEdit()` | 请求体包含 `image_url`、`prompt`、`model`、`width`、`height`、`imageSize`、`size`、`max_images`、`num`；主 PixelFlow 图片流程的直接图片编辑分支、plan 后图片编辑分支和视频场景包全局素材编辑都会复用；生成后通过 `/api/task/{taskId}/status` 轮询结果；图片默认最多等 10 分钟。模型、比例和清晰度的可选项由 `/api/modelParamConfig/listByCategory/image_generate` 提供，Python 侧不再维护模型级清晰度白名单，避免前端可选但网关旧规则提前拦截。 |
| `/api/picture/multi_image_fusion` | `POST` | `run_generation.multi_image_fusion()`，由 `pixelflow_image.fuse_image_asset()` 和普通图片多图融合生成触发 | 多图融合成一张图片。 | `ImageController.multiImageFusion()` | 视频场景包全局素材引用后，如果同一条用户消息含有效上传图片，前端先展示模型/比例/清晰度确认卡，再调用 `/agent/flows/image/fuse-asset/start`；Python 将 `source_image_url` 作为第一张图并追加有效上传图片，最多 9 张，且透传用户确认的 `model/ratio/size`。生成后通过 `/api/task/{taskId}/status` 轮询结果，图片默认最多等 10 分钟；成功后先展示候选图，用户确认后才替换场景包素材。 |
| `/api/picture/batch_text_to_image` | `POST` | `run_generation.batch_text_to_image()` | 批量文生图。 | `ImageController.batchTextToImage()` | 可能返回多个 task id；每个图片任务默认最多等 10 分钟。 |

## 外部剪映草稿 Provider（非 content-app）

以下接口不属于 content-app，仅在此记录它们与 `/api/upload` 的衔接关系。调用实现集中在 `backend/pixelflow/jianying_draft/http_skill.py`，域名和固定 token 从 profile 配置读取。

| 接口 | 方法 | 用途 | 重试与状态规则 |
| --- | --- | --- | --- |
| `/api/jianying/draft/tasks` | `POST` | 按分镜顺序提交 `[{videoUrl, videoOrder}]` 并取得第三方任务 ID。 | 网络和 HTTP 5xx 最多重试 2 次；HTTP 200 但业务码非 200 不重试。 |
| `/api/jianying/draft/tasks/result` | `POST` | 传 `{taskId}` 查询草稿结果；成功返回单个 ZIP HTTPS URL，当前真实响应为 `data=[zipUrl]`。 | 首次等待 2 秒，此后每 2 秒查询；`20201/20202` 继续，`200` 成功，其他业务码失败；总预算 30 分钟。只接受纯字符串或单元素数组包装的一个 ZIP URL。 |

## 当前已知注意点

- 不要在 `pixelflow` 配置文件、IDEA Run Configuration、环境变量或代码中写死用户 token、用户名、密码。
- `run_generation.py` 会覆盖调用方误传的 `Authorization`，始终使用当前请求上下文中的 content-app token。
- `content-app` dev 配置端口是 `8082`，本地联动时 `BORGRISE_BASE_URL` 应指向 `http://localhost:8082/api`。
