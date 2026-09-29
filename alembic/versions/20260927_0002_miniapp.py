"""Mini App idempotency and event leaderboard preferences.

The baseline migration uses current metadata, so each step is conditional for
both existing installations and databases initialized from scratch.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    inspect,
)

from alembic import op

revision = "20260927_0002"
down_revision = "20260924_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "idempotency_key" not in columns:
        op.add_column("orders", Column("idempotency_key", String(36), nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("orders")}
    if "uq_orders_event_user_idempotency" not in indexes:
        op.create_index(
            "uq_orders_event_user_idempotency",
            "orders",
            ["event_id", "user_id", "idempotency_key"],
            unique=True,
        )
    if "event_leaderboard_preferences" not in inspect(bind).get_table_names():
        op.create_table(
            "event_leaderboard_preferences",
            Column("id", Integer, primary_key=True),
            Column(
                "event_id", Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
            ),
            Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            Column("show_telegram_name", Boolean, nullable=False, server_default="false"),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint(
                "event_id", "user_id", name="uq_event_leaderboard_preference_event_user"
            ),
        )
        op.create_index(
            "ix_event_leaderboard_preferences_event_id",
            "event_leaderboard_preferences",
            ["event_id"],
        )
        op.create_index(
            "ix_event_leaderboard_preferences_user_id", "event_leaderboard_preferences", ["user_id"]
        )


def downgrade() -> None:
    # Keep user consent and idempotency history intact on rollback.
    pass
