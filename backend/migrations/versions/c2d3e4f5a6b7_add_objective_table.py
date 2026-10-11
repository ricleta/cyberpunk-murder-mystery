"""add_objective_table

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-11 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d3e4f5a6b7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    # Fresh databases are bootstrapped with db.create_all() (init_db.py), which already
    # creates this table; only create it when it is missing.
    if sa.inspect(op.get_bind()).has_table('objective'):
        return

    op.create_table(
        'objective',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('character_id', sa.String(), nullable=False),
        sa.Column('template_id', sa.String(), nullable=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('gm_hint', sa.Text(), nullable=True),
        sa.Column('related_character_id', sa.String(), nullable=True),
        sa.Column('related_secret_id', sa.String(), nullable=True),
        sa.Column('related_relationship_id', sa.String(), nullable=True),
        sa.Column('linked_objective_id', sa.String(), nullable=True),
        sa.Column('link_type', sa.String(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('protect_secret', 'exploit_secret', 'relationship', 'flavor', 'find_killer', 'hide_evidence', 'frame', 'manual')",
            name='valid_objective_kind'),
        sa.CheckConstraint('points >= 1 AND points <= 3', name='valid_objective_points'),
        sa.CheckConstraint("link_type IS NULL OR link_type IN ('opposed', 'aligned')", name='valid_objective_link_type'),
        sa.ForeignKeyConstraint(['session_id'], ['session.id']),
        sa.ForeignKeyConstraint(['character_id'], ['character.id']),
        sa.ForeignKeyConstraint(['related_character_id'], ['character.id']),
        sa.ForeignKeyConstraint(['related_secret_id'], ['secret.id']),
        sa.ForeignKeyConstraint(['related_relationship_id'], ['relationship.id']),
        sa.ForeignKeyConstraint(['linked_objective_id'], ['objective.id']),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('objective', schema=None) as batch_op:
        batch_op.create_index('idx_objective_session', ['session_id'], unique=False)
        batch_op.create_index('idx_objective_character', ['character_id'], unique=False)


def downgrade():
    with op.batch_alter_table('objective', schema=None) as batch_op:
        batch_op.drop_index('idx_objective_character')
        batch_op.drop_index('idx_objective_session')
    op.drop_table('objective')
