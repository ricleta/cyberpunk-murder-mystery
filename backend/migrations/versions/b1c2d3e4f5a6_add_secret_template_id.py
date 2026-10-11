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


def _create_secret_tables():
    """Empty database: the relationship/secret tables were never created by a migration
    (they came from db.create_all()), so create them in their final shape."""
    op.create_table(
        'relationship',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('source_character_id', sa.String(), nullable=False),
        sa.Column('target_character_id', sa.String(), nullable=False),
        sa.Column('rel_type', sa.String(), nullable=False),
        sa.Column('is_symmetric', sa.Boolean(), nullable=False),
        sa.Column('description_source', sa.Text(), nullable=True),
        sa.Column('description_target', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint('source_character_id != target_character_id', name='no_self_loops'),
        sa.ForeignKeyConstraint(['session_id'], ['session.id']),
        sa.ForeignKeyConstraint(['source_character_id'], ['character.id']),
        sa.ForeignKeyConstraint(['target_character_id'], ['character.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_character_id', 'target_character_id', name='one_relationship_per_pair')
    )
    with op.batch_alter_table('relationship', schema=None) as batch_op:
        batch_op.create_index('idx_relationship_source', ['source_character_id'], unique=False)
        batch_op.create_index('idx_relationship_target', ['target_character_id'], unique=False)
    op.create_table(
        'secret',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('subject_character_id', sa.String(), nullable=False),
        sa.Column('template_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=False),
        sa.Column('is_public', sa.Boolean(), nullable=True),
        sa.Column('related_character_id', sa.String(), nullable=True),
        sa.Column('related_relationship_id', sa.String(), nullable=True),
        sa.Column('is_killer_core', sa.Boolean(), nullable=True),
        sa.Column('is_killer_trail', sa.Boolean(), nullable=True),
        sa.Column('text_subject', sa.Text(), nullable=False),
        sa.Column('text_knower', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind IN ('victim_tie', 'self', 'lie', 'observation')", name='valid_secret_kind'),
        sa.ForeignKeyConstraint(['session_id'], ['session.id']),
        sa.ForeignKeyConstraint(['subject_character_id'], ['character.id']),
        sa.ForeignKeyConstraint(['related_character_id'], ['character.id']),
        sa.ForeignKeyConstraint(['related_relationship_id'], ['relationship.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'subject_character_id', 'template_id', name='unique_secret_template_per_subject')
    )
    op.create_table(
        'secret_knowledge',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('secret_id', sa.String(), nullable=False),
        sa.Column('knower_character_id', sa.String(), nullable=False),
        sa.Column('how_learned', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['secret_id'], ['secret.id']),
        sa.ForeignKeyConstraint(['knower_character_id'], ['character.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('secret_id', 'knower_character_id', name='unique_secret_knowledge')
    )


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('secret'):
        _create_secret_tables()
        return

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
