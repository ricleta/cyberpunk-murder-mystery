"""Name tokens (Phase 6 §6): generated text stores {{c:<id>}} and names resolve on serve."""
import json
import random

import pytest

from extensions import db
from models import Character, Relationship, Secret, SecretKnowledge, Objective, Session
from conftest import create_test_session, create_ungenerated_session, generate_via_api, all_visible_output, ROLES_10
from generation.name_tokens import (
    RAW_TOKEN_RE, name_token, resolve_names, contains_raw_token, session_name_map,
)
from generation.relationships import _generate_relationships
from generation.secrets import _generate_secrets
from generation.objectives import _generate_objectives
from generation.secret_templates import validate_secret_templates

ROLES5 = ["solo", "exec", "netrunner", "fixer", "media"]


def test_resolve_names_is_single_pass_and_handles_unknown_ids():
    names = {"a": "{{c:b}}", "b": "Bee", "c_sess_0": "Ann"}
    assert resolve_names("hi {{c:a}} and {{c:b}}", names) == "hi {{c:b}} and Bee"
    assert resolve_names("{{c:nobody}}", names) == "Unknown"
    assert resolve_names("{{c:c_sess_0}}!", names) == "Ann!"
    assert resolve_names(None, names) is None
    assert name_token("c_sess_0") == "{{c:c_sess_0}}"
    assert contains_raw_token("x {{c:abc-1_2}} y") and not contains_raw_token("plain")


def test_generated_rows_store_tokens_not_names(app):
    session, chars = create_ungenerated_session("tok_store", ROLES5)
    rng = random.Random(5)
    _generate_relationships(session, rng=rng)
    _generate_secrets(session, rng=rng)
    _generate_objectives(session, rng=rng)

    names = [c.display_name for c in chars]
    stored = [r.description_source for r in Relationship.query.all()] + \
             [r.description_target for r in Relationship.query.all()] + \
             [s.text_subject for s in Secret.query.all()] + [s.text_knower or '' for s in Secret.query.all()] + \
             [o.text for o in Objective.query.all()] + [o.gm_hint or '' for o in Objective.query.all()]
    assert any(RAW_TOKEN_RE.search(t) for t in stored)
    for text in stored:
        for name in names:
            assert name not in text, f"name baked into generated text: {text}"


def test_rename_changes_every_endpoint(client, app, auth):
    session, chars = create_ungenerated_session("tok_rename", ROLES5)
    generate_via_api(client, auth, session, seed=11)

    before = all_visible_output(client, auth, session, chars)
    assert any("Char_solo_0" in text for text in before)

    victim = chars[0]
    res = client.patch(f'/api/sessions/{session.id}/characters/{victim.id}', json={'display_name': 'Zed Renamed'},
                       headers=auth(victim.player_id))
    assert res.status_code == 200, res.json

    after = all_visible_output(client, auth, session, chars)
    joined = "\n".join(after)
    assert "Zed Renamed" in joined
    # The new name replaces the old one in secrets, relationships, objectives and sheets
    assert "Char_solo_0" not in joined

    # ...including for the GM's master sheet once it exists
    master = client.get(f'/api/sessions/{session.id}/master', headers=auth(session.gm_id))
    assert master.status_code == 200
    assert "Zed Renamed" in master.get_data(as_text=True)
    assert "Char_solo_0" not in master.get_data(as_text=True)


def test_name_that_looks_like_a_token_is_not_expanded(client, app, auth):
    session, chars = create_ungenerated_session("tok_fake", ROLES5)
    generate_via_api(client, auth, session, seed=3)
    # The API refuses such a name, so plant it directly in the database
    chars[0].display_name = name_token(chars[1].id)
    db.session.commit()
    app.config['NAME_TOKEN_TRIPWIRE'] = False  # the literal text is legitimate here

    names = session_name_map(session.id)
    assert names[chars[0].id] == name_token(chars[1].id)
    output = "\n".join(all_visible_output(client, auth, session, chars))
    # Char 1's real name must not be substituted for char 0's literal "name"
    assert name_token(chars[1].id) in output
    secret_for_gm = client.get(f'/api/sessions/{session.id}/secrets', headers=auth(session.gm_id)).json['secrets']
    for s in secret_for_gm:
        if s['subject_character_id'] == chars[0].id:
            assert s['subject_name'] == name_token(chars[1].id)


def test_tripwire_fires_on_a_leaked_token(client, app, auth):
    session, chars = create_ungenerated_session("tok_trip", ROLES5)
    chars[0].display_name = '{{c:leak}}'
    db.session.commit()
    with pytest.raises(AssertionError, match='Raw name token leaked'):
        client.get(f'/api/sessions/{session.id}/characters', headers=auth(session.gm_id))


@pytest.mark.parametrize("payload_factory", [
    lambda c: ('patch_char', {'display_name': 'x {{c:abc}} y'}),
    lambda c: ('patch_char', {'description': 'about {{c:abc}}'}),
    lambda c: ('patch_char', {'traits': ['fine', '{{c:abc}}']}),
    lambda c: ('patch_char', {'friend_faction_name': '{{c:abc}}'}),
    lambda c: ('patch_char', {'enemy_faction_name': 'The {{c:abc}} Gang'}),
    lambda c: ('objective', {'character_id': c.id, 'text': 'do {{c:abc}}', 'points': 1}),
    lambda c: ('objective', {'character_id': c.id, 'text': 'ok', 'points': 1, 'gm_hint': '{{c:abc}}'}),
])
def test_user_text_with_reserved_sequence_is_rejected(client, app, auth, payload_factory):
    session, chars = create_ungenerated_session("tok_reject", ROLES5)
    kind, payload = payload_factory(chars[0])
    if kind == 'patch_char':
        res = client.patch(f'/api/sessions/{session.id}/characters/{chars[0].id}', json=payload,
                           headers=auth(session.gm_id))
    else:
        res = client.post(f'/api/sessions/{session.id}/objectives', json=payload, headers=auth(session.gm_id))
    assert res.status_code == 400
    assert 'reserved sequence' in res.json['error']


def test_create_character_and_session_name_reject_tokens(client, app, auth):
    from models import User, SessionMember
    session, chars = create_ungenerated_session("tok_create", ROLES5)
    newbie = User(id="newbie_tok", username="newbie_tok", password_hash="x")
    db.session.add(newbie)
    db.session.add(SessionMember(session_id=session.id, user_id=newbie.id))
    db.session.commit()
    res = client.post(f'/api/sessions/{session.id}/characters', json={'role_key': 'solo', 'display_name': '{{c:x}}'},
                      headers=auth(newbie.id))
    assert res.status_code == 400
    res = client.post('/api/sessions', json={'name': 'Night {{c:x}}'}, headers=auth(newbie.id))
    assert res.status_code == 400


def test_user_edited_objective_text_keeps_tokens_when_unchanged(client, app, auth):
    session, chars = create_ungenerated_session("tok_obj", ROLES5)
    generate_via_api(client, auth, session, seed=21)
    gm = auth(session.gm_id)
    objectives = client.get(f'/api/sessions/{session.id}/objectives', headers=gm).json['objectives']
    target = next(o for o in objectives if o['kind'] in ('relationship', 'exploit_secret'))
    stored_before = Objective.query.get(target['id']).text
    assert contains_raw_token(stored_before)

    # Client echoes the served (resolved) text back: stored version must stay tokenised
    res = client.patch(f'/api/sessions/{session.id}/objectives/{target["id"]}',
                       json={'text': target['text'], 'points': target['points']}, headers=gm)
    assert res.status_code == 200
    db.session.expire_all()
    assert Objective.query.get(target['id']).text == stored_before

    # A real edit replaces the text with the user's plain string
    res = client.patch(f'/api/sessions/{session.id}/objectives/{target["id"]}', json={'text': 'Do something else'}, headers=gm)
    assert res.status_code == 200
    db.session.expire_all()
    assert Objective.query.get(target['id']).text == 'Do something else'


def test_tokens_never_land_in_character_columns(client, app, auth):
    session, chars = create_ungenerated_session("tok_cols", ROLES5)
    generate_via_api(client, auth, session, seed=4)
    client.patch(f'/api/sessions/{session.id}/characters/{chars[1].id}',
                 json={'display_name': 'Renamed', 'description': 'desc', 'traits': ['a', 'b']},
                 headers=auth(chars[1].player_id))
    for c in Character.query.filter_by(session_id=session.id).all():
        for column in Character.__table__.columns:
            assert '{{c:' not in json.dumps(getattr(c, column.name), default=str), column.name


def test_protect_secret_never_mentions_a_knower_token_across_seeds(app):
    from generation.objectives import _check_objectives  # noqa: F401  (invariant also enforced in generation)
    for seed in range(50):
        session, chars = create_ungenerated_session(f"tok_prot_{seed}", ROLES5)
        rng = random.Random(seed)
        _generate_relationships(session, rng=rng)
        _generate_secrets(session, rng=rng)
        _generate_objectives(session, rng=rng)
        knowers = {}
        for k in SecretKnowledge.query.join(Secret).filter(Secret.session_id == session.id).all():
            knowers.setdefault(k.secret_id, set()).add(k.knower_character_id)
        for o in Objective.query.filter_by(session_id=session.id, kind='protect_secret').all():
            for knower_id in knowers.get(o.related_secret_id, set()):
                assert name_token(knower_id) not in o.text
                assert name_token(knower_id) not in (o.gm_hint or '')


def test_templates_with_a_name_modifier_are_rejected(tmp_path):
    from generation.secret_templates import assert_no_name_modifiers
    assert_no_name_modifiers("You told #subjectName# the truth.", "t", "templateSubject")
    with pytest.raises(ValueError, match='modifier'):
        assert_no_name_modifiers("#subjectName.capitalize# lied.", "t", "templateSubject")
    with pytest.raises(ValueError, match='modifier'):
        assert_no_name_modifiers("Ask #otherName.a#.", "t", "templateKnower")
    assert validate_secret_templates()
