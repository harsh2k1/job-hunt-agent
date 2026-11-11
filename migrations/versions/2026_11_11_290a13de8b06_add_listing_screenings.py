"""add listing screenings

Revision ID: 290a13de8b06
Revises: ae54e79ac141
Created: 2026-11-11 16:09:52.235088
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "290a13de8b06"
down_revision: str | None = "ae54e79ac141"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "listing_screenings",
        sa.Column("listing_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("fit", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sponsorship", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("start_date", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("experience_years", sa.Integer(), nullable=True),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("screened_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("listing_key", name=op.f("pk_listing_screenings")),
    )


def downgrade() -> None:
    op.drop_table("listing_screenings")
