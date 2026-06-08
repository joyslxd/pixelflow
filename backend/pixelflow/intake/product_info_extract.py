"""product_info_extract — fetch a product page and LLM-extract structured info (PRD §8.1).

Fetches the product URL (blocking httpx, offloaded to a thread so the event loop
stays free), reduces the HTML to plain text, and asks the config-driven chat
model to extract a :class:`ProductInfo` via structured output. The model is
instructed to drop coupons/nav/reviews and keep only core promo info (§8.1).

Best-effort: transport failures raise so the caller (``intake_node``) can fall
back to asking the user; the demand-integrity gate then enforces required fields.
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
_MAX_PAGE_CHARS = 8000  # cap the text we hand the LLM
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
    """Fetch ``product_url`` and extract a :class:`ProductInfo`. Raises on fetch error."""
    raw = await asyncio.to_thread(_fetch, product_url)
    page_text = _html_to_text(raw)
    model = create_chat_model(thinking_enabled=False)
    structured = model.with_structured_output(ProductInfo)
    human = f"【商品页文本】\n{page_text}"
    if user_note:
        human = f"【用户备注】{user_note[:200]}\n\n{human}"
    logger.info("[pixelflow] product_info_extract url=%s chars=%d", product_url, len(page_text))
    return await structured.ainvoke([("system", _SYSTEM_PROMPT), ("human", human)])
