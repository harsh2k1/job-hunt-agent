"""add role field, kind, start and why-valid bullets to screenings

Revision ID: c46ca3b75ca0
Revises: 290a13de8b06
Created: 2026-11-13 17:02:32.183767
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "c46ca3b75ca0"
down_revision: str | None = "290a13de8b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listing_screenings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("role_field", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("part_time", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("starts_when_free", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("valid_because", sa.Text(), nullable=True))
        batch_op.drop_column("experience_years")


def downgrade() -> None:
    with op.batch_alter_table("listing_screenings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("experience_years", sa.Integer(), nullable=True))
        batch_op.drop_column("valid_because")
        batch_op.drop_column("starts_when_free")
        batch_op.drop_column("part_time")
        batch_op.drop_column("kind")
        batch_op.drop_column("role_field")
