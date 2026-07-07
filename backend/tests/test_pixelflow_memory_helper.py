from __future__ import annotations

import asyncio

import pytest

from app.gateway.pixelflow_memory import record_power_mem, record_power_mem_background


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
        content="图片生成 Agent 完成同步生成；endpoint=/api/picture/text_to_image；ok=True",
        category="experience",
        source_agent="image_generation_agent",
        metadata={"source": "image_generate", "endpoint": "/api/picture/text_to_image"},
        memory_type="experience",
        infer=False,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [record["category"] for record in service.records] == ["experience", "skill"]
    assert service.records[1]["memory_type"] == "skill"
    assert service.records[1]["metadata"]["linked_category"] == "experience"
    assert "可复用 Skill 经验" in service.records[1]["content"]
