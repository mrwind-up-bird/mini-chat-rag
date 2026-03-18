"""fix nyxcore enum case — add uppercase NYXCORE

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-18 01:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLAlchemy stores StrEnum member NAMES (uppercase) in PostgreSQL enum types.
    # Previous migration added lowercase 'nyxcore'; we need uppercase 'NYXCORE'.
    op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'NYXCORE'")


def downgrade() -> None:
    pass
