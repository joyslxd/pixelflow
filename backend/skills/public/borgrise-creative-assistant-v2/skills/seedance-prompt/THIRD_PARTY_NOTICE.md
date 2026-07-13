# 第三方来源与改编说明

本目录 `SKILL.md` 是 PixelFlow 针对运行时约束重新编写的 Seedance 系列通用 Skill。该文件吸收了下列资料中的通用概念，但未整段复制来源文本。

## 已使用来源

1. 用户先前提供的 `seedance-prompt-skill-master.zip`
   - 上游：`https://github.com/songguoxs/seedance-prompt-skill`
   - 归档 revision：`57d1e2f273747c238dd892698a05137ab2f10d4a`
   - 上游 README 声明：MIT
   - 用户归档内未包含独立 LICENSE 或版权文件，因此只记录可核验元数据。

2. 用户提供的 `BGEC-SD2-book-prompts-skill.zip`
   - SHA-256：`D1B24E9C412B95BBFB1D4CE3677EC36255E374B8A251784020FC6DE193078D94`
   - 根目录未发现统一 LICENSE/NOTICE。
   - 仅 `short-drama` 子树声明 MIT（Copyright 2025 0xsline）；其他资料没有足够清晰的许可声明。
   - PixelFlow 只提炼“主体、动作、场景、镜头、光影、声音、时间组织、参考一致性和质量检查”等通用方法，未整段复制无明确授权的正文。

## 官方能力核验来源

- Seed 模型列表：`https://seed.bytedance.com/en/models`
- Seedance 2.0 官方发布说明：`https://seed.bytedance.com/en/blog/seedance-2-0-official-launch`

模型能力仍以 content-app 实时配置和实际生成 API 为准。本说明用于保留来源链路与授权边界，不能删除或当作第三方代码许可证替代品。
