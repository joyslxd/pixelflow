from __future__ import annotations

import asyncio
import json

import pytest

from pixelflow.creative.plan_markdown import build_plan_markdown, build_plan_markdown_with_llm, revise_plan_markdown_with_llm
from pixelflow.creative.scene_blueprint import (
    apply_asset_requirement_repairs,
    apply_shot_description_repairs,
    asset_requirement_quality_issues,
    enrich_incomplete_shot_descriptions,
    normalize_scene_blueprints,
    shot_description_quality_issues,
)
from pixelflow.generate.scene_packages import prepare_video_scene_packages

VIDEO_FORM = {
    "product_info": "防水通勤背包",
    "product_category": "服饰鞋包",
    "target_audience": "25-35 岁通勤人群",
    "conversion_goal": "直接购买",
    "video_duration_sec": 8,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _SequenceFakeModel:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.prompts: list[object] = []

    def invoke(self, prompt: object) -> _FakeMessage:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.contents) - 1)
        return _FakeMessage(self.contents[index])


def _blueprint(shot_description: str) -> dict[str, object]:
    return {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "雨水钩子",
        "structure_role": "opening",
        "start_sec": 0,
        "end_sec": 8,
        "duration_sec": 8,
        "storyline": "突降暴雨，通勤者立刻护住背包。",
        "shot_description": shot_description,
        "narration": "下雨最怕重要物品一起遭殃。",
        "transition": "顺着雨滴方向切入下一镜。",
        "asset_requirements": {
            "characters": ["通勤者"],
            "scenes": ["雨中街道"],
            "props": ["防水背包"],
        },
    }


def test_shot_description_quality_reports_each_missing_dimension() -> None:
    issues = shot_description_quality_issues([_blueprint("0-8秒: 特写雨滴砸向防水背包，镜头快速推近材质。")])

    assert issues == ["分镜 1 镜头描述缺少：地点、光影、声音、收束"]


def test_enrich_incomplete_shot_description_preserves_plan_semantics() -> None:
    original = _blueprint("0-8秒: 特写雨滴砸向防水背包，镜头快速推近材质。")

    enriched = enrich_incomplete_shot_descriptions([original], visual_style="电影写实风")

    assert enriched[0]["storyline"] == original["storyline"]
    assert enriched[0]["duration_sec"] == 8
    assert enriched[0]["asset_requirements"] == original["asset_requirements"]
    assert shot_description_quality_issues(enriched) == []
    assert "地点：雨中街道" in str(enriched[0]["shot_description"])
    assert "主体：通勤者、防水背包" in str(enriched[0]["shot_description"])
    assert "声音：" in str(enriched[0]["shot_description"])
    assert "收束：" in str(enriched[0]["shot_description"])


def test_complete_shot_description_passes_quality_check() -> None:
    description = (
        "0-8秒: 地点：雨中街道；主体：通勤者与防水背包；动作：通勤者抬手护住背包并擦去表面雨水；"
        "景别：中近景切材质特写；运镜：稳定跟拍后缓慢推近；光影：冷色自然光勾勒雨滴高光；"
        "声音：保留雨声、脚步声并压入旁白；收束：停在干燥拉链细节，沿水滴方向衔接下一镜。"
    )

    assert shot_description_quality_issues([_blueprint(description)]) == []


def test_vague_keywords_do_not_satisfy_shot_description_dimensions() -> None:
    issues = shot_description_quality_issues([_blueprint("0-8秒: 背景虚化，画面中展示产品，中景推近，暖光，旁白结束。")])

    assert issues == ["分镜 1 镜头描述缺少：地点、主体、动作、声音、收束"]


def test_empty_dimension_labels_do_not_pass_quality_check() -> None:
    description = "0-8秒: 地点：；主体：；动作：；景别：；运镜：；光影：；声音：；收束：。"

    assert shot_description_quality_issues([_blueprint(description)]) == ["分镜 1 镜头描述缺少：地点、主体、动作、景别、运镜、光影、声音、收束"]


def test_shot_description_local_range_must_match_scene_duration() -> None:
    blueprint = _blueprint(_complete_description().replace("0-8秒", "0-99秒"))

    with pytest.raises(ValueError, match="镜头描述时间范围必须从 0 秒连续覆盖到 8 秒"):
        normalize_scene_blueprints([blueprint], total_duration_sec=8)


def test_shot_description_accepts_continuous_sub_ranges() -> None:
    description = (
        "0-3秒: 地点：雨中街道；主体：通勤者与防水背包；动作：通勤者快步进入画面；"
        "景别：中景；运镜：稳定跟拍；光影：冷色自然光；声音：雨声和脚步声；"
        "收束：动作停在背包拉链处。"
        "3-8秒: 地点：雨中街道；主体：防水背包；动作：雨滴落下后从面料表面滑走；"
        "景别：材质特写；运镜：缓慢推近后固定；光影：侧光勾勒水珠高光；"
        "声音：保留雨声与水珠滑落音效；收束：定格在干燥拉链并结束。"
    )

    normalized = normalize_scene_blueprints([_blueprint(description)], total_duration_sec=8)

    assert normalized[0]["shot_description"] == description


def test_body_duration_phrase_is_not_treated_as_timeline_range() -> None:
    description = _complete_description().replace("擦去表面雨水", "等待提示动画持续1-2秒后擦去表面雨水")

    normalized = normalize_scene_blueprints([_blueprint(description)], total_duration_sec=8)

    assert normalized[0]["shot_description"] == description


def test_scene_package_compatibly_localizes_historical_global_shot_ranges() -> None:
    first = _blueprint(_complete_description())
    second = _blueprint(_complete_description().replace("0-8秒", "8-16秒"))
    second.update(
        {
            "scene_id": "scene-2",
            "scene_index": 2,
            "structure_role": "conclusion",
            "start_sec": 8,
            "end_sec": 16,
        }
    )

    result = prepare_video_scene_packages(
        form_values=VIDEO_FORM,
        plan_markdown="# 历史视频 Plan\n\n按已审核蓝图执行。",
        selected_direction={"title": "雨天防水实测", "description": "用雨水冲突完成证明。"},
        target_duration_ms=16_000,
        scene_blueprints=[first, second],
    )

    assert result["scene_packages"][1]["shot_description"]["text"].startswith("0-8秒")


def test_apply_shot_description_repairs_ignores_already_complete_scenes() -> None:
    incomplete = _blueprint("0-8秒: 特写雨滴砸向防水背包，镜头快速推近材质。")
    complete = _blueprint(_complete_description())
    complete.update(
        {
            "scene_id": "scene-2",
            "scene_index": 2,
            "structure_role": "conclusion",
            "start_sec": 8,
            "end_sec": 16,
        }
    )
    original_complete_description = complete["shot_description"]

    repaired = apply_shot_description_repairs(
        [incomplete, complete],
        [
            {"scene_index": 1, "shot_description": _complete_description()},
            {"scene_index": 2, "shot_description": "0-8秒: 恶意覆盖完整分镜。"},
        ],
        total_duration_sec=16,
    )

    assert repaired[0]["shot_description"] == _complete_description()
    assert repaired[1]["shot_description"] == original_complete_description


def test_asset_requirement_quality_rejects_creation_metadata_and_keeps_entities() -> None:
    blueprint = _blueprint(_complete_description())
    blueprint["asset_requirements"] = {
        "characters": ["周衡", "林悦", "三秒钩子", "段A"],
        "scenes": ["G500头等舱", "万米高空金色云海", "音乐厅", "0-3秒", "穿透运镜", "黄金时刻光影", "9:16竖屏"],
        "props": [
            "蓝妹啤酒瓶",
            "玻璃杯",
            "开瓶器",
            "反转伞",
            "85mm镜头",
            "4K摄像机",
            "3秒胶",
            "背景音乐",
            "画面无字幕",
            "高清清晰度",
            "营造高级感",
            "8K真人质感",
            "@图片1",
            "@视频3",
        ],
    }

    issues = asset_requirement_quality_issues([blueprint])

    for invalid_value in (
        "三秒钩子",
        "段A",
        "0-3秒",
        "穿透运镜",
        "黄金时刻光影",
        "9:16竖屏",
        "背景音乐",
        "画面无字幕",
        "高清清晰度",
        "营造高级感",
        "8K真人质感",
        "@图片1",
        "@视频3",
    ):
        assert any(invalid_value in issue for issue in issues)
    for valid_value in (
        "周衡",
        "林悦",
        "G500头等舱",
        "万米高空金色云海",
        "音乐厅",
        "蓝妹啤酒瓶",
        "玻璃杯",
        "开瓶器",
        "反转伞",
        "85mm镜头",
        "4K摄像机",
        "3秒胶",
    ):
        assert all(valid_value not in issue for issue in issues)


def test_apply_asset_requirement_repairs_only_changes_invalid_asset_contract() -> None:
    original = _blueprint(_complete_description())
    original["asset_requirements"] = {
        "characters": ["周衡", "三秒钩子"],
        "scenes": ["G500头等舱", "穿透运镜"],
        "props": ["蓝妹啤酒瓶", "@图片1"],
    }

    repaired = apply_asset_requirement_repairs(
        [original],
        [
            {
                "scene_index": 1,
                "asset_requirements": {
                    "characters": ["周衡", "林悦"],
                    "scenes": ["G500头等舱", "万米高空金色云海"],
                    "props": ["蓝妹啤酒瓶", "玻璃杯", "开瓶器"],
                },
                "storyline": "不应被采纳",
                "shot_description": "不应被采纳",
                "duration_sec": 15,
            }
        ],
        total_duration_sec=8,
    )

    assert repaired[0]["asset_requirements"] == {
        "characters": ["周衡", "林悦"],
        "scenes": ["G500头等舱", "万米高空金色云海"],
        "props": ["蓝妹啤酒瓶", "玻璃杯", "开瓶器"],
    }
    for field in ("duration_sec", "storyline", "shot_description", "narration", "transition"):
        assert repaired[0][field] == original[field]


def test_scene_package_rejects_polluted_historical_plan_assets_before_execution() -> None:
    polluted = _blueprint(_complete_description())
    polluted["asset_requirements"] = {
        "characters": ["通勤者", "三秒钩子"],
        "scenes": ["雨中街道", "穿透运镜"],
        "props": ["防水背包", "背景音乐"],
    }

    with pytest.raises(ValueError, match="三秒钩子"):
        prepare_video_scene_packages(
            form_values=VIDEO_FORM,
            plan_markdown="# 历史视频 Plan\n\n按已审核蓝图执行。",
            selected_direction={"title": "雨天防水实测", "description": "用雨水冲突完成证明。"},
            target_duration_ms=8_000,
            scene_blueprints=[polluted],
        )


def _single_scene_plan_payload(shot_description: str) -> dict[str, object]:
    return {
        "plan_markdown": ("# 防水通勤背包短片\n\n## 一、选题方向\n用雨天冲突证明防水。\n\n## 三、视频规格\n- 时长：8 秒\n- 画幅：9:16\n\n## 五、镜头列表\n按权威蓝图执行。"),
        "scene_image_ratio": "9:16",
        "scene_image_size": "4K",
        "scene_blueprints": [_blueprint(shot_description)],
    }


def _complete_description(duration: int = 8) -> str:
    return (
        f"0-{duration}秒: 地点：雨中街道；主体：通勤者与防水背包；动作：通勤者抬手护住背包并擦去表面雨水；"
        "景别：中近景切材质特写；运镜：稳定跟拍后快速推近；光影：冷色自然光勾勒雨滴高光；"
        "声音：保留雨声和脚步声并压入旁白；收束：停在干燥拉链细节并定格结束。"
    )


def test_build_video_plan_repairs_incomplete_shot_description_once_with_llm() -> None:
    sparse = _single_scene_plan_payload("0-8秒: 特写雨滴砸向防水背包，镜头快速推近材质。")
    repaired_description = _complete_description()
    fake_model = _SequenceFakeModel(
        [
            json.dumps(sparse, ensure_ascii=False),
            json.dumps(
                {"scene_blueprints": [{"scene_index": 1, "shot_description": repaired_description}]},
                ensure_ascii=False,
            ),
        ]
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {"direction_id": "direction_1", "title": "雨天防水实测", "description": "用雨水冲突完成证明。"},
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 2
    assert "分镜 1 镜头描述缺少：地点、光影、声音、收束" in str(fake_model.prompts[1])
    assert result.scene_blueprints[0]["shot_description"] == repaired_description
    assert result.scene_blueprints[0]["storyline"] == sparse["scene_blueprints"][0]["storyline"]
    assert shot_description_quality_issues(result.scene_blueprints) == []


def test_build_video_plan_uses_rich_rule_fallback_after_one_failed_llm_repair() -> None:
    sparse = _single_scene_plan_payload("0-8秒: 特写雨滴砸向防水背包，镜头快速推近材质。")
    fake_model = _SequenceFakeModel(
        [
            json.dumps(sparse, ensure_ascii=False),
            json.dumps(
                {"scene_blueprints": [{"scene_index": 1, "shot_description": "0-8秒: 继续特写防水背包。"}]},
                ensure_ascii=False,
            ),
        ]
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {"direction_id": "direction_1", "title": "雨天防水实测", "description": "用雨水冲突完成证明。"},
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 2
    assert shot_description_quality_issues(result.scene_blueprints) == []
    assert "地点：雨中街道" in result.scene_blueprints[0]["shot_description"]
    assert any("镜头描述已使用规则增强" in issue for issue in result.consistency_issues)


def test_build_video_plan_does_not_repair_complete_shot_description() -> None:
    complete = _single_scene_plan_payload(_complete_description())
    fake_model = _SequenceFakeModel([json.dumps(complete, ensure_ascii=False)])

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {"direction_id": "direction_1", "title": "雨天防水实测", "description": "用雨水冲突完成证明。"},
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 1
    assert shot_description_quality_issues(result.scene_blueprints) == []


def test_build_video_plan_repairs_polluted_asset_requirements_once_with_llm() -> None:
    polluted = _single_scene_plan_payload(_complete_description())
    polluted["scene_blueprints"][0]["asset_requirements"] = {
        "characters": ["周衡", "三秒钩子"],
        "scenes": ["G500头等舱", "穿透运镜"],
        "props": ["蓝妹啤酒瓶", "@图片1", "背景音乐"],
    }
    repaired_assets = {
        "characters": ["周衡", "林悦"],
        "scenes": ["G500头等舱"],
        "props": ["蓝妹啤酒瓶", "玻璃杯"],
    }
    fake_model = _SequenceFakeModel(
        [
            json.dumps(polluted, ensure_ascii=False),
            json.dumps(
                {"scene_blueprints": [{"scene_index": 1, "asset_requirements": repaired_assets}]},
                ensure_ascii=False,
            ),
        ]
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {"direction_id": "direction_1", "title": "机舱品鉴", "description": "用高空体验证明产品价值。"},
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 2
    assert "三秒钩子" in str(fake_model.prompts[1])
    assert result.scene_blueprints[0]["asset_requirements"] == repaired_assets
    assert result.scene_blueprints[0]["storyline"] == polluted["scene_blueprints"][0]["storyline"]
    assert asset_requirement_quality_issues(result.scene_blueprints) == []


def _revision_blueprints() -> list[dict[str, object]]:
    blueprints: list[dict[str, object]] = []
    for index in range(1, 4):
        role = "opening" if index == 1 else "conclusion" if index == 3 else "development"
        blueprint = _blueprint(_complete_description(10))
        blueprint.update(
            {
                "scene_id": f"scene-{index}",
                "scene_index": index,
                "title": f"新版分镜{index}",
                "structure_role": role,
                "start_sec": (index - 1) * 10,
                "end_sec": index * 10,
                "duration_sec": 10,
            }
        )
        blueprints.append(blueprint)
    return blueprints


def test_revise_video_plan_retries_incomplete_shot_descriptions_once() -> None:
    form = {**VIDEO_FORM, "video_duration_sec": 30}
    direction = {"direction_id": "direction_1", "title": "雨天防水实测", "description": "用雨水冲突完成证明。"}
    original = build_plan_markdown("video", form, direction)
    sparse_blueprints = _revision_blueprints()
    for blueprint in sparse_blueprints:
        blueprint["shot_description"] = f"0-10秒: 特写防水背包，展示第{blueprint['scene_index']}段内容。"
    candidate_markdown = "# 防水背包体验片\n\n## 一、选题方向\n延续当前创意。\n\n## 三、视频规格\n- 时长：30 秒\n\n## 五、镜头列表\n按蓝图执行。"
    malicious_repair = _revision_blueprints()
    malicious_repair[0]["storyline"] = "不应被定向修正采纳的故事线"
    malicious_repair[0]["narration"] = "不应被定向修正采纳的旁白"
    malicious_repair[0]["asset_requirements"] = {"characters": [], "scenes": [], "props": ["错误道具"]}
    fake_model = _SequenceFakeModel(
        [
            json.dumps(
                {"plan_markdown": candidate_markdown, "creation_contract_patch": {}, "scene_blueprints": sparse_blueprints},
                ensure_ascii=False,
            ),
            json.dumps(
                {"plan_markdown": "# 不应被采纳的 Plan", "creation_contract_patch": {}, "scene_blueprints": malicious_repair},
                ensure_ascii=False,
            ),
        ]
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values=form,
            selected_direction=direction,
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="保持创意和时长，只把每个镜头描述写得更完整",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 2
    assert "分镜 1 镜头描述缺少" in str(fake_model.prompts[1])
    assert revised.plan_version == 2
    assert shot_description_quality_issues(revised.scene_blueprints) == []
    assert revised.scene_blueprints[0]["storyline"] == sparse_blueprints[0]["storyline"]
    assert revised.scene_blueprints[0]["narration"] == sparse_blueprints[0]["narration"]
    assert revised.scene_blueprints[0]["asset_requirements"] == sparse_blueprints[0]["asset_requirements"]
    assert "不应被采纳的 Plan" not in revised.plan_markdown


def test_revise_video_plan_repairs_only_polluted_asset_requirements() -> None:
    form = {**VIDEO_FORM, "video_duration_sec": 30}
    direction = {"direction_id": "direction_1", "title": "机舱品鉴", "description": "用高空体验证明产品价值。"}
    original = build_plan_markdown("video", form, direction)
    candidate_blueprints = _revision_blueprints()
    candidate_blueprints[0]["asset_requirements"] = {
        "characters": ["周衡", "三秒钩子"],
        "scenes": ["G500头等舱", "0-3秒"],
        "props": ["蓝妹啤酒瓶", "@图片1"],
    }
    repaired_assets = {
        "characters": ["周衡", "林悦"],
        "scenes": ["G500头等舱"],
        "props": ["蓝妹啤酒瓶", "玻璃杯"],
    }
    fake_model = _SequenceFakeModel(
        [
            json.dumps(
                {
                    "plan_markdown": original.plan_markdown,
                    "creation_contract_patch": {},
                    "scene_blueprints": candidate_blueprints,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {"scene_blueprints": [{"scene_index": 1, "asset_requirements": repaired_assets}]},
                ensure_ascii=False,
            ),
        ]
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values=form,
            selected_direction=direction,
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="按我提供的 Seedance 内容细化分镜",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 2
    assert revised.plan_version == 2
    assert revised.scene_blueprints[0]["asset_requirements"] == repaired_assets
    assert revised.scene_blueprints[0]["storyline"] == candidate_blueprints[0]["storyline"]
    assert asset_requirement_quality_issues(revised.scene_blueprints) == []


def test_revise_video_plan_preserves_current_version_when_quality_retry_still_fails() -> None:
    form = {**VIDEO_FORM, "video_duration_sec": 30}
    direction = {"direction_id": "direction_1", "title": "雨天防水实测", "description": "用雨水冲突完成证明。"}
    original = build_plan_markdown("video", form, direction)
    sparse_blueprints = _revision_blueprints()
    for blueprint in sparse_blueprints:
        blueprint["shot_description"] = f"0-10秒: 特写防水背包，展示第{blueprint['scene_index']}段内容。"
    candidate_markdown = "# 防水背包体验片\n\n## 一、选题方向\n延续当前创意。\n\n## 三、视频规格\n- 时长：30 秒\n\n## 五、镜头列表\n按蓝图执行。"
    failed_payload = json.dumps(
        {"plan_markdown": candidate_markdown, "creation_contract_patch": {}, "scene_blueprints": sparse_blueprints},
        ensure_ascii=False,
    )
    fake_model = _SequenceFakeModel([failed_payload, failed_payload])

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values=form,
            selected_direction=direction,
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="保持其他内容，只把镜头描述写完整",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 2
    assert revised.plan_version == original.plan_version
    assert revised.plan_markdown == original.plan_markdown
    assert revised.scene_blueprints == original.scene_blueprints
    assert "分镜镜头描述完整度校验失败" in str(revised.error)
