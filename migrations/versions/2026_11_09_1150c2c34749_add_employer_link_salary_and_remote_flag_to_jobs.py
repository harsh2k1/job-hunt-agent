"""add employer link, salary and remote flag to jobs

Revision ID: 1150c2c34749
Revises: 71d6304de601
Created: 2026-11-09 17:15:02.692706
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "1150c2c34749"
down_revision: str | None = "71d6304de601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("employer_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("salary_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("is_remote", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("is_remote")
        batch_op.drop_column("salary_text")
        batch_op.drop_column("employer_url")
