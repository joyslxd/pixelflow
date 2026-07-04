from __future__ import annotations

import asyncio

from pixelflow.qc.scene_semantic import evaluate_scene_semantic_contracts


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


def test_evaluate_scene_semantic_contracts_uses_structured_llm_results() -> None:
    fake_model = FakeModel(
        """
        [
          {
            "scene_id": "scene-1",
            "passed": true,
            "category": "product_consistency",
            "severity": "info",
            "message": "",
            "expected": "有线耳机通勤广告",
            "observed": "用户佩戴有线耳机",
            "suggestion": ""
          },
          {
            "scene_id": "scene-2",
            "passed": false,
            "category": "product_consistency",
            "severity": "major",
            "message": "第2个分镜画面主体是口红，偏离有线耳机方案。",
            "expected": "有线耳机稳定连接卖点",
            "observed": "红色口红美妆展示",
            "suggestion": "只重生成第2个分镜，恢复有线耳机展示。"
          },
          {
            "scene_id": "scene-99",
            "passed": false,
            "category": "product_consistency",
            "severity": "major",
            "message": "不存在的分镜",
            "expected": "",
            "observed": "",
            "suggestion": ""
          }
        ]
        """
    )

    result = asyncio.run(
        evaluate_scene_semantic_contracts(
            global_contract_text="原始方案：有线耳机广告，突出稳定连接、低延迟、线控麦克风。",
            items=[
                {
                    "scene_id": "scene-1",
                    "scene_index": 1,
                    "scene_contract_text": "用户佩戴有线耳机通勤听音乐",
                    "observed_text": "用户佩戴有线耳机连接手机",
                },
                {
                    "scene_id": "scene-2",
                    "scene_index": 2,
                    "scene_contract_text": "有线耳机稳定连接卖点展示",
                    "observed_text": "红色口红放在化妆台上，美妆上唇展示",
                },
            ],
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert [item["scene_id"] for item in result] == ["scene-1", "scene-2"]
    assert result[0]["passed"] is True
    assert result[1]["passed"] is False
    assert result[1]["severity"] == "major"
    assert "口红" in result[1]["message"]
    assert "有线耳机" in fake_model.prompts[0]
    assert "scene-99" not in [item["scene_id"] for item in result]


def test_evaluate_scene_semantic_contracts_returns_empty_when_model_fails() -> None:
    class BrokenModel:
        def invoke(self, _prompt: str) -> None:
            raise RuntimeError("model down")

    result = asyncio.run(
        evaluate_scene_semantic_contracts(
            global_contract_text="原始方案：有线耳机广告。",
            items=[
                {
                    "scene_id": "scene-1",
                    "scene_index": 1,
                    "scene_contract_text": "有线耳机展示",
                    "observed_text": "口红展示",
                }
            ],
            model_factory=lambda *_args, **_kwargs: BrokenModel(),
        )
    )

    assert result == []
