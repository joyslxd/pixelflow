"""Brief schema — authoritative per PRD §9.4.

This is the contract between CREATIVE and every downstream phase (GENERATE,
EDIT, QC, local-retouch). Keep the field set aligned with the PRD; downstream
nodes read ``shots[].generation_prompt``, ``shots[].duration``,
``shots[].asset_strategy`` etc. directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SceneType = Literal["hook", "pain_point", "solution", "demo", "social_proof", "cta"]
AssetStrategy = Literal["use_real_asset", "generate_asset", "use_reference_structure", "mixed"]


class GlobalVisual(BaseModel):
    """Cross-shot visual constants that keep the video coherent."""

    subject_type: str  # 人物/主体设定
    environment: str  # 场景环境
    lighting: str  # 光线与质感
    character_style: str  # 人物造型（有真人时）
    overall_style: str  # 整体风格
    forbidden_elements: str  # 禁止元素


class ShotAudio(BaseModel):
    bgm_vibe: str | None = None
    sfx: str | None = None
    tts_voice: str | None = None  # 博观 TTS voice_type


class Shot(BaseModel):
    shot_id: str  # 'shot_001'
    time_range: str  # '0-3.5s'
    duration: float  # 秒
    shot_type: str  # 特写/近景/中景/全景
    camera_movement: str  # 推/拉/平移/跟随
    visual_description: str  # 画面内容描述（给用户看，中文）
    generation_prompt: str  # 模型提示词（≠ 用户可见文案，由 PromptEngine 扩写）
    narration_text: str = ""  # 旁白（≤ 50 字）
    onscreen_text: str = ""  # 花字（≤ 20 字）
    audio: ShotAudio = Field(default_factory=ShotAudio)
    scene_type: SceneType
    asset_strategy: AssetStrategy
    transition_in: str = ""
    transition_out: str = ""


class HardConstraints(BaseModel):
    total_duration_tolerance: str = "+2s"
    first_shot_must_be_hook: bool = True
    last_shot_must_be_cta: bool = True
    max_narration_length: int = 50
    max_onscreen_length: int = 20


class Brief(BaseModel):
    brief_id: str
    version: str = "1.0"
    create_time: str = ""
    global_visual: GlobalVisual
    shots: list[Shot]
    # 输出参数
    platform: str  # douyin / taobao / ...
    duration_sec: int
    ratio: str = "9:16"
    size: str = "1080x1920"
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
