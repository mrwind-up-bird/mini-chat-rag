"""add refresh_schedule and last_refreshed_at to sources

Revision ID: c8b5dd2aff5e
Revises: 
Create Date: 2026-02-11 04:58:37.489550

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8b5dd2aff5e'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("refresh_schedule", sa.String(20), nullable=True))
    op.add_column("sources", sa.Column("last_refreshed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "last_refreshed_at")
    op.drop_column("sources", "refresh_schedule")
