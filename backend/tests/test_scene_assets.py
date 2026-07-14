from __future__ import annotations

import asyncio
from typing import Any

from pixelflow.generate.scene_assets import (
    collect_prop_reference_image_urls,
    collect_uploaded_reference_image_urls,
    enhance_global_asset_edit_prompt,
    enhance_prop_reference_prompt,
    enhance_scene_reference_prompt,
    generate_scene_assets,
    global_asset_edit_ratio,
    resolve_scene_asset_endpoint,
)
from pixelflow.skills import ImageGenerationResult


def test_collect_prop_reference_image_urls_from_materials_and_scene_packages():
    urls = collect_prop_reference_image_urls(
        materials=[
            {"url": "https://x/product.png", "mediaType": "image"},
            {"artifact_url": "https://x/artifact.png"},
            {"url": "https://x/ref.mp4", "mediaType": "video"},
            {"url": "https://x/generated.png", "source": "scene_global_asset"},
        ],
        scene_packages=[{"image_urls": ["https://x/scene-ref.png", "https://x/product.png"]}],
    )
    assert urls == ["https://x/product.png", "https://x/artifact.png", "https://x/scene-ref.png"]


def test_collect_uploaded_reference_image_urls_excludes_scene_global_asset():
    urls = collect_uploaded_reference_image_urls(
        [
            {"url": "https://x/fila1.jpg", "mediaType": "image"},
            {"url": "https://x/fila2.jpg", "mediaType": "image"},
            {"url": "https://x/old-prop.png", "source": "scene_global_asset"},
        ]
    )
    assert urls == ["https://x/fila1.jpg", "https://x/fila2.jpg"]


def test_global_asset_edit_ratio_and_prompt():
    assert global_asset_edit_ratio("scenes") == "9:16"
    assert global_asset_edit_ratio("props") == "1:1"
    assert "参考图" in enhance_global_asset_edit_prompt("更新为新的鞋子", "props")
    assert "场景风格" in enhance_global_asset_edit_prompt("更新场景", "scenes")
    assert enhance_global_asset_edit_prompt("改三视图", "characters") == "改三视图"


def test_enhance_prop_reference_prompt_appends_suffix_once():
    prompt = enhance_prop_reference_prompt("耳机道具图")
    assert prompt.startswith("耳机道具图")
    assert "参考图" in prompt
    assert enhance_prop_reference_prompt(prompt) == prompt


def test_enhance_scene_reference_prompt_appends_suffix_once():
    prompt = enhance_scene_reference_prompt("桌面场景图")
    assert prompt.startswith("桌面场景图")
    assert "场景风格" in prompt
    assert enhance_scene_reference_prompt(prompt) == prompt


def test_resolve_scene_asset_endpoint():
    assert resolve_scene_asset_endpoint(set()) == "/api/picture/text_to_image"
    assert resolve_scene_asset_endpoint({"text_to_image"}) == "/api/picture/text_to_image"
    assert resolve_scene_asset_endpoint({"reference_image"}) == "/api/picture/multi_reference_image_generation"
    assert resolve_scene_asset_endpoint({"text_to_image", "reference_image"}) == "/api/picture/mixed"


def test_generate_scene_assets_passes_all_collected_reference_images_for_props():
    captured: dict[str, Any] = {}

    class FakeImageSkill:
        async def text_to_image(self, **_kwargs):
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/other.png"}], raw={})

        async def reference_image(self, **kwargs):
            captured.update(kwargs)
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/prop.png"}], raw={})

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={"props": [{"asset_id": "prop-product", "image_prompt": "耳机道具图"}]},
            scene_packages=[{"scene_id": "scene-1", "image_urls": ["https://x/scene-ref.png"]}],
            materials=[
                {"url": "https://x/product-a.png", "mediaType": "image"},
                {"artifact_url": "https://x/product-b.png"},
            ],
            image_ratio="9:16",
            image_size="4K",
            model="gpt-image-2",
            quota_checker=lambda _value: False,
        )
    )

    assert result["ok"] is True
    assert captured["reference_images"] == [
        "https://x/product-a.png",
        "https://x/product-b.png",
        "https://x/scene-ref.png",
    ]
    assert captured["model"] == "gpt-image-2"
    assert captured["ratio"] == "9:16"
    assert captured["size"] == "4K"


def test_generate_scene_assets_uses_plan_image_contract_for_every_asset_call():
    calls: list[dict[str, Any]] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append({"method": "text_to_image", **kwargs})
            return ImageGenerationResult(ok=True, images=[{"url": f"https://x/text-{len(calls)}.png"}], raw={})

        async def reference_image(self, **kwargs):
            calls.append({"method": "reference_image", **kwargs})
            return ImageGenerationResult(ok=True, images=[{"url": f"https://x/ref-{len(calls)}.png"}], raw={})

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={
                "characters": [{"asset_id": "character-presenter", "three_view_prompt": "讲解者人物三视图"}],
                "scenes": [{"asset_id": "scene-office", "image_prompt": "办公室场景图"}],
                "props": [{"asset_id": "prop-backpack", "image_prompt": "背包道具图"}],
            },
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            materials=[{"url": "https://x/backpack.png", "mediaType": "image"}],
            image_ratio="9:16",
            image_size="4K",
            model="gpt-image-2",
            quota_checker=lambda _value: False,
        )
    )

    assert result["ok"] is True
    assert len(calls) == 3
    assert {call["method"] for call in calls} == {"text_to_image", "reference_image"}
    assert all(call["ratio"] == "9:16" for call in calls)
    assert all(call["size"] == "4K" for call in calls)
    assert all(call["model"] == "gpt-image-2" for call in calls)


def test_generate_scene_assets_only_retries_target_assets_and_preserves_completed_images():
    calls: list[str] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append(kwargs["prompt"])
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/scene-retried.png"}], raw={})

        async def reference_image(self, **_kwargs):
            raise AssertionError("reference_image should not be called")

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={
                "characters": [
                    {
                        "asset_id": "character-presenter",
                        "three_view_prompt": "讲解者人物三视图",
                        "three_view_images": ["https://x/role-completed.png"],
                    }
                ],
                "scenes": [{"asset_id": "scene-office", "image_prompt": "办公室场景图", "images": []}],
                "props": [
                    {
                        "asset_id": "prop-product",
                        "image_prompt": "产品道具图",
                        "images": ["https://x/prop-completed.png"],
                    }
                ],
            },
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            materials=[],
            image_ratio="9:16",
            image_size="4K",
            model="gpt-image-2",
            quota_checker=lambda _value: False,
            target_assets=[{"asset_id": "scene-office", "asset_type": "scene_image"}],
        )
    )

    assert result["ok"] is True
    assert calls == ["办公室场景图"]
    assert result["global_assets"]["characters"][0]["three_view_images"] == ["https://x/role-completed.png"]
    assert result["global_assets"]["scenes"][0]["images"] == ["https://x/scene-retried.png"]
    assert result["global_assets"]["props"][0]["images"] == ["https://x/prop-completed.png"]


def test_generate_scene_assets_keeps_unattempted_retry_targets_after_quota_pause():
    calls: list[str] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append(kwargs["prompt"])
            return ImageGenerationResult(
                ok=False,
                error="用户没有有效的额度",
                raw={"quota_insufficient": True},
            )

        async def reference_image(self, **_kwargs):
            raise AssertionError("reference_image should not be called")

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={
                "scenes": [{"asset_id": "scene-office", "image_prompt": "办公室场景图", "images": []}],
                "props": [{"asset_id": "prop-product", "image_prompt": "产品道具图", "images": []}],
            },
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            materials=[],
            quota_checker=lambda value: isinstance(value, dict) and value.get("quota_insufficient") is True,
            target_assets=[
                {"asset_id": "scene-office", "asset_type": "scene_image"},
                {"asset_id": "prop-product", "asset_type": "prop_image"},
            ],
        )
    )

    assert result["ok"] is False
    assert result["quota_insufficient"] is True
    assert calls == ["办公室场景图"]
    assert [(item["asset_id"], item.get("retry_pending", False)) for item in result["failed_assets"]] == [
        ("scene-office", False),
        ("prop-product", True),
    ]


def test_generate_scene_assets_records_unattempted_initial_assets_after_quota_pause():
    class FakeImageSkill:
        async def text_to_image(self, **_kwargs):
            return ImageGenerationResult(
                ok=False,
                error="用户没有有效的额度",
                raw={"quota_insufficient": True},
            )

        async def reference_image(self, **_kwargs):
            raise AssertionError("reference_image should not be called")

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={
                "characters": [{"asset_id": "character-presenter", "three_view_prompt": "讲解者人物三视图"}],
                "scenes": [{"asset_id": "scene-office", "image_prompt": "办公室场景图"}],
                "props": [{"asset_id": "prop-product", "image_prompt": "产品道具图"}],
            },
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            quota_checker=lambda value: isinstance(value, dict) and value.get("quota_insufficient") is True,
        )
    )

    assert [item["asset_id"] for item in result["failed_assets"]] == [
        "character-presenter",
        "scene-office",
        "prop-product",
    ]
    assert all(item.get("retry_pending") is True for item in result["failed_assets"][1:])


def test_generate_scene_assets_uses_reference_image_for_props_and_scenes_when_materials_present():
    calls: list[str] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append(f"text:{kwargs['prompt']}")
            if "角色三视图" in kwargs["prompt"]:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/role.png"}], raw={})
            raise AssertionError(f"unexpected text_to_image prompt: {kwargs['prompt']}")

        async def reference_image(self, **kwargs):
            calls.append(f"ref:{kwargs['prompt']}")
            assert kwargs["reference_images"] == ["https://x/product.png"]
            assert kwargs["model"] == "gpt-image-2"
            assert kwargs["ratio"] == "9:16"
            assert kwargs["size"] == "4K"
            if "场景图" in kwargs["prompt"]:
                assert "场景风格" in kwargs["prompt"]
                return ImageGenerationResult(
                    ok=True,
                    images=[{"url": "https://x/scene.png"}],
                    raw={"endpoint": "/api/picture/multi_reference_image_generation"},
                )
            assert "参考图" in kwargs["prompt"]
            return ImageGenerationResult(
                ok=True,
                images=[{"url": "https://x/prop.png"}],
                raw={"endpoint": "/api/picture/multi_reference_image_generation"},
            )

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={
                "characters": [{"asset_id": "character-presenter", "three_view_prompt": "讲解者角色三视图"}],
                "scenes": [{"asset_id": "scene-desk", "image_prompt": "桌面场景图"}],
                "props": [{"asset_id": "prop-product", "image_prompt": "耳机道具图"}],
            },
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            materials=[{"url": "https://x/product.png", "mediaType": "image"}],
            image_ratio="9:16",
            image_size="4K",
            model="gpt-image-2",
            quota_checker=lambda _value: False,
        )
    )

    assert result["ok"] is True
    assert result["endpoint"] == "/api/picture/mixed"
    assert result["global_assets"]["scenes"][0]["images"] == ["https://x/scene.png"]
    assert result["global_assets"]["props"][0]["images"] == ["https://x/prop.png"]
    assert sum(1 for call in calls if call.startswith("ref:")) == 2
    assert any(call.startswith("text:") for call in calls)


def test_generate_scene_assets_falls_back_scene_to_text_to_image_when_reference_fails():
    calls: list[str] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append("text")
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/scene-fallback.png"}], raw={})

        async def reference_image(self, **_kwargs):
            calls.append("ref")
            return ImageGenerationResult(ok=False, error="Task failed", raw={"status": "FAILED", "message": "Task failed"})

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={"scenes": [{"asset_id": "scene-desk", "image_prompt": "桌面场景图"}]},
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            materials=[{"url": "https://x/product.png", "mediaType": "image"}],
            image_size="1080p",
            quota_checker=lambda _value: False,
        )
    )

    assert result["ok"] is True
    assert calls == ["ref", "text"]
    assert result["global_assets"]["scenes"][0]["images"] == ["https://x/scene-fallback.png"]


def test_generate_scene_assets_falls_back_to_text_to_image_when_reference_fails():
    calls: list[str] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append("text")
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/prop-fallback.png"}], raw={})

        async def reference_image(self, **_kwargs):
            calls.append("ref")
            return ImageGenerationResult(ok=False, error="Task failed", raw={"status": "FAILED", "message": "Task failed"})

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={"props": [{"asset_id": "prop-product", "image_prompt": "耳机道具图"}]},
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            materials=[{"url": "https://x/product.png", "mediaType": "image"}],
            image_size="1080p",
            quota_checker=lambda _value: False,
        )
    )

    assert result["ok"] is True
    assert calls == ["ref", "text"]
    assert result["global_assets"]["props"][0]["images"] == ["https://x/prop-fallback.png"]


def test_generate_scene_assets_falls_back_to_text_to_image_without_materials():
    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            if "道具图" in kwargs["prompt"]:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/prop.png"}], raw={})
            if "场景图" in kwargs["prompt"]:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/scene.png"}], raw={})
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/other.png"}], raw={})

        async def reference_image(self, **_kwargs):
            raise AssertionError("reference_image should not be called")

    result = asyncio.run(
        generate_scene_assets(
            image_skill=FakeImageSkill(),
            global_assets={
                "scenes": [{"asset_id": "scene-desk", "image_prompt": "桌面场景图"}],
                "props": [{"asset_id": "prop-product", "image_prompt": "耳机道具图"}],
            },
            scene_packages=[{"scene_id": "scene-1", "scene_index": 1}],
            materials=[],
            image_size="1080p",
            quota_checker=lambda _value: False,
        )
    )

    assert result["ok"] is True
    assert result["endpoint"] == "/api/picture/text_to_image"
    assert result["global_assets"]["props"][0]["images"] == ["https://x/prop.png"]
    assert result["global_assets"]["scenes"][0]["images"] == ["https://x/scene.png"]
