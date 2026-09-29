"""Schedule automatic order stage transitions."""

from sqlalchemy import Boolean, Column, DateTime, Integer, inspect

from alembic import op

revision = "20260929_0008"
down_revision = "20260929_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("orders")}
    additions = (
        Column("next_transition_at", DateTime(timezone=True), nullable=True),
        Column("auto_queue_size_snapshot", Integer, nullable=True),
        Column("completed_automatically", Boolean, nullable=False, server_default="0"),
        Column("status_automatically", Boolean, nullable=False, server_default="0"),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("orders", column)


def downgrade() -> None:
    for name in (
        "status_automatically",
        "completed_automatically",
        "auto_queue_size_snapshot",
        "next_transition_at",
    ):
        op.drop_column("orders", name)
