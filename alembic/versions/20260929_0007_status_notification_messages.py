"""Track the Telegram status message for each order and special request."""

from sqlalchemy import BigInteger, Column, inspect

from alembic import op

revision = "20260929_0007"
down_revision = "20260929_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("orders", "special_requests"):
        columns = {column["name"] for column in inspect(bind).get_columns(table)}
        if "status_notification_message_id" not in columns:
            op.add_column(
                table,
                Column("status_notification_message_id", BigInteger, nullable=True),
            )


def downgrade() -> None:
    for table in ("special_requests", "orders"):
        op.drop_column(table, "status_notification_message_id")
