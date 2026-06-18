"""QC 判定结果 DTO：质检阶段的输出合同。

QC 检查的是“已经产出的内容”（生成 clips + 装配后的 Timeline），不是 Brief 计划。
``passed`` 会直接影响 LangGraph 路由：存在 ``fail`` 时回到 GENERATE 做有限重试；
``warn`` 只记录质量风险，不强制重试。
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
    score: float  # 生成覆盖率，范围 0..1，当前按 clips produced / segments attempted 计算。
    check_results: list[QCItem]
