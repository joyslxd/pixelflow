"""允许 Supervisor 全局 clarification 不绑定 Workflow。

迁移版本：20260801_06
前置版本：20260731_05
创建日期：2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_06"
down_revision: str | None = "20260731_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INTERRUPT_TABLE = "pixelflow_agent_interrupts"
_WORKFLOW_COLUMN = "workflow_id"


def _workflow_column() -> dict[str, object]:
    """读取并校验目标列，兼容非事务 DDL 失败后的安全重试。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_INTERRUPT_TABLE):
        raise RuntimeError("缺少 pixelflow_agent_interrupts，不能迁移全局追问")
    columns = {
        str(item["name"]): item
        for item in inspector.get_columns(_INTERRUPT_TABLE)
    }
    column = columns.get(_WORKFLOW_COLUMN)
    if column is None:
        raise RuntimeError("interrupt 表缺少 workflow_id，不能迁移全局追问")
    column_type = column["type"]
    if not isinstance(column_type, sa.String) or column_type.length != 64:
        raise RuntimeError("interrupt.workflow_id 类型不符合迁移合同")
    return column


def upgrade() -> None:
    """把 workflow_id 改为可空；DTO 继续限制只有全局追问可使用 NULL。"""

    if op.get_context().as_sql:
        raise RuntimeError("全局追问迁移需要在线检查现有 schema")
    if bool(_workflow_column()["nullable"]):
        return
    with op.batch_alter_table(_INTERRUPT_TABLE) as batch_op:
        batch_op.alter_column(
            _WORKFLOW_COLUMN,
            existing_type=sa.String(length=64),
            nullable=True,
        )


def downgrade() -> None:
    """只有不存在 NULL 全局追问时才恢复非空约束，绝不静默删除业务数据。"""

    if op.get_context().as_sql:
        raise RuntimeError("全局追问迁移需要在线检查现有 schema")
    if not bool(_workflow_column()["nullable"]):
        return
    null_count = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM pixelflow_agent_interrupts "
            "WHERE workflow_id IS NULL"
        )
    ).scalar_one()
    if null_count:
        # 降级后的旧合同无法表达这些记录；必须先由运维确认并处理，不能伪造 Workflow 或删行。
        raise RuntimeError(
            "存在未处理的全局 clarification，拒绝降级；请先升级恢复或人工处理对应记录"
        )
    with op.batch_alter_table(_INTERRUPT_TABLE) as batch_op:
        batch_op.alter_column(
            _WORKFLOW_COLUMN,
            existing_type=sa.String(length=64),
            nullable=False,
        )
