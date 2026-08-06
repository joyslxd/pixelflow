from __future__ import annotations

import pytest
from fastapi import FastAPI


def test_gateway_reference_provider_fails_closed_without_service_credential(
    monkeypatch,
) -> None:
    from app.gateway.app import _configure_content_app_provider_services

    monkeypatch.delenv("PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION", raising=False)
    app = FastAPI()

    client = _configure_content_app_provider_services(app)

    assert client is None
    assert app.state.pixelflow_reference_analysis_job_service is None
    assert app.state.pixelflow_reference_analysis_provider_adapter is None
    assert app.state.pixelflow_generate_scene_video_job_service is None
    assert app.state.pixelflow_merge_video_job_service is None
    assert app.state.pixelflow_quality_review_job_service is None
    assert app.state.pixelflow_jianying_draft_job_service is None
    assert app.state.pixelflow_reference_analysis_provider_reason == (
        "content_app_status_authorization_unavailable"
    )


@pytest.mark.asyncio
async def test_gateway_reference_provider_reads_service_credential_from_environment(
    monkeypatch,
) -> None:
    from app.gateway.app import _configure_content_app_provider_services

    credential = "Bearer gateway-service-test"
    monkeypatch.setenv("PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION", credential)
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "false")
    app = FastAPI()

    client = _configure_content_app_provider_services(app)
    try:
        assert client is not None
        assert app.state.pixelflow_reference_analysis_job_service is not None
        assert app.state.pixelflow_reference_analysis_provider_adapter is not None
        assert app.state.pixelflow_generate_scene_video_job_service is not None
        assert app.state.pixelflow_merge_video_job_service is not None
        assert app.state.pixelflow_quality_review_job_service is not None
        assert app.state.pixelflow_jianying_draft_job_service is None
        assert app.state.pixelflow_reference_analysis_provider_reason is None
        assert credential not in repr(
            app.state.pixelflow_reference_analysis_job_service
        )
    finally:
        if client is not None:
            await client.aclose()


@pytest.mark.asyncio
async def test_gateway_registers_all_four_live_providers_when_jianying_is_configured(
    monkeypatch,
) -> None:
    from app.gateway.app import _configure_content_app_provider_services
    from app.gateway.pixelflow_agent_live_providers import (
        make_video_live_provider_adapters,
    )

    monkeypatch.setenv(
        "PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION",
        "Bearer gateway-service-test",
    )
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "true")
    monkeypatch.setenv("PIXELFLOW_CONTENT_APP_INTERNAL_UPLOAD_ENABLED", "true")
    monkeypatch.setenv(
        "PIXELFLOW_JIANYING_DRAFT_BASE_URL",
        "https://jianying.example.invalid",
    )
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_TOKEN", "provider-fixed-token")
    app = FastAPI()

    client = _configure_content_app_provider_services(app)
    try:
        providers = make_video_live_provider_adapters(
            generate_scene_video=app.state.pixelflow_generate_scene_video_job_service,
            merge_video=app.state.pixelflow_merge_video_job_service,
            quality_review=app.state.pixelflow_quality_review_job_service,
            jianying_draft=app.state.pixelflow_jianying_draft_job_service,
        )

        assert providers.ready is True
        assert tuple(providers.adapters) == (
            "generate_scene_video",
            "merge_video",
            "quality_review",
            "jianying_draft",
        )
        assert "provider-fixed-token" not in repr(
            app.state.pixelflow_jianying_draft_job_service
        )
    finally:
        if client is not None:
            await client.aclose()


@pytest.mark.asyncio
async def test_gateway_keeps_jianying_live_provider_closed_before_internal_upload_deployment(
    monkeypatch,
) -> None:
    from app.gateway.app import _configure_content_app_provider_services

    monkeypatch.setenv(
        "PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION",
        "Bearer gateway-service-test",
    )
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "true")
    monkeypatch.setenv(
        "PIXELFLOW_JIANYING_DRAFT_BASE_URL",
        "https://jianying.example.invalid",
    )
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_TOKEN", "provider-fixed-token")
    monkeypatch.setenv("PIXELFLOW_CONTENT_APP_INTERNAL_UPLOAD_ENABLED", "false")
    app = FastAPI()

    client = _configure_content_app_provider_services(app)
    try:
        assert app.state.pixelflow_jianying_draft_job_service is None
    finally:
        if client is not None:
            await client.aclose()
