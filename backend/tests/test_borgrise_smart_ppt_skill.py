from __future__ import annotations

import asyncio

from pixelflow.skills.borgrise import run_generation
from pixelflow.skills.borgrise.skill import BorgriseSkill


def test_generate_ppt_summary_submits_and_polls_content_app_contract(monkeypatch):
    captured: dict[str, object] = {}

    def fake_make_request(endpoint, data=None, **kwargs):
        captured["endpoint"] = endpoint
        captured["data"] = data
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "data": {
                "taskId": "task-summary-1",
                "smartPptProjectId": 88,
                "status": "processing",
            },
        }

    def fake_poll_task(task_id, timeout=None, *, default_timeout=None):
        assert task_id == "task-summary-1"
        captured["default_timeout"] = default_timeout
        return {
            "success": True,
            "data": {
                "status": "completed",
                "result": {"summary": "# 汇报大纲\n## P1. 封面"},
            },
        }

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)
    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    result = run_generation.generate_ppt_summary(
        topic="绿色供应链转型汇报",
        ppt_style="极简商务",
        file_urls=["https://x/report.docx?token=abc", "https://x/data.xlsx"],
    )

    assert captured["endpoint"] == "/picture/smart-ppt/generatePptSummary"
    assert captured["data"] == {
        "topic": "绿色供应链转型汇报",
        "pptStyle": "极简商务",
        "fileUrls": ["https://x/report.docx?token=abc", "https://x/data.xlsx"],
    }
    assert captured["default_timeout"] == run_generation.PPT_POLL_TIMEOUT
    assert result["endpoint"] == "/api/picture/smart-ppt/generatePptSummary"
    assert result["task_id"] == "task-summary-1"
    assert result["smart_ppt_project_id"] == 88
    assert result["summary"] == "# 汇报大纲\n## P1. 封面"


def test_generate_ppt_summary_reads_nested_task_result_data(monkeypatch):
    monkeypatch.setattr(
        run_generation,
        "make_request",
        lambda *_args, **_kwargs: {"success": True, "data": {"taskId": "task-summary-2", "smartPptProjectId": 27}},
    )
    monkeypatch.setattr(
        run_generation,
        "poll_task",
        lambda *_args, **_kwargs: {
            "success": True,
            "data": {
                "status": "completed",
                "result": {
                    "data": {"summary": "# AI零售运营月度复盘PPT\n\n## P1. 封面"},
                    "message": "智能PPT大纲生成成功",
                },
            },
        },
    )

    result = run_generation.generate_ppt_summary(
        topic="AI零售运营月度复盘PPT",
        ppt_style="科技数据",
        file_urls=["https://x/report.pdf"],
    )

    assert result["summary"].startswith("# AI零售运营月度复盘PPT")
    assert result["smart_ppt_project_id"] == 27


def test_generate_ppt_image_normalizes_string_task_result(monkeypatch):
    monkeypatch.setattr(
        run_generation,
        "make_request",
        lambda *_args, **_kwargs: {"success": True, "data": {"taskId": "task-image-1", "smartPptProjectId": 88}},
    )
    monkeypatch.setattr(
        run_generation,
        "poll_task",
        lambda *_args, **_kwargs: {
            "success": True,
            "data": {
                "status": "completed",
                "result": "https://x/ppt-page-1.png",
            },
        },
    )

    result = run_generation.generate_ppt_image(json_content='{"page_index":1}', smart_ppt_project_id=88)

    assert result["image_url"] == "https://x/ppt-page-1.png"
    assert result["smart_ppt_project_id"] == 88


def test_generate_ppt_content_json_keeps_explicit_pages_and_downgrades_subheadings(monkeypatch):
    captured: dict[str, object] = {}

    def fake_make_request(endpoint, data=None, **kwargs):
        captured["endpoint"] = endpoint
        captured["data"] = data
        return {"success": True, "data": {"taskId": "task-content-1", "smartPptProjectId": 88}}

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)
    monkeypatch.setattr(
        run_generation,
        "poll_task",
        lambda *_args, **_kwargs: {
            "success": True,
            "data": {"status": "completed", "result": {"contentJson": [{"title": "P1"}]}},
        },
    )

    outline = "# 发布会\n## P1. 封面\n### 页面说明\n### 视觉要求\n## P2. 产品\n### 卖点"
    result = run_generation.generate_ppt_content_json(outline, "极简商务", 88)

    assert result["content_json"] == [{"title": "P1"}]
    assert captured["endpoint"] == "/picture/smart-ppt/generatePptContentToJson"
    assert captured["data"]["originalOutline"] == (
        "# 发布会\n## P1. 封面\n**页面说明**\n**视觉要求**\n## P2. 产品\n**卖点**"
    )


def test_borgrise_smart_ppt_skill_maps_file_result(monkeypatch):
    def fake_generate_ppt_file(**kwargs):
        assert kwargs == {"file_urls": ["https://x/1.png"], "smart_ppt_project_id": 88}
        return {
            "success": True,
            "task_id": "task-file-1",
            "smart_ppt_project_id": 88,
            "ppt_url": "https://x/result.pptx",
            "filename": "result.pptx",
            "slide_count": 1,
            "endpoint": "/api/picture/smart-ppt/generatePptFile",
        }

    monkeypatch.setattr(run_generation, "generate_ppt_file", fake_generate_ppt_file)

    result = asyncio.run(BorgriseSkill().generate_ppt_file(file_urls=["https://x/1.png"], smart_ppt_project_id=88))

    assert result.ok is True
    assert result.task_id == "task-file-1"
    assert result.smart_ppt_project_id == 88
    assert result.ppt_url == "https://x/result.pptx"
    assert result.filename == "result.pptx"
    assert result.slide_count == 1
