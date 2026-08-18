"""脚本镜头抽取 → 场景包镜数对齐。"""

from __future__ import annotations

import pytest

from pixelflow.creative.script_shots import (
    extract_script_scene_blueprints,
    extract_script_shot_entries,
    prefer_structured_shot_markdown,
    resolve_shot_source_markdown,
    sync_shot_source_into_pipeline,
)
from pixelflow.generate.scene_packages import prepare_video_scene_packages


SAMPLE_SCRIPT = """
## 五、镜头列表

- 镜头1-「00:00-00:05」
  - 画面：办公室走廊中景，照片被摔在地面。
- 镜头2-「00:05-00:12」
  - 画面：林晓低头捡起照片。
- 镜头3-「00:12-00:18」
  - 画面：赵总监转身离开。
- 镜头4-「00:18-00:25」
  - 画面：林晓攥紧拳头。
- 镜头5-「00:25-00:32」
  - 画面：夜色中的秀场入口。
- 镜头6-「00:32-00:40」
  - 画面：林晓举起手机取景。
"""


def test_extract_script_shot_entries_reads_plan_video_style_shots() -> None:
    entries = extract_script_shot_entries(SAMPLE_SCRIPT)
    assert len(entries) == 6
    assert entries[0]["start_sec"] == 0
    assert entries[0]["end_sec"] == 5
    assert entries[-1]["end_sec"] == 40
    assert "秀场" in entries[4]["storyline"]


TIMELINE_SCRIPT = """
0—10秒｜所有退路同时消失
【剧情/动作】 最终提案倒计时40分钟。安然盯着手机。
【原片对白】 安然：“如果失败呢？”

10—20秒｜Yann第一次不兜底
【剧情/动作】 Yann把手机放进安然手里。

20—35秒｜删掉漂亮空镜
【剧情/动作】 安然删除海岛航拍。

35—50秒｜片名确定
【剧情/动作】 输入片名。

50—65秒｜九段原片仍缺结尾
【剧情/动作】 粗剪完成。

65—80秒｜角色互换
【剧情/动作】 安然架起手机。

80—95秒｜Yann学会真正放权
【剧情/动作】 Yann坐下。

95—105秒｜固定铃声回归
【剧情/动作】 日出进入房间。

105—120秒｜核心答案
【新增对白】 Yann：“我选氧气防晒。”

120—130秒｜先防护，再贴妆
【产品演示】 叠加粉底。

130—140秒｜固定口播收束
【新增对白】 安然画外音：“妆前用它，就是底气。”

140—155秒｜五集真实旅程成为成片
【剧情/动作】 最终提案播放。

155—170秒｜预算与人物弧光同时落地
【剧情/动作】 联名方恢复预算。

170—180秒｜系列收束
【追剧钩子】 回到办公室梳妆台。
"""


def test_extract_timeline_shot_entries_keeps_fourteen_beats() -> None:
    entries = extract_script_shot_entries(TIMELINE_SCRIPT)
    assert len(entries) == 14
    assert entries[0]["start_sec"] == 0
    assert entries[0]["end_sec"] == 10
    assert "退路" in entries[0]["title"]
    assert "提案倒计时" in entries[0]["storyline"]
    assert "提案倒计时" in entries[0]["shot_description"]
    assert "时间" not in entries[0]["storyline"]
    assert entries[-1]["end_sec"] == 180


EPISODE_WITH_TIME_META = """
0—10秒｜所有退路同时消失
* **时间**: 00:00 - 00:10
* **画面**: 最终提案倒计时40分钟。安然盯着手机。
* **对白**: 安然：“如果失败呢？”

10—20秒｜Yann第一次不兜底
* **时间**: 00:10 - 00:20
* **剧情/动作**: Yann把手机放进安然手里。
"""


def test_extract_traditional_narration_column_into_shot_description() -> None:
    """繁体表头「旁白/對白」与【旁白/對白】须归一进镜头描述，不能丢列。"""

    table = """
| 时间 | 景别 | 运镜 | 画面 | 旁白/對白 | 屏幕文案 | 行动引导 |
| --- | --- | --- | --- | --- | --- | --- |
| 0-10秒 | 近景 | 推 | 安然盯着手机 | 安然：「如果失敗呢？」 | 倒计时 | 无 |
"""
    entries = extract_script_shot_entries(table)
    assert len(entries) == 1
    text = entries[0]["shot_description"]
    assert "旁白（对白）：安然：「如果失敗呢？」" in text
    assert entries[0]["narration"].startswith("安然")

    bracket = """
0—10秒｜标题
【剧情/动作】安然盯着手机。
【旁白/對白】安然：「如果失敗呢？」
"""
    bracket_entries = extract_script_shot_entries(bracket)
    assert len(bracket_entries) == 1
    assert "旁白（对白）：安然：「如果失敗呢？」" in bracket_entries[0]["shot_description"]
    assert "如果失敗" in bracket_entries[0]["narration"]


def test_extract_skips_bold_time_meta_keeps_picture() -> None:
    """镜块首行常是 * **时间**:，不得当成故事线/镜头描述。"""

    entries = extract_script_shot_entries(EPISODE_WITH_TIME_META)
    assert len(entries) == 2
    assert entries[0]["storyline"].startswith("最终提案倒计时")
    assert "00:00" not in entries[0]["storyline"]
    assert "最终提案倒计时" in entries[0]["shot_description"]
    assert "如果失败" in entries[0]["narration"]
    assert "Yann把手机" in entries[1]["shot_description"]


EPISODE_FULL_FIELDS = """
## 镜头1 0:00-0:10
景别：近景。运镜：缓推。画面：安然盯着手机倒计时。旁白：如果失败呢？屏幕文案：倒计时 40:00。行动引导：无

## 镜头2 0:10-0:20
* **时间**: 00:10 - 00:20
* **景别**: 中景
* **运镜**: 跟拍
* **画面**: Yann把手机放进安然手里
* **旁白**: Yann：“你自己定。”
* **屏幕文案**: 第一次不兜底
* **行动引导**: 无
"""


def test_extract_episode_keeps_shot_size_camera_picture_narration_copy_cta() -> None:
    """场景包镜头描述须带齐 episode 六字段，不能只剩画面摘要。"""

    entries = extract_script_shot_entries(EPISODE_FULL_FIELDS)
    assert len(entries) == 2
    first = entries[0]["shot_description"]
    assert "景别：近景" in first
    assert "运镜：缓推" in first
    assert "画面：安然盯着手机" in first
    assert "旁白（对白）：如果失败" in first
    assert "屏幕文案：倒计时" in first
    assert "行动引导：无" in first
    assert entries[0]["narration"].startswith("如果失败")
    assert entries[0]["storyline"].startswith("安然盯着手机")

    second = entries[1]["shot_description"]
    assert "景别：中景" in second
    assert "运镜：跟拍" in second
    assert "画面：Yann把手机" in second
    assert "旁白（对白）：" in second and "自己定" in second
    assert "屏幕文案：第一次不兜底" in second
    assert "行动引导：无" in second


def test_extract_episode_repairs_visual_action_accidentally_written_in_location() -> None:
    """模型偶发把场景标题和整段画面写入「地点」时，入库前应恢复标准字段。"""

    malformed = """
## 镜头1 0:00-0:10
景别：特写转中景
运镜：拉镜头
旁白（对白）：@安然（低落、焦虑）：“如果失败呢？”
屏幕文案：最终提案倒计时：40分钟
行动引导：无
地点：`### 海岛临时工作室/房间`。镜头从桌上闪烁红光的`### 损毁的硬盘`慢拉，露出焦躁的`### 安然`正盯着`### 安然的手机`上的剪辑轨道。
"""

    entries = extract_script_shot_entries(malformed)
    assert len(entries) == 1
    text = entries[0]["shot_description"]
    assert "地点：海岛临时工作室/房间" in text
    assert "画面：镜头从桌上闪烁红光" in text
    assert entries[0]["storyline"].startswith("镜头从桌上闪烁红光")
    assert "###" not in text
    assert "`" not in text


EPISODE_WITH_AT_REFS = """
## 镜头1 0:00-0:10
景别：近景
运镜：缓推
画面：人物形象@yann 与女2形象参考@安然 在会议室对视，手机倒计时在桌上。
旁白（对白）：安然：如果失败呢？
屏幕文案：倒计时 40:00
行动引导：无

## 镜头2 0:10-0:20
景别：中景
运镜：跟拍
画面：Yann把手机放进安然手里，形象参考@yann
旁白：Yann：你自己定。
屏幕文案：第一次不兜底
行动引导：无
"""

CHARACTERS_WITH_YANN = """
## 角色设定
### yann
男主，冷静。
### 安然
女主，短发。
## 场景设定
### 会议室
日光办公室。
## 道具设定
### 手机
黑色智能手机。
"""


def test_extract_episode_pulls_at_tokens_into_asset_requirements() -> None:
    entries = extract_script_shot_entries(EPISODE_WITH_AT_REFS)
    assert entries[0]["asset_requirements"]["characters"] == ["yann", "安然"]
    assert "yann" in entries[1]["asset_requirements"]["characters"]


def test_prepare_binds_picture_at_refs_into_shot_mentions() -> None:
    """画面里的人物形象@yann / 形象参考@安然 必须原地变成 @asset_id 并写入 mentions。"""

    result = prepare_video_scene_packages(
        form_values={
            "product_info": "氧气防晒",
            "product_category": "美妆",
            "video_ratio": "9:16",
            "video_model": "seedance-2.0",
        },
        plan_markdown=CHARACTERS_WITH_YANN + "\n" + EPISODE_WITH_AT_REFS,
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=20_000,
        shot_source_markdown=EPISODE_WITH_AT_REFS,
        settings_source_markdown=CHARACTERS_WITH_YANN,
    )
    assert result["ok"] is True
    scene = result["scene_packages"][0]
    text = scene["shot_description"]["text"]
    mentions = scene["shot_description"]["mentions"]
    name_by_id = {item["asset_id"]: item["name"] for item in mentions}
    assert "景别：近景" in text
    assert "运镜：缓推" in text
    assert "画面：" in text
    assert "旁白（对白）：" in text
    assert "屏幕文案：" in text
    assert "行动引导：" in text
    assert {"yann", "安然"}.issubset(set(name_by_id.values()))
    for asset_id, name in name_by_id.items():
        assert f"@{asset_id}" in text
        # 展示名 @ 已被替换，不再残留游离 @yann/@安然
        assert f"@{name}" not in text or name == asset_id
    # 保留上下文位置，而不是只在文末甩「参考素材」
    assert "人物形象@" in text
    assert "形象参考@" in text
    assert scene["reference_asset_ids"] == [item["asset_id"] for item in mentions]


def test_prepare_binds_bare_setting_names_into_shot_mentions() -> None:
    """成稿画面写裸名「安然盯着…」时，仍须按设定集绑定 @asset_id 与 mentions。"""

    episode_bare = """
## 镜头1 0:00-0:10
景别：近景
运镜：缓推
画面：安然盯着只剩九段原片的手机，会议室里倒计时在闪。
旁白（对白）：安然：如果失败呢？
屏幕文案：倒计时 40:00
行动引导：无

## 镜头2 0:10-0:20
景别：中景
运镜：跟拍
画面：Yann把手机放进安然手里。
旁白：Yann：你自己定。
屏幕文案：第一次不兜底
行动引导：无
"""
    result = prepare_video_scene_packages(
        form_values={
            "product_info": "氧气防晒",
            "product_category": "美妆",
            "video_ratio": "9:16",
            "video_model": "seedance-2.0",
        },
        plan_markdown=CHARACTERS_WITH_YANN + "\n" + episode_bare,
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=20_000,
        shot_source_markdown=episode_bare,
        settings_source_markdown=CHARACTERS_WITH_YANN,
    )
    assert result["ok"] is True
    scene = result["scene_packages"][0]
    text = scene["shot_description"]["text"]
    mentions = scene["shot_description"]["mentions"]
    names = {item["name"] for item in mentions}
    assert "安然" in names
    # 中文裸名后紧跟汉字也必须能绑上（安然盯着 / 会议室里）
    assert any(item["name"] == "安然" and f"@{item['asset_id']}" in text for item in mentions)
    assert "会议室" in names or "手机" in names
    for item in mentions:
        assert f"@{item['asset_id']}" in text
    # 画面里的裸名应被换成 @asset_id，而不是整段被模板覆盖
    assert "景别：近景" in text
    assert "倒计时" in text
    # 画面段优先出现 @character，而不是只改旁白主语
    picture_line = next((line for line in text.splitlines() if "画面：" in line), text)
    assert "@" in picture_line
    assert scene["reference_asset_ids"] == [item["asset_id"] for item in mentions]

    second = result["scene_packages"][1]
    second_names = {str(item["name"]).casefold() for item in second["shot_description"]["mentions"]}
    assert "yann" in second_names
    assert any(
        f"@{item['asset_id']}" in second["shot_description"]["text"]
        for item in second["shot_description"]["mentions"]
        if str(item["name"]).casefold() == "yann"
    )


def test_prepare_scene_packages_follows_timeline_shot_count() -> None:
    result = prepare_video_scene_packages(
        form_values={"product_info": "氧气防晒", "product_category": "美妆", "video_ratio": "9:16"},
        plan_markdown=TIMELINE_SCRIPT,
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=30_000,
    )
    assert result["ok"] is True
    assert len(result["scene_packages"]) == 14
    assert result["target_duration_ms"] == 180_000
    character_names = [item["name"] for item in result["global_assets"]["characters"]]
    assert "安然" in character_names
    assert "Yann" in character_names
    prop_names = [item["name"] for item in result["global_assets"]["props"]]
    assert any("防晒" in name for name in prop_names)
    assert len(result["global_assets"]["scenes"]) >= 1


def test_prepare_scene_packages_maps_annotated_cast_and_locations_to_formal_settings() -> None:
    settings = """
## 角色/场景/道具设定
### 角色设定
### 安然
- 视觉形象：年轻制片
### Yann
- 视觉形象：资深导师
### 场景设定
### 临时剪辑室
- 时空背景：清晨
### 提案现场
- 时空背景：白天
### 办公室梳妆台
- 时空背景：午后
### 道具与产品设定
### 氧气防晒
- 外观材质：白色瓶身

---
## 角色设定
*(已在上方详细定义，此处为结构对齐保留)*
## 场景设定
*(已在上方详细定义，此处为结构对齐保留)*
## 道具与产品设定
*(已在上方详细定义，此处为结构对齐保留)*
"""
    shots = """
0-5秒｜剪辑室
画面：在@临时剪辑室，@安然 焦虑查看素材。
旁白（对白）：@安然（焦虑低喃）：“如果失败呢？”
5-10秒｜提案
画面：在@提案现场，@Yann（轻声）看向@安然。
10-15秒｜收束
画面：回到@办公室梳妆台，@氧气防晒 与合照并列。
旁白（对白）：@安然（画外音）：“准备好了。”
"""

    result = prepare_video_scene_packages(
        form_values={"product_info": "氧气防晒", "video_ratio": "16:9"},
        plan_markdown=shots,
        target_duration_ms=15_000,
        shot_source_markdown=shots,
        settings_source_markdown=settings,
    )

    assets = result["global_assets"]
    assert [item["name"] for item in assets["characters"]] == ["安然", "Yann"]
    assert [item["name"] for item in assets["scenes"]] == [
        "临时剪辑室",
        "提案现场",
        "办公室梳妆台",
    ]
    assert [item["name"] for item in assets["props"]] == ["氧气防晒"]


def test_extract_dialogue_cast_from_timeline_script() -> None:
    from pixelflow.creative.asset_manifest import extract_script_setting_assets

    seed = extract_script_setting_assets(TIMELINE_SCRIPT)
    names = [item["name"] for item in seed["characters"]]
    assert "安然" in names
    assert "Yann" in names
    assert any("防晒" in item["name"] for item in seed["props"])
    assert seed["scenes"]


def test_extract_script_scene_blueprints_keeps_shot_count() -> None:
    duration_ms, blueprints = extract_script_scene_blueprints(SAMPLE_SCRIPT, target_duration_ms=30_000)
    assert duration_ms == 40_000
    assert len(blueprints) == 6
    assert blueprints[0]["structure_role"] == "opening"
    assert blueprints[-1]["structure_role"] == "conclusion"
    assert sum(item["duration_sec"] for item in blueprints) == 40


def test_resolve_shot_source_prefers_confirmed_episode() -> None:
    """脚本确认后抽镜头优先读 script_pipeline.episode，不被设定拼接稿干扰。"""

    from pixelflow.creative.script_shots import resolve_shot_source_markdown

    noisy_settings = """
## 角色设定
### 视觉特征
好看

## 分镜提示词
- 镜头1-「00:00-00:04」
  - 画面：只有设定里的一镜噪声。
"""
    payload = {
        "script_pipeline": {
            "characters": {"stage": "characters", "content": noisy_settings},
            "episode": {"stage": "episode", "content": TIMELINE_SCRIPT, "source": "user_complete_script"},
        },
        "script": {"content": "只有一句话的旧稿"},
        "plan_markdown": noisy_settings,
    }
    source = resolve_shot_source_markdown(payload, noisy_settings)
    assert "退路同时消失" in source
    entries = extract_script_shot_entries(payload=payload)
    assert len(entries) == 14
    assert entries[-1]["end_sec"] == 180


def test_extract_script_shot_entries_episode_kwarg() -> None:
    entries = extract_script_shot_entries("噪声设定稿", episode=TIMELINE_SCRIPT)
    assert len(entries) == 14


def test_prepare_scene_packages_uses_shot_source_markdown() -> None:
    """prepare 用 shot_source_markdown 定镜数，plan_markdown 仍可带设定。"""

    settings_plus_noise = """
## 角色设定
### 安然
女主

## 分镜提示词
- 镜头1-「00:00-00:04」
  - 画面：噪声一镜。
"""
    result = prepare_video_scene_packages(
        form_values={"product_info": "氧气防晒", "product_category": "美妆", "video_ratio": "9:16"},
        plan_markdown=settings_plus_noise + "\n\n---\n\n" + TIMELINE_SCRIPT,
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=30_000,
        shot_source_markdown=TIMELINE_SCRIPT,
    )
    assert result["ok"] is True
    assert len(result["scene_packages"]) == 14
    assert result["target_duration_ms"] == 180_000


CHARACTERS_SETTINGS = """
## 角色设定
### 安然
年轻女性策划，短发，职场通勤装。

## 场景设定
### 办公室梳妆台
晨光侧窗，桌面护肤品陈列。

## 道具设定
### 氧气防晒
妆前防晒乳，银色软管。
"""


CHARACTERS_SETTINGS_WITH_BULLETED_HEADINGS = """
## 角色/场景/道具设定

### 角色设定
- ### 安然
  - **视觉形象**：职场新人，干练低马尾。
- ### Yann
  - **视觉形象**：资深项目负责人，短发微卷。

### 场景设定
- ### 酒店套房
  - **光线氛围**：日出前的冷暖交织自然光。
- ### 会议室
  - **光线氛围**：明亮微冷的商务顶光。

### 道具与产品设定
- ### 氧气防晒
  - **外观材质**：白色磨砂软管。
"""


def test_prepare_parses_bulleted_setting_headings() -> None:
    result = prepare_video_scene_packages(
        form_values={"product_info": "氧气防晒", "product_category": "美妆", "video_ratio": "16:9"},
        plan_markdown="已确认脚本",
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=30_000,
        shot_source_markdown=TIMELINE_SCRIPT,
        settings_source_markdown=CHARACTERS_SETTINGS_WITH_BULLETED_HEADINGS,
    )

    assets = result["global_assets"]
    assert {item["name"] for item in assets["characters"]} == {"安然", "Yann"}
    assert {item["name"] for item in assets["scenes"]} == {"酒店套房", "会议室"}
    assert "氧气防晒" in {item["name"] for item in assets["props"]}


def test_prepare_prefers_richer_current_script_settings_over_stale_pipeline_settings() -> None:
    current_script = """
## 角色设定
### 安然
职场新人。
### Yann
资深导师。
### 联名方代表
商务客户代表。

## 场景设定
### 酒店套房
日出前的临时剪辑室。
### 会议室
提案现场。
### 办公室梳妆台
系列收束场景。

## 道具设定
### 氧气防晒
核心产品。
### 任命函
安然成长的实体证明。
"""
    stale_pipeline_settings = """
## 角色设定
### 安然
职场新人。
## 场景设定
### 酒店套房
临时剪辑室。
## 道具设定
### 氧气防晒
核心产品。
"""

    result = prepare_video_scene_packages(
        form_values={"product_info": "氧气防晒", "product_category": "美妆", "video_ratio": "16:9"},
        plan_markdown=current_script,
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=20_000,
        shot_source_markdown=EPISODE_WITH_OUTLINE_AND_SHOT_TABLE,
        settings_source_markdown=stale_pipeline_settings,
    )

    assets = result["global_assets"]
    assert {item["name"] for item in assets["characters"]} == {"安然", "Yann"}
    yann = next(item for item in assets["characters"] if item["name"] == "Yann")
    assert "资深导师" in yann["description"]
    assert {item["name"] for item in assets["scenes"]} == {"酒店套房"}


def test_prepare_uses_settings_source_for_global_assets() -> None:
    """角色/场景/道具来自 settings_source（characters），不靠拼接稿噪声。"""

    result = prepare_video_scene_packages(
        form_values={"product_info": "氧气防晒", "product_category": "美妆", "video_ratio": "9:16"},
        plan_markdown="只有噪声，没有可用设定",
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=30_000,
        shot_source_markdown=TIMELINE_SCRIPT,
        settings_source_markdown=CHARACTERS_SETTINGS,
    )
    assert result["ok"] is True
    assert len(result["scene_packages"]) == 14
    names = {item["name"] for item in result["global_assets"]["characters"]}
    assert "安然" in names
    scene_names = {item["name"] for item in result["global_assets"]["scenes"]}
    assert "办公室梳妆台" in scene_names
    prop_names = {item["name"] for item in result["global_assets"]["props"]}
    assert any("防晒" in name for name in prop_names)


@pytest.mark.asyncio
async def test_prepare_with_llm_skips_structure_model_when_pipeline_ready() -> None:
    """有 episode 镜头 + characters 设定时不得再调结构 LLM。"""

    from pixelflow.generate.scene_packages import prepare_video_scene_packages_with_llm

    def _boom(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("不应再调用结构模型")

    result = await prepare_video_scene_packages_with_llm(
        form_values={"product_info": "氧气防晒", "product_category": "美妆", "video_ratio": "9:16"},
        plan_markdown=CHARACTERS_SETTINGS,
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=30_000,
        shot_source_markdown=TIMELINE_SCRIPT,
        settings_source_markdown=CHARACTERS_SETTINGS,
        model_factory=_boom,
    )
    assert result["ok"] is True
    assert result["llm_used"] is False
    assert len(result["scene_packages"]) == 14
    assert "安然" in {item["name"] for item in result["global_assets"]["characters"]}


def test_prepare_scene_packages_follows_script_shot_count() -> None:
    result = prepare_video_scene_packages(
        form_values={"product_info": "苹果PRO", "product_category": "手机", "video_ratio": "9:16"},
        plan_markdown=SAMPLE_SCRIPT,
        selected_direction={"title": "职场逆袭"},
        materials=[],
        target_duration_ms=30_000,
    )
    assert result["ok"] is True
    assert len(result["scene_packages"]) == 6
    assert result["target_duration_ms"] == 40_000


EPISODE_MARKDOWN_TABLE = """
时长：20秒 画幅：16:9

镜头列表：

| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |
| --- | --- | --- | --- | --- | --- | --- |
| 0-10秒 | 中景→特写 | 推 | 在@后期剪辑室，@安然盯着手机上仅剩的九段原片，身旁的硬盘指示灯熄灭。 | 安然：“如果失败呢？” | 最终提案倒计时40分钟 | |
| 10-20秒 | 中景 | 跟 | @安然把手机递给@yann。 | Yann：“你自己定。” | 第一次不兜底 | 无 |
"""

EPISODE_WITH_OUTLINE_AND_SHOT_TABLE = """
## 大纲

* **0-10秒**：【所有退路同时消失】安然陷入焦虑。
* **10-20秒**：【Yann第一次不兜底】Yann将决定权交还安然。

## 完整镜头脚本

| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |
| --- | --- | --- | --- | --- | --- | --- |
| 0-10秒 | 中景 | 缓慢向前微推 | 在@酒店套房中，@安然盯着仅剩九段原片的手机。 | @安然：“如果失败呢？” | 最终提案倒计时 | 无 |
| 10-20秒 | 中景转近景 | 侧平移 | @Yann将手机放进@安然手里，随后退到镜头外。 | @Yann：“选择，你来做。” | 选择，你来做 | 无 |
"""


def test_extract_prefers_complete_shot_table_over_outline_time_ranges() -> None:
    entries = extract_script_shot_entries(EPISODE_WITH_OUTLINE_AND_SHOT_TABLE)

    assert len(entries) == 2
    assert entries[0]["storyline"].startswith("在@酒店套房中")
    assert "景别：中景" in entries[0]["shot_description"]
    assert "运镜：缓慢向前微推" in entries[0]["shot_description"]
    assert "旁白（对白）：@安然" in entries[0]["shot_description"]
    assert entries[0]["asset_requirements"]["characters"]


CHARACTERS_WITH_EDIT_ROOM = """
## 角色设定
### 安然
女主，短发。
### yann
男主，冷静。
## 场景设定
### 后期剪辑室
剪辑台与硬盘指示灯。
## 道具设定
### 手机
黑色智能手机。
"""


def test_extract_episode_markdown_shot_table_into_six_fields() -> None:
    """episode Markdown 镜头表须抽出六字段；旁白/对白归一为旁白（对白）。"""

    entries = extract_script_shot_entries(EPISODE_MARKDOWN_TABLE)
    assert len(entries) == 2
    first = entries[0]["shot_description"]
    assert "景别：中景→特写" in first
    assert "运镜：推" in first
    assert "画面：在@后期剪辑室，@安然" in first
    assert "旁白（对白）：安然：“如果失败呢？”" in first
    assert "屏幕文案：最终提案倒计时40分钟" in first
    assert entries[0]["start_sec"] == 0
    assert entries[0]["end_sec"] == 10
    assert entries[1]["start_sec"] == 10
    assert "行动引导：无" in entries[1]["shot_description"]


def test_prepare_binds_episode_markdown_table_into_shot_mentions() -> None:
    """表格成稿里的 @后期剪辑室 / @安然盯着… 须落到设定集资产，六字段进入镜头描述。"""

    result = prepare_video_scene_packages(
        form_values={
            "product_info": "氧气防晒",
            "product_category": "美妆",
            "video_ratio": "16:9",
            "video_model": "seedance-2.0",
        },
        plan_markdown=CHARACTERS_WITH_EDIT_ROOM + "\n" + EPISODE_MARKDOWN_TABLE,
        selected_direction={"title": "妆前防晒"},
        materials=[],
        target_duration_ms=20_000,
        shot_source_markdown=EPISODE_MARKDOWN_TABLE,
        settings_source_markdown=CHARACTERS_WITH_EDIT_ROOM,
    )
    assert result["ok"] is True
    assert len(result["scene_packages"]) == 2
    scene = result["scene_packages"][0]
    text = scene["shot_description"]["text"]
    mentions = scene["shot_description"]["mentions"]
    names = {item["name"] for item in mentions}
    assert "景别：中景→特写" in text
    assert "运镜：推" in text
    assert "画面：" in text
    assert "旁白（对白）：" in text
    assert "屏幕文案：最终提案倒计时40分钟" in text
    assert "安然" in names
    assert "后期剪辑室" in names
    assert "安然盯着手机上仅剩的九段原片" not in names
    for item in mentions:
        assert f"@{item['asset_id']}" in text
    assert any(item["name"] == "安然" and "盯着手机" in text for item in mentions)
    scene_names = {item["name"] for item in result["global_assets"]["scenes"]}
    assert "后期剪辑室" in scene_names
    character_names = {item["name"] for item in result["global_assets"]["characters"]}
    assert "安然" in character_names
    assert "yann" in character_names


RAW_IMPORT_TIMELINE = """
第10集｜最后一镜，换我来拍
0—10秒｜所有退路同时消失
【剧情/动作】 最终提案倒计时40分钟。安然盯着只剩九段原片的手机。
【原关键对白】 安然：“如果失败呢？”
10—20秒｜Yann第一次不兜底
【剧情/动作】 Yann把手机放进安然手里。
【新增对白】 Yann：“失败就一起承担。”
"""

STRUCTURED_EPISODE_TABLE = """
## 生成剧本正文 /episode

时长：20秒 画幅：16:9

镜头列表：

| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |
| --- | --- | --- | --- | --- | --- | --- |
| 0-10秒 | 近景 | 推 | 在@后期剪辑室，@安然盯着手机。 | 安然：“如果失败呢？” | 倒计时40分钟 | 无 |
| 10-20秒 | 中景 | 跟 | @安然把手机递给@yann。 | Yann：“失败就一起承担。” | 第一次不兜底 | 无 |
"""


def test_extract_episode_traditional_narration_column() -> None:
    """繁体表头「旁白/對白」与【旁白/對白】须归一进镜头描述。"""

    table = """
| 时间 | 景别 | 运镜 | 画面 | 旁白/對白 | 屏幕文案 | 行动引导 |
| --- | --- | --- | --- | --- | --- | --- |
| 0-10秒 | 近景 | 推 | 安然盯着手机 | 安然：「如果失敗呢？」 | 倒计时 | 无 |
"""
    entries = extract_script_shot_entries(table)
    assert len(entries) == 1
    assert "旁白（对白）：安然：「如果失敗呢？」" in entries[0]["shot_description"]
    assert "如果失敗" in entries[0]["narration"]

    bracket = """
0—10秒｜标题
【剧情/动作】安然盯着手机。
【旁白/對白】安然：「如果失敗呢？」
"""
    bracket_entries = extract_script_shot_entries(bracket)
    assert len(bracket_entries) == 1
    assert "旁白（对白）：安然：「如果失敗呢？」" in bracket_entries[0]["shot_description"]
    assert "如果失敗" in bracket_entries[0]["narration"]


def test_ensure_narration_injected_when_shot_text_omits_field() -> None:
    from pixelflow.creative.script_shots import ensure_narration_in_shot_description

    text = ensure_narration_in_shot_description(
        "景别：近景\n画面：安然盯着手机",
        "安然：如果失败呢？",
    )
    assert "旁白（对白）：安然：如果失败呢？" in text
    # 已有字段时不重复追加
    again = ensure_narration_in_shot_description(text, "安然：如果失败呢？")
    assert again.count("旁白（对白）：") == 1


def test_sync_shot_source_does_not_overwrite_structured_episode_with_raw_import() -> None:
    """确认时误传导入原文，不得盖掉拆解后的六列表 episode。"""

    payload = {
        "script_pipeline": {
            "episode": {
                "stage": "episode",
                "content": STRUCTURED_EPISODE_TABLE,
                "source": "import_structure",
            }
        }
    }
    assert sync_shot_source_into_pipeline(payload, RAW_IMPORT_TIMELINE) is None
    preferred = prefer_structured_shot_markdown(RAW_IMPORT_TIMELINE, payload)
    assert "旁白/对白" in preferred
    assert "【剧情/动作】" not in preferred


def test_sync_shot_source_still_updates_when_confirm_markdown_is_richer() -> None:
    payload = {
        "script_pipeline": {
            "episode": {
                "stage": "episode",
                "content": RAW_IMPORT_TIMELINE,
                "source": "user_complete_script",
            }
        }
    }
    patch = sync_shot_source_into_pipeline(payload, STRUCTURED_EPISODE_TABLE)
    assert patch is not None
    assert "旁白/对白" in patch["script_pipeline"]["episode"]["content"]
