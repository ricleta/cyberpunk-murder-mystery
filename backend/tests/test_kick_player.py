"""GM kicking players: allowed at any time; after generation it clears the game and
sends the session back to the lobby so the GM regenerates."""
from extensions import db
from models import Session, SessionMember, Character, Relationship, Secret, SecretKnowledge, Objective
from conftest import create_ungenerated_session, generate_via_api

ROLES5 = ["solo", "exec", "netrunner", "fixer", "media"]


def kick(client, auth, session, user_id, confirm=False, as_user=None):
    url = f'/api/sessions/{session.id}/members/{user_id}' + ('?clear_generated=true' if confirm else '')
    return client.delete(url, headers=auth(as_user or session.gm_id))


def counts(session_id):
    return (Relationship.query.filter_by(session_id=session_id).count(),
            Secret.query.filter_by(session_id=session_id).count(),
            Objective.query.filter_by(session_id=session_id).count())


def test_gm_kicks_player_in_lobby(client, app, auth):
    session, chars = create_ungenerated_session("kick_lobby", ROLES5)
    victim_id, victim_player = chars[1].id, chars[1].player_id
    res = kick(client, auth, session, victim_player)
    assert res.status_code == 200, res.json
    assert res.json['regeneration_required'] is False
    assert res.json['status'] == 'lobby'
    assert SessionMember.query.filter_by(session_id=session.id, user_id=victim_player).first() is None
    assert Character.query.get(victim_id) is None


def test_kick_after_generation_requires_confirmation(client, app, auth):
    session, chars = create_ungenerated_session("kick_confirm", ROLES5)
    generate_via_api(client, auth, session)
    before = counts(session.id)
    assert all(before)

    res = kick(client, auth, session, chars[2].player_id)
    assert res.status_code == 409 and res.json['code'] == 'regeneration_required'
    # nothing changed
    assert counts(session.id) == before
    assert Character.query.get(chars[2].id) is not None
    assert Session.query.get(session.id).status == 'generated'


def test_confirmed_kick_after_generation_clears_game_and_allows_regeneration(client, app, auth):
    session, chars = create_ungenerated_session("kick_generated", ROLES5)
    generate_via_api(client, auth, session, seed=3)
    killer_id = next(c.id for c in chars if c.is_killer)
    victim_id, victim_player = next((c.id, c.player_id) for c in chars if not c.is_killer)

    res = kick(client, auth, session, victim_player, confirm=True)
    assert res.status_code == 200, res.json
    assert res.json['regeneration_required'] is True
    assert res.json['status'] == 'lobby'

    db.session.expire_all()
    s = Session.query.get(session.id)
    assert s.status == 'lobby'
    assert s.generation_seed is None and s.generated_at is None and s.generation_report is None
    assert counts(session.id) == (0, 0, 0)
    assert SecretKnowledge.query.count() == 0
    assert Character.query.get(victim_id) is None
    # the killer choice survives, like a reset
    assert Character.query.get(killer_id).is_killer is True

    # lobby is unlocked again and the game regenerates with the remaining four
    res = client.get(f'/api/sessions/{session.id}', headers=auth(session.gm_id))
    assert res.json['status'] == 'lobby'
    generate_via_api(client, auth, s, seed=3)
    assert all(counts(session.id))


def test_kicking_the_killer_lets_regeneration_pick_a_new_one(client, app, auth):
    session, chars = create_ungenerated_session("kick_killer", ROLES5)
    generate_via_api(client, auth, session, seed=5)
    killer = next(c for c in chars if c.is_killer)

    assert kick(client, auth, session, killer.player_id, confirm=True).status_code == 200
    db.session.expire_all()
    assert Character.query.filter_by(session_id=session.id, is_killer=True).count() == 0
    generate_via_api(client, auth, Session.query.get(session.id), seed=5)
    assert Character.query.filter_by(session_id=session.id, is_killer=True).count() == 1


def test_kicked_player_loses_access(client, app, auth):
    session, chars = create_ungenerated_session("kick_access", ROLES5)
    generate_via_api(client, auth, session)
    victim_player = chars[0].player_id
    assert kick(client, auth, session, victim_player, confirm=True).status_code == 200
    res = client.get(f'/api/sessions/{session.id}', headers=auth(victim_player))
    assert res.status_code == 403


def test_only_gm_can_kick_and_gm_cannot_be_kicked(client, app, auth):
    session, chars = create_ungenerated_session("kick_authz", ROLES5)
    res = kick(client, auth, session, chars[1].player_id, as_user=chars[0].player_id)
    assert res.status_code == 403
    res = kick(client, auth, session, session.gm_id)
    assert res.status_code == 400
    res = kick(client, auth, session, 'nobody')
    assert res.status_code == 404
