import pytest
from app import create_app
from extensions import db
from models import User, Session, SessionMember, Character
from config import Config
import jwt
from datetime import datetime, timedelta

class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    JWT_SECRET_KEY = 'test-secret'

@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
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
        }, TestConfig.JWT_SECRET_KEY, algorithm='HS256')
    return _generate

@pytest.fixture
def setup_data(app):
    with app.app_context():
        # Create users
        gm_user = User(username="GM", password_hash="hash")
        player1 = User(username="Player 1", password_hash="hash")
        player2 = User(username="Player 2", password_hash="hash")
        db.session.add_all([gm_user, player1, player2])
        db.session.commit()

        # Create session
        session = Session(gm_id=gm_user.id, name="Test Session", join_code="TEST12")
        db.session.add(session)
        db.session.commit()

        # Add members
        sm_gm = SessionMember(session_id=session.id, user_id=gm_user.id)
        sm_p1 = SessionMember(session_id=session.id, user_id=player1.id)
        sm_p2 = SessionMember(session_id=session.id, user_id=player2.id)
        db.session.add_all([sm_gm, sm_p1, sm_p2])
        db.session.commit()

        # Create characters
        char_p1 = Character(session_id=session.id, player_id=player1.id, role_key="solo")
        char_p2 = Character(session_id=session.id, player_id=player2.id, role_key="netrunner")
        db.session.add_all([char_p1, char_p2])
        db.session.commit()

        return {
            'gm': gm_user.id,
            'p1': player1.id,
            'p2': player2.id,
            'session': session.id,
            'char_p1': char_p1.id,
            'char_p2': char_p2.id
        }

def test_missing_token_returns_401(client):
    response = client.get('/api/auth/me')
    assert response.status_code == 401

def test_gm_can_view_master_list(client, setup_data, generate_token):
    token = generate_token(setup_data['gm'])
    response = client.get(
        f'/api/sessions/{setup_data["session"]}/characters',
        headers={'Authorization': f'Bearer {token}'}
    )
    assert response.status_code == 200
    assert len(response.json['characters']) == 2

def test_player_cannot_view_master_list(client, setup_data, generate_token):
    token = generate_token(setup_data['p1'])
    response = client.get(
        f'/api/sessions/{setup_data["session"]}/characters',
        headers={'Authorization': f'Bearer {token}'}
    )
    assert response.status_code == 403

def test_player_can_view_own_character(client, setup_data, generate_token):
    token = generate_token(setup_data['p1'])
    response = client.get(
        f'/api/sessions/{setup_data["session"]}/characters/{setup_data["char_p1"]}',
        headers={'Authorization': f'Bearer {token}'}
    )
    assert response.status_code == 200

def test_player_cannot_view_others_character(client, setup_data, generate_token):
    token = generate_token(setup_data['p1'])
    response = client.get(
        f'/api/sessions/{setup_data["session"]}/characters/{setup_data["char_p2"]}',
        headers={'Authorization': f'Bearer {token}'}
    )
    assert response.status_code == 403
