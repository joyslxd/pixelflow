from __future__ import annotations

import asyncio

import pytest

from pixelflow.generate.scene_asset_revision import revise_scene_package_asset
from pixelflow.skills.base import ImageAnalysisResult


def _global_assets() -> dict:
    return {
        "characters": [
            {
                "asset_id": "character-president",
                "name": "周衡-总裁造型",
                "description": "40岁男性，黑色西装，白色衬衫，短发。",
                "three_view_images": ["https://old.example.com/president.png"],
            },
            {
                "asset_id": "character-assistant",
                "name": "助理",
                "description": "年轻女性，灰色职业套装。",
                "three_view_images": ["https://old.example.com/assistant.png"],
            },
        ],
        "scenes": [
            {
                "asset_id": "scene-office",
                "name": "总裁办公室",
                "description": "深色木质办公室。",
                "images": ["https://old.example.com/office.png"],
            }
        ],
        "props": [
            {
                "asset_id": "prop-wine",
                "name": "高端红酒",
                "description": "深绿色瓶身。",
                "images": ["https://old.example.com/wine.png"],
            }
        ],
        "visual_style": {"name": "电影写实"},
    }


def _scene_packages() -> list[dict]:
    return [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "duration_ms": 8_000,
            "title": "总裁登场",
            "storyline": "总裁进入办公室。",
            "prompt": "保持电影写实风格。",
            "narration": "真正的选择，从容而坚定。",
            "transition": "动作匹配剪辑。",
            "reference_asset_ids": ["character-president", "scene-office", "prop-wine"],
            "image_urls": [
                "https://old.example.com/president.png",
                "https://old.example.com/office.png",
                "https://old.example.com/wine.png",
            ],
            "shot_description": {
                "text": (
                    "0-8秒: 地点:@总裁办公室 中，角色:@周衡-总裁造型 身穿黑色西装和白色衬衫，"
                    "短发整齐，拿起道具:@高端红酒。中景缓慢推进，暖色轮廓光，伴随脚步声，"
                    "最后定格在酒标。"
                ),
                "mentions": [
                    {
                        "asset_id": "character-president",
                        "name": "周衡-总裁造型",
                        "image_url": "https://old.example.com/president.png",
                    },
                    {
                        "asset_id": "scene-office",
                        "name": "总裁办公室",
                        "image_url": "https://old.example.com/office.png",
                    },
                    {
                        "asset_id": "prop-wine",
                        "name": "高端红酒",
                        "image_url": "https://old.example.com/wine.png",
                    },
                ],
            },
        },
        {
            "scene_id": "scene-2",
            "scene_index": 2,
            "duration_ms": 6_000,
            "title": "助理汇报",
            "storyline": "助理汇报数据。",
            "prompt": "保持电影写实风格。",
            "narration": "每一个数字都有答案。",
            "transition": "淡出。",
            "reference_asset_ids": ["character-assistant", "scene-office"],
            "shot_description": {
                "text": "0-6秒: 地点:@总裁办公室 中，角色:@助理 展示报表，近景固定镜头，冷色侧光。",
                "mentions": [
                    {
                        "asset_id": "character-assistant",
                        "name": "助理",
                        "image_url": "https://old.example.com/assistant.png",
                    },
                    {
                        "asset_id": "scene-office",
                        "name": "总裁办公室",
                        "image_url": "https://old.example.com/office.png",
                    },
                ],
            },
        },
    ]


class _ImageAnalysisSkill:
    async def analyze_image(self, image_url: str) -> ImageAnalysisResult:
        assert image_url == "https://new.example.com/president.png"
        return ImageAnalysisResult(
            ok=True,
            task_id="analysis-task-1",
            analysis_markdown="## 人物\n45岁女性，米白色西装，栗色长发，气质沉稳。",
        )


async def _replace_patch_provider(**kwargs):
    assert kwargs["operation"] == "replace"
    assert "45岁女性" in kwargs["image_analysis_markdown"]
    return {
        "scenes": [
            {
                "scene_id": "scene-1",
                "replacements": [
                    {
                        "old_text": "身穿黑色西装和白色衬衫，短发整齐",
                        "new_text": "身穿米白色西装，栗色长发自然垂落，气质沉稳",
                    }
                ],
            }
        ]
    }


def test_replacement_analyzes_new_image_and_only_updates_affected_shot_description():
    original_scenes = _scene_packages()
    result = asyncio.run(
        revise_scene_package_asset(
            operation="replace",
            asset_id="character-president",
            asset_group="characters",
            asset_name="周衡-总裁造型",
            source_image_url="https://old.example.com/president.png",
            new_image_url="https://new.example.com/president.png",
            generation_reference_url="asset://digital-human-88",
            global_assets=_global_assets(),
            scene_packages=original_scenes,
            image_analysis_skill=_ImageAnalysisSkill(),
            patch_provider=_replace_patch_provider,
        )
    )

    assert result["ok"] is True
    assert result["affected_scene_ids"] == ["scene-1"]
    assert result["image_analysis_markdown"].startswith("## 人物")
    character = result["global_assets"]["characters"][0]
    assert character["three_view_images"][0] == "https://new.example.com/president.png"
    assert character["generation_reference_url"] == "asset://digital-human-88"
    assert character["image_analysis_markdown"].startswith("## 人物")
    updated_scene = result["scene_packages"][0]
    assert "米白色西装" in updated_scene["shot_description"]["text"]
    assert "黑色西装" not in updated_scene["shot_description"]["text"]
    assert updated_scene["shot_description"]["mentions"][0]["image_url"] == "https://new.example.com/president.png"
    assert updated_scene["image_urls"] == [
        "https://new.example.com/president.png",
        "https://old.example.com/office.png",
        "https://old.example.com/wine.png",
    ]
    assert updated_scene["storyline"] == original_scenes[0]["storyline"]
    assert updated_scene["narration"] == original_scenes[0]["narration"]
    assert updated_scene["transition"] == original_scenes[0]["transition"]
    assert result["scene_packages"][1] == original_scenes[1]


def test_replacement_updates_legacy_scene_image_urls_without_mentions():
    scenes = _scene_packages()
    scenes[0]["shot_description"].pop("mentions")
    result = asyncio.run(
        revise_scene_package_asset(
            operation="replace",
            asset_id="character-president",
            asset_group="characters",
            asset_name="周衡-总裁造型",
            source_image_url="https://old.example.com/president.png",
            new_image_url="https://new.example.com/president.png",
            global_assets=_global_assets(),
            scene_packages=scenes,
            image_analysis_skill=_ImageAnalysisSkill(),
            patch_provider=_replace_patch_provider,
        )
    )

    assert result["scene_packages"][0]["image_urls"][0] == "https://new.example.com/president.png"


async def _delete_patch_provider(**kwargs):
    assert kwargs["operation"] == "delete"
    return {
        "scenes": [
            {
                "scene_id": "scene-1",
                "replacements": [
                    {
                        "old_text": "，拿起道具:@高端红酒",
                        "new_text": "",
                    },
                    {
                        "old_text": "，最后定格在酒标",
                        "new_text": "，最后定格在人物坚定的神情",
                    },
                ],
            }
        ]
    }


def test_delete_removes_asset_reference_and_related_description_only():
    result = asyncio.run(
        revise_scene_package_asset(
            operation="delete",
            asset_id="prop-wine",
            asset_group="props",
            asset_name="高端红酒",
            source_image_url="https://old.example.com/wine.png",
            global_assets=_global_assets(),
            scene_packages=_scene_packages(),
            patch_provider=_delete_patch_provider,
        )
    )

    assert result["ok"] is True
    assert result["affected_scene_ids"] == ["scene-1"]
    prop = result["global_assets"]["props"][0]
    assert prop["images"] == []
    assert prop["image_url"] == ""
    updated_scene = result["scene_packages"][0]
    assert "@高端红酒" not in updated_scene["shot_description"]["text"]
    assert "酒标" not in updated_scene["shot_description"]["text"]
    assert updated_scene["reference_asset_ids"] == ["character-president", "scene-office"]
    assert [item["asset_id"] for item in updated_scene["shot_description"]["mentions"]] == [
        "character-president",
        "scene-office",
    ]
    assert updated_scene["image_urls"] == [
        "https://old.example.com/president.png",
        "https://old.example.com/office.png",
    ]


async def _unsafe_patch_provider(**_kwargs):
    return {
        "scenes": [
            {
                "scene_id": "scene-1",
                "replacements": [
                    {
                        "old_text": "0-8秒",
                        "new_text": "0-15秒",
                    }
                ],
            }
        ]
    }


def test_revision_rejects_llm_patch_that_changes_time_structure():
    with pytest.raises(ValueError, match="时间结构"):
        asyncio.run(
            revise_scene_package_asset(
                operation="replace",
                asset_id="character-president",
                asset_group="characters",
                asset_name="周衡-总裁造型",
                source_image_url="https://old.example.com/president.png",
                new_image_url="https://new.example.com/president.png",
                global_assets=_global_assets(),
                scene_packages=_scene_packages(),
                image_analysis_skill=_ImageAnalysisSkill(),
                patch_provider=_unsafe_patch_provider,
            )
        )


def test_revision_retries_once_after_patch_violates_protected_mentions():
    calls: list[str] = []

    async def repair_provider(**kwargs):
        calls.append(str(kwargs.get("validation_feedback") or ""))
        if len(calls) == 1:
            return {
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "replacements": [
                            {
                                "old_text": "，拿起道具:@高端红酒",
                                "new_text": "",
                            }
                        ],
                    }
                ]
            }
        return {
            "scenes": [
                {
                    "scene_id": "scene-1",
                    "replacements": [
                        {
                            "old_text": "角色:@周衡-总裁造型 身穿黑色西装和白色衬衫，短发整齐，",
                            "new_text": "",
                        }
                    ],
                }
            ]
        }

    result = asyncio.run(
        revise_scene_package_asset(
            operation="delete",
            asset_id="character-president",
            asset_group="characters",
            asset_name="周衡-总裁造型",
            source_image_url="https://old.example.com/president.png",
            global_assets=_global_assets(),
            scene_packages=_scene_packages(),
            patch_provider=repair_provider,
        )
    )

    assert len(calls) == 2
    assert "不允许修改其他素材引用" in calls[1]
    text = result["scene_packages"][0]["shot_description"]["text"]
    assert "@周衡-总裁造型" not in text
    assert "@高端红酒" in text


async def _unrelated_mention_patch_provider(**_kwargs):
    return {
        "scenes": [
            {
                "scene_id": "scene-1",
                "replacements": [
                    {
                        "old_text": "@总裁办公室",
                        "new_text": "@室外广场",
                    }
                ],
            }
        ]
    }


def test_revision_rejects_changes_to_unrelated_asset_mentions():
    with pytest.raises(ValueError, match="其他素材"):
        asyncio.run(
            revise_scene_package_asset(
                operation="replace",
                asset_id="character-president",
                asset_group="characters",
                asset_name="周衡-总裁造型",
                source_image_url="https://old.example.com/president.png",
                new_image_url="https://new.example.com/president.png",
                global_assets=_global_assets(),
                scene_packages=_scene_packages(),
                image_analysis_skill=_ImageAnalysisSkill(),
                patch_provider=_unrelated_mention_patch_provider,
            )
        )
