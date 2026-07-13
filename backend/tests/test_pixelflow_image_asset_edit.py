from __future__ import annotations

import asyncio
from typing import Any

from app.gateway.routers import pixelflow_image
from pixelflow.skills import ImageGenerationResult


def test_image_asset_edit_requests_default_to_gpt_image_2():
    edit = pixelflow_image.ImageAssetEditRequest(
        asset_id="asset-1",
        asset_group="props",
        source_image_url="https://x/source.png",
        prompt="update",
    )
    fusion = pixelflow_image.ImageAssetFusionRequest(
        asset_id="asset-1",
        asset_group="props",
        source_image_url="https://x/source.png",
        prompt="update",
    )

    assert edit.model == "gpt-image-2"
    assert fusion.model == "gpt-image-2"


def test_edit_asset_reference_branch_uses_confirmed_model_ratio_and_size(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeImageSkill:
        async def reference_image(self, **kwargs):
            captured.update(kwargs)
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/edited.png"}], raw={})

        async def image_edit(self, **_kwargs):  # pragma: no cover - should not be called
            raise AssertionError("image_edit fallback should not run")

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    result = asyncio.run(
        pixelflow_image._edit_image_asset_response(
            pixelflow_image.ImageAssetEditRequest(
                asset_id="asset-1",
                asset_group="props",
                source_image_url="https://x/source.png",
                prompt="make it blue",
                materials=[{"url": "https://x/ref.png", "type": "image"}],
                model="gpt-image-2",
                ratio="16:9",
                size="4K",
            )
        )
    )

    assert result.ok is True
    assert captured["model"] == "gpt-image-2"
    assert captured["ratio"] == "16:9"
    assert captured["size"] == "4K"


def test_asset_reference_and_fusion_keep_explicit_square_ratio(monkeypatch):
    captured_reference: dict[str, Any] = {}
    captured_fusion: dict[str, Any] = {}

    class FakeImageSkill:
        async def reference_image(self, **kwargs):
            captured_reference.update(kwargs)
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/edited.png"}], raw={})

        async def image_edit(self, **_kwargs):  # pragma: no cover - should not be called
            raise AssertionError("image_edit fallback should not run")

        async def multi_image_fusion(self, **kwargs):
            captured_fusion.update(kwargs)
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/fused.png"}], raw={})

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    edit_result = asyncio.run(
        pixelflow_image._edit_image_asset_response(
            pixelflow_image.ImageAssetEditRequest(
                asset_id="scene-1",
                asset_group="scenes",
                source_image_url="https://x/source.png",
                prompt="make it brighter",
                materials=[{"url": "https://x/ref.png", "type": "image"}],
                ratio="1:1",
                size="2K",
            )
        )
    )
    fusion_result = asyncio.run(
        pixelflow_image._fuse_image_asset_response(
            pixelflow_image.ImageAssetFusionRequest(
                asset_id="scene-1",
                asset_group="scenes",
                source_image_url="https://x/source.png",
                prompt="blend style",
                materials=[{"url": "https://x/ref.png", "type": "image"}],
                ratio="1:1",
                size="2K",
            )
        )
    )

    assert edit_result.ok is True
    assert fusion_result.ok is True
    assert captured_reference["ratio"] == "1:1"
    assert captured_reference["size"] == "2K"
    assert captured_fusion["ratio"] == "1:1"
    assert captured_fusion["size"] == "2K"
