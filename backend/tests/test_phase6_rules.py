"""Phase 6: lobby locks, reset, seed/determinism, validation, config and error handling."""
import json

import pytest

from app import create_app
from config import Config
from extensions import db
from models import (
    User, Session, SessionMember, Character, Relationship, Secret, SecretKnowledge, Objective,
)
from conftest import (
    create_ungenerated_session, generate_via_api, PhaseTestConfig,
)
from services.lifepath_data import load_lifepath_data

ROLES5 = ["solo", "exec", "netrunner", "fixer", "media"]


def shared_options(field_key):
    shared = load_lifepath_data()[0]
    return [o['id'] for o in shared[field_key]['options']]


def generated_session(client, auth, sid, seed=1):
    session, chars = create_ungenerated_session(sid, ROLES5)
    generate_via_api(client, auth, session, seed=seed)
    return session, chars


def patch_char(client, auth, session, char, body, as_user=None):
    return client.patch(f'/api/sessions/{session.id}/characters/{char.id}', json=body,
                        headers=auth(as_user or char.player_id))


# --- §7.1 locks -------------------------------------------------------------------------

def test_locked_fields_409_on_change_but_noop_when_identical(client, app, auth):
    session, chars = create_ungenerated_session("lock_fields", ROLES5)
    c = chars[0]
    c.personality = shared_options('personality')[0]  # fixture characters have no personality set
    db.session.commit()
    generate_via_api(client, auth, session)
    other_role = next(r for r in ("tech", "medtech", "nomad") if r != c.role_key)
    other_personality = next(o for o in shared_options('personality') if o != c.personality)

    changes = [
        {'role_key': other_role},
        {'personality': other_personality},
        {'life_goals': next(o for o in shared_options('lifeGoals') if o != c.life_goals)},
        {'friend_faction_name': 'Somebody New'},
        {'enemy_faction_type': load_lifepath_data()[1]['options'][0]['id']},
    ]
    for body in changes:
        res = patch_char(client, auth, session, c, body)
        assert res.status_code == 409, body
        assert res.json['code'] == 'session_locked'
        assert 'reset' in res.json['error'].lower()

    # Re-sending identical values (a full form) is fine, and free fields still work
    same = {
        'role_key': c.role_key, 'personality': c.personality, 'display_name': 'Renamed While Locked',
        'description': 'New description', 'traits': ['calm', 'sharp'],
    }
    res = patch_char(client, auth, session, c, same)
    assert res.status_code == 200, res.json
    db.session.expire_all()
    assert Character.query.get(c.id).display_name == 'Renamed While Locked'
    assert Character.query.get(c.id).role_key == c.role_key


def test_locked_role_specific_lifepath_compares_merged_dict(client, app, auth):
    session, chars = create_ungenerated_session("lock_rsl", ROLES5)
    c = chars[0]
    tables = load_lifepath_data()[3][c.role_key]['tables']
    key = next(iter(tables))
    first, second = tables[key]['options'][0]['id'], tables[key]['options'][1]['id']
    c.role_specific_lifepath = {key: first}
    db.session.commit()
    generate_via_api(client, auth, session)

    assert patch_char(client, auth, session, c, {'role_specific_lifepath': {key: first}}).status_code == 200
    res = patch_char(client, auth, session, c, {'role_specific_lifepath': {key: second}})
    assert res.status_code == 409 and res.json['code'] == 'session_locked'


def test_locked_killer_flag_gm_only_and_boolean(client, app, auth):
    session, chars = generated_session(client, auth, "lock_killer")
    killer = next(c for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer)
    innocent = next(c for c in Character.query.filter_by(session_id=session.id).all() if not c.is_killer)
    gm = session.gm_id

    assert patch_char(client, auth, session, killer, {'is_killer': True}, as_user=gm).status_code == 200
    res = patch_char(client, auth, session, innocent, {'is_killer': True}, as_user=gm)
    assert res.status_code == 409 and res.json['code'] == 'session_locked'
    # A player's is_killer is ignored as before: no change, no error
    assert patch_char(client, auth, session, innocent, {'is_killer': True}).status_code == 200
    db.session.expire_all()
    assert not Character.query.get(innocent.id).is_killer
    assert patch_char(client, auth, session, innocent, {'is_killer': 'yes'}, as_user=gm).status_code == 400


def test_lobby_session_allows_everything(client, app, auth):
    session, chars = create_ungenerated_session("lock_lobby", ROLES5)
    c = chars[0]
    res = patch_char(client, auth, session, c, {'personality': next(o for o in shared_options('personality') if o != c.personality)})
    assert res.status_code == 200
    assert patch_char(client, auth, session, c, {'is_killer': True}, as_user=session.gm_id).status_code == 200


def test_roster_actions_are_locked_but_session_delete_is_not(client, app, auth):
    session, chars = generated_session(client, auth, "lock_roster")
    newcomer = User(id="newcomer_lock", username="newcomer_lock", password_hash="x")
    db.session.add(newcomer)
    db.session.add(SessionMember(session_id=session.id, user_id=newcomer.id))
    db.session.commit()

    res = client.post(f'/api/sessions/{session.id}/characters', json={'role_key': 'solo', 'display_name': 'Late'},
                      headers=auth(newcomer.id))
    assert res.status_code == 409 and res.json['code'] == 'session_locked'

    res = client.post('/api/sessions/join', json={'join_code': session.join_code}, headers=auth("stranger_lock"))
    # an unknown user id still authenticates (token only); a new member is refused while locked
    assert res.status_code == 409 and res.json['code'] == 'session_locked'
    # an existing member re-joining still gets the friendly 200
    res = client.post('/api/sessions/join', json={'join_code': session.join_code}, headers=auth(chars[0].player_id))
    assert res.status_code == 200 and res.json['message'] == 'Already a member'

    res = client.delete(f'/api/sessions/{session.id}/members/{chars[1].player_id}', headers=auth(session.gm_id))
    assert res.status_code == 409 and res.json['code'] == 'session_locked'
    res = client.delete(f'/api/sessions/{session.id}/characters/{chars[1].id}', headers=auth(session.gm_id))
    assert res.status_code == 409 and res.json['code'] == 'session_locked'

    res = client.delete(f'/api/sessions/{session.id}', headers=auth(session.gm_id))
    assert res.status_code == 200


def test_authz_checks_come_before_lock_checks(client, app, auth):
    session, chars = generated_session(client, auth, "lock_order")
    stranger = "outsider_lock"
    db.session.add(User(id=stranger, username=stranger, password_hash="x"))
    db.session.commit()

    # not a member: 403, not 409
    res = client.post(f'/api/sessions/{session.id}/characters', json={'role_key': 'solo', 'display_name': 'x'},
                      headers=auth(stranger))
    assert res.status_code == 403
    # a player editing somebody else's character: 403, never reaching the lock check
    res = patch_char(client, auth, session, chars[1], {'role_key': 'tech'}, as_user=chars[0].player_id)
    assert res.status_code == 403
    res = client.delete(f'/api/sessions/{session.id}/members/{chars[1].player_id}', headers=auth(chars[0].player_id))
    assert res.status_code == 403
    res = client.delete(f'/api/sessions/{session.id}/characters/{chars[1].id}', headers=auth(chars[0].player_id))
    assert res.status_code == 403
    res = patch_char(client, auth, session, chars[1], {'role_key': 'tech'}, as_user=stranger)
    assert res.status_code == 403
    # unknown session / character: 404
    assert client.post('/api/sessions/missing/characters', json={}, headers=auth(chars[0].player_id)).status_code == 404
    res = client.patch(f'/api/sessions/{session.id}/characters/missing', json={}, headers=auth(session.gm_id))
    assert res.status_code == 404


# --- §7.2 reset ---------------------------------------------------------------------------

def test_reset_clears_generated_content_and_keeps_characters(client, app, auth):
    session, chars = generated_session(client, auth, "reset_all", seed=9)
    gm = auth(session.gm_id)
    # a manual objective and a manual edit must go too
    res = client.post(f'/api/sessions/{session.id}/objectives',
                      json={'character_id': chars[0].id, 'text': 'Manual goal', 'points': 1}, headers=gm)
    assert res.status_code == 201
    killer_ids = {c.id for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer}
    n_chars = Character.query.filter_by(session_id=session.id).count()
    assert Objective.query.filter_by(session_id=session.id).count() > 0

    res = client.post(f'/api/sessions/{session.id}/reset', headers=gm)
    assert res.status_code == 200, res.json
    assert res.json['deleted']['objectives'] > 0 and res.json['deleted']['secrets'] > 0
    assert res.json['deleted']['relationships'] > 0

    db.session.expire_all()
    assert Objective.query.filter_by(session_id=session.id).count() == 0
    assert Secret.query.filter_by(session_id=session.id).count() == 0
    assert SecretKnowledge.query.count() == 0
    assert Relationship.query.filter_by(session_id=session.id).count() == 0
    s = Session.query.get(session.id)
    assert s.status == 'lobby'
    assert s.generation_seed is None and s.generated_at is None and s.generation_report is None
    assert Character.query.filter_by(session_id=session.id).count() == n_chars
    assert {c.id for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer} == killer_ids

    # idempotent on a clean lobby
    res = client.post(f'/api/sessions/{session.id}/reset', headers=gm)
    assert res.status_code == 200
    assert res.json['deleted'] == {'objectives': 0, 'secrets': 0, 'relationships': 0}

    # the killer survives regeneration, role/Lifepath unlock again
    generate_via_api(client, auth, session, seed=10)
    db.session.expire_all()
    assert {c.id for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer} == killer_ids


def test_reset_unlocks_role_changes(client, app, auth):
    session, chars = generated_session(client, auth, "reset_unlock")
    assert patch_char(client, auth, session, chars[0], {'role_key': 'tech'}).status_code == 409
    assert client.post(f'/api/sessions/{session.id}/reset', headers=auth(session.gm_id)).status_code == 200
    assert patch_char(client, auth, session, chars[0], {'role_key': 'tech'}).status_code == 200


def test_reset_authz_and_status_guards(client, app, auth):
    session, chars = generated_session(client, auth, "reset_authz")
    url = f'/api/sessions/{session.id}/reset'
    assert client.post(url).status_code == 401
    assert client.post(url, headers=auth(chars[0].player_id)).status_code == 403
    assert client.post('/api/sessions/missing/reset', headers=auth(session.gm_id)).status_code == 404
    for status in ('active', 'closed'):
        session.status = status
        db.session.commit()
        res = client.post(url, headers=auth(session.gm_id))
        assert res.status_code == 409
        assert Secret.query.filter_by(session_id=session.id).count() > 0


def test_failed_reset_leaves_data_intact(client, app, auth, monkeypatch):
    session, chars = generated_session(client, auth, "reset_fail")
    before = {
        'objectives': Objective.query.filter_by(session_id=session.id).count(),
        'secrets': Secret.query.filter_by(session_id=session.id).count(),
        'relationships': Relationship.query.filter_by(session_id=session.id).count(),
    }

    def boom(_session):
        raise RuntimeError('simulated failure after the rows were deleted')

    monkeypatch.setattr('routes.generation_routes.clear_generation_metadata', boom)
    res = client.post(f'/api/sessions/{session.id}/reset', headers=auth(session.gm_id))
    assert res.status_code == 500 and res.json == {'error': 'Internal server error'}

    db.session.expire_all()
    assert Objective.query.filter_by(session_id=session.id).count() == before['objectives']
    assert Secret.query.filter_by(session_id=session.id).count() == before['secrets']
    assert Relationship.query.filter_by(session_id=session.id).count() == before['relationships']
    s = Session.query.get(session.id)
    assert s.status == 'generated' and s.generation_seed is not None


def test_regenerate_clears_old_metadata_and_keeps_killer(client, app, auth):
    session, chars = generated_session(client, auth, "regen_meta", seed=100)
    killer = next(c.id for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer)
    generate_via_api(client, auth, session, seed=200, regenerate=True)
    db.session.expire_all()
    s = Session.query.get(session.id)
    assert s.generation_seed == 200 and s.generation_report['seed'] == 200
    assert {c.id for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer} == {killer}


# --- §5 seed / determinism -----------------------------------------------------------------

def snapshot(session_id):
    names = {c.id: i for i, c in enumerate(sorted(Character.query.filter_by(session_id=session_id).all(), key=lambda c: c.id))}
    rels = sorted((names[r.source_character_id], names[r.target_character_id], r.rel_type, r.description_source, r.description_target)
                  for r in Relationship.query.filter_by(session_id=session_id).all())
    secrets = sorted((names[s.subject_character_id], s.template_id, s.kind, s.text_subject, s.text_knower or '')
                     for s in Secret.query.filter_by(session_id=session_id).all())
    objectives = sorted((names[o.character_id], o.template_id, o.kind, o.points, o.text, o.gm_hint or '')
                        for o in Objective.query.filter_by(session_id=session_id).all())
    return rels, secrets, objectives


def test_same_seed_gives_identical_output_through_the_pipeline(client, app, auth):
    session, chars = create_ungenerated_session("seed_same", ROLES5)
    generate_via_api(client, auth, session, seed=2024)
    db.session.expire_all()
    first = snapshot(session.id)
    generate_via_api(client, auth, session, seed=2024, regenerate=True)
    db.session.expire_all()
    assert snapshot(session.id) == first
    generate_via_api(client, auth, session, seed=2025, regenerate=True)
    db.session.expire_all()
    assert snapshot(session.id) != first


def test_seed_is_gm_only(client, app, auth):
    session, chars = generated_session(client, auth, "seed_authz", seed=555)
    gm = client.get(f'/api/sessions/{session.id}', headers=auth(session.gm_id)).json
    assert gm['generation_seed'] == 555 and gm['generated_at']
    for c in chars:
        player = client.get(f'/api/sessions/{session.id}', headers=auth(c.player_id)).json
        assert 'generation_seed' not in player and 'generated_at' not in player
        assert '555' not in json.dumps(player)
    master = client.get(f'/api/sessions/{session.id}/master', headers=auth(chars[0].player_id))
    assert master.status_code == 403


def test_seed_param_ignored_outside_debug_and_random_by_default(client, app, auth):
    session, chars = create_ungenerated_session("seed_prod", ROLES5)
    app.config['DEBUG'] = False
    app.config['TESTING'] = False
    try:
        res = client.post(f'/api/sessions/{session.id}/generate?seed=42', headers=auth(session.gm_id))
        assert res.status_code == 200, res.json
    finally:
        app.config['DEBUG'] = True
        app.config['TESTING'] = True
    db.session.expire_all()
    # (a 1-in-4-billion collision aside) the requested seed was not honoured
    assert Session.query.get(session.id).generation_seed != 42


def test_bad_seed_is_400(client, app, auth):
    session, chars = create_ungenerated_session("seed_bad", ROLES5)
    res = client.post(f'/api/sessions/{session.id}/generate?seed=abc', headers=auth(session.gm_id))
    assert res.status_code == 400


def test_report_is_persisted_across_app_instances(tmp_path, auth_factory=None):
    import jwt as pyjwt
    from datetime import datetime, timedelta
    uri = 'sqlite:///' + str(tmp_path / 'persist.db').replace('\\', '/')

    class FileConfig(PhaseTestConfig):
        SQLALCHEMY_DATABASE_URI = uri

    def token(uid):
        return {'Authorization': 'Bearer ' + pyjwt.encode(
            {'user_id': uid, 'exp': datetime.utcnow() + timedelta(days=1)}, 'test-secret', algorithm='HS256')}

    app1 = create_app(FileConfig)
    with app1.app_context():
        db.create_all()
        session, chars = create_ungenerated_session("persist_s", ROLES5)
        res = app1.test_client().post(f'/api/sessions/{session.id}/generate?seed=77', headers=token(session.gm_id))
        assert res.status_code == 200, res.json
        gm_id = session.gm_id
        db.session.remove()
        db.engine.dispose()

    app2 = create_app(FileConfig)  # a fresh app object: nothing survives in memory
    with app2.app_context():
        c2 = app2.test_client()
        for kind in ('secrets', 'objectives'):
            res = c2.get(f'/api/sessions/persist_s/{kind}/report', headers=token(gm_id))
            assert res.status_code == 200, res.json
            assert len(res.json['characters']) == 5
            assert any(ch.get('secrets' if kind == 'secrets' else 'objectives') for ch in res.json['characters'])
        report = Session.query.get('persist_s').generation_report
        assert report['seed'] == 77 and set(report) >= {'secrets', 'objectives'}
        db.session.remove()
        db.engine.dispose()


def test_report_endpoints_resolve_names_and_are_debug_only(client, app, auth):
    session, chars = generated_session(client, auth, "report_names")
    gm = auth(session.gm_id)
    for kind in ('secrets', 'objectives'):
        res = client.get(f'/api/sessions/{session.id}/{kind}/report', headers=gm)
        assert res.status_code == 200
        assert '{{c:' not in res.get_data(as_text=True)
    app.config['DEBUG'] = False
    try:
        for kind in ('secrets', 'objectives'):
            assert client.get(f'/api/sessions/{session.id}/{kind}/report', headers=gm).status_code == 404
    finally:
        app.config['DEBUG'] = True


# --- §4.7 standalone generators -------------------------------------------------------------

def test_standalone_generators_are_404_outside_debug_and_testing(app, auth):
    session, chars = create_ungenerated_session("standalone", ROLES5)
    app.config['DEBUG'] = False
    app.config['TESTING'] = False
    try:
        client = app.test_client()
        for name in ('generate-relationships', 'generate-secrets', 'generate-objectives'):
            res = client.post(f'/api/sessions/{session.id}/{name}', headers=auth(session.gm_id))
            assert res.status_code == 404, name
    finally:
        app.config['DEBUG'] = True
        app.config['TESTING'] = True


# --- §4.2 validation ------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {'display_name': ''}, {'display_name': '   '}, {'display_name': 'x' * 51}, {'display_name': 7},
    {'display_name': None}, {'description': 'x' * 4001}, {'description': 5}, {'traits': 'calm'},
    {'traits': ['x'] * 21}, {'traits': ['x' * 61]}, {'traits': [1]}, {'friend_faction_name': 'x' * 61},
    {'enemy_faction_name': 12}, {'role_key': 'astronaut'}, {'role_key': 5}, {'role_specific_lifepath': []},
    {'role_specific_lifepath': 'x'}, {'is_killer': 'true'}, {'personality': 'not_an_option'},
])
def test_character_patch_validation_is_400_never_500(client, app, auth, body):
    session, chars = create_ungenerated_session("val_patch", ROLES5)
    res = patch_char(client, auth, session, chars[0], body, as_user=session.gm_id)
    assert res.status_code == 400, (body, res.get_data(as_text=True))
    assert 'error' in res.json


def test_character_patch_accepts_boundary_values(client, app, auth):
    session, chars = create_ungenerated_session("val_ok", ROLES5)
    body = {'display_name': 'x' * 50, 'description': 'd' * 4000, 'traits': ['t' * 60] * 20,
            'friend_faction_name': 'f' * 60, 'enemy_faction_name': 'e' * 60}
    assert patch_char(client, auth, session, chars[0], body).status_code == 200
    res = patch_char(client, auth, session, chars[0], {'display_name': '  trimmed  '})
    assert res.status_code == 200
    db.session.expire_all()
    assert Character.query.get(chars[0].id).display_name == 'trimmed'


@pytest.mark.parametrize("body", [[], "text", 5, [1, 2], None])
def test_non_object_json_bodies_are_400(client, app, auth, body):
    session, chars = create_ungenerated_session("val_body", ROLES5)
    gm = auth(session.gm_id)
    for method, url in [
        ('patch', f'/api/sessions/{session.id}/characters/{chars[0].id}'),
        ('post', '/api/sessions'),
        ('post', '/api/sessions/join'),
        ('post', f'/api/sessions/{session.id}/objectives'),
        ('post', '/api/auth/signup'),
        ('post', '/api/auth/login'),
    ]:
        res = getattr(client, method)(url, data=json.dumps(body), content_type='application/json', headers=gm)
        if body is None and method in ('post', 'patch'):
            # literal JSON null is also not an object
            pass
        assert res.status_code in (400, 404), (method, url, res.status_code, res.get_data(as_text=True))
        assert res.status_code != 500


def test_create_character_validation(client, app, auth):
    session, chars = create_ungenerated_session("val_create", ROLES5)
    newbie = User(id="newbie_val", username="newbie_val", password_hash="x")
    db.session.add(newbie)
    db.session.add(SessionMember(session_id=session.id, user_id=newbie.id))
    db.session.commit()
    url = f'/api/sessions/{session.id}/characters'
    assert client.post(url, json={'role_key': 'astronaut', 'display_name': 'Neil'}, headers=auth(newbie.id)).status_code == 400
    assert client.post(url, json={'role_key': 'solo', 'display_name': 'x' * 51}, headers=auth(newbie.id)).status_code == 400
    assert client.post(url, json={'role_key': 'solo', 'display_name': 5}, headers=auth(newbie.id)).status_code == 400
    assert client.post(url, json={'role_key': ['solo'], 'display_name': 'ok'}, headers=auth(newbie.id)).status_code == 400
    assert client.post(url, json={'role_key': 'solo', 'display_name': ' Neil '}, headers=auth(newbie.id)).status_code == 201


def test_session_name_and_objective_limits(client, app, auth):
    session, chars = create_ungenerated_session("val_obj", ROLES5)
    gm = auth(session.gm_id)
    assert client.post('/api/sessions', json={'name': ''}, headers=gm).status_code == 400
    assert client.post('/api/sessions', json={'name': 'x' * 81}, headers=gm).status_code == 400
    assert client.post('/api/sessions', json={'name': 5}, headers=gm).status_code == 400
    assert client.post('/api/sessions', json={'name': 'x' * 80}, headers=gm).status_code == 201

    url = f'/api/sessions/{session.id}/objectives'
    base = {'character_id': chars[0].id, 'points': 2}
    assert client.post(url, json={**base, 'text': 'x' * 501}, headers=gm).status_code == 400
    assert client.post(url, json={**base, 'text': 'ok', 'gm_hint': 'x' * 501}, headers=gm).status_code == 400
    assert client.post(url, json={**base, 'text': 'ok', 'gm_hint': 5}, headers=gm).status_code == 400
    assert client.post(url, json={**base, 'text': 'x' * 500, 'gm_hint': 'h' * 500}, headers=gm).status_code == 201
    created = client.post(url, json={**base, 'text': 'patch me'}, headers=gm).json['objective']
    assert client.patch(f'{url}/{created["id"]}', json={'text': 'y' * 501}, headers=gm).status_code == 400
    assert client.patch(f'{url}/{created["id"]}', json={'gm_hint': 'z' * 501}, headers=gm).status_code == 400
    assert client.post(url, json={**base, 'text': 5}, headers=gm).status_code == 400
    assert client.post(url, json={'character_id': ['x'], 'text': 'ok', 'points': 1}, headers=gm).status_code == 404


@pytest.mark.parametrize("username,password,ok", [
    ("ab", "longenough", False), ("a" * 33, "longenough", False), ("bad name", "longenough", False),
    ("emoji😀user", "longenough", False), ("good_user.1-x", "short", False), ("good_user", "12345678", True),
])
def test_signup_rules(client, app, username, password, ok):
    res = client.post('/api/auth/signup', json={'username': username, 'password': password})
    assert (res.status_code == 201) == ok, res.get_data(as_text=True)
    if not ok:
        assert res.status_code == 400 and 'error' in res.json


def test_existing_short_passwords_can_still_log_in(client, app):
    from werkzeug.security import generate_password_hash
    db.session.add(User(id='legacy', username='ab', password_hash=generate_password_hash('pw')))
    db.session.commit()
    res = client.post('/api/auth/login', json={'username': 'ab', 'password': 'pw'})
    assert res.status_code == 200 and res.json['token']


# --- §4.3 config -----------------------------------------------------------------------------

class ProdConfig(Config):
    TESTING = False
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SECRET_KEY = 'a-real-secret'
    JWT_SECRET_KEY = 'a-real-jwt-secret'
    CORS_ORIGINS = 'https://me.github.io'


def test_production_refuses_default_secrets():
    class DefaultSecret(ProdConfig):
        SECRET_KEY = 'dev-secret-key'
    class DefaultJwt(ProdConfig):
        JWT_SECRET_KEY = 'jwt-dev-secret-key'
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        create_app(DefaultSecret)
    with pytest.raises(RuntimeError, match='JWT_SECRET_KEY'):
        create_app(DefaultJwt)


def test_production_requires_cors_origins():
    class NoCors(ProdConfig):
        CORS_ORIGINS = None
    class Wildcard(ProdConfig):
        CORS_ORIGINS = '*'
    with pytest.raises(RuntimeError, match='CORS_ORIGINS'):
        create_app(NoCors)
    with pytest.raises(RuntimeError, match='CORS_ORIGINS'):
        create_app(Wildcard)


def test_production_starts_with_proper_config_and_restricts_cors():
    app = create_app(ProdConfig)
    client = app.test_client()
    ok = client.get('/api/health', headers={'Origin': 'https://me.github.io'})
    assert ok.headers.get('Access-Control-Allow-Origin') == 'https://me.github.io'
    evil = client.get('/api/health', headers={'Origin': 'https://evil.example'})
    assert 'Access-Control-Allow-Origin' not in evil.headers
    # localhost is not implicitly allowed once origins are configured
    local = client.get('/api/health', headers={'Origin': 'http://localhost:8000'})
    assert 'Access-Control-Allow-Origin' not in local.headers


def test_dev_defaults_allow_any_localhost_port_only():
    class DevConfig(ProdConfig):
        DEBUG = True
        SECRET_KEY = 'dev-secret-key'
        JWT_SECRET_KEY = 'jwt-dev-secret-key'
        CORS_ORIGINS = None
    client = create_app(DevConfig).test_client()
    for origin in ('http://localhost:8000', 'http://127.0.0.1:5500', 'http://localhost'):
        assert client.get('/api/health', headers={'Origin': origin}).headers.get('Access-Control-Allow-Origin') == origin
    for origin in ('https://evil.example', 'http://localhost.evil.com'):
        assert 'Access-Control-Allow-Origin' not in client.get('/api/health', headers={'Origin': origin}).headers


def test_cors_only_applies_to_api_routes():
    client = create_app(ProdConfig).test_client()
    res = client.get('/not-api', headers={'Origin': 'https://me.github.io'})
    assert 'Access-Control-Allow-Origin' not in res.headers


# --- §4.5 error handling ------------------------------------------------------------------------

def test_unexpected_errors_are_json_and_logged(app, caplog):
    @app.route('/api/boom')
    def boom():
        raise RuntimeError('secret internals')

    client = app.test_client()
    with caplog.at_level('ERROR'):
        res = client.get('/api/boom')
    assert res.status_code == 500
    assert res.is_json and res.json == {'error': 'Internal server error'}
    assert 'secret internals' not in res.get_data(as_text=True)
    assert 'secret internals' in caplog.text  # traceback went to the server log

    missing = client.get('/api/no-such-route')
    assert missing.status_code == 404 and missing.is_json
    assert client.post('/api/health').status_code == 405 and client.post('/api/health').is_json


def test_pipeline_crash_is_logged_and_rolled_back(client, app, auth, monkeypatch, caplog):
    session, chars = create_ungenerated_session("crash_log", ROLES5)

    def boom(*a, **k):
        raise RuntimeError('stage exploded')

    monkeypatch.setattr('routes.generation_routes._generate_secrets', boom)
    with caplog.at_level('ERROR'):
        res = client.post(f'/api/sessions/{session.id}/generate', headers=auth(session.gm_id))
    assert res.status_code == 500 and res.json == {'error': 'Internal server error'}
    assert 'stage exploded' in caplog.text
    db.session.expire_all()
    assert Relationship.query.filter_by(session_id=session.id).count() == 0
    assert Session.query.get(session.id).status == 'lobby'


# --- §4.6 data path --------------------------------------------------------------------------------

def test_lifepath_data_comes_from_backend_data_and_fails_loudly(monkeypatch, tmp_path):
    from services import lifepath_data
    assert lifepath_data.get_base_data_dir().replace('\\', '/').endswith('backend/data')
    monkeypatch.setattr(lifepath_data, 'get_base_data_dir', lambda: str(tmp_path))
    with pytest.raises(RuntimeError, match='missing'):
        lifepath_data.load_lifepath_data(reload=True)
    monkeypatch.undo()
    lifepath_data.load_lifepath_data(reload=True)
