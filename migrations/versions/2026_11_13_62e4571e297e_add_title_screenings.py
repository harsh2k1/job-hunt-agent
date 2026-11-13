"""add title screenings

Revision ID: 62e4571e297e
Revises: c46ca3b75ca0
Created: 2026-11-13 17:29:18.684744
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "62e4571e297e"
down_revision: str | None = "c46ca3b75ca0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "title_screenings",
        sa.Column("title_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("related", sa.Boolean(), nullable=False),
        sa.Column("question_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("screened_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("title_key", name=op.f("pk_title_screenings")),
    )


def downgrade() -> None:
    op.drop_table("title_screenings")
