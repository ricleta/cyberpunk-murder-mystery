import uuid
from extensions import db
from models import User, Session, SessionMember, Character, Relationship

# Shared session builder for the generation test suites (secrets, objectives).

def create_test_session(session_id, roles, seed=42):
    """Creates a session with the specified roles and fully completed lifepath and relationship ring."""
    gm_user = User(id=f"gm_{session_id}", username=f"GM_{session_id}", password_hash="hash")
    db.session.add(gm_user)
    session = Session(id=session_id, gm_id=gm_user.id, name=f"Session {session_id}", join_code=f"J{uuid.uuid4().hex[:6].upper()}")
    db.session.add(session)
    db.session.add(SessionMember(session_id=session.id, user_id=gm_user.id))

    chars = []
    for i, role in enumerate(roles):
        u = User(id=f"u_{session_id}_{i}", username=f"P_{session_id}_{i}", password_hash="hash")
        db.session.add(u)
        db.session.add(SessionMember(session_id=session.id, user_id=u.id))
        c = Character(
            id=f"c_{session_id}_{i}",
            session_id=session.id,
            player_id=u.id,
            role_key=role,
            display_name=f"Char_{role}_{i}",
            lifepath_complete=True,
            life_goals="Live down your past life and try to forget it.",
            childhood_environment="safe_corp_zone",
            family_background="nomad_pack",
            value_most="money"
        )
        db.session.add(c)
        chars.append(c)

    db.session.commit()

    # Form a connected relationship ring
    n = len(chars)
    for i in range(n):
        c1 = chars[i]
        c2 = chars[(i + 1) % n]
        r = Relationship(
            id=f"rel_{session_id}_{i}",
            session_id=session.id,
            source_character_id=c1.id,
            target_character_id=c2.id,
            rel_type="rivals",
            is_symmetric=True,
            description_source="You are rivals.",
            description_target="You are rivals."
        )
        db.session.add(r)
    db.session.commit()
    return session, chars


# --- Shared fixtures for the Phase 6 suites (older test files define their own copies) ---
import json
from datetime import datetime, timedelta

import jwt
import pytest

from app import create_app
from config import Config
from models import Secret, SecretKnowledge, Objective

ROLES_10 = ["solo", "netrunner", "tech", "medtech", "media", "lawman", "exec", "fixer", "nomad", "rockerboy"]


class PhaseTestConfig(Config):
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    JWT_SECRET_KEY = 'test-secret'


@pytest.fixture
def app():
    app = create_app(PhaseTestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def generate_token():
    def _generate(user_id):
        return jwt.encode({
            'user_id': user_id,
            'exp': datetime.utcnow() + timedelta(days=1)
        }, PhaseTestConfig.JWT_SECRET_KEY, algorithm='HS256')
    return _generate


@pytest.fixture
def auth(generate_token):
    def _auth(user_id):
        return {'Authorization': f'Bearer {generate_token(user_id)}'}
    return _auth


def create_ungenerated_session(session_id, roles):
    """A lobby session with complete characters and nothing generated yet."""
    session, chars = create_test_session(session_id, roles)
    Relationship.query.filter_by(session_id=session.id).delete()
    db.session.commit()
    return session, chars


def generate_via_api(client, auth, session, seed=1, regenerate=False):
    url = f'/api/sessions/{session.id}/generate?seed={seed}' + ('&regenerate=true' if regenerate else '')
    res = client.post(url, headers=auth(session.gm_id))
    assert res.status_code == 200, res.json
    return res


def all_visible_output(client, auth, session, chars):
    """Every API payload that can carry generated text, as one list of JSON strings."""
    gm = auth(session.gm_id)
    base = f'/api/sessions/{session.id}'
    out = [
        client.get(f'{base}/secrets', headers=gm).get_data(as_text=True),
        client.get(f'{base}/relationships', headers=gm).get_data(as_text=True),
        client.get(f'{base}/objectives', headers=gm).get_data(as_text=True),
    ]
    for c in chars:
        out.append(client.get(f'{base}/characters/{c.id}', headers=gm).get_data(as_text=True))
        out.append(client.get(f'{base}/characters/mine', headers=auth(c.player_id)).get_data(as_text=True))
        out.append(client.get(f'{base}/secrets', headers=auth(c.player_id)).get_data(as_text=True))
        out.append(client.get(f'{base}/relationships', headers=auth(c.player_id)).get_data(as_text=True))
        out.append(client.get(f'{base}/objectives', headers=auth(c.player_id)).get_data(as_text=True))
    return out
