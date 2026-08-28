"""Content-App 视频拆解 HTTP Adapter。"""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from pydantic import JsonValue

from ..port import VideoAnalysisResult


class ContentAppVideoUnderstandingAdapter:
    """调用已确认的 ``decompose_video_to_storyboard`` 合同。"""

    def __init__(self, *, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30)

    async def analyze(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        project_id: int | None = None,
    ) -> VideoAnalysisResult:
        video_url = request.get("video_url")
        if not isinstance(video_url, str) or not video_url.startswith(("https://", "http://127.0.0.1:")):
            raise ValueError("视频拆解只接受受控 HTTPS 视频地址")
        response = await self._client.post(
            f"{self._base_url}/creative/decompose_video_to_storyboard",
            params={} if project_id is None else {"projectId": project_id},
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            json={"video_url": video_url},
        )
        if response.status_code >= 400:
            raise RuntimeError("content_app_video_analysis_failed")
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("task_id"), str):
            raise RuntimeError("content_app_video_analysis_invalid_response")
        task_id = data["task_id"]
        parent_id = data.get("parent_generation_dialog_id")
        return VideoAnalysisResult(
            task_id=task_id,
            parent_generation_dialog_id=parent_id if isinstance(parent_id, str) else None,
            status=str(payload.get("status") or "processing"),
            public_summary="视频拆解任务已提交，完成后将回写分镜分析结果",
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["ContentAppVideoUnderstandingAdapter"]
