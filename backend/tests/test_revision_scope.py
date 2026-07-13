from __future__ import annotations

import asyncio

from pixelflow.qc.revision_scope import resolve_revision_scope


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> FakeMessage:
        self.prompts.append(prompt)
        return FakeMessage(self.content)


def _scenes() -> list[dict]:
    return [
        {"scene_id": "scene-1", "scene_index": 1, "storyline": "蓝牙耳机开场"},
        {"scene_id": "scene-2", "scene_index": 2, "storyline": "蓝牙耳机降噪卖点"},
        {"scene_id": "scene-3", "scene_index": 3, "storyline": "蓝牙耳机续航收口"},
    ]


def test_resolve_revision_scope_uses_llm_json_for_multiple_target_scenes() -> None:
    fake_model = FakeModel(
        """
        {
          "target_scene_ids": ["scene-2", "scene-3", "scene-99"],
          "excluded_scene_ids": ["scene-1"],
          "action": "fix_specific",
          "confidence": "high"
        }
        """
    )

    result = asyncio.run(
        resolve_revision_scope(
            feedback="第2个分镜和第3个分镜内容错误，第1个分镜没有问题，不要重新生成。",
            scenes=_scenes(),
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert result.target_scene_ids == ["scene-2", "scene-3"]
    assert result.excluded_scene_ids == ["scene-1"]
    assert result.action == "fix_specific"
    assert result.confidence == "high"
    assert result.llm_used is True
    assert "scene-99" not in result.target_scene_ids
    assert "第2个分镜和第3个分镜内容错误" in fake_model.prompts[0]


def test_resolve_revision_scope_uses_llm_json_for_natural_single_scene_followup() -> None:
    fake_model = FakeModel(
        """
        {"target_scene_ids":["scene-3"],"excluded_scene_ids":[],"action":"fix_specific","confidence":"high"}
        """
    )

    result = asyncio.run(
        resolve_revision_scope(
            feedback="分镜3也不对 你怎么没修改",
            scenes=_scenes(),
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert result.target_scene_ids == ["scene-3"]
    assert result.action == "fix_specific"
    assert result.llm_used is True


def test_resolve_revision_scope_returns_unknown_without_defaulting_to_all_scenes_when_llm_fails() -> None:
    class BrokenModel:
        def invoke(self, _prompt: str) -> None:
            raise RuntimeError("model down")

    result = asyncio.run(
        resolve_revision_scope(
            feedback="第2个分镜和第3个分镜内容错误",
            scenes=_scenes(),
            model_factory=lambda *_args, **_kwargs: BrokenModel(),
        )
    )

    assert result.target_scene_ids == []
    assert result.excluded_scene_ids == []
    assert result.action == "unknown"
    assert result.llm_used is False
    assert result.error == "model down"
