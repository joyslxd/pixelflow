"""产物质检，纯逻辑实现。

QC 检查的是 GENERATE/EDIT 已经产出的结果，而不是 Brief 计划本身。当前包含两类
检查：

- 片段完整性（阻塞）：每个尝试生成的 segment 都应该有可用 clip。缺失会产生
  ``fail``，让图回到 GENERATE 重试，适合处理第三方偶发失败。
- 时长达标（非阻塞）：剪辑后的总时长应落在 Brief 容忍区间内。重新生成通常不能
  改变 shot 时长，所以不触发重试，只记录 ``warn``。

空 Brief 或没有生成尝试时，完整性检查会自然通过；这种问题属于上游策划/采集，
不是 QC 应该修复的生成缺陷。本模块不做 I/O，方便离线单测。
"""

from __future__ import annotations

import re

from .models import QCItem, QCResult

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _parse_tolerance(spec: str) -> float:
    """从 ``'+2s'`` 这类容忍度字符串中提取秒数。"""
    m = _NUM.search(spec or "")
    return float(m.group()) if m else 0.0


def qc_check(brief: dict, generated_assets: list[dict], timeline: dict) -> QCResult:
    """评估产物是否通过质检。

    覆盖率比较的是 Timeline 中已经装配的 clips 和 GENERATE 实际尝试过的
    ``generated_assets``。当前生成粒度是 segment，不再是单 shot。
    """
    total_segments = len(generated_assets)
    n_clips = len(timeline.get("clips", []))

    checks: list[QCItem] = []

    coverage_ok = n_clips == total_segments  # 两者同为 0 时视为自然通过。
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
