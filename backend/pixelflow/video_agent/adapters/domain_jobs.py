"""场景包/参考图领域任务：进程内执行，供 V2 Operation Adapter 使用。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import JsonValue

from pixelflow.generate.scene_packages import (
    prepare_video_scene_packages,
    prepare_video_scene_packages_with_llm,
)

logger = logging.getLogger(__name__)

PrepareRunner = Callable[..., Any]
GenerateRunner = Callable[..., Any]


def _job_id(prefix: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{idempotency_key}".encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _sanitize_json(value: Any, *, depth: int = 0) -> JsonValue:
    """去掉敏感键与带 query 的 URL，满足 Provider 结果安全合同。"""

    if depth > 12:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in {float("inf"), float("-inf")} else None
    if isinstance(value, str):
        stripped = value.strip()
        if "://" in stripped:
            parts = urlsplit(stripped)
            if parts.scheme in {"http", "https"}:
                # Operation 合同禁止 query/fragment/userinfo；保留可展示的 path URL。
                host = parts.netloc.split("@")[-1]
                return urlunsplit((parts.scheme, host, parts.path, "", ""))[:8_000]
        return value[:8_000]
    if isinstance(value, Mapping):
        safe: dict[str, JsonValue] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            lowered = key.casefold()
            if any(token in lowered for token in ("token", "secret", "password", "authorization", "api_key", "credential")):
                continue
            safe[key] = _sanitize_json(child, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item, depth=depth + 1) for item in value[:200]]
    return str(value)[:2_000]


class PrepareScenePackageJobService:
    """把 prepare_video_scene_packages(_with_llm) 包装为 ExistingJobService。"""

    def __init__(
        self,
        *,
        runner: PrepareRunner | None = None,
        use_llm: bool = True,
    ) -> None:
        self._runner = runner
        self._use_llm = use_llm
        self._jobs: dict[str, dict[str, JsonValue]] = {}

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> dict[str, JsonValue]:
        del authorization
        job_id = _job_id("prepare-scene-packages", idempotency_key)
        existing = self._jobs.get(job_id)
        if existing is not None:
            return copy.deepcopy(existing)

        form_values = request.get("form_values")
        plan_markdown = str(request.get("plan_markdown") or "")
        if not isinstance(form_values, Mapping) or not plan_markdown.strip():
            payload: dict[str, JsonValue] = {
                "job_id": job_id,
                "status": "failed",
                "ok": False,
                "message": "prepare_scene_packages 缺少 form_values 或 plan_markdown",
            }
            self._jobs[job_id] = payload
            return copy.deepcopy(payload)

        selected_direction = request.get("selected_direction")
        materials = request.get("materials")
        target_duration_ms = request.get("target_duration_ms")
        shot_source_markdown = str(request.get("shot_source_markdown") or "").strip()
        settings_source_markdown = str(request.get("settings_source_markdown") or "").strip()
        try:
            if self._runner is not None:
                result = await self._invoke_runner(
                    self._runner,
                    form_values=dict(form_values),
                    plan_markdown=plan_markdown,
                    selected_direction=dict(selected_direction) if isinstance(selected_direction, Mapping) else {},
                    materials=list(materials) if isinstance(materials, list) else [],
                    target_duration_ms=int(target_duration_ms) if isinstance(target_duration_ms, int) else 30_000,
                    shot_source_markdown=shot_source_markdown,
                    settings_source_markdown=settings_source_markdown,
                )
            elif self._use_llm:
                result = await prepare_video_scene_packages_with_llm(
                    form_values=dict(form_values),
                    plan_markdown=plan_markdown,
                    selected_direction=dict(selected_direction) if isinstance(selected_direction, Mapping) else {},
                    materials=list(materials) if isinstance(materials, list) else [],
                    target_duration_ms=int(target_duration_ms) if isinstance(target_duration_ms, int) else 30_000,
                    shot_source_markdown=shot_source_markdown,
                    settings_source_markdown=settings_source_markdown,
                )
            else:
                def _run_prepare() -> Any:
                    return prepare_video_scene_packages(
                        dict(form_values),
                        plan_markdown,
                        dict(selected_direction) if isinstance(selected_direction, Mapping) else {},
                        list(materials) if isinstance(materials, list) else [],
                        int(target_duration_ms) if isinstance(target_duration_ms, int) else 30_000,
                        shot_source_markdown=shot_source_markdown,
                        settings_source_markdown=settings_source_markdown,
                    )

                result = await asyncio.to_thread(_run_prepare)
        except Exception:  # noqa: BLE001
            logger.exception("prepare_scene_packages domain job failed")
            payload = {
                "job_id": job_id,
                "status": "failed",
                "ok": False,
                "message": "场景包准备失败",
            }
            self._jobs[job_id] = payload
            return copy.deepcopy(payload)

        payload = {
            "job_id": job_id,
            "status": "succeeded",
            "ok": True,
            "result": _sanitize_json(
                {
                    "ok": True,
                    "global_assets": result.get("global_assets") if isinstance(result, Mapping) else {},
                    "scene_packages": result.get("scene_packages") if isinstance(result, Mapping) else [],
                    "creation_contract": (
                        result.get("creation_contract")
                        if isinstance(result, Mapping) and isinstance(result.get("creation_contract"), Mapping)
                        else None
                    ),
                    "message": str(result.get("message") or "场景包已准备") if isinstance(result, Mapping) else "场景包已准备",
                    "target_duration_ms": (
                        result.get("target_duration_ms")
                        if isinstance(result, Mapping)
                        else None
                    )
                    or (
                        int(target_duration_ms)
                        if isinstance(target_duration_ms, int)
                        else 30_000
                    ),
                }
            ),
        }
        self._jobs[job_id] = payload
        return copy.deepcopy(payload)

    async def status(self, provider_job_id: str) -> dict[str, JsonValue]:
        payload = self._jobs.get(provider_job_id)
        if payload is None:
            return {"job_id": provider_job_id, "status": "expired", "ok": False}
        return copy.deepcopy(payload)

    async def _invoke_runner(self, runner: PrepareRunner, **kwargs: Any) -> Any:
        maybe = runner(**kwargs)
        if asyncio.iscoroutine(maybe) or asyncio.isfuture(maybe):
            return await maybe
        return maybe


class GenerateSceneAssetsJobService:
    """把 generate_scene_assets 包装为 ExistingJobService（需注入 runner）。"""

    def __init__(self, *, runner: GenerateRunner | None = None) -> None:
        self._runner = runner
        self._jobs: dict[str, dict[str, JsonValue]] = {}

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> dict[str, JsonValue]:
        del authorization
        job_id = _job_id("generate-scene-assets", idempotency_key)
        existing = self._jobs.get(job_id)
        if existing is not None:
            return copy.deepcopy(existing)

        if self._runner is None:
            payload: dict[str, JsonValue] = {
                "job_id": job_id or f"generate-scene-assets-{uuid4().hex[:12]}",
                "status": "failed",
                "ok": False,
                "message": "generate_scene_assets runner 未装配",
            }
            self._jobs[job_id] = payload
            return copy.deepcopy(payload)

        try:
            maybe = self._runner(dict(request))
            result = await maybe if asyncio.iscoroutine(maybe) or asyncio.isfuture(maybe) else maybe
        except ValueError as exc:
            # 资产名质量校验等业务错误：公开中文原因，不泄漏堆栈。
            detail = str(exc).strip()[:280] or "参考图生成失败"
            logger.warning("generate_scene_assets domain validation failed: %s", type(exc).__name__)
            payload = {
                "job_id": job_id,
                "status": "failed",
                "ok": False,
                "message": detail,
                # Provider 合同禁止 FAILED 带 result；message 供本进程 status 回读。
            }
            self._jobs[job_id] = payload
            return copy.deepcopy(payload)
        except Exception:  # noqa: BLE001
            logger.exception("generate_scene_assets domain job failed")
            payload = {
                "job_id": job_id,
                "status": "failed",
                "ok": False,
                "message": "参考图生成失败",
            }
            self._jobs[job_id] = payload
            return copy.deepcopy(payload)

        if not isinstance(result, Mapping):
            payload = {
                "job_id": job_id,
                "status": "failed",
                "ok": False,
                "message": "参考图生成结果无效",
            }
            self._jobs[job_id] = payload
            return copy.deepcopy(payload)

        quota_insufficient = bool(result.get("quota_insufficient"))
        business_ok = bool(result.get("ok", True))
        payload = {
            "job_id": job_id,
            # Provider Job 的成功表示领域 runner 已返回可持久化结果；
            # 单个素材失败由 result.ok/failed_assets 表达，不得丢掉部分产物。
            "status": "failed" if quota_insufficient else "succeeded",
            "ok": False if quota_insufficient else True,
            "result": _sanitize_json(
                {
                    "ok": business_ok,
                    "global_assets": result.get("global_assets") or {},
                    "scene_packages": result.get("scene_packages") or [],
                    "failed_assets": result.get("failed_assets") or [],
                    "quota_insufficient": quota_insufficient,
                    "retryable": bool(result.get("retryable")),
                    "message": str(
                        result.get("message")
                        or (
                            "参考图生成完成"
                            if business_ok
                            else "参考图生成失败"
                        )
                    ),
                }
            ),
        }
        self._jobs[job_id] = payload
        return copy.deepcopy(payload)

    async def status(self, provider_job_id: str) -> dict[str, JsonValue]:
        payload = self._jobs.get(provider_job_id)
        if payload is None:
            return {"job_id": provider_job_id, "status": "expired", "ok": False}
        return copy.deepcopy(payload)


def make_generate_scene_assets_runner(
    *,
    image_skill_factory: Callable[[], Any],
    quota_checker: Callable[..., bool],
    workspace_progress: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None = None,
) -> GenerateRunner:
    """网关装配：把 Provider request 接到 generate_scene_assets 领域函数。

    ``workspace_progress(request, payload)`` 在每张参考图完成后回调，用于增量写入 Workspace。
    """

    async def _run(request: Mapping[str, Any]) -> dict[str, Any]:
        from pixelflow.generate.scene_assets import generate_scene_assets as run_generate

        global_assets = request.get("global_assets")
        scene_packages = request.get("scene_packages")
        materials = request.get("materials")
        target_assets = request.get("target_assets")
        model = str(request.get("image_model") or request.get("model") or "gpt-image-2").strip()

        async def on_progress(payload: Mapping[str, Any]) -> None:
            if workspace_progress is None:
                return
            maybe = workspace_progress(request, payload)
            if asyncio.iscoroutine(maybe) or asyncio.isfuture(maybe):
                await maybe

        return await run_generate(
            image_skill=image_skill_factory(),
            global_assets=dict(global_assets) if isinstance(global_assets, Mapping) else {},
            scene_packages=[
                dict(item) for item in scene_packages if isinstance(item, Mapping)
            ]
            if isinstance(scene_packages, list)
            else [],
            materials=[dict(item) for item in materials if isinstance(item, Mapping)]
            if isinstance(materials, list)
            else [],
            image_ratio=str(request.get("image_ratio") or "9:16"),
            image_size=str(request.get("image_size") or "2K"),
            model=model or "gpt-image-2",
            quota_checker=quota_checker,
            # 空列表必须当 None：否则领域层会按「重试目标」过滤掉全部作业。
            target_assets=[
                dict(item) for item in target_assets if isinstance(item, Mapping)
            ]
            if isinstance(target_assets, list) and target_assets
            else None,
            reference_brief=str(request.get("reference_brief") or ""),
            on_progress=on_progress if workspace_progress is not None else None,
        )

    return _run


def make_scene_assets_workspace_progress(
    video_repository: Any,
    *,
    clock: Callable[[], Any] | None = None,
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], Any]:
    """把参考图进度增量写入 VideoWorkspace，供分镜画布逐步展示。"""

    from datetime import datetime, timezone

    from pixelflow.agent_runtime.persistence.repositories import (
        AgentRuntimeRecordConflictError,
    )

    def _now() -> datetime:
        if clock is not None:
            value = clock()
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    def _has_images(assets: Mapping[str, Any], packages: list[dict[str, Any]]) -> bool:
        for key in ("characters", "scenes", "props"):
            items = assets.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                for field in ("images", "three_view_images"):
                    urls = item.get(field)
                    if isinstance(urls, list) and any(str(url or "").strip() for url in urls):
                        return True
        for scene in packages:
            urls = scene.get("image_urls")
            if isinstance(urls, list) and any(str(url or "").strip() for url in urls):
                return True
        return False

    async def _progress(request: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        user_id = str(request.get("user_id") or "").strip()
        workspace_id = str(request.get("workspace_id") or "").strip()
        if not user_id or not workspace_id or video_repository is None:
            return
        assets = payload.get("global_assets")
        packages = payload.get("scene_packages")
        if not isinstance(assets, Mapping):
            return
        assets_dict = dict(assets)
        packages_list = (
            [dict(item) for item in packages if isinstance(item, Mapping)]
            if isinstance(packages, list)
            else []
        )
        if not _has_images(assets_dict, packages_list):
            return
        progress_meta = {
            "completed": int(payload.get("completed") or 0),
            "total": int(payload.get("total") or 0),
            "asset_id": str(payload.get("asset_id") or ""),
            "asset_name": str(payload.get("asset_name") or ""),
            "asset_type": str(payload.get("asset_type") or ""),
            "ok": bool(payload.get("ok")),
        }
        patch = {
            "global_assets": assets_dict,
            "scene_packages": packages_list,
            "scenes": packages_list,
            "scene_asset_progress": progress_meta,
        }
        for _ in range(5):
            workspace = await video_repository.get_workspace(user_id, workspace_id)
            if workspace is None:
                return
            try:
                await video_repository.apply_workspace_patch(
                    user_id,
                    workspace_id,
                    patch,
                    expected_revision=int(workspace.revision),
                    now=_now(),
                )
                return
            except AgentRuntimeRecordConflictError:
                continue
            except Exception:  # noqa: BLE001 - 进度回写失败不打断生图主链路
                logger.exception(
                    "参考图进度回写 Workspace 失败 workspace_id=%s completed=%s/%s",
                    workspace_id,
                    progress_meta.get("completed"),
                    progress_meta.get("total"),
                )
                return

    return _progress
