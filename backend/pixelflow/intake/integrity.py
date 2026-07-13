"""需求完整性检查，纯逻辑实现（PRD §8.7）。

这个模块不调用 API、不访问数据库，只根据当前 ``TaskState`` 里的 dict 做判断，
所以可以离线单测。它位于“采集 → 策划”的交接处，相当于 Controller 入参校验
之后、Service 真正执行业务前的完整性 gate。

检查结果分两类：

1. ``fail``：阻塞项，缺失时不能进入 CREATIVE，会触发前端追问。
2. ``warn``：非阻塞风险，任务可以继续，但要让用户知道可能影响效果。

``is_complete`` 只有在没有任何 ``fail`` 时才为 True。这里直接接收普通 dict，
是因为采集过程中状态可能还不完整，未必已经能构造成完整 DTO。
"""

from __future__ import annotations

from .models import IntegrityItem, IntegrityResult


def _has(value) -> bool:
    """判断字段是否有可用内容。

    字符串要去掉空白后非空，列表/字典要有元素，其他非 None 值视为存在。
    """
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

    # 阻塞检查：缺少这些信息时，后续 Brief 和生成都会缺关键输入。
    items.append(_check("商品名称", _has(pi.get("product_name")), "请提供商品名称"))
    items.append(_check("商品图片", _has(pi.get("main_image_url")), "请至少上传 1 张商品图片"))
    core_message = _has(cd.get("core_message")) or _has(pi.get("core_message")) or _has(vp.get("business_goal"))
    items.append(_check("核心诉求", core_message, "请确认视频的核心宣传目标"))
    items.append(_check("平台", _has(vp.get("platform")), "请选择目标投放平台"))
    items.append(_check("时长", _has(vp.get("video_duration_sec")), "请选择视频时长"))
    items.append(_check("创意方向", _has(cd.get("creative_style")), "请确认创意方向"))

    # 非阻塞风险：不影响进入下一阶段，但需要记录给用户或后续节点参考。
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
