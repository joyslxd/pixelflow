"""为旧对话聚合增加 CAS revision。

迁移版本：20260724_02
前置版本：20260724_01
创建日期：2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_02"
down_revision: str | None = "20260724_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNERSHIP_INDEX = "ix_pf_conversation_revision_m01_3"


def _has_revision_column() -> bool:
    """在线迁移时确认旧对话表及 revision 是否已经存在。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("pixelflow_conversations"):
        return False
    return any(
        column["name"] == "revision"
        for column in inspector.get_columns("pixelflow_conversations")
    )


def _has_ownership_index() -> bool:
    """确认 revision 是否由本迁移创建并登记。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("pixelflow_conversations"):
        return False
    return any(
        index["name"] == _OWNERSHIP_INDEX
        for index in inspector.get_indexes("pixelflow_conversations")
    )


def upgrade() -> None:
    """为已存在的旧对话表增加并回填初始 revision。"""

    context = op.get_context()
    if context.as_sql:
        raise RuntimeError(
            "conversation revision migration requires online schema inspection"
        )
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("pixelflow_conversations"):
        return
    if _has_revision_column():
        return
    op.add_column(
        "pixelflow_conversations",
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_index(
        _OWNERSHIP_INDEX,
        "pixelflow_conversations",
        ["revision"],
        unique=False,
    )


def downgrade() -> None:
    """只移除本迁移增加的 revision，不改写对话其他字段。"""

    context = op.get_context()
    if context.as_sql:
        raise RuntimeError(
            "conversation revision migration requires online schema inspection"
        )
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("pixelflow_conversations"):
        return
    if not _has_ownership_index():
        return
    if not _has_revision_column():
        raise RuntimeError(
            "conversation revision ownership marker exists without its column"
        )
    op.drop_index(
        _OWNERSHIP_INDEX,
        table_name="pixelflow_conversations",
    )
    with op.batch_alter_table("pixelflow_conversations") as batch_op:
        batch_op.drop_column("revision")
