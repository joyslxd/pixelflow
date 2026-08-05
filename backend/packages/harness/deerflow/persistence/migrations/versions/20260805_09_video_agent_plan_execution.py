"""补齐 VideoAgent 计划执行参数与确认策略。

迁移版本：20260805_09
前置版本：20260804_08
创建日期：2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_09"
down_revision: str | None = "20260804_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """只扩展 V2 步骤表，不读取或修改任何 V1 业务记录。"""

    with op.batch_alter_table("pixelflow_video_agent_plan_steps") as batch_op:
        batch_op.add_column(
            sa.Column(
                "arguments_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "confirmation_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    """存在执行参数或确认要求时拒绝丢失审计数据。"""

    connection = op.get_bind()
    unsafe_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM pixelflow_video_agent_plan_steps "
            "WHERE arguments_json <> '{}' OR confirmation_required = 1"
        )
    ).scalar_one()
    if unsafe_count:
        raise RuntimeError("VideoAgent 步骤已包含执行或确认数据，拒绝降级")
    with op.batch_alter_table("pixelflow_video_agent_plan_steps") as batch_op:
        batch_op.drop_column("confirmation_required")
        batch_op.drop_column("arguments_json")
