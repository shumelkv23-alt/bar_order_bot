"""Event secret offers, guest unlocks, and order stock references."""

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

revision = "20260929_0004"
down_revision = "20260929_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "secret_offers" not in inspect(bind).get_table_names():
        op.create_table(
            "secret_offers",
            Column("id", Integer, primary_key=True),
            Column(
                "event_id", Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
            ),
            Column("menu_item_id", Integer, ForeignKey("menu_items.id"), nullable=False),
            Column("riddle_ru", String(500), nullable=False),
            Column("riddle_en", String(500), nullable=False),
            Column("answer_salt", String(32), nullable=False),
            Column("answer_hash", String(64), nullable=False),
            Column("available_from", DateTime(timezone=True), nullable=False),
            Column("available_until", DateTime(timezone=True), nullable=False),
            Column("portions_total", Integer, nullable=False),
            Column("portions_used", Integer, nullable=False, server_default="0"),
            Column("is_active", Boolean, nullable=False, server_default="true"),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint("event_id", "menu_item_id"),
        )
        op.create_index("ix_secret_offers_event_id", "secret_offers", ["event_id"])
        op.create_index("ix_secret_offers_menu_item_id", "secret_offers", ["menu_item_id"])
    if "secret_unlocks" not in inspect(bind).get_table_names():
        op.create_table(
            "secret_unlocks",
            Column("id", Integer, primary_key=True),
            Column(
                "offer_id", Integer,
                ForeignKey("secret_offers.id", ondelete="CASCADE"), nullable=False,
            ),
            Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            Column("unlocked_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint("offer_id", "user_id"),
        )
    order_columns = {column["name"] for column in inspect(bind).get_columns("order_items")}
    if "secret_offer_id" not in order_columns:
        with op.batch_alter_table("order_items") as batch:
            batch.add_column(
                Column(
                    "secret_offer_id", Integer,
                    ForeignKey("secret_offers.id", name="fk_order_items_secret_offer_id"),
                )
            )


def downgrade() -> None:
    # Order and unlock history are retained on rollback.
    pass
