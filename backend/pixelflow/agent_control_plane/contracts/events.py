"""持久化后再通过 SSE 投递的统一事件合同。"""

from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue

from .base import ContractModel
from .enums import AgentEventType


class AgentEvent(ContractModel):
    """conversation 内按 sequence 单调递增的事件 envelope。"""

    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    cursor: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    occurred_at: datetime
    type: AgentEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)
