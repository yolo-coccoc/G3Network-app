"""Use timezone-aware timestamps for vehicles.

Revision ID: c0f4a8b6e2d1
Revises: 70cefd03d3d5
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision: str = "c0f4a8b6e2d1"
down_revision: str | None = "70cefd03d3d5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Convert vehicle timestamps from naive UTC to TIMESTAMPTZ."""
    for column_name in ("created_at", "updated_at", "deleted_at"):
        op.alter_column(
            "vehicles",
            column_name,
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
            existing_nullable=column_name == "deleted_at",
        )


def downgrade() -> None:
    """Convert vehicle timestamps back to naive UTC values."""
    for column_name in ("created_at", "updated_at", "deleted_at"):
        op.alter_column(
            "vehicles",
            column_name,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
            existing_nullable=column_name == "deleted_at",
        )
