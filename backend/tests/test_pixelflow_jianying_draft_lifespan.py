from __future__ import annotations

import asyncio

from fastapi import FastAPI


def test_jianying_draft_lifespan_configuration_injects_runtime_settings(monkeypatch):
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "true")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS", "1.5")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "2")

    from app.gateway.app import _configure_jianying_draft_service
    from pixelflow.jianying_draft import MissingProviderJianyingDraftSkill

    app = FastAPI()
    _configure_jianying_draft_service(app)
    service = app.state.pixelflow_jianying_draft_service

    assert service._timeout_seconds == 42.0
    assert service._max_retries == 2
    assert service._poll_interval_seconds == 1.5
    assert app.state.jianying_draft_poll_interval_seconds == 1.5
    assert isinstance(service._skill, MissingProviderJianyingDraftSkill)


def test_jianying_draft_skill_builder_distinguishes_disabled_and_missing_provider():
    from app.gateway.app import _build_jianying_draft_skill
    from pixelflow.jianying_draft import (
        DisabledJianyingDraftSkill,
        JianyingDraftRuntimeConfig,
        MissingProviderJianyingDraftSkill,
    )

    disabled_skill = _build_jianying_draft_skill(
        JianyingDraftRuntimeConfig(enabled=False)
    )
    missing_provider_skill = _build_jianying_draft_skill(
        JianyingDraftRuntimeConfig(enabled=True)
    )

    assert isinstance(disabled_skill, DisabledJianyingDraftSkill)
    assert isinstance(missing_provider_skill, MissingProviderJianyingDraftSkill)
    assert type(disabled_skill) is not type(missing_provider_skill)
    assert asyncio.run(disabled_skill.capability()).model_dump() == {
        "available": False,
        "reason": "剪映草稿服务待接入",
        "poll_interval_seconds": 2.0,
    }
    assert asyncio.run(missing_provider_skill.capability()).model_dump() == {
        "available": False,
        "reason": "剪映草稿服务待接入",
        "poll_interval_seconds": 2.0,
    }
