"""为 Operation 增加配额暂停审计代次。

迁移版本：20260802_07
前置版本：20260801_06
创建日期：2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_07"
down_revision: str | None = "20260801_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPERATION_TABLE = "pixelflow_agent_operations"
_QUOTA_PAUSE_REVISION_COLUMN = "quota_pause_revision"
_QUOTA_PAUSE_REVISION_CONSTRAINT = "ck_pf_agent_operations_quota_pause_revision"


def _normalize_default(value: object) -> str:
    """归一化方言反射的零默认值，拒绝缺失或非零默认值。"""

    return str(value).strip("() '\"") if value is not None else ""


def _normalize_quota_pause_check(sqltext: object) -> str:
    """归一化本迁移单列 CHECK 的方言引号、空白和外层括号。"""

    return "".join(str(sqltext).replace("`", "").replace('"', "").split()).strip("()")


def _has_valid_quota_pause_revision() -> bool:
    """在线校验已有列和约束，防止错误 schema 被静默接受。"""

    if op.get_context().as_sql:
        raise RuntimeError("quota pause revision 迁移需要在线检查现有 schema")
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_OPERATION_TABLE):
        raise RuntimeError("缺少 pixelflow_agent_operations，不能迁移 quota pause revision")
    columns = {
        str(item["name"]): item
        for item in inspector.get_columns(_OPERATION_TABLE)
    }
    column = columns.get(_QUOTA_PAUSE_REVISION_COLUMN)
    if column is None:
        return False
    if not isinstance(column["type"], sa.Integer) or bool(column["nullable"]):
        raise RuntimeError("Operation quota pause revision 列不符合迁移合同")
    if _normalize_default(column.get("default")) != "0":
        raise RuntimeError("Operation quota pause revision 默认值不符合迁移合同")
    constraints = {
        str(item["name"]): str(item["sqltext"])
        for item in inspector.get_check_constraints(_OPERATION_TABLE)
        if item["name"] is not None
    }
    if _normalize_quota_pause_check(
        constraints.get(_QUOTA_PAUSE_REVISION_CONSTRAINT, "")
    ) != _normalize_quota_pause_check(f"{_QUOTA_PAUSE_REVISION_COLUMN} >= 0"):
        raise RuntimeError("Operation quota pause revision 约束不符合迁移合同")
    return True


def upgrade() -> None:
    """幂等增加非负 quota pause revision，并保留既有 Operation。"""

    # 先在线检查表和列；已存在正确列时只校验并返回。
    if _has_valid_quota_pause_revision():
        return
    with op.batch_alter_table(_OPERATION_TABLE) as batch_op:
        batch_op.add_column(
            sa.Column(
                _QUOTA_PAUSE_REVISION_COLUMN,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_check_constraint(
            _QUOTA_PAUSE_REVISION_CONSTRAINT,
            f"{_QUOTA_PAUSE_REVISION_COLUMN} >= 0",
        )


def downgrade() -> None:
    """只有全部 revision 仍为 0 时才移除列，拒绝丢失生产审计数据。"""

    count = op.get_bind().execute(
        sa.text(
            f"SELECT COUNT(*) FROM {_OPERATION_TABLE} "
            f"WHERE {_QUOTA_PAUSE_REVISION_COLUMN} <> 0"
        )
    ).scalar_one()
    if count:
        raise RuntimeError("存在 quota pause revision，拒绝降级并丢失审计数据")
    with op.batch_alter_table(_OPERATION_TABLE) as batch_op:
        batch_op.drop_constraint(
            _QUOTA_PAUSE_REVISION_CONSTRAINT,
            type_="check",
        )
        batch_op.drop_column(_QUOTA_PAUSE_REVISION_COLUMN)
