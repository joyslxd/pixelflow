from __future__ import annotations

import pytest

from pixelflow.creative import plan_llm


def test_default_plan_model_has_finite_timeout_and_no_transport_retry(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_chat_model(name: str, **kwargs):
        captured.update({"name": name, **kwargs})
        return object()

    monkeypatch.setattr("deerflow.models.factory.create_chat_model", fake_create_chat_model)

    assert plan_llm._default_model_factory("deepseek-v4-pro") is not None
    assert captured["timeout"] == 600.0
    assert captured["max_retries"] == 0


def test_invoke_json_model_maps_provider_timeout_to_safe_exception() -> None:
    class TimeoutModel:
        def invoke(self, _prompt):
            raise TimeoutError("供应商原始异常不得越过边界")

    with pytest.raises(plan_llm.PlanModelTimeoutError, match="Plan 模型请求超时") as exc_info:
        plan_llm._invoke_json_model(
            "prompt",
            "deepseek-v4-pro",
            lambda *_args, **_kwargs: TimeoutModel(),
        )

    assert "供应商原始异常" not in str(exc_info.value)
