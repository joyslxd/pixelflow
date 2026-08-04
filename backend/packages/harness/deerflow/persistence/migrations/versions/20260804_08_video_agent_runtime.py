"""新增 VideoAgent workspace、plan 与可见步骤表。

迁移版本：20260804_08
前置版本：20260802_07
创建日期：2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260804_08"
down_revision: str | None = "20260802_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_type() -> sa.DateTime:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    """只创建 V2 表和索引，不读取或修改任何既有业务记录。"""

    op.create_table(
        "pixelflow_video_agent_workspaces",
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_pf_video_agent_workspaces_revision"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_index(
        "ix_pf_video_agent_workspaces_owner_conversation",
        "pixelflow_video_agent_workspaces",
        ["user_id", "conversation_id"],
        unique=False,
    )

    op.create_table(
        "pixelflow_video_agent_plans",
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("public_goal", sa.Text(), nullable=True),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.PrimaryKeyConstraint("plan_id"),
    )
    op.create_index(
        "ix_pf_video_agent_plans_owner_workspace",
        "pixelflow_video_agent_plans",
        ["user_id", "workspace_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "pixelflow_video_agent_plan_steps",
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("public_summary", sa.Text(), nullable=True),
        sa.Column("artifact_refs_json", sa.JSON(), nullable=False),
        sa.Column("started_at", _timestamp_type(), nullable=True),
        sa.Column("completed_at", _timestamp_type(), nullable=True),
        sa.CheckConstraint("sequence >= 1", name="ck_pf_video_agent_plan_steps_sequence"),
        sa.PrimaryKeyConstraint("plan_id", "step_id"),
        sa.UniqueConstraint("plan_id", "sequence", name="uq_pf_video_agent_plan_steps_sequence"),
    )
    op.create_index(
        "ix_pf_video_agent_plan_steps_owner_plan",
        "pixelflow_video_agent_plan_steps",
        ["user_id", "plan_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    """仅删除本迁移新建且尚未承载业务数据的表。"""

    connection = op.get_bind()
    for table_name in (
        "pixelflow_video_agent_workspaces",
        "pixelflow_video_agent_plans",
        "pixelflow_video_agent_plan_steps",
    ):
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one():
            raise RuntimeError(f"{table_name} 已包含 VideoAgent 数据，拒绝降级并丢失记录")

    op.drop_index(
        "ix_pf_video_agent_plan_steps_owner_plan",
        table_name="pixelflow_video_agent_plan_steps",
    )
    op.drop_table("pixelflow_video_agent_plan_steps")
    op.drop_index(
        "ix_pf_video_agent_plans_owner_workspace",
        table_name="pixelflow_video_agent_plans",
    )
    op.drop_table("pixelflow_video_agent_plans")
    op.drop_index(
        "ix_pf_video_agent_workspaces_owner_conversation",
        table_name="pixelflow_video_agent_workspaces",
    )
    op.drop_table("pixelflow_video_agent_workspaces")
