"""add source runs

Revision ID: ae54e79ac141
Revises: 16e462dbf879
Created: 2026-11-10 17:29:33.088132
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "ae54e79ac141"
down_revision: str | None = "16e462dbf879"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("started_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("finished_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("fetched_jobs", sa.Integer(), nullable=False),
        sa.Column("new_jobs", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_runs")),
    )
    with op.batch_alter_table("source_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_source_runs_source"), ["source"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("source_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_runs_source"))
    op.drop_table("source_runs")
