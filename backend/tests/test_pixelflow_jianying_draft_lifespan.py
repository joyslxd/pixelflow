from __future__ import annotations

from fastapi import FastAPI


def test_jianying_draft_lifespan_configuration_injects_runtime_settings(monkeypatch):
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "true")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS", "1.5")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "2")

    from app.gateway.app import _configure_jianying_draft_service

    app = FastAPI()
    _configure_jianying_draft_service(app)
    service = app.state.pixelflow_jianying_draft_service

    assert service._timeout_seconds == 42.0
    assert service._max_retries == 2
    assert app.state.jianying_draft_poll_interval_seconds == 1.5
