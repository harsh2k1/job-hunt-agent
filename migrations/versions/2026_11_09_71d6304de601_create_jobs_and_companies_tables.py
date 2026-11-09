"""create jobs and companies tables

Revision ID: 71d6304de601
Revises: none (first migration)
Created: 2026-11-09 17:09:36.150594
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = '71d6304de601'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('companies',
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('ats_platform', sa.Enum('greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'workday', 'teamtailor', 'recruitee', 'pinpoint', name='atsplatform', native_enum=False, length=32), nullable=False),
    sa.Column('ats_board_slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('discovered_via', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('added_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_companies')),
    sa.UniqueConstraint('ats_platform', 'ats_board_slug', name=op.f('uq_companies_ats_platform_ats_board_slug'))
    )
    op.create_table('jobs',
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source_job_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('company_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('location', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('posted_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
    sa.Column('employment_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('first_seen_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('last_seen_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
    sa.UniqueConstraint('source', 'source_job_id', name=op.f('uq_jobs_source_source_job_id'))
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_first_seen_at'), ['first_seen_at'], unique=False)

def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_first_seen_at'))

    op.drop_table('jobs')
    op.drop_table('companies')
