"""Add event-scoped achievements."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, inspect

from alembic import op

revision = "20260929_0003"
down_revision = "20260927_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "event_achievements" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "event_achievements",
        Column("id", Integer, primary_key=True),
        Column("event_id", Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("code", String(40), nullable=False),
        Column("awarded_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("event_id", "user_id", "code"),
    )
    op.create_index("ix_event_achievements_event_id", "event_achievements", ["event_id"])
    op.create_index("ix_event_achievements_user_id", "event_achievements", ["user_id"])


def downgrade() -> None:
    op.drop_table("event_achievements")
