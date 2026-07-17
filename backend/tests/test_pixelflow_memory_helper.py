from __future__ import annotations

import asyncio
import logging

import pytest

from app.gateway.pixelflow_memory import current_user_id, record_power_mem, record_power_mem_background
from pixelflow.memory import PowerMemConfig, PowerMemService


@pytest.mark.asyncio
async def test_record_power_mem_defaults_to_infer_false():
    """结构化业务摘要默认走 infer=False，避免服务端 ~36s 的 LLM 抽取把后台写入拖超时。"""

    class FakePowerMemService:
        def __init__(self):
            self.records = []

        async def record(self, **kwargs):
            self.records.append(kwargs)
            return True

    service = FakePowerMemService()
    ok = await record_power_mem(
        service,
        user_id="u1",
        content="图片生成 Agent 完成同步生成",
        category="experience",
        source_agent="image_generation_agent",
    )

    assert ok is True
    assert service.records[0]["infer"] is False


@pytest.mark.asyncio
async def test_record_power_mem_defaults_to_infer_true_for_preference():
    """用户偏好需要让 PowerMem 侧抽取语义，便于中文偏好进入向量检索。"""

    class FakePowerMemService:
        def __init__(self):
            self.records = []

        async def record(self, **kwargs):
            self.records.append(kwargs)
            return True

    service = FakePowerMemService()
    ok = await record_power_mem(
        service,
        user_id="u1",
        content="以后默认真实摄影风格，不要价格文字",
        category="preference",
        source_agent="preference_api",
        memory_type="preference",
    )

    assert ok is True
    assert service.records[0]["infer"] is True


@pytest.mark.asyncio
async def test_record_power_mem_background_defaults_to_infer_false():
    class FakePowerMemService:
        def __init__(self):
            self.records = []

        async def record(self, **kwargs):
            self.records.append(kwargs)
            return True

    service = FakePowerMemService()
    record_power_mem_background(
        service,
        user_id="u1",
        content="采集 Agent 完成意图识别",
        category="experience",
        source_agent="intake_agent",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert service.records[0]["infer"] is False


@pytest.mark.asyncio
async def test_record_power_mem_background_defaults_to_infer_true_for_preference():
    class FakePowerMemService:
        def __init__(self):
            self.records = []

        async def record(self, **kwargs):
            self.records.append(kwargs)
            return True

    service = FakePowerMemService()
    record_power_mem_background(
        service,
        user_id="u1",
        content="以后默认真实摄影风格，不要价格文字",
        category="preference",
        source_agent="preference_api",
        memory_type="preference",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert service.records[0]["infer"] is True


@pytest.mark.asyncio
async def test_record_power_mem_background_dual_writes_skill_memory_for_skill_sources():
    class FakePowerMemService:
        def __init__(self):
            self.records = []

        async def record(self, **kwargs):
            self.records.append(kwargs)
            return True

    service = FakePowerMemService()

    record_power_mem_background(
        service,
        user_id="u1",
        content="剪映草稿 Agent 异步任务结束；stage=jianying_draft；ok=True",
        category="experience",
        source_agent="jianying_draft_agent",
        metadata={"source": "video_jianying_draft_job"},
        memory_type="experience",
        infer=False,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [record["category"] for record in service.records] == ["experience", "skill"]
    assert service.records[0]["infer"] is False
    assert service.records[1]["memory_type"] == "skill"
    assert service.records[1]["metadata"]["linked_category"] == "experience"
    assert "可复用 Skill 经验" in service.records[1]["content"]


@pytest.mark.asyncio
async def test_record_power_mem_background_is_cancelled_and_awaited_when_service_closes():
    started = asyncio.Event()
    release = asyncio.Event()
    finalized = asyncio.Event()
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test"),
        http_client=object(),
    )

    async def blocking_record(**kwargs):
        started.set()
        try:
            await release.wait()
        finally:
            finalized.set()
        return True

    service.record = blocking_record
    record_power_mem_background(
        service,
        user_id="u1",
        content="需要由服务关闭回收的后台任务",
        category="experience",
        source_agent="intake_agent",
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    await service.aclose()
    was_finalized_by_close = finalized.is_set()
    if not was_finalized_by_close:
        release.set()
        await asyncio.wait_for(finalized.wait(), timeout=1)
        await asyncio.sleep(0)

    assert was_finalized_by_close
    assert not getattr(service, "_background_tasks", set())


@pytest.mark.asyncio
async def test_record_power_mem_background_error_log_does_not_expose_exception_text_or_traceback(
    caplog: pytest.LogCaptureFixture,
):
    private_error_text = "provider-private-response-7f2a"

    class FakePowerMemService:
        async def record(self, **kwargs):
            raise RuntimeError(private_error_text)

    with caplog.at_level(logging.WARNING, logger="app.gateway.pixelflow_memory"):
        record_power_mem_background(
            FakePowerMemService(),
            user_id="u1",
            content="用户不希望出现在日志里的内容",
            category="experience",
            source_agent="intake_agent",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "PowerMem background record failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert private_error_text not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_current_user_id_fail_open_log_does_not_expose_auth_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    private_error_text = "auth-private-detail-cc81"

    async def failing_get_current_user(request):
        raise RuntimeError(private_error_text)

    monkeypatch.setattr("app.gateway.pixelflow_memory.get_current_user", failing_get_current_user)
    with caplog.at_level(logging.DEBUG, logger="app.gateway.pixelflow_memory"):
        user_id = await current_user_id(object())

    assert user_id is None
    assert "Unable to resolve current user for PowerMem" in caplog.text
    assert "RuntimeError" in caplog.text
    assert private_error_text not in caplog.text
    assert "Traceback" not in caplog.text
