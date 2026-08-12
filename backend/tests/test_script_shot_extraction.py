"""脚本镜头抽取 → 场景包镜数对齐。"""

from __future__ import annotations

from pixelflow.creative.script_shots import extract_script_scene_blueprints, extract_script_shot_entries
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


def test_extract_script_scene_blueprints_keeps_shot_count() -> None:
    duration_ms, blueprints = extract_script_scene_blueprints(SAMPLE_SCRIPT, target_duration_ms=30_000)
    assert duration_ms == 40_000
    assert len(blueprints) == 6
    assert blueprints[0]["structure_role"] == "opening"
    assert blueprints[-1]["structure_role"] == "conclusion"
    assert sum(item["duration_sec"] for item in blueprints) == 40


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
