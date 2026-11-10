"""add closing date to jobs

Revision ID: 932642f7f891
Revises: 1150c2c34749
Created: 2026-11-10 10:44:09.184428
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "932642f7f891"
down_revision: str | None = "1150c2c34749"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("closes_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("closes_at")
