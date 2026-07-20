from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = os.getenv("PIXELFLOW_REAL_FLOW_BASE_URL", "http://127.0.0.1:8001/agent").rstrip("/")
CONTENT_APP_BASE_URL = os.getenv("PIXELFLOW_REAL_FLOW_CONTENT_APP_BASE_URL", "https://test-video.borgrise.com/api").rstrip("/")
AUTHORIZATION = os.getenv("PIXELFLOW_REAL_FLOW_AUTHORIZATION", "").strip()
POLL_TIMEOUT_SECONDS = int(os.getenv("PIXELFLOW_REAL_FLOW_POLL_TIMEOUT_SECONDS", "1200"))
PLAN_TIMEOUT_SECONDS = int(os.getenv("PIXELFLOW_REAL_FLOW_PLAN_TIMEOUT_SECONDS", "900"))


def request_json(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 300) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": AUTHORIZATION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail[:2000]}") from exc


def enabled_image_model() -> tuple[str, dict[str, list[str]]]:
    request = urllib.request.Request(
        f"{CONTENT_APP_BASE_URL}/modelParamConfig/listByCategory/image_generate",
        method="GET",
        headers={"Authorization": AUTHORIZATION, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    configs = [item for item in data if isinstance(item, dict) and item.get("isEnabled") is not False] if isinstance(data, list) else []
    selected = next((item for item in configs if item.get("modelType") == "gpt-image-2"), configs[0] if configs else None)
    if not isinstance(selected, dict):
        raise AssertionError("content-app 未返回可用图片模型")
    params = selected.get("paramConfig") if isinstance(selected.get("paramConfig"), dict) else {}
    ratios = [str(value) for value in params.get("aspectRatioList", []) if str(value).strip()]
    sizes = [str(value) for value in params.get("sizeList", []) if str(value).strip()]
    if "9:16" not in ratios or not sizes:
        raise AssertionError(f"图片模型不支持本次 9:16 验收：{selected.get('modelType')}, ratios={ratios}, sizes={sizes}")
    return str(selected["modelType"]), {"aspect_ratios": ratios, "sizes": sizes}


def manifest_names(manifest: dict[str, Any], collection: str) -> list[str]:
    return [str(item.get("name") or "").strip() for item in manifest.get(collection, []) if isinstance(item, dict)]


def referenced_names(blueprints: list[dict[str, Any]], collection: str) -> list[str]:
    result: list[str] = []
    for blueprint in blueprints:
        requirements = blueprint.get("asset_requirements") if isinstance(blueprint, dict) else None
        values = requirements.get(collection) if isinstance(requirements, dict) else None
        for value in values if isinstance(values, list) else []:
            name = str(value or "").strip()
            if name and name not in result:
                result.append(name)
    return result


def assert_plan_contract(plan: dict[str, Any]) -> None:
    if not plan.get("llm_used"):
        raise AssertionError(f"真实 Plan 未使用 LLM：{plan.get('error')}")
    markdown = str(plan.get("plan_markdown") or "")
    for heading in ("## 四、全局资产清单", "### 4.1 出场角色列表", "### 4.2 道具列表", "### 4.3 场景列表"):
        if heading not in markdown:
            raise AssertionError(f"plan.md 缺少章节：{heading}")
    manifest = plan.get("asset_manifest")
    blueprints = plan.get("scene_blueprints")
    if not isinstance(manifest, dict) or not isinstance(blueprints, list) or not blueprints:
        raise AssertionError("Plan 缺少结构化 asset_manifest 或 scene_blueprints")
    all_names: list[str] = []
    for collection in ("characters", "scenes", "props"):
        names = manifest_names(manifest, collection)
        expected = referenced_names(blueprints, collection)
        if names != expected:
            raise AssertionError(f"{collection} 清单与分镜引用不一致：manifest={names}, blueprints={expected}")
        all_names.extend(names)
        for name in names:
            if f"名称：{name}" not in markdown:
                raise AssertionError(f"plan.md 未按正式名称渲染资产：{name}")
    normalized = ["".join(name.split()).casefold() for name in all_names]
    if len(normalized) != len(set(normalized)):
        raise AssertionError("Plan 三类资产名称不是全局唯一")
    expected_counts = {"characters": 2, "scenes": 1, "props": 3}
    expected_fragments = {
        "characters": ("林晓", "陈默"),
        "scenes": ("雨夜公交站",),
        "props": ("背包", "雨伞", "保温杯"),
    }
    for collection, expected_count in expected_counts.items():
        names = manifest_names(manifest, collection)
        if len(names) != expected_count:
            raise AssertionError(f"{collection} 未保留用户要求的精确资产数量：expected={expected_count}, actual={names}")
        for fragment in expected_fragments[collection]:
            if not any(fragment in name for name in names):
                raise AssertionError(f"{collection} 丢失用户明确命名资产：{fragment}；actual={names}")
    generic_names = {"目标用户", "人物角色", "真实使用场景", "产品", "主要产品"}
    if generic_names.intersection(all_names):
        raise AssertionError(f"Plan 使用了泛化占位资产：{sorted(generic_names.intersection(all_names))}")


def poll_scene_package_job(job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last_stage = ""
    while time.monotonic() < deadline:
        status = request_json("GET", f"/flows/video/prepare-scene-packages/jobs/{urllib.parse.quote(job_id)}", timeout=30)
        stage = str(status.get("stage") or "")
        if stage != last_stage:
            print(json.dumps({"job_id": job_id, "status": status.get("status"), "stage": stage}, ensure_ascii=False), flush=True)
            last_stage = stage
        if status.get("status") in {"completed", "quota_paused", "failed"}:
            if status.get("status") == "failed":
                raise RuntimeError(f"场景包 job 失败：{status.get('error')}")
            result = status.get("result")
            if not isinstance(result, dict):
                raise AssertionError("场景包 job 终态缺少 result")
            return result
        time.sleep(2)
    raise TimeoutError(f"场景包 job 超过 {POLL_TIMEOUT_SECONDS} 秒仍未完成")


def first_image_url(asset: dict[str, Any], collection: str) -> str:
    field = "three_view_images" if collection == "characters" else "images"
    values = asset.get(field)
    if not isinstance(values, list) or len(values) != 1:
        raise AssertionError(f"资产 {asset.get('name')} 的 {field} 必须恰好有一个 URL，实际为 {values}")
    return str(values[0])


def assert_scene_package_contract(plan: dict[str, Any], scene_package_result: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str, str]]]:
    packages = scene_package_result.get("videoScenePackages")
    if not isinstance(packages, dict):
        raise AssertionError("job result 缺少 videoScenePackages")
    failures = scene_package_result.get("sceneAssetFailures")
    if failures:
        raise AssertionError(f"场景资产生成存在失败：{json.dumps(failures, ensure_ascii=False)[:3000]}")
    global_assets = packages.get("global_assets")
    scenes = packages.get("scene_packages")
    if not isinstance(global_assets, dict) or not isinstance(scenes, list):
        raise AssertionError("场景包缺少 global_assets 或 scene_packages")
    manifest = plan["asset_manifest"]
    image_records: list[tuple[str, str, str]] = []
    canonical_names: dict[str, str] = {}
    for collection in ("characters", "scenes", "props"):
        expected_items = manifest.get(collection, [])
        actual_items = global_assets.get(collection, [])
        if not isinstance(actual_items, list) or len(actual_items) != len(expected_items):
            raise AssertionError(f"{collection} 数量不一致：Plan={len(expected_items)}, 场景包={len(actual_items) if isinstance(actual_items, list) else 'invalid'}")
        prompt_field = "three_view_prompt" if collection == "characters" else "image_prompt"
        for expected, actual in zip(expected_items, actual_items, strict=True):
            for field in ("asset_id", "name", "description", prompt_field):
                if actual.get(field) != expected.get(field):
                    raise AssertionError(f"{collection}.{field} 未严格继承 Plan：expected={expected.get(field)!r}, actual={actual.get(field)!r}")
            canonical_names[str(actual["asset_id"])] = str(actual["name"])
            image_records.append((collection, str(actual["name"]), first_image_url(actual, collection)))
    for scene in scenes:
        mentions = (scene.get("shot_description") or {}).get("mentions")
        if not isinstance(mentions, list):
            raise AssertionError(f"分镜 {scene.get('scene_id')} 缺少 mentions")
        for mention in mentions:
            asset_id = str(mention.get("asset_id") or "")
            if asset_id not in canonical_names:
                raise AssertionError(f"分镜引用了 Plan 外资产：{asset_id}")
            if mention.get("name") != canonical_names[asset_id]:
                raise AssertionError(f"分镜 @ 名称不一致：{mention}")
    return packages, image_records


def download_images(image_records: list[tuple[str, str, str]]) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="pixelflow-real-asset-manifest-"))
    for index, (collection, name, url) in enumerate(image_records, start=1):
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".jpg"
        target = directory / f"{index:02d}-{collection}-{name}{suffix}"
        request = urllib.request.Request(url, headers={"User-Agent": "PixelFlow-real-flow-verifier/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            target.write_bytes(response.read())
    return directory


def main() -> None:
    if not AUTHORIZATION.startswith("Bearer "):
        raise RuntimeError("请通过 PIXELFLOW_REAL_FLOW_AUTHORIZATION 提供 Bearer token；脚本不会把 token 写入文件")
    image_model, image_model_capabilities = enabled_image_model()
    form_values = {
        "product_info": "曜石黑城市通勤防水背包",
        "product_category": "箱包",
        "target_audience": "22-35岁城市通勤上班族",
        "conversion_goal": "引导进入直播间了解防水与收纳能力",
        "video_duration_sec": 20,
        "video_ratio": "9:16",
        "video_model_mode": "system_recommended",
        "video_model": "seedance-2.0",
        "video_size": "1080p",
        "video_sound": "on",
        "image_model": image_model,
        "image_model_capabilities": image_model_capabilities,
        "video_usage": "电商商品宣传",
        "visual_style": "真实电影感雨夜广告",
    }
    direction = {
        "direction_id": "real-flow-rain-commute",
        "title": "雨夜通勤双人接力实测",
        "description": (
            "用两个连续分镜讲述林晓和陈默在同一雨夜公交站接力验证背包防水与收纳。"
            "林晓始终穿浅灰风衣，陈默始终穿藏蓝夹克；两个分镜都复用同一个曜石黑防水背包、透明雨伞和银色保温杯，"
            "不得增加其他角色、场景或道具。"
        ),
    }
    plan = request_json(
        "POST",
        "/flows/planning/plan",
        {
            "intent": "video",
            "form_values": form_values,
            "selected_direction": direction,
            "product_creative_profile": {"industry": "箱包", "constraints": ["两镜连续", "资产跨镜复用"]},
            "intake_context": {"original_prompt": direction["description"]},
            "materials": [],
        },
        timeout=PLAN_TIMEOUT_SECONDS,
    )
    assert_plan_contract(plan)
    print(
        json.dumps(
            {
                "plan_version": plan.get("plan_version"),
                "scene_count": len(plan["scene_blueprints"]),
                "asset_names": {collection: manifest_names(plan["asset_manifest"], collection) for collection in ("characters", "scenes", "props")},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    started = request_json(
        "POST",
        "/flows/video/prepare-scene-packages/start",
        {
            "form_values": form_values,
            "plan_markdown": plan["plan_markdown"],
            "selected_direction": direction,
            "materials": [],
            "target_duration_ms": 20_000,
            "creation_contract": plan["creation_contract"],
            "scene_blueprints": plan["scene_blueprints"],
            "asset_manifest": plan["asset_manifest"],
        },
    )
    job_result = poll_scene_package_job(str(started["job_id"]))
    packages, image_records = assert_scene_package_contract(plan, job_result)
    image_directory = download_images(image_records)
    report = {
        "ok": True,
        "plan_version": plan.get("plan_version"),
        "scene_count": len(packages.get("scene_packages") or []),
        "asset_count": len(image_records),
        "assets": [{"collection": collection, "name": name, "url": url} for collection, name, url in image_records],
        "plan_asset_manifest": plan["asset_manifest"],
        "scene_blueprints": plan["scene_blueprints"],
        "scene_packages": packages.get("scene_packages") or [],
        "consistency_issues": plan.get("consistency_issues") or [],
        "image_directory": str(image_directory),
    }
    report_path = image_directory / "verification-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - CLI verifier needs one readable failure boundary
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise
