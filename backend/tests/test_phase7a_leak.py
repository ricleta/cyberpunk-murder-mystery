"""Phase 7A §2: secret metadata must not let a player tell the killer's trail secrets apart."""
import pytest

from extensions import db
from models import Character, Secret, SecretKnowledge
from conftest import create_ungenerated_session, generate_via_api, ROLES_10

FORBIDDEN = {'template_id', 'category', 'is_killer_core', 'is_killer_trail', 'severity'}
KNOWER_KEYS = {'id', 'kind', 'text', 'about_self', 'how_learned', 'subject_character_id', 'subject_name'}
SIZES = {4: ROLES_10[:4], 8: ROLES_10[:8], 11: ROLES_10 + ['solo']}


def other_secret_dicts(client, auth, session, char):
    """Every secret dict the player gets about OTHER characters, from every endpoint."""
    base = f'/api/sessions/{session.id}'
    headers = auth(char.player_id)
    found = []
    for path in (f'{base}/characters/{char.id}', f'{base}/characters/mine'):
        body = client.get(path, headers=headers).json
        found += [s for s in body['secrets'] if not s.get('about_self')]
    for s in client.get(f'{base}/secrets', headers=headers).json['secrets']:
        if s['subject_character_id'] != char.id:
            found.append(s)
    sheet = client.get(f'{base}/characters/{char.id}/sheet', headers=headers)
    if sheet.status_code == 200:
        found += sheet.json['secrets']['known']
    return found


@pytest.mark.parametrize('size', sorted(SIZES))
def test_known_secrets_have_identical_lean_shape_for_trail_and_normal(client, app, auth, size):
    seeds = range(30) if size == 4 else range(10)
    for seed in seeds:
        session, chars = create_ungenerated_session(f'leak_{size}_{seed}', SIZES[size])
        generate_via_api(client, auth, session, seed=seed)
        trail_ids = {s.id for s in Secret.query.filter_by(session_id=session.id, is_killer_trail=True).all()}
        assert len(trail_ids) == 2
        shapes = set()
        saw_trail = False
        for c in chars:
            for d in other_secret_dicts(client, auth, session, c):
                assert not (FORBIDDEN & set(d)), (set(d) & FORBIDDEN, d)
                shapes.add((frozenset(d), ))
                saw_trail = saw_trail or d['id'] in trail_ids
        assert saw_trail  # a trail knower exists and was checked
        # one shape per endpoint family at most: never a trail-only shape
        by_id = {}
        for c in chars:
            for d in other_secret_dicts(client, auth, session, c):
                by_id.setdefault(d['id'], set()).add(frozenset(d))
        trail_shapes = set().union(*(by_id[i] for i in trail_ids if i in by_id))
        normal_shapes = set().union(*(v for i, v in by_id.items() if i not in trail_ids))
        assert trail_shapes <= normal_shapes


def test_own_secrets_expose_severity_but_not_template_or_category(client, app, auth):
    session, chars = create_ungenerated_session('leak_own', ROLES_10[:5])
    generate_via_api(client, auth, session, seed=3)
    for c in chars:
        own = [s for s in client.get(f'/api/sessions/{session.id}/characters/mine', headers=auth(c.player_id)).json['secrets']
               if s['about_self']]
        assert own
        for s in own:
            assert 'severity' in s
            assert not ({'template_id', 'category'} & set(s))
        for s in client.get(f'/api/sessions/{session.id}/secrets', headers=auth(c.player_id)).json['secrets']:
            if s['subject_character_id'] == c.id:
                assert not ({'template_id', 'category'} & set(s))


def test_gm_still_gets_full_fields(client, app, auth):
    session, chars = create_ungenerated_session('leak_gm', ROLES_10[:5])
    generate_via_api(client, auth, session, seed=4)
    gm = auth(session.gm_id)
    sheet = client.get(f'/api/sessions/{session.id}/characters/{chars[0].id}', headers=gm).json
    assert all({'template_id', 'category', 'severity'} <= set(s) for s in sheet['secrets'])
    for s in client.get(f'/api/sessions/{session.id}/secrets', headers=gm).json['secrets']:
        assert {'template_id', 'category', 'severity', 'is_killer_core', 'is_killer_trail', 'knowers'} <= set(s)
    master = client.get(f'/api/sessions/{session.id}/master', headers=gm).json
    assert all({'template_id', 'category', 'severity'} <= set(s) for s in master['secrets'])
