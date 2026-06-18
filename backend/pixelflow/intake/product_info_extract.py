"""商品页信息抽取（PRD §8.1）。

这个模块负责把商品详情页转换成结构化 ``ProductInfo``。流程是：

1. 用 httpx 拉取商品页 HTML。
2. 清理 script/style/tag，把 HTML 压缩成适合给 LLM 的纯文本。
3. 调用配置驱动的 chat model，并要求它按 ``ProductInfo`` 结构化输出。

``httpx.get`` 是阻塞 I/O，所以调用方会通过 ``asyncio.to_thread`` 把它放到线程
里执行，避免卡住 FastAPI/LangGraph 的 async event loop。

这是 best-effort 能力：网络失败会抛给 ``intake_node``，由 node 记录日志并回退
到人工补充；真正的必填字段仍由需求完整性检查统一兜底。
"""

from __future__ import annotations

import asyncio
import html as _html
import logging
import re

import httpx

from deerflow.models import create_chat_model

from .models import ProductInfo

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = 15.0
_MAX_PAGE_CHARS = 8000  # 限制交给 LLM 的页面文本长度，避免 prompt 过大。
_UA = "Mozilla/5.0 (compatible; PixelFlowBot/1.0)"

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

_SYSTEM_PROMPT = """你是电商商品页信息抽取器。从给定的商品详情页文本中抽取结构化商品信息。要求：
- 只保留商品本身信息：名称、价格、原价、类目、规格、卖点(3-5条)、主图/详情图地址。
- 丢弃：优惠券/店铺券、导航栏/面包屑、用户评价/问答、店铺推荐/关联商品、店铺评分/销量。
- 仅保留核心促销（限时折扣/满减/赠品/秒杀）到 promotion_info。
- 抓不到的字段留空，绝不编造（尤其是价格）。
只输出符合 schema 的结构化数据。"""


def _fetch(url: str) -> str:
    resp = httpx.get(url, timeout=_FETCH_TIMEOUT_SEC, follow_redirects=True, headers={"User-Agent": _UA})
    resp.raise_for_status()
    return resp.text


def _html_to_text(raw: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", raw)
    text = _TAG.sub(" ", text)
    text = _html.unescape(text)
    text = _WS.sub(" ", text).strip()
    return text[:_MAX_PAGE_CHARS]


async def product_info_extract(product_url: str, user_note: str = "") -> ProductInfo:
    """抓取 ``product_url`` 并抽取 ``ProductInfo``。

    抓取异常会继续抛出，由上层 ``intake_node`` 决定如何降级。
    """
    raw = await asyncio.to_thread(_fetch, product_url)
    page_text = _html_to_text(raw)
    model = create_chat_model(thinking_enabled=False)
    structured = model.with_structured_output(ProductInfo)
    human = f"【商品页文本】\n{page_text}"
    if user_note:
        human = f"【用户备注】{user_note[:200]}\n\n{human}"
    logger.info("[pixelflow] product_info_extract url=%s chars=%d", product_url, len(page_text))
    return await structured.ainvoke([("system", _SYSTEM_PROMPT), ("human", human)])
