"""新增 conversation 上下文压缩租约表。

迁移版本：20260725_03
前置版本：20260724_02
创建日期：2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260725_03"
down_revision: str | None = "20260724_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_type() -> sa.DateTime:
    """MySQL 使用微秒精度，其他数据库保留时区类型声明。"""

    return sa.DateTime(timezone=True).with_variant(
        mysql.DATETIME(fsp=6),
        "mysql",
    )


def upgrade() -> None:
    """只新增压缩租约表，不改写既有业务记录。"""

    op.create_table(
        "pixelflow_agent_compaction_locks",
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column(
            "lease_owner",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "lease_token",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "lease_expires_at",
            _timestamp_type(),
            nullable=True,
        ),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.CheckConstraint(
            "state IN ('idle', 'active', 'retry_required')",
            name="ck_pf_agent_compaction_locks_state",
        ),
        sa.CheckConstraint(
            "(state = 'idle' AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR (state IN ('active', 'retry_required') AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_pf_agent_compaction_locks_lease_fields",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_pf_agent_compaction_locks_owner_expiry",
        "pixelflow_agent_compaction_locks",
        ["user_id", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """只移除本迁移创建的压缩租约表。"""

    op.drop_index(
        "ix_pf_agent_compaction_locks_owner_expiry",
        table_name="pixelflow_agent_compaction_locks",
    )
    op.drop_table("pixelflow_agent_compaction_locks")
