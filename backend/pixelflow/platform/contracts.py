"""跨领域 DTO 共享的 Pydantic 合同基类。"""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """拒绝未知字段，并在赋值时持续执行合同校验。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


__all__ = ["ContractModel"]
