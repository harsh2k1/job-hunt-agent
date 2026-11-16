"""add role end to screenings

Revision ID: 715c41082ac1
Revises: 62e4571e297e
Created: 2026-11-16 01:20:21.857102
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "715c41082ac1"
down_revision: str | None = "62e4571e297e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listing_screenings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("role_end", sqlmodel.sql.sqltypes.AutoString(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table("listing_screenings", schema=None) as batch_op:
        batch_op.drop_column("role_end")

