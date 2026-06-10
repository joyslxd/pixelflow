"""ORM row for PixelFlow P0 structured user preferences."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class PixelFlowUserPreferenceRow(Base):
    __tablename__ = "pixelflow_user_preferences"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    style_json: Mapped[dict] = mapped_column(JSON, default=dict)
    negatives_json: Mapped[list] = mapped_column(JSON, default=list)
    defaults_json: Mapped[dict] = mapped_column(JSON, default=dict)
    recent_feedback_json: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
