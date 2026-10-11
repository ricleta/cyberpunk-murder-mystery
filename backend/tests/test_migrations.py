import os

import pytest
import sqlalchemy as sa
from flask_migrate import upgrade

from app import create_app
from config import Config
from extensions import db

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), '..', 'migrations')
PREVIOUS_HEAD = 'c2d3e4f5a6b7'


def make_config(db_path):
    class MigrationConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + str(db_path).replace('\\', '/')
        JWT_SECRET_KEY = 'test-secret'
    return MigrationConfig


def describe_schema(engine):
    """{table: {'columns': {name: (type, nullable)}, 'fks': {...}, 'uniques': {...}}}"""
    insp = sa.inspect(engine)
    schema = {}
    for table in sorted(insp.get_table_names()):
        if table == 'alembic_version':
            continue
        schema[table] = {
            'columns': {c['name']: (type(c['type']).__name__.upper().replace('VARCHAR', 'STRING'), bool(c['nullable']))
                        for c in insp.get_columns(table)},
            'fks': sorted((tuple(fk['constrained_columns']), fk['referred_table'], tuple(fk['referred_columns']))
                          for fk in insp.get_foreign_keys(table)),
            'uniques': sorted(tuple(sorted(u['column_names'])) for u in insp.get_unique_constraints(table)),
        }
    return schema


def create_all_schema(tmp_path):
    path = tmp_path / 'create_all.db'
    app = create_app(make_config(path))
    with app.app_context():
        db.create_all()
        schema = describe_schema(db.engine)
        db.session.remove()
        db.engine.dispose()
    return schema


def test_fresh_upgrade_matches_create_all(tmp_path):
    expected = create_all_schema(tmp_path)

    app = create_app(make_config(tmp_path / 'migrated.db'))
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR)
        actual = describe_schema(db.engine)
        db.session.remove()
        db.engine.dispose()

    assert set(actual) == set(expected)
    for table in expected:
        assert actual[table]['columns'] == expected[table]['columns'], table
        assert actual[table]['fks'] == expected[table]['fks'], table
        assert actual[table]['uniques'] == expected[table]['uniques'], table


def test_upgrade_from_previous_head_adds_columns_and_drops_objectives(tmp_path):
    app = create_app(make_config(tmp_path / 'previous.db'))
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR, revision=PREVIOUS_HEAD)
        insp = sa.inspect(db.engine)
        assert 'objectives' in {c['name'] for c in insp.get_columns('character')}
        assert 'generation_seed' not in {c['name'] for c in insp.get_columns('session')}

        # A row written before the upgrade must survive it
        with db.engine.begin() as conn:
            conn.execute(sa.text('INSERT INTO user (id, username, password_hash) VALUES (:i, :u, :p)'),
                         {'i': 'u1', 'u': 'old_user', 'p': 'hash'})
            conn.execute(sa.text("INSERT INTO session (id, gm_id, name, join_code, status) "
                                 "VALUES ('s1', 'u1', 'Old', 'OLDCOD', 'lobby')"))

        upgrade(directory=MIGRATIONS_DIR)
        insp = sa.inspect(db.engine)
        assert 'objectives' not in {c['name'] for c in insp.get_columns('character')}
        session_cols = {c['name'] for c in insp.get_columns('session')}
        assert {'generation_seed', 'generated_at', 'generation_report'} <= session_cols
        with db.engine.begin() as conn:
            assert conn.execute(sa.text("SELECT name FROM session WHERE id = 's1'")).scalar() == 'Old'
        db.session.remove()
        db.engine.dispose()
