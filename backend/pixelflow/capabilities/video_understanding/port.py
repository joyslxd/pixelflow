"""视频理解能力的稳定 Port，不暴露 Content-App HTTP DTO。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class VideoAnalysisResult:
    """只包含可安全投影的拆解任务摘要。"""

    task_id: str
    parent_generation_dialog_id: str | None
    status: str
    public_summary: str


@runtime_checkable
class VideoUnderstandingPort(Protocol):
    """类似 Java Client Port：提交视频拆解并返回稳定任务身份。"""

    async def analyze(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        project_id: int | None = None,
    ) -> VideoAnalysisResult: ...


__all__ = ["VideoAnalysisResult", "VideoUnderstandingPort"]
