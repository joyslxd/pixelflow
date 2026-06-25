"""生成阶段辅助能力入口。

这里放 GENERATE 阶段的纯逻辑：把 Brief 分镜转成视频生成 prompt，以及把多个
shot 合并成符合第三方单次时长上限的 segment。
"""

from pixelflow.generate.image_prepare import ImageGenerationPrepareResult, prepare_image_generation
from pixelflow.generate.prompt_engine import build_seedance_prompt
from pixelflow.generate.scene_packages import prepare_video_scene_packages
from pixelflow.generate.segment_plan import build_segment_prompt, plan_segments

__all__ = [
    "ImageGenerationPrepareResult",
    "build_seedance_prompt",
    "build_segment_prompt",
    "plan_segments",
    "prepare_image_generation",
    "prepare_video_scene_packages",
]
