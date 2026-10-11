"""add_secret_template_id

Revision ID: b1c2d3e4f5a6
Revises: 33ab6ee88ef2
Create Date: 2026-10-11 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a6'
down_revision = '33ab6ee88ef2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('secret', schema=None) as batch_op:
        batch_op.add_column(sa.Column('template_id', sa.String(), nullable=True))

    op.execute("UPDATE secret SET template_id = 'legacy_' || id WHERE template_id IS NULL")

    with op.batch_alter_table('secret', schema=None) as batch_op:
        batch_op.alter_column('template_id', nullable=False)
        batch_op.create_unique_constraint('unique_secret_template_per_subject', ['session_id', 'subject_character_id', 'template_id'])


def downgrade():
    with op.batch_alter_table('secret', schema=None) as batch_op:
        batch_op.drop_constraint('unique_secret_template_per_subject', type_='unique')
        batch_op.drop_column('template_id')
