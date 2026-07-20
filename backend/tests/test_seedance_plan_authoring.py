from __future__ import annotations

import asyncio
import copy
import json

import pytest

from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.plan_llm import author_seedance_plan_payload
from pixelflow.creative.plan_markdown import (
    build_plan_markdown,
    build_plan_markdown_with_llm,
    revise_plan_markdown_with_llm,
)
from pixelflow.creative.seedance_plan import (
    apply_seedance_plan_authoring,
    build_seedance_plan_authoring_prompt,
)

CONTRACT = {
    "video_duration_sec": 10,
    "video_ratio": "9:16",
    "video_model": "seedance-current-enabled-model",
    "video_size": "1080p",
    "sound": True,
    "conversion_goal": "引流直播间",
    "selling_points": ["防水面料", "通勤收纳"],
    "video_model_capabilities": {
        "durations": [4, 5, 10, 15],
        "ratios": ["9:16"],
        "supports_sound": True,
    },
}

BLUEPRINTS = [
    {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "雨夜防水实测",
        "structure_role": "opening",
        "start_sec": 0,
        "end_sec": 10,
        "duration_sec": 10,
        "storyline": "林晓在雨夜公交站完成背包防水实测。",
        "shot_description": (
            "0-10秒：地点：雨夜公交站；主体：林晓与防水背包；动作：林晓将水泼向背包；"
            "景别：中景切特写；运镜：镜头推近；光影：冷蓝路灯；声音：雨声；收束：定格水珠。"
        ),
        "narration": "雨夜通勤也从容。",
        "transition": "产品定格收束。",
        "asset_requirements": {
            "characters": ["林晓"],
            "scenes": ["雨夜公交站"],
            "props": ["防水背包"],
        },
    }
]

MANIFEST = {
    "characters": [
        {
            "asset_id": "character-linxiao",
            "name": "林晓",
            "description": "28 岁城市通勤女性，黑色短发，浅灰风衣。",
            "three_view_prompt": "林晓同一人物正面、侧面、背面三视图。",
        }
    ],
    "scenes": [
        {
            "asset_id": "scene-rain-stop",
            "name": "雨夜公交站",
            "description": "冷蓝路灯下的玻璃公交站，持续小雨。",
            "image_prompt": "雨夜公交站环境参考图。",
        }
    ],
    "props": [
        {
            "asset_id": "prop-backpack",
            "name": "防水背包",
            "description": "曜石黑哑光防水背包，银色拉链。",
            "image_prompt": "曜石黑防水背包产品参考图。",
        }
    ],
}

VIDEO_FORM = {
    "product_info": "曜石黑防水背包",
    "product_category": "箱包",
    "target_audience": "城市通勤者",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 10,
    "video_ratio": "9:16",
    "video_model": "seedance-current-enabled-model",
    "video_size": "1080p",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
    "video_model_capabilities": CONTRACT["video_model_capabilities"],
    "video_usage": "商品宣传",
    "visual_style": "写实电影感",
    "sound": True,
}

DIRECTION = {
    "direction_id": "direction_1",
    "title": "雨夜通勤实测",
    "description": "林晓在雨夜公交站验证曜石黑防水背包。",
}

PLAN_MARKDOWN = """# 雨夜通勤实测

## 一、选题方向

用雨夜连续泼水形成防水证据。

## 二、选题优势

- 卖点：防水面料
- 转化目标：引流直播间

## 三、视频规格

- 时长：10 秒
- 画幅：9:16

## 四、全局资产清单

由结构化合同覆盖。

## 五、镜头列表

由结构化蓝图覆盖。
"""


def _authored_shot() -> str:
    return (
        "0-3秒：以 @scene-rain-stop 固定雨夜公交站空间，以 @character-linxiao 固定林晓身份，"
        "镜头用中景建立人物与背包关系，冷蓝路灯形成冷光并勾勒雨丝；3-7秒：林晓抬起水杯将水连续泼向背包，"
        "摄像机沿手部动作推近，保留雨声与泼水声；7-10秒：以 @prop-backpack 固定商品外观，"
        "切换防水面料特写，本分镜旁白说“雨夜通勤也从容”，定格在水珠滚落且面料未浸湿的证据画面。"
    )


def _authored_payload(**overrides: object) -> list[dict[str, object]]:
    item: dict[str, object] = {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "雨夜防水证据",
        "storyline": "林晓在雨夜用连续泼水动作证明背包面料防水。",
        "shot_description": _authored_shot(),
        "narration": "雨夜通勤也从容。",
        "transition": "以水珠特写定格收束。",
    }
    item.update(overrides)
    return [item]


def _authored_for_manifest(
    manifest: dict[str, list[dict[str, str]]],
    **overrides: object,
) -> list[dict[str, object]]:
    character_id = manifest["characters"][0]["asset_id"]
    scene_id = manifest["scenes"][0]["asset_id"]
    prop_id = manifest["props"][0]["asset_id"]
    shot = (
        f"0-3秒：以 @{scene_id} 固定雨夜公交站空间，以 @{character_id} 固定林晓人物身份，"
        "中景建立人物与商品关系，冷蓝路灯形成冷光并勾勒雨丝；3-7秒：动作：林晓抬手从肩上取下背包并连续泼水，"
        "稳定器跟随手部动作推近，雨声、布料摩擦声和泼水声同步增强；"
        f"7-10秒：以 @{prop_id} 固定商品外观，切换面料与拉链大特写，"
        "对白“雨夜通勤也从容”清晰落下，镜头定格在水珠滚落且内层干燥的证据画面。"
    )
    return _authored_payload(shot_description=shot, **overrides)


class SequenceModel:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = payloads
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.payloads) - 1)
        return type(
            "Message",
            (),
            {"content": json.dumps(self.payloads[index], ensure_ascii=False)},
        )()


def test_prompt_contains_confirmed_contract_skill_context_and_stable_assets() -> None:
    prompt = build_seedance_plan_authoring_prompt(
        plan_markdown="# 当前 Plan\n\n卖点：防水面料",
        scene_blueprints=BLUEPRINTS,
        asset_manifest=MANIFEST,
        creation_contract=CONTRACT,
        form_values={"video_usage": "商品宣传", "visual_style": "写实电影感"},
        selected_direction={"title": "雨夜通勤实测"},
        intake_context={"original_prompt": "突出防水并引流直播间"},
        materials=[{"name": "背包正面图.png", "url": "https://example.test/bag.png"}],
        revision_feedback="加强前三秒钩子",
        validation_feedback="上次错误：使用了毫秒",
    )

    for expected in (
        "seedance-current-enabled-model",
        '"video_ratio": "9:16"',
        '"supports_sound": true',
        "# 当前 Plan",
        "scene-1",
        "character-linxiao",
        "以 @character-linxiao 固定人物“林晓”的身份与外观",
        "28 岁城市通勤女性",
        "scene-rain-stop",
        "以 @scene-rain-stop 固定场景“雨夜公交站”的环境空间",
        "prop-backpack",
        "以 @prop-backpack 固定道具“防水背包”的商品外观",
        "雨夜通勤实测",
        "突出防水并引流直播间",
        "背包正面图.png",
        "加强前三秒钩子",
        "上次错误：使用了毫秒",
        "参考素材与一致性",
        "PixelFlow 创作合同",
    ):
        assert expected in prompt

    assert "不得修改 video_model" in prompt
    assert "不得修改分镜数量、顺序、全局时间线和单镜时长" in prompt
    assert "不得修改商品卖点、转化目标" in prompt
    assert "不得新增、删除、改名或跨分镜挪用资产" in prompt
    assert "每个分镜最多 9 张" in prompt


def test_prompt_requires_confirmed_video_model() -> None:
    contract = copy.deepcopy(CONTRACT)
    contract["video_model"] = ""

    with pytest.raises(ValueError, match="video_model"):
        build_seedance_plan_authoring_prompt(
            plan_markdown="# Plan",
            scene_blueprints=BLUEPRINTS,
            asset_manifest=MANIFEST,
            creation_contract=contract,
            form_values={},
            selected_direction={},
            intake_context={},
            materials=[],
        )


def test_apply_merges_only_narrative_fields_and_preserves_authoritative_contract() -> None:
    result = apply_seedance_plan_authoring(
        BLUEPRINTS,
        _authored_payload(
            structure_role="conclusion",
            start_sec=99,
            end_sec=109,
            duration_sec=99,
            asset_requirements={"characters": [], "scenes": [], "props": []},
            video_model="another-model",
        ),
        asset_manifest=MANIFEST,
        total_duration_sec=10,
    )

    assert result[0]["title"] == "雨夜防水证据"
    assert result[0]["shot_description"] == _authored_shot()
    for field in (
        "scene_id",
        "scene_index",
        "structure_role",
        "start_sec",
        "end_sec",
        "duration_sec",
        "asset_requirements",
    ):
        assert result[0][field] == BLUEPRINTS[0][field]


def test_apply_accepts_clear_asset_usage_explanation_with_natural_chinese_wording() -> None:
    shot = _authored_shot().replace(
        "以 @prop-backpack 固定商品外观",
        "使用 @prop-backpack 作为商品外观依据",
    )

    result = apply_seedance_plan_authoring(
        BLUEPRINTS,
        _authored_payload(shot_description=shot),
        asset_manifest=MANIFEST,
        total_duration_sec=10,
    )

    assert "使用 @prop-backpack 作为商品外观依据" in result[0]["shot_description"]


@pytest.mark.parametrize(
    ("authored", "message"),
    [
        (_authored_payload(scene_id="scene-unknown"), "scene_id"),
        (_authored_payload(shot_description=_authored_shot().replace("@prop-backpack", "@prop-unknown")), "未声明"),
        (_authored_payload(shot_description=_authored_shot().replace("以 @prop-backpack 固定", "@prop-backpack")), "用途"),
        (_authored_payload(shot_description=_authored_shot().replace("0-3秒", "0-3000ms")), "毫秒"),
        (_authored_payload(shot_description=_authored_shot().replace("0-3秒", "0-3.5秒")), "小数"),
        (_authored_payload(shot_description=_authored_shot().replace("3-7秒", "4-7秒")), "连续"),
        (_authored_payload(shot_description=_authored_shot().replace("冷蓝路灯形成冷光", "灰色背景")), "光影"),
        (_authored_payload(shot_description=_authored_shot() + "\n第二段"), "一整段"),
    ],
)
def test_apply_rejects_invalid_seedance_authoring(
    authored: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        apply_seedance_plan_authoring(
            BLUEPRINTS,
            authored,
            asset_manifest=MANIFEST,
            total_duration_sec=10,
        )


def test_apply_rejects_more_than_nine_unique_references() -> None:
    blueprints = copy.deepcopy(BLUEPRINTS)
    manifest = copy.deepcopy(MANIFEST)
    for index in range(2, 11):
        name = f"道具{index}"
        blueprints[0]["asset_requirements"]["props"].append(name)
        manifest["props"].append(
            {
                "asset_id": f"prop-{index}",
                "name": name,
                "description": f"道具 {index}",
                "image_prompt": f"道具 {index} 参考图",
            }
        )
    references = "，".join(f"以 @prop-{index} 固定道具{index}外观" for index in range(2, 11))
    authored = _authored_payload(shot_description=_authored_shot() + references)

    with pytest.raises(ValueError, match="最多 9"):
        apply_seedance_plan_authoring(
            blueprints,
            authored,
            asset_manifest=manifest,
            total_duration_sec=10,
        )


def test_seedance_plan_client_uses_existing_model_factory_and_returns_json_object() -> None:
    class FakeModel:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return type(
                "Message",
                (),
                {"content": json.dumps({"scene_blueprints": _authored_payload()}, ensure_ascii=False)},
            )()

    model = FakeModel()
    result = asyncio.run(
        author_seedance_plan_payload(
            plan_markdown="# Plan",
            scene_blueprints=BLUEPRINTS,
            asset_manifest=MANIFEST,
            creation_contract=CONTRACT,
            form_values={"video_usage": "商品宣传"},
            selected_direction={"title": "雨夜实测"},
            intake_context={"original_prompt": "突出防水"},
            materials=[],
            model_factory=lambda *_args, **_kwargs: model,
        )
    )

    assert result["scene_blueprints"][0]["scene_id"] == "scene-1"
    assert len(model.prompts) == 1
    assert "seedance-current-enabled-model" in model.prompts[0]
    assert "Seedance Plan 分镜写作 Skill" in model.prompts[0]


def test_seedance_plan_client_rejects_non_object_response() -> None:
    class FakeModel:
        def invoke(self, _prompt: str):
            return type("Message", (), {"content": "[]"})()

    with pytest.raises(ValueError, match="JSON object"):
        asyncio.run(
            author_seedance_plan_payload(
                plan_markdown="# Plan",
                scene_blueprints=BLUEPRINTS,
                asset_manifest=MANIFEST,
                creation_contract=CONTRACT,
                form_values={},
                selected_direction={},
                intake_context={},
                materials=[],
                model_factory=lambda *_args, **_kwargs: FakeModel(),
            )
        )


def test_initial_video_plan_runs_dedicated_seedance_authoring_after_stable_manifest() -> None:
    stable_manifest = normalize_asset_manifest(MANIFEST, BLUEPRINTS)
    model = SequenceModel(
        [
            {
                "plan_markdown": PLAN_MARKDOWN,
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": BLUEPRINTS,
                "asset_manifest": MANIFEST,
            },
            {"scene_blueprints": _authored_for_manifest(stable_manifest)},
        ]
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            DIRECTION,
            materials=[{"name": "背包正面图.png", "url": "https://example.test/bag.png"}],
            intake_context={"original_prompt": "突出防水面料并引流直播间"},
            model_factory=lambda *_args, **_kwargs: model,
        )
    )

    assert result.error is None
    assert len(model.prompts) == 2
    assert "Seedance Plan 分镜写作 Skill" in model.prompts[1]
    assert "seedance-current-enabled-model" in model.prompts[1]
    assert stable_manifest["props"][0]["asset_id"] in model.prompts[1]
    assert result.scene_blueprints[0]["shot_description"] == _authored_for_manifest(stable_manifest)[0]["shot_description"]
    assert result.scene_blueprints[0]["duration_sec"] == 10
    assert result.scene_blueprints[0]["asset_requirements"] == BLUEPRINTS[0]["asset_requirements"]
    assert "防水面料" in result.plan_markdown
    assert "引流直播间" in result.plan_markdown
    assert f"@{stable_manifest['props'][0]['asset_id']}" in result.plan_markdown


def test_initial_video_plan_retries_seedance_authoring_with_validation_feedback() -> None:
    stable_manifest = normalize_asset_manifest(MANIFEST, BLUEPRINTS)
    invalid = _authored_for_manifest(stable_manifest)
    invalid[0]["shot_description"] = str(invalid[0]["shot_description"]).replace("0-3秒", "0-3000ms")
    model = SequenceModel(
        [
            {
                "plan_markdown": PLAN_MARKDOWN,
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": BLUEPRINTS,
                "asset_manifest": MANIFEST,
            },
            {"scene_blueprints": invalid},
            {"scene_blueprints": _authored_for_manifest(stable_manifest)},
        ]
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            DIRECTION,
            model_factory=lambda *_args, **_kwargs: model,
        )
    )

    assert result.error is None
    assert len(model.prompts) == 3
    assert "不能使用毫秒时间码" in model.prompts[2]
    assert "ms" not in result.scene_blueprints[0]["shot_description"]


def test_video_plan_revision_runs_same_seedance_authoring_with_feedback_and_materials() -> None:
    stable_manifest = normalize_asset_manifest(MANIFEST, BLUEPRINTS)
    current = build_plan_markdown("video", VIDEO_FORM, DIRECTION)
    revised_structural = copy.deepcopy(BLUEPRINTS)
    revised_structural[0]["storyline"] = "林晓用更强烈的连续泼水证明防水效果。"
    authored = _authored_for_manifest(
        stable_manifest,
        storyline="林晓用更强烈的连续泼水和内部干燥特写完成防水证明。",
    )
    model = SequenceModel(
        [
            {
                "plan_markdown": PLAN_MARKDOWN.replace("连续泼水", "加强前三秒冲突后连续泼水"),
                "creation_contract_patch": {},
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": revised_structural,
                "asset_manifest": MANIFEST,
            },
            {"scene_blueprints": authored},
        ]
    )

    result = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values=VIDEO_FORM,
            selected_direction=DIRECTION,
            current_plan_markdown=PLAN_MARKDOWN,
            current_plan_version=1,
            plan_history=current.plan_history,
            revision_feedback="加强前三秒冲突，但不要修改模型和防水卖点",
            creation_contract=current.creation_contract,
            current_scene_blueprints=BLUEPRINTS,
            current_asset_manifest=stable_manifest,
            materials=[{"name": "用户补充角度.png", "url": "https://example.test/angle.png"}],
            intake_context={"original_prompt": "引流直播间"},
            model_factory=lambda *_args, **_kwargs: model,
        )
    )

    assert result.error is None
    assert result.plan_version == 2
    assert len(model.prompts) == 2
    assert "Seedance Plan 分镜写作 Skill" in model.prompts[1]
    assert "加强前三秒冲突" in model.prompts[1]
    assert "用户补充角度.png" in model.prompts[1]
    assert "seedance-current-enabled-model" in model.prompts[1]
    assert stable_manifest["props"][0]["asset_id"] in model.prompts[1]
    assert result.scene_blueprints[0]["storyline"] == authored[0]["storyline"]
    assert result.scene_blueprints[0]["duration_sec"] == 10
    assert result.asset_manifest == stable_manifest
