"""phase6: generation metadata on session, drop deprecated character.objectives

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-11 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3e4f5a6b7c8'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def _columns(table):
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    # Each step checks first, so a database that was bootstrapped with db.create_all()
    # from the new models (and stamped at an older revision) upgrades cleanly too.
    session_cols = _columns('session')
    with op.batch_alter_table('session', schema=None) as batch_op:
        if 'generation_seed' not in session_cols:
            batch_op.add_column(sa.Column('generation_seed', sa.Integer(), nullable=True))
        if 'generated_at' not in session_cols:
            batch_op.add_column(sa.Column('generated_at', sa.DateTime(), nullable=True))
        if 'generation_report' not in session_cols:
            batch_op.add_column(sa.Column('generation_report', sa.JSON(), nullable=True))

    if 'objectives' in _columns('character'):
        # Objectives live in the objective table since Phase 5; nothing reads this column.
        with op.batch_alter_table('character', schema=None) as batch_op:
            batch_op.drop_column('objectives')


def downgrade():
    with op.batch_alter_table('character', schema=None) as batch_op:
        batch_op.add_column(sa.Column('objectives', sa.JSON(), nullable=True))
    with op.batch_alter_table('session', schema=None) as batch_op:
        batch_op.drop_column('generation_report')
        batch_op.drop_column('generated_at')
        batch_op.drop_column('generation_seed')
