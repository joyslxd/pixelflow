"""demand_integrity_check — pure-logic completeness gate (PRD §8.7).

No API calls. Runs the fixed checklist before the 采集 → 策划 hand-off:
six blocking checks (``fail``) plus three non-blocking risk checks (``warn``).
``is_complete`` is true only when no blocking check fails. Operates on plain
dicts because state may hold partial demand mid-collection.
"""

from __future__ import annotations

from .models import IntegrityItem, IntegrityResult


def _has(value) -> bool:
    """True when a field carries usable content (non-empty / non-None)."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def demand_integrity_check(
    product_info: dict | None,
    video_params: dict | None,
    creative_direction: dict | None = None,
    reference_videos: list | None = None,
) -> IntegrityResult:
    pi = product_info or {}
    vp = video_params or {}
    cd = creative_direction or {}
    items: list[IntegrityItem] = []

    # -- Blocking checks (fail) --
    items.append(_check("商品名称", _has(pi.get("product_name")), "请提供商品名称"))
    items.append(_check("商品图片", _has(pi.get("main_image_url")), "请至少上传 1 张商品图片"))
    core_message = _has(cd.get("core_message")) or _has(pi.get("core_message")) or _has(vp.get("business_goal"))
    items.append(_check("核心诉求", core_message, "请确认视频的核心宣传目标"))
    items.append(_check("平台", _has(vp.get("platform")), "请选择目标投放平台"))
    items.append(_check("时长", _has(vp.get("video_duration_sec")), "请选择视频时长"))
    items.append(_check("创意方向", _has(cd.get("creative_style")), "请确认创意方向"))

    # -- Non-blocking risk checks (warn) --
    if _has(pi.get("main_image_url")) and not _has(pi.get("cleaned_assets")):
        items.append(IntegrityItem(item="图片清洗", status="warn", message="商品图片清洗后台异步处理中", action="无需等待，可继续"))
    if not _has(pi.get("price")):
        items.append(IntegrityItem(item="价格缺失", status="warn", message="无价格信息，Brief 不会生成价格相关内容", action="如需价格请补充"))
    if reference_videos:
        pending = [r for r in reference_videos if (r or {}).get("status") != "done"]
        if pending:
            items.append(IntegrityItem(item="参考视频", status="warn", message="参考视频下载未完成，异步等待中", action="无需等待，可继续"))

    is_complete = not any(c.status == "fail" for c in items)
    return IntegrityResult(is_complete=is_complete, check_results=items)


def _check(item: str, ok: bool, action: str) -> IntegrityItem:
    if ok:
        return IntegrityItem(item=item, status="pass")
    return IntegrityItem(item=item, status="fail", message=action, action=action)
