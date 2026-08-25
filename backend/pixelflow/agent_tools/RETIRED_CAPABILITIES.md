# M1 已下线能力边界

M1 只保留 Harness 可直接调用的非计费视频工作区 Tool：

- `inspect_video_workspace`
- `inspect_scene`
- `patch_scene`
- `replace_scene_asset`

图片生成、视频生成、PPT、参考视频分析、脚本生成、场景包生成、合并、剪映交付及全部 Provider Router 已随旧 v2 架构物理下线。它们不得通过恢复旧 Router、旧 Prompt、旧 LangGraph 或浏览器轮询重新启用。

重新引入任何下线能力时，必须新建 Harness Skill 指导、稳定 Capability Tool、PixelFlow Workspace Command、Provider Port/Adapter、M06 Operation/确认边界和合同测试；付费能力还必须经过独立批准。
