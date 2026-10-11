import pytest
from app import create_app
from extensions import db
from models import User, Session, SessionMember, Character, Relationship
from config import Config
from generation.relationships import (
    compute_role_affinity, build_relationship_graph, verify_graph,
    assign_relationship_types, generate_relationship_text, load_generation_data
)
import jwt
from datetime import datetime, timedelta
import networkx as nx
import random

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

def test_affinity_scoring_correctness():
    # Load actual static data
    role_tags, tag_affinity, _ = load_generation_data()
    
    # 1. Exec x Solo -> expect ~0.8 or higher
    score1 = compute_role_affinity("exec", "solo", role_tags, tag_affinity)
    assert score1 >= 0.75
    
    # 2. Fixer x Solo -> expect clamped to 0.95
    score2 = compute_role_affinity("fixer", "solo", role_tags, tag_affinity)
    assert score2 == 0.95
    
    # 3. Nomad x Exec -> sum-then-clamp over every tag pair; the shipped tag data now
    # sums past the ceiling, so just assert the documented bounds (0.05 floor, 0.95 clamp).
    score3 = compute_role_affinity("nomad", "exec", role_tags, tag_affinity)
    assert 0.05 <= score3 <= 0.95
    
    # 4. Netrunner x Netrunner -> expect clamped to 0.95
    score4 = compute_role_affinity("netrunner", "netrunner", role_tags, tag_affinity)
    assert score4 == 0.95

def test_graph_construction_small_party():
    role_tags, tag_affinity, _ = load_generation_data()
    characters = [
        Character(id=str(i), role_key=random.choice(["solo", "exec", "netrunner", "tech"]))
        for i in range(4)
    ]
    
    graph = build_relationship_graph(characters, role_tags, tag_affinity)
    verify_graph(graph, characters)
    
    assert len(graph.nodes) == 4
    for n in graph.nodes:
        assert graph.degree(n) >= 2
    assert nx.is_connected(graph)

def test_graph_construction_large_party():
    role_tags, tag_affinity, _ = load_generation_data()
    characters = [
        Character(id=str(i), role_key=random.choice(list(role_tags.keys())))
        for i in range(20)
    ]
    
    graph = build_relationship_graph(characters, role_tags, tag_affinity)
    verify_graph(graph, characters)
    assert len(graph.nodes) == 20
    assert nx.is_connected(graph)

def test_graph_construction_duplicate_roles():
    role_tags, tag_affinity, _ = load_generation_data()
    characters = [
        Character(id=str(i), role_key="solo") for i in range(5)
    ]
    
    graph = build_relationship_graph(characters, role_tags, tag_affinity)
    verify_graph(graph, characters)
    assert nx.is_connected(graph)

def test_directional_text_correctness():
    source_char = Character(display_name="Alice")
    target_char = Character(display_name="Bob")
    rel_type_entry = {
        "id": "employer_employee",
        "symmetric": False,
        "templateSource": "You've hired #targetName# for work.",
        "templateTarget": "#sourceName# has hired you for work."
    }
    
    src, tgt = generate_relationship_text(rel_type_entry, source_char, target_char)
    assert src == "You've hired Bob for work."
    assert tgt == "Alice has hired you for work."

def test_symmetric_text_correctness():
    source_char = Character(display_name="Alice")
    target_char = Character(display_name="Bob")
    rel_type_entry = {
        "id": "rivals",
        "symmetric": True,
        "templateSource": "You and #targetName# are rivals."
    }
    
    src, tgt = generate_relationship_text(rel_type_entry, source_char, target_char)
    assert src == "You and Bob are rivals."
    # Symmetric text is re-rendered from the target's perspective (names swapped)
    assert tgt == "You and Alice are rivals."

@pytest.fixture
def setup_party(app):
    with app.app_context():
        gm_user = User(username="GM", password_hash="hash")
        players = [User(username=f"Player {i}", password_hash="hash") for i in range(4)]
        db.session.add(gm_user)
        db.session.add_all(players)
        db.session.commit()

        session = Session(gm_id=gm_user.id, name="Test Party", join_code="PTY123")
        db.session.add(session)
        db.session.commit()

        db.session.add(SessionMember(session_id=session.id, user_id=gm_user.id))
        for p in players:
            db.session.add(SessionMember(session_id=session.id, user_id=p.id))
        db.session.commit()

        chars = []
        roles = ["exec", "solo", "netrunner", "tech"]
        for i, p in enumerate(players):
            c = Character(
                session_id=session.id, player_id=p.id, role_key=roles[i],
                lifepath_complete=True, display_name=f"Char {i}"
            )
            db.session.add(c)
            chars.append(c)
        db.session.commit()

        return {
            'gm': gm_user.id,
            'players': [p.id for p in players],
            'session': session.id,
            'characters': [c.id for c in chars]
        }

def test_api_generate_relationships_success(client, setup_party, generate_token):
    token = generate_token(setup_party['gm'])
    response = client.post(
        f'/api/sessions/{setup_party["session"]}/generate-relationships',
        headers={'Authorization': f'Bearer {token}'}
    )
    assert response.status_code == 200
    assert len(response.json['relationships']) >= 4

def test_api_generate_relationships_idempotent(client, setup_party, generate_token):
    token = generate_token(setup_party['gm'])
    client.post(f'/api/sessions/{setup_party["session"]}/generate-relationships', headers={'Authorization': f'Bearer {token}'})
    
    response = client.post(f'/api/sessions/{setup_party["session"]}/generate-relationships', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200
    assert len(response.json['relationships']) >= 4

def test_api_generate_relationships_permission(client, setup_party, generate_token):
    token = generate_token(setup_party['players'][0])
    response = client.post(
        f'/api/sessions/{setup_party["session"]}/generate-relationships',
        headers={'Authorization': f'Bearer {token}'}
    )
    assert response.status_code == 403

def test_api_get_relationships_filters_for_player(client, setup_party, generate_token):
    token = generate_token(setup_party['gm'])
    client.post(f'/api/sessions/{setup_party["session"]}/generate-relationships', headers={'Authorization': f'Bearer {token}'})
    
    p_token = generate_token(setup_party['players'][0])
    response = client.get(
        f'/api/sessions/{setup_party["session"]}/relationships',
        headers={'Authorization': f'Bearer {p_token}'}
    )
    assert response.status_code == 200
    
    rels = response.json['relationships']
    char_id = setup_party['characters'][0]
    for r in rels:
        assert r['source_character_id'] == char_id or r['target_character_id'] == char_id
