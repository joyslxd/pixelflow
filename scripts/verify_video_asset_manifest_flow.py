from __future__ import annotations

import json
import os
import re
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
TIMELINE_PATTERN = re.compile(r"(?P<prefix>^|[\n。；;！？!?】])\s*(?P<start>\d+)\s*[-~—至]\s*(?P<end>\d+)\s*秒")
ASSET_REFERENCE_PATTERN = re.compile(r"@(?P<asset_id>[A-Za-z0-9_-]+)")
ASSET_USAGE_MARKERS = ("固定", "保持", "参考", "锚定", "锁定", "作为", "用于", "依据", "参照", "统一", "确保", "延续", "基准", "为准", "锚点", "绑定", "一致")


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


def enabled_video_model() -> tuple[str, dict[str, Any], str, str, str]:
    request = urllib.request.Request(
        f"{CONTENT_APP_BASE_URL}/modelParamConfig/listByCategory/video_generate",
        method="GET",
        headers={"Authorization": AUTHORIZATION, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    configs = [item for item in data if isinstance(item, dict) and item.get("isEnabled") is not False] if isinstance(data, list) else []
    seedance_configs = [item for item in configs if "seedance" in str(item.get("modelType") or "").casefold()]
    selected = next(
        (item for item in seedance_configs if item.get("modelType") == "seedance-2.0"),
        seedance_configs[0] if seedance_configs else None,
    )
    if not isinstance(selected, dict):
        raise AssertionError("content-app 未返回已启用的 Seedance 视频模型")
    params = selected.get("paramConfig") if isinstance(selected.get("paramConfig"), dict) else {}
    ratios = [str(value).strip() for value in params.get("aspectRatioList", []) if str(value).strip()]
    sizes = [str(value).strip() for value in params.get("sizeList", []) if str(value).strip()]
    sound_options: list[str] = []
    for value in params.get("onSoundList", []):
        normalized = str(value or "").strip().casefold()
        option = "on" if normalized in {"on", "yes", "true", "1", "开启", "有声"} else "off" if normalized in {"off", "no", "false", "0", "关闭", "静音"} else ""
        if option and option not in sound_options:
            sound_options.append(option)
    durations = [int(value) for value in params.get("videoDurationList", []) if str(value).strip().isdigit()]
    generation_types = [str(value).strip() for value in params.get("modelGenerateTypeList", []) if str(value).strip()]
    upload_types = [str(value).strip() for value in params.get("uploadFileTypeList", []) if str(value).strip()]
    if not generation_types:
        raise AssertionError(f"视频模型缺少实时 generation_types：{selected.get('modelType')}")
    ratio = "9:16" if "9:16" in ratios else (ratios[0] if ratios else "")
    size = "1080p" if "1080p" in sizes else (sizes[0] if sizes else "")
    sound = "on" if "on" in sound_options else (sound_options[0] if sound_options else "off")
    if not ratio or not size:
        raise AssertionError(f"视频模型实时能力不完整：{selected.get('modelType')}, ratios={ratios}, sizes={sizes}")
    return (
        str(selected["modelType"]),
        {
            "generation_types": generation_types,
            "upload_file_types": upload_types,
            "aspect_ratios": ratios,
            "sizes": sizes,
            "sound_options": sound_options,
            "durations_sec": durations,
        },
        ratio,
        size,
        sound,
    )


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
    manifest_ids_by_name = {
        collection: {
            str(item.get("name") or "").strip(): str(item.get("asset_id") or "").strip()
            for item in manifest.get(collection, [])
            if isinstance(item, dict)
        }
        for collection in ("characters", "scenes", "props")
    }
    dimension_markers = {
        "动作": ("动作", "拿起", "抬手", "打开", "转身", "进入", "操作", "使用"),
        "景别": ("景别", "全景", "中景", "近景", "特写", "微距"),
        "运镜": ("运镜", "推近", "拉远", "跟拍", "环绕", "横移", "固定镜头", "稳定器"),
        "光影": ("光影", "光线", "冷光", "暖光", "逆光", "轮廓光", "高光"),
        "声音": ("声音", "音效", "环境声", "对白", "旁白", "音乐", "雨声", "脚步声"),
        "收束": ("收束", "结尾", "镜尾", "定格", "停在", "落版", "淡出", "结束"),
    }
    for position, blueprint in enumerate(blueprints, start=1):
        duration = blueprint.get("duration_sec")
        description = str(blueprint.get("shot_description") or "")
        if not isinstance(duration, int) or isinstance(duration, bool) or not 4 <= duration <= 15:
            raise AssertionError(f"分镜 {position} 时长不是 4-15 秒整数：{duration}")
        if "\n" in description or "\r" in description:
            raise AssertionError(f"分镜 {position} 镜头描述不是一整段中文")
        if re.search(r"(?:\bms\b|毫秒|\d+\.\d+\s*(?:[-~—至]|秒))", description, flags=re.IGNORECASE):
            raise AssertionError(f"分镜 {position} 使用了毫秒或小数时间码")
        ranges = [(int(match.group("start")), int(match.group("end"))) for match in TIMELINE_PATTERN.finditer(description)]
        cursor = 0
        for start, end in ranges:
            if start != cursor or end <= start:
                raise AssertionError(f"分镜 {position} 局部时间码不连续：{ranges}")
            cursor = end
        if not ranges or cursor != duration:
            raise AssertionError(f"分镜 {position} 时间码未从 0 连续覆盖到 {duration} 秒：{ranges}")
        missing_dimensions = [label for label, markers in dimension_markers.items() if not any(marker in description for marker in markers)]
        if missing_dimensions:
            raise AssertionError(f"分镜 {position} 缺少镜头维度：{missing_dimensions}")
        requirements = blueprint.get("asset_requirements") if isinstance(blueprint.get("asset_requirements"), dict) else {}
        allowed_ids = {
            manifest_ids_by_name[collection][str(name)]
            for collection in ("characters", "scenes", "props")
            for name in requirements.get(collection, []) if str(name) in manifest_ids_by_name[collection]
        }
        referenced_ids = {match.group("asset_id") for match in ASSET_REFERENCE_PATTERN.finditer(description)}
        if referenced_ids != allowed_ids:
            raise AssertionError(f"分镜 {position} @引用与 Plan 资产需求不一致：allowed={sorted(allowed_ids)}, actual={sorted(referenced_ids)}")
        if len(referenced_ids) > 9:
            raise AssertionError(f"分镜 {position} 图片引用超过 9 张")
        for asset_id in referenced_ids:
            token = f"@{asset_id}"
            has_usage = any(
                token in clause and any(marker in clause for marker in ASSET_USAGE_MARKERS)
                for clause in re.split(r"[，。；;\n]", description)
            )
            if not has_usage:
                raise AssertionError(f"分镜 {position} 的 @{asset_id} 没有说明引用用途")


def poll_plan_job(job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + PLAN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = request_json("GET", f"/flows/planning/plan/jobs/{urllib.parse.quote(job_id)}", timeout=30)
        if status.get("status") == "completed":
            result = status.get("result")
            if not isinstance(result, dict):
                raise AssertionError("Plan job 终态缺少 result")
            return result
        if status.get("status") == "failed":
            raise RuntimeError(f"Plan job 失败：{status.get('error')}")
        time.sleep(1)
    raise TimeoutError(f"Plan job 超过 {PLAN_TIMEOUT_SECONDS} 秒仍未完成")


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
    blueprints = plan["scene_blueprints"]
    if len(scenes) != len(blueprints):
        raise AssertionError(f"分镜包数量不一致：Plan={len(blueprints)}, 场景包={len(scenes)}")
    for blueprint, scene in zip(blueprints, scenes, strict=True):
        expected_fields = {
            "scene_id": blueprint["scene_id"],
            "scene_index": blueprint["scene_index"],
            "title": blueprint["title"],
            "duration_ms": int(blueprint["duration_sec"]) * 1000,
            "storyline": blueprint["storyline"],
            "narration": blueprint["narration"],
            "transition": blueprint["transition"],
        }
        for field, expected in expected_fields.items():
            if scene.get(field) != expected:
                raise AssertionError(f"分镜 {blueprint['scene_id']} 的 {field} 未继承 Plan：expected={expected!r}, actual={scene.get(field)!r}")
        if (scene.get("shot_description") or {}).get("text") != blueprint["shot_description"]:
            raise AssertionError(f"分镜 {blueprint['scene_id']} 的镜头描述未逐字继承最终 Plan")
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


def download_images(image_records: list[tuple[str, str, str]], directory: Path) -> None:
    for index, (collection, name, url) in enumerate(image_records, start=1):
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".jpg"
        target = directory / f"{index:02d}-{collection}-{name}{suffix}"
        request = urllib.request.Request(url, headers={"User-Agent": "PixelFlow-real-flow-verifier/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            target.write_bytes(response.read())
def main() -> None:
    if not AUTHORIZATION.startswith("Bearer "):
        raise RuntimeError("请通过 PIXELFLOW_REAL_FLOW_AUTHORIZATION 提供 Bearer token；脚本不会把 token 写入文件")
    image_model, image_model_capabilities = enabled_image_model()
    video_model, video_model_capabilities, video_ratio, video_size, video_sound = enabled_video_model()
    form_values = {
        "product_info": "曜石黑城市通勤防水背包",
        "product_category": "箱包",
        "target_audience": "22-35岁城市通勤上班族",
        "conversion_goal": "引导进入直播间了解防水与收纳能力",
        "video_duration_sec": 20,
        "video_ratio": video_ratio,
        "video_model_mode": "system_recommended",
        "video_model": video_model,
        "video_model_capabilities": video_model_capabilities,
        "video_size": video_size,
        "video_sound": video_sound,
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
    started_plan = request_json(
        "POST",
        "/flows/planning/plan/start",
        {
            "intent": "video",
            "form_values": form_values,
            "selected_direction": direction,
            "product_creative_profile": {"industry": "箱包", "constraints": ["两镜连续", "资产跨镜复用"]},
            "intake_context": {"original_prompt": direction["description"]},
            "materials": [],
        },
    )
    plan = poll_plan_job(str(started_plan["job_id"]))
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
    artifact_directory = Path(tempfile.mkdtemp(prefix="pixelflow-real-seedance-plan-"))
    download_images(image_records, artifact_directory)
    (artifact_directory / "plan.md").write_text(str(plan["plan_markdown"]), encoding="utf-8")
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
        "artifact_directory": str(artifact_directory),
    }
    report_path = artifact_directory / "verification-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - CLI verifier needs one readable failure boundary
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise
