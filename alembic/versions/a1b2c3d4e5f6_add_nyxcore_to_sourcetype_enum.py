"""add nyxcore to sourcetype enum

Revision ID: a1b2c3d4e5f6
Revises: c8b5dd2aff5e
Create Date: 2026-03-18 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "c8b5dd2aff5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLAlchemy stores StrEnum member NAMES (uppercase) in PostgreSQL enum types
    op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'NYXCORE'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from enum types.
    # To fully revert, you would need to recreate the type and column.
    pass
