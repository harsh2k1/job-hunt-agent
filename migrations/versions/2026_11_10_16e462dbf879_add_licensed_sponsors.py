"""add licensed sponsors

Revision ID: 16e462dbf879
Revises: 932642f7f891
Created: 2026-11-10 16:10:03.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "16e462dbf879"
down_revision: str | None = "932642f7f891"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "licensed_sponsors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("trading_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("normalised_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("normalised_trading_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("town_city", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("county", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("type_and_rating", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("route", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("register_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_licensed_sponsors")),
    )
    with op.batch_alter_table("licensed_sponsors", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_licensed_sponsors_normalised_name"), ["normalised_name"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_licensed_sponsors_normalised_trading_name"),
            ["normalised_trading_name"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("licensed_sponsors", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_licensed_sponsors_normalised_trading_name"))
        batch_op.drop_index(batch_op.f("ix_licensed_sponsors_normalised_name"))
    op.drop_table("licensed_sponsors")
