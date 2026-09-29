"""Store an attributed recipe suggestion with a special request."""

from sqlalchemy import JSON, Column, String, Text, inspect

from alembic import op

revision = "20260929_0006"
down_revision = "20260929_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("special_requests")}
    additions = (
        Column("recipe_name", String(100), nullable=True),
        Column("recipe_ingredients", JSON, nullable=True),
        Column("recipe_instructions", Text, nullable=True),
        Column("recipe_source_url", String(300), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("special_requests", column)


def downgrade() -> None:
    for name in (
        "recipe_source_url",
        "recipe_instructions",
        "recipe_ingredients",
        "recipe_name",
    ):
        op.drop_column("special_requests", name)
