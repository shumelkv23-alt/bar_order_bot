"""Create the MVP schema while adopting existing installations safely.

Revision ID: 20260924_0001
Revises:
Create Date: 2026-09-24
"""

from alembic import op
from app import models  # noqa: F401
from app.db import Base

revision = "20260924_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
