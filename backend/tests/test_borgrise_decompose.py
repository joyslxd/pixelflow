"""Tests for the 博观 decompose response parsing (offline, pure helpers)."""

from __future__ import annotations

from pixelflow.skills.borgrise.skill import _extract_shots, _normalize_segment, _parse_time_range

# The real decompose response nests the segment list under data.result.video_url.
_REAL_RESPONSE = {
    "success": True,
    "data": {
        "result": {
            "message": "视频拆解成功",
            "video_url": {
                "segments": [
                    {"shotType": "全局视觉风格", "timeRange": "0-0s", "visualContent": "【全局视觉风格】真人UGC风格", "cameraMovement": "无", "voiceContent": "", "subtitle": ""},
                    {"shotType": "近景", "timeRange": "4-22s", "visualContent": "【口播分享】女性主播真诚分享", "cameraMovement": "静止", "voiceContent": "Things I wish somebody told me", "subtitle": "TRUST ME"},
                ]
            },
        }
    },
}


# -- _extract_shots --


def test_extracts_segments_from_nested_real_response():
    shots = _extract_shots(_REAL_RESPONSE)
    assert len(shots) == 2
    assert shots[0]["shotType"] == "全局视觉风格"
    assert shots[1]["timeRange"] == "4-22s"


def test_extracts_simple_shapes():
    assert len(_extract_shots({"shots": [{"a": 1}, {"b": 2}]})) == 2
    assert len(_extract_shots({"data": {"scenes": [{"x": 1}]}})) == 1


def test_string_segments_become_descriptions():
    shots = _extract_shots({"segments": ["镜头一", "镜头二"]})
    assert shots == [{"description": "镜头一"}, {"description": "镜头二"}]


def test_no_shots_returns_empty():
    assert _extract_shots({"data": {"result": {"message": "处理中"}}}) == []
    assert _extract_shots("not a dict") == []


# -- _parse_time_range --


def test_parse_time_range():
    assert _parse_time_range("4-22s") == 18.0
    assert _parse_time_range("0-0s") == 0.0
    assert _parse_time_range("33-36s") == 3.0
    assert _parse_time_range("") == 0.0
    assert _parse_time_range(None) == 0.0


# -- _normalize_segment --


def test_normalize_maps_camelcase_to_underscore():
    seg = {"visualContent": "画面", "voiceContent": "旁白", "subtitle": "花字", "shotType": "特写", "cameraMovement": "推", "timeRange": "25-27s"}
    out = _normalize_segment(seg)
    assert out["visual_description"] == "画面"
    assert out["narration_text"] == "旁白"
    assert out["onscreen_text"] == "花字"
    assert out["shot_type"] == "特写"
    assert out["camera_movement"] == "推"
    assert out["duration"] == 2.0
    assert out["time_range"] == "25-27s"


def test_normalize_tolerates_missing_fields_and_non_dict():
    out = _normalize_segment({"timeRange": "1-3s"})
    assert out["visual_description"] == ""
    assert out["duration"] == 2.0
    assert _normalize_segment("纯文本")["visual_description"] == "纯文本"
