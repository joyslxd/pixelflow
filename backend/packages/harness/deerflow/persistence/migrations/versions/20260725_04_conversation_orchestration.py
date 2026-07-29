"""补齐旧对话编排归属和 Context 外置载荷支撑表。

迁移版本：20260725_04
前置版本：20260725_03
创建日期：2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_04"
down_revision: str | None = "20260725_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNERSHIP_INDEX = "ix_pf_conversation_orchestration_m13_1"
_PAYLOAD_TABLE = "pixelflow_agent_context_payloads"
_PAYLOAD_OWNERSHIP_INDEX = "ix_pf_agent_context_payloads_owner_created"


def _conversation_columns() -> set[str]:
    """读取旧对话表当前字段，避免重复升级或误删他人字段。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("pixelflow_conversations"):
        return set()
    return {str(column["name"]) for column in inspector.get_columns("pixelflow_conversations")}


def _has_ownership_index() -> bool:
    """用迁移专属索引标记这两个字段确由本版本管理。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("pixelflow_conversations"):
        return False
    return any(index["name"] == _OWNERSHIP_INDEX for index in inspector.get_indexes("pixelflow_conversations"))


def _has_payload_ownership_index() -> bool:
    """只有迁移专属索引存在时才允许回滚载荷表。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_PAYLOAD_TABLE):
        return False
    return any(index["name"] == _PAYLOAD_OWNERSHIP_INDEX for index in inspector.get_indexes(_PAYLOAD_TABLE))


def _create_payload_table() -> None:
    """新增可恢复的完整载荷表，Prompt 只保留稳定引用。"""

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_PAYLOAD_TABLE):
        return
    op.create_table(
        _PAYLOAD_TABLE,
        sa.Column("payload_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column("original_bytes", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_kind IN ('tool', 'artifact')",
            name="ck_pf_agent_context_payloads_kind",
        ),
        sa.PrimaryKeyConstraint("payload_id"),
        sa.UniqueConstraint(
            "user_id",
            "conversation_id",
            "source_kind",
            "source_ref",
            "content_hash",
            name="uq_pf_agent_context_payloads_identity",
        ),
    )
    op.create_index(
        _PAYLOAD_OWNERSHIP_INDEX,
        _PAYLOAD_TABLE,
        ["user_id", "conversation_id", "created_at"],
        unique=False,
    )


def upgrade() -> None:
    """旧对话归旧 v2，并建立可跨进程恢复的外置载荷存储。"""

    context = op.get_context()
    if context.as_sql:
        raise RuntimeError("conversation orchestration migration requires online schema inspection")
    _create_payload_table()
    columns = _conversation_columns()
    if columns:
        missing_mode = "orchestration_mode" not in columns
        missing_version = "orchestration_version" not in columns
        if missing_mode != missing_version:
            raise RuntimeError("conversation orchestration columns must be created and owned together")
        if not missing_mode:
            return
        op.add_column(
            "pixelflow_conversations",
            sa.Column(
                "orchestration_mode",
                sa.String(length=24),
                server_default=sa.text("'frontend_v2'"),
                nullable=False,
            ),
        )
        op.add_column(
            "pixelflow_conversations",
            sa.Column(
                "orchestration_version",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
        )
        op.create_index(
            _OWNERSHIP_INDEX,
            "pixelflow_conversations",
            ["orchestration_mode", "orchestration_version"],
            unique=False,
        )


def downgrade() -> None:
    """只移除本迁移拥有的字段和支撑表，不改变旧业务数据。"""

    context = op.get_context()
    if context.as_sql:
        raise RuntimeError("conversation orchestration migration requires online schema inspection")
    if _has_ownership_index():
        columns = _conversation_columns()
        required = {"orchestration_mode", "orchestration_version"}
        if not required.issubset(columns):
            raise RuntimeError("conversation orchestration ownership marker exists without both columns")
        op.drop_index(
            _OWNERSHIP_INDEX,
            table_name="pixelflow_conversations",
        )
        with op.batch_alter_table("pixelflow_conversations") as batch_op:
            batch_op.drop_column("orchestration_version")
            batch_op.drop_column("orchestration_mode")
    if _has_payload_ownership_index():
        op.drop_index(
            _PAYLOAD_OWNERSHIP_INDEX,
            table_name=_PAYLOAD_TABLE,
        )
        op.drop_table(_PAYLOAD_TABLE)
