from __future__ import annotations

import time
from uuid import UUID

from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from tests._router_auth_helpers import make_authed_test_app


def _stable_user() -> User:
    return User(
        email="pixelflow-ppt@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000903"),
    )


def test_pixelflow_ppt_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_ppt

    paths = {route.path for route in pixelflow_ppt.router.routes}
    assert pixelflow_ppt.router.prefix == "/agent/flows/ppt"
    assert "/agent/flows/ppt/summary/start" in paths
    assert "/agent/flows/ppt/summary/update/start" in paths
    assert "/agent/flows/ppt/content-json/start" in paths
    assert "/agent/flows/ppt/images/start" in paths
    assert "/agent/flows/ppt/images/regenerate/start" in paths
    assert "/agent/flows/ppt/file/start" in paths
    assert "/agent/flows/ppt/jobs/{job_id}" in paths


def test_with_ppt_memory_truncates_topic_to_safe_length():
    """长期记忆拼进 topic 时必须截断，否则 content-app SmartPPT 的 topic 列会 Data truncation。"""
    from app.gateway.routers import pixelflow_ppt
    from pixelflow.memory import SemanticMemoryItem

    # 构造 5 条长记忆，semantic_memory_text 拼出来远超 _PPT_TOPIC_MAX_CHARS。
    memories = [
        SemanticMemoryItem(memory_id=f"m{i}", content=f"用户偏好风格倾向偏好第{i}条" * 20)
        for i in range(5)
    ]
    body = pixelflow_ppt.PptSummaryJobStartRequest(
        ppt_topic="保温杯",
        ppt_style="简约",
        attachments=[{"name": "a.pdf", "url": "https://x/a.pdf"}],
    )

    merged = pixelflow_ppt._with_ppt_memory(body, memories)

    assert merged.ppt_topic.startswith("保温杯\n长期记忆约束：")
    assert len(merged.ppt_topic) <= pixelflow_ppt._PPT_TOPIC_MAX_CHARS
    assert merged.ppt_style == "简约"
    assert merged.attachments == body.attachments


def test_with_ppt_memory_noop_without_memories():
    from app.gateway.routers import pixelflow_ppt

    body = pixelflow_ppt.PptSummaryJobStartRequest(ppt_topic="保温杯", ppt_style="简约")
    merged = pixelflow_ppt._with_ppt_memory(body, [])
    assert merged is body or merged.ppt_topic == "保温杯"


def test_with_ppt_memory_skips_when_topic_already_long():
    """用户自己传的 topic 已接近上限时，不再追加记忆，避免溢出。"""
    from app.gateway.routers import pixelflow_ppt
    from pixelflow.memory import SemanticMemoryItem

    memories = [SemanticMemoryItem(memory_id="m1", content="偏好真实摄影风格")]
    body = pixelflow_ppt.PptSummaryJobStartRequest(ppt_topic="x" * pixelflow_ppt._PPT_TOPIC_MAX_CHARS, ppt_style="简约")
    merged = pixelflow_ppt._with_ppt_memory(body, memories)
    assert merged.ppt_topic == body.ppt_topic


def test_ppt_router_generates_summary_as_async_job(monkeypatch):
    from app.gateway.routers import pixelflow_ppt
    from pixelflow.skills import PptGenerationResult

    class FakePptSkill:
        async def generate_ppt_summary(self, **kwargs):
            assert kwargs["topic"] == "绿色供应链转型汇报"
            assert kwargs["ppt_style"] == "极简商务"
            assert kwargs["file_urls"] == ["https://x/report.docx"]
            return PptGenerationResult(
                ok=True,
                task_id="task-summary-1",
                smart_ppt_project_id=88,
                summary="# 绿色供应链转型汇报\n## P1. 封面",
                raw={"endpoint": "/api/picture/smart-ppt/generatePptSummary"},
            )

    monkeypatch.setattr(pixelflow_ppt, "get_ppt_skill", lambda: FakePptSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_ppt.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/ppt/summary/start",
            json={
                "ppt_topic": "绿色供应链转型汇报",
                "ppt_style": "极简商务",
                "attachments": [{"name": "report.docx", "url": "https://x/report.docx"}],
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()
        assert started["ok"] is True
        assert started["job_id"]

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/ppt/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "completed":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "completed"
    assert status["result"]["summary"] == "# 绿色供应链转型汇报\n## P1. 封面"
    assert status["result"]["smart_ppt_project_id"] == 88


def test_ppt_router_generates_images_with_partial_page_status(monkeypatch):
    from app.gateway.routers import pixelflow_ppt
    from pixelflow.skills import PptGenerationResult

    calls: list[str] = []

    class FakePptSkill:
        async def generate_ppt_image(self, **kwargs):
            calls.append(kwargs["json_content"])
            index = len(calls)
            return PptGenerationResult(
                ok=True,
                task_id=f"task-image-{index}",
                smart_ppt_project_id=88,
                image_url=f"https://x/page-{index}.png",
                raw={"endpoint": "/api/picture/smart-ppt/generatePptImage"},
            )

    monkeypatch.setattr(pixelflow_ppt, "get_ppt_skill", lambda: FakePptSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_ppt.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/ppt/images/start",
            json={
                "smart_ppt_project_id": 88,
                "content_json": [
                    {"page_index": 1, "title": "封面"},
                    {"page_index": 2, "title": "趋势"},
                ],
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/ppt/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "completed":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "completed"
    pages = status["result"]["pages"]
    assert [page["status"] for page in pages] == ["completed", "completed"]
    assert [page["image_url"] for page in pages] == ["https://x/page-1.png", "https://x/page-2.png"]


def test_ppt_router_pauses_job_on_quota_insufficient(monkeypatch):
    from app.gateway.routers import pixelflow_ppt
    from pixelflow.skills import PptGenerationResult

    class FakePptSkill:
        async def generate_ppt_file(self, **kwargs):
            return PptGenerationResult(
                ok=False,
                task_id="task-file-1",
                smart_ppt_project_id=88,
                error="额度不足，剩余额度: 0，需要: 1",
                raw={"quota_insufficient": True, "message": "额度不足，剩余额度: 0，需要: 1"},
            )

    monkeypatch.setattr(pixelflow_ppt, "get_ppt_skill", lambda: FakePptSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_ppt.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/ppt/file/start",
            json={
                "smart_ppt_project_id": 88,
                "file_urls": ["https://x/page-1.png"],
            },
        )
        started = start_response.json()

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/ppt/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "quota_paused":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "quota_paused"
    assert status["result"]["quota_insufficient"] is True
    assert "充值后" in status["result"]["message"]
