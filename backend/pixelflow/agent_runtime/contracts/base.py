"""Agent Runtime 线协议模型的共同校验规则。"""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """拒绝未冻结字段，并在赋值时继续执行合同校验。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )
