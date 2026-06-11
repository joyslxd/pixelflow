"""qc_check — verdict over the produced output (pure logic).

Two checks over the GENERATE/EDIT results:

- 片段完整性 (blocking): every attempted segment produced a usable clip. A miss
  ``fail``s so the graph retries GENERATE (the right remediation for a transient
  generation failure). An empty Brief (no segments) passes vacuously — its
  emptiness is an upstream condition, not a generation defect QC should catch.
- 时长达标 (warn): the assembled duration is within the Brief's tolerance.
  Regeneration can't change shot durations, so a miss warns rather than retries.

Pure and deterministic: no I/O, fully testable offline.
"""

from __future__ import annotations

import re

from .models import QCItem, QCResult

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _parse_tolerance(spec: str) -> float:
    """Extract seconds from a tolerance spec like ``'+2s'`` -> ``2.0``."""
    m = _NUM.search(spec or "")
    return float(m.group()) if m else 0.0


def qc_check(brief: dict, generated_assets: list[dict], timeline: dict) -> QCResult:
    """Evaluate the produced output. Coverage compares the assembled clips against
    the segments GENERATE attempted (``generated_assets``), since generation is now
    per-segment, not per-shot."""
    total_segments = len(generated_assets)
    n_clips = len(timeline.get("clips", []))

    checks: list[QCItem] = []

    coverage_ok = n_clips == total_segments  # both 0 -> vacuous pass
    score = 1.0 if total_segments == 0 else n_clips / total_segments
    checks.append(
        QCItem(
            item="片段完整性",
            status="pass" if coverage_ok else "fail",
            message=f"{n_clips}/{total_segments} 个片段生成成功",
        )
    )

    target = brief.get("duration_sec", 0)
    if target:
        actual = timeline.get("total_duration", 0.0)
        tol = _parse_tolerance(brief.get("hard_constraints", {}).get("total_duration_tolerance", "+2s"))
        within = abs(actual - target) <= tol
        checks.append(
            QCItem(
                item="时长达标",
                status="pass" if within else "warn",
                message=f"成片 {actual}s / 目标 {target}s (±{tol}s)",
            )
        )

    passed = not any(c.status == "fail" for c in checks)
    return QCResult(passed=passed, score=round(score, 2), check_results=checks)
