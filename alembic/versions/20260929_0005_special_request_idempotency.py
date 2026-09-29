"""Track a confirmed waiter request on each special request."""

from sqlalchemy import Column, Integer, String, inspect

from alembic import op

revision = "20260929_0005"
down_revision = "20260929_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("special_requests")}
    if "request_key" not in columns:
        op.add_column("special_requests", Column("request_key", String(36), nullable=True))
    if "request_index" not in columns:
        op.add_column("special_requests", Column("request_index", Integer, nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("special_requests")}
    if "uq_special_requests_event_user_request" not in indexes:
        op.create_index(
            "uq_special_requests_event_user_request",
            "special_requests",
            ["event_id", "user_id", "request_key", "request_index"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_special_requests_event_user_request", table_name="special_requests")
    op.drop_column("special_requests", "request_index")
    op.drop_column("special_requests", "request_key")
