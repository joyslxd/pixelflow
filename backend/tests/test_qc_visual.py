from __future__ import annotations

import asyncio

from pixelflow.qc.visual import product_consistency_check


class _FakeArkClient:
    def __init__(self):
        self.payloads = []

    def chat_completions(self, payload):
        self.payloads.append(payload)
        return {"choices": [{"message": {"content": '{"status":"pass","message":"商品颜色和结构一致"}'}}]}


def test_product_consistency_check_builds_multimodal_payload(monkeypatch):
    monkeypatch.setattr("pixelflow.qc.visual._sample_frame_urls", lambda path, duration=None: ["data:image/jpeg;base64,FRAME"])
    client = _FakeArkClient()

    item = asyncio.run(
        product_consistency_check(
            product_image_url="data:image/jpeg;base64,PRODUCT",
            final_video_url="/tmp/final.mp4",
            brief={"global_visual": {"subject_type": "水杯"}},
            video_duration=5,
            client=client,
        )
    )

    assert item.status == "pass"
    assert item.item == "产品一致性/变形"
    content = client.payloads[0]["messages"][0]["content"]
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,PRODUCT"
    assert content[2]["image_url"]["url"] == "data:image/jpeg;base64,FRAME"


def test_product_consistency_check_degrades_without_frames(monkeypatch):
    monkeypatch.setattr("pixelflow.qc.visual._sample_frame_urls", lambda path, duration=None: [])

    item = asyncio.run(
        product_consistency_check(
            product_image_url="https://example.com/product.jpg",
            final_video_url="/tmp/missing.mp4",
            brief={},
        )
    )

    assert item.status == "warn"
    assert "未能抽取" in item.message
