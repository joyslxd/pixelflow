"""QC verdict schema — the 质检 phase's output contract.

QC inspects the *produced* output (generated clips + assembled Timeline), not
the plan. ``passed`` gates the pipeline: a ``fail`` routes back to GENERATE
(bounded retry); ``warn`` records a quality concern without forcing a retry.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class QCItem(BaseModel):
    item: str
    status: Literal["pass", "fail", "warn"]
    message: str = ""


class QCResult(BaseModel):
    passed: bool
    score: float  # generation coverage, 0..1 (clips produced / shots planned)
    check_results: list[QCItem]
