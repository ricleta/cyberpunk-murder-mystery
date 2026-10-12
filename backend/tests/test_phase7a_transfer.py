"""Phase 7A: character export (§6) and import (§7)."""
import json

import pytest

from extensions import db
from models import Character, SessionMember, User
from conftest import create_ungenerated_session, generate_via_api
from services.character_export import FORMAT_BUNDLE, FORMAT_CHARACTER, SCHEMA_VERSION
from services.character_updates import SHARED_FIELDS_MAP
from services.lifepath_data import load_lifepath_data
from test_phase7a import FORBIDDEN_SHEET_KEYS, walk_keys

ROLES5 = ["solo", "exec", "netrunner", "fixer", "media"]


def complete_character(c, offset=0):
    """Fill every Lifepath field with real options (the shared fixture characters have none)."""
    shared, faction_types, faction_feelings, roles = load_lifepath_data()
    for i, (field, key) in enumerate(SHARED_FIELDS_MAP.items()):
        options = shared[key]['options']
        setattr(c, field, options[(i + offset) % len(options)]['id'])
    c.friend_faction_type = faction_types['options'][offset % len(faction_types['options'])]['id']
    c.friend_faction_name = f'Friends of {c.role_key}'
    c.friend_faction_feeling = faction_feelings['friendFeelings']['options'][0]['id']
    c.enemy_faction_type = faction_types['options'][(offset + 1) % len(faction_types['options'])]['id']
    c.enemy_faction_name = f'Enemies of {c.role_key}'
    c.enemy_faction_feeling = faction_feelings['enemyFeelings']['options'][1]['id']
    c.role_specific_lifepath = {k: t['options'][(offset + 1) % len(t['options'])]['id']
                                for k, t in roles[c.role_key]['tables'].items()}
    c.description = f'About {c.role_key}.\nSecond line.'
    c.traits = ['secretive', 'mirrorshades']
    c.lifepath_complete = True
    db.session.commit()


def export_file(client, auth, session, char, user=None):
    res = client.get(f'/api/sessions/{session.id}/characters/{char.id}/export', headers=auth(user or char.player_id))
    assert res.status_code == 200, res.json
    return res.json


def new_member_session(name):
    """A lobby session with a GM, five characters and one fresh member without a character."""
    session, chars = create_ungenerated_session(name, ROLES5)
    user = User(id=f'newcomer_{name}', username=f'newcomer_{name}', password_hash='x')
    db.session.add(user)
    db.session.add(SessionMember(session_id=session.id, user_id=user.id))
    db.session.commit()
    return session, chars, user


def post_import(client, auth, url, body, user, **query):
    qs = '&'.join(f'{k}={v}' for k, v in query.items())
    payload = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.post(url + (f'?{qs}' if qs else ''), data=payload, content_type='application/json', headers=auth(user))


def mutated(data, fn):
    copy = json.loads(json.dumps(data))
    fn(copy)
    return copy


def row(report, field):
    return next(f for f in report['fields'] if f['field'] == field)


# --- export ----------------------------------------------------------------------------------------------

def test_export_content_matches_the_character_and_hides_everything_else(client, app, auth):
    session, chars = create_ungenerated_session('ex_content', ROLES5)
    complete_character(chars[2])
    generate_via_api(client, auth, session, seed=2)
    killer = next(c for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer)
    for c in chars:
        data = export_file(client, auth, session, c)
        assert data['format'] == FORMAT_CHARACTER and data['schema_version'] == SCHEMA_VERSION
        assert data['exported_at'].endswith('Z')
        text = json.dumps(data)
        for forbidden in ('is_killer', 'player_id', session.id, c.id, c.player_id, 'gm_hint', 'template_id'):
            assert forbidden not in text
        assert not (FORBIDDEN_SHEET_KEYS & set(walk_keys(data)))
    data = export_file(client, auth, session, chars[2])
    ch = data['character']
    assert ch['display_name'] == chars[2].display_name
    assert ch['role'] == {'id': 'netrunner', 'text': 'Netrunner'}
    assert ch['description'] == chars[2].description and ch['traits'] == ['secretive', 'mirrorshades']
    shared = ch['lifepath']['shared']
    assert set(shared) == set(SHARED_FIELDS_MAP) and all(set(v) == {'id', 'text'} for v in shared.values())
    assert shared['personality']['id'] == chars[2].personality
    assert ch['lifepath']['factions']['friend']['name'] == 'Friends of netrunner'
    tables = load_lifepath_data()[3]['netrunner']['tables']
    assert set(ch['lifepath']['role_specific']) == set(tables)
    assert killer.id not in json.dumps(data)


def test_incomplete_lifepath_exports_nulls(client, app, auth):
    session, chars = create_ungenerated_session('ex_null', ROLES5)
    for field in SHARED_FIELDS_MAP:
        setattr(chars[0], field, None)
    chars[0].role_specific_lifepath = {}
    chars[0].lifepath_complete = False
    db.session.commit()
    ch = export_file(client, auth, session, chars[0])['character']
    assert all(v is None for v in ch['lifepath']['shared'].values())
    assert ch['lifepath']['factions']['friend'] == {'type': None, 'name': None, 'feeling': None}
    assert all(v is None for v in ch['lifepath']['role_specific'].values())


def test_export_authz_and_bundle(client, app, auth):
    session, chars = create_ungenerated_session('ex_authz', ROLES5)
    base = f'/api/sessions/{session.id}/characters'
    assert client.get(f'{base}/{chars[0].id}/export').status_code == 401
    assert client.get(f'{base}/{chars[0].id}/export', headers=auth(chars[1].player_id)).status_code == 403
    assert client.get(f'{base}/{chars[0].id}/export', headers=auth(session.gm_id)).status_code == 200
    assert client.get(f'{base}/missing/export', headers=auth(session.gm_id)).status_code == 404
    res = client.get(f'{base}/{chars[0].id}/export', headers=auth(chars[0].player_id))
    assert res.headers['Cache-Control'] == 'no-store'

    # /characters/export is the bundle, not a character called "export"
    assert client.get(f'{base}/export', headers=auth(chars[0].player_id)).status_code == 403
    assert client.get('/api/sessions/missing/characters/export', headers=auth(session.gm_id)).status_code == 404
    res = client.get(f'{base}/export', headers=auth(session.gm_id))
    assert res.status_code == 200
    bundle = res.json
    assert bundle['format'] == FORMAT_BUNDLE and bundle['session_name'] == session.name
    names = [c['display_name'] for c in bundle['characters']]
    assert names == sorted(names, key=str.lower) and len(names) == 5
    assert res.headers['Cache-Control'] == 'no-store'
    assert 'is_killer' not in json.dumps(bundle)


def test_export_works_in_any_status(client, app, auth):
    session, chars = create_ungenerated_session('ex_status', ROLES5)
    generate_via_api(client, auth, session)
    assert client.get(f'/api/sessions/{session.id}/characters/{chars[0].id}/export', headers=auth(chars[0].player_id)).status_code == 200
    session.status = 'closed'
    db.session.commit()
    assert client.get(f'/api/sessions/{session.id}/characters/export', headers=auth(session.gm_id)).status_code == 200


# --- round trip ---------------------------------------------------------------------------------------------

AUTHORED = ['display_name', 'role_key', 'description', 'traits', *SHARED_FIELDS_MAP,
            'friend_faction_type', 'friend_faction_name', 'friend_faction_feeling',
            'enemy_faction_type', 'enemy_faction_name', 'enemy_faction_feeling',
            'role_specific_lifepath', 'lifepath_complete']


def test_round_trip_through_create_from_file(client, app, auth):
    source, chars = create_ungenerated_session('rt_src', ROLES5)
    complete_character(chars[1], offset=3)
    data = export_file(client, auth, source, chars[1])

    target, _, user = new_member_session('rt_dst')
    url = f'/api/sessions/{target.id}/characters/import'
    dry = post_import(client, auth, url, data, user.id, dry_run=1)
    assert dry.status_code == 200 and dry.json['dry_run'] and not dry.json['created']
    assert Character.query.filter_by(session_id=target.id, player_id=user.id).count() == 0

    res = post_import(client, auth, url, data, user.id)
    assert res.status_code == 201, res.json
    assert res.json['created'] and res.json['lifepath_complete'] is True and res.json['summary']['skipped'] == 0
    new = Character.query.get(res.json['character_id'])
    assert new.player_id == user.id and new.session_id == target.id and not new.is_killer
    for field in AUTHORED:
        assert getattr(new, field) == getattr(chars[1], field), field
    assert export_file(client, auth, target, new)['character'] == data['character']


# --- import hostility ------------------------------------------------------------------------------------------

@pytest.fixture
def import_env(client, app, auth):
    session, chars = create_ungenerated_session('imp_env', ROLES5)
    complete_character(chars[0], offset=2)
    data = export_file(client, auth, session, chars[0])
    url = f'/api/sessions/{session.id}/characters/{chars[0].id}/import'

    class Env:
        pass
    env = Env()
    env.session, env.chars, env.data, env.url = session, chars, data, url
    env.owner = chars[0].player_id
    env.post = lambda body, **q: post_import(client, auth, url, body, env.owner, **q)
    return env


def test_oversize_body_is_413(import_env):
    huge = json.dumps({**import_env.data, 'padding': 'x' * 70000})
    assert import_env.post(huge).status_code == 413


@pytest.mark.parametrize('body', [[], 'plain', 5, {'format': 'something-else', 'schema_version': 1, 'character': {}},
                                  {'format': FORMAT_CHARACTER, 'schema_version': '1', 'character': {}},
                                  {'format': FORMAT_CHARACTER, 'schema_version': True, 'character': {}},
                                  {'format': FORMAT_CHARACTER, 'schema_version': 2, 'character': {}},
                                  {'format': FORMAT_CHARACTER, 'schema_version': 0, 'character': {}},
                                  {'format': FORMAT_CHARACTER, 'schema_version': 1},
                                  {'format': FORMAT_CHARACTER, 'schema_version': 1, 'character': []}])
def test_structural_problems_are_400(import_env, body):
    res = import_env.post(json.dumps(body))
    assert res.status_code == 400, res.get_data(as_text=True)


def test_bundle_sent_to_single_endpoint_and_garbage_are_400(import_env):
    res = import_env.post({'format': FORMAT_BUNDLE, 'schema_version': 1, 'characters': []})
    assert res.status_code == 400 and 'several characters' in res.json['error']
    assert import_env.post(b'not json at all').status_code == 400
    newer = import_env.post({'format': FORMAT_CHARACTER, 'schema_version': 9, 'character': {}})
    assert 'newer version' in newer.json['error']


def test_invalid_option_is_skipped_rest_applies_and_text_fallback_matches(import_env):
    options = load_lifepath_data()[0]['personality']['options']
    target = options[4]

    def change(d):
        lp = d['character']['lifepath']['shared']
        lp['personality'] = {'id': 'definitely_not_an_id', 'text': target['text'].upper()}   # text fallback
        lp['hairstyle'] = {'id': 'nope', 'text': 'no such hairstyle'}                        # nothing matches
        lp['affectation'] = {'id': 'nope2'}
        d['character']['unexpected'] = 'ignored'
        d['character']['display_name'] = 'Imported Name'

    res = import_env.post(mutated(import_env.data, change))
    assert res.status_code == 200, res.json
    report = res.json
    assert row(report, 'personality')['status'] == 'apply'
    assert row(report, 'hairstyle')['status'] == 'skipped' and 'longer exists' in row(report, 'hairstyle')['reason']
    assert row(report, 'affectation')['status'] == 'skipped'
    assert row(report, 'display_name')['status'] == 'apply'
    db.session.expire_all()
    c = Character.query.get(import_env.chars[0].id)
    assert c.personality == target['id'] and c.display_name == 'Imported Name'
    assert c.hairstyle  # untouched, still the old valid value
    assert 'Ignored: unexpected' in report['warnings']


def test_bad_authored_text_is_skipped_not_fatal(import_env):
    def change(d):
        ch = d['character']
        ch['display_name'] = '{{c:abc}}'
        ch['description'] = 'x' * 4001
        ch['traits'] = ['ok', '{{c:abc}}']
        ch['lifepath']['factions']['friend']['name'] = 'The {{c:abc}} Gang'
        ch['lifepath']['factions']['enemy']['name'] = 'e' * 61

    res = import_env.post(mutated(import_env.data, change))
    assert res.status_code == 200
    for field in ('display_name', 'description', 'traits', 'friend_faction_name', 'enemy_faction_name'):
        assert row(res.json, field)['status'] == 'skipped', field
    db.session.expire_all()
    c = Character.query.get(import_env.chars[0].id)
    assert c.display_name == import_env.data['character']['display_name'] and '{{c:' not in json.dumps(c.traits)


def test_killer_flag_and_ids_in_a_file_are_ignored(import_env):
    def change(d):
        d['character']['is_killer'] = True
        d['character']['id'] = 'other'
        d['character']['player_id'] = 'someone'
        d['id'] = 'x'

    res = import_env.post(mutated(import_env.data, change))
    assert res.status_code == 200
    assert {'Ignored: is_killer', 'Ignored: id', 'Ignored: player_id'} <= set(res.json['warnings'])
    db.session.expire_all()
    c = Character.query.get(import_env.chars[0].id)
    assert not c.is_killer and c.id == import_env.chars[0].id and c.player_id == import_env.owner


def test_missing_keys_leave_values_and_null_clears(import_env):
    before = import_env.chars[0].hairstyle

    def change(d):
        del d['character']['lifepath']['shared']['hairstyle']
        d['character']['lifepath']['shared']['affectation'] = None
        del d['character']['description']

    res = import_env.post(mutated(import_env.data, change))
    assert res.status_code == 200
    assert any('Missing from file' in w and 'Hairstyle' in w for w in res.json['warnings'])
    assert any('description' in w for w in res.json['warnings'])
    db.session.expire_all()
    c = Character.query.get(import_env.chars[0].id)
    assert c.hairstyle == before and c.affectation is None
    assert res.json['lifepath_complete'] is False


def test_role_specific_is_replaced_and_role_change_clears_old_picks(import_env):
    other_role = 'tech'
    tables = load_lifepath_data()[3][other_role]['tables']
    first_key = next(iter(tables))

    def change(d):
        d['character']['role'] = {'id': other_role, 'text': 'Tech'}
        d['character']['lifepath']['role_specific'] = {first_key: {'id': tables[first_key]['options'][2]['id'], 'text': ''}}

    res = import_env.post(mutated(import_env.data, change))
    assert res.status_code == 200
    db.session.expire_all()
    c = Character.query.get(import_env.chars[0].id)
    assert c.role_key == other_role
    assert c.role_specific_lifepath == {first_key: tables[first_key]['options'][2]['id']}
    assert c.lifepath_complete is False


def test_import_requires_edit_permission(client, app, auth, import_env):
    assert post_import(client, auth, import_env.url, import_env.data, import_env.chars[1].player_id).status_code == 403
    assert post_import(client, auth, import_env.url, import_env.data, import_env.session.gm_id).status_code == 200
    assert client.post(import_env.url, json=import_env.data).status_code == 401


# --- locks ------------------------------------------------------------------------------------------------------

def test_locked_session_applies_only_name_description_and_traits(client, app, auth):
    session, chars = create_ungenerated_session('imp_lock', ROLES5)
    complete_character(chars[0], offset=1)
    generate_via_api(client, auth, session)
    db.session.expire_all()
    c = Character.query.get(chars[0].id)
    old = {f: getattr(c, f) for f in AUTHORED}

    data = export_file(client, auth, session, c)
    shared = load_lifepath_data()[0]
    data['character']['display_name'] = 'Locked Rename'
    data['character']['description'] = 'new text'
    data['character']['traits'] = ['new']
    data['character']['role'] = {'id': 'tech', 'text': 'Tech'}
    data['character']['lifepath']['shared']['personality'] = {
        'id': next(o['id'] for o in shared['personality']['options'] if o['id'] != c.personality), 'text': ''}
    url = f'/api/sessions/{session.id}/characters/{c.id}/import'
    res = post_import(client, auth, url, data, c.player_id)
    assert res.status_code == 200, res.json
    assert row(res.json, 'display_name')['status'] == 'apply'
    assert row(res.json, 'role_key')['status'] == 'skipped' and row(res.json, 'role_key')['code'] == 'session_locked'
    assert row(res.json, 'personality')['code'] == 'session_locked'
    assert 'reset' in row(res.json, 'personality')['reason'].lower()
    db.session.expire_all()
    c = Character.query.get(c.id)
    assert c.display_name == 'Locked Rename' and c.description == 'new text' and c.traits == ['new']
    for f in AUTHORED:
        if f not in ('display_name', 'description', 'traits'):
            assert getattr(c, f) == old[f], f


def test_create_from_file_conflicts(client, app, auth):
    session, chars, user = new_member_session('imp_conflict')
    complete_character(chars[0])
    data = export_file(client, auth, session, chars[0])
    url = f'/api/sessions/{session.id}/characters/import'

    outsider = User(id='outsider_imp', username='outsider_imp', password_hash='x')
    db.session.add(outsider)
    db.session.commit()
    assert post_import(client, auth, url, data, outsider.id).status_code == 403
    assert client.post('/api/sessions/missing/characters/import', json=data, headers=auth(user.id)).status_code == 404

    res = post_import(client, auth, url, data, chars[1].player_id)   # already has a character
    assert res.status_code == 409 and 'already have a character' in res.json['error']

    session.status = 'generated'                                      # a generated session is locked
    db.session.commit()
    res = post_import(client, auth, url, data, user.id)
    assert res.status_code == 409 and res.json['code'] == 'session_locked'


def test_create_from_file_needs_valid_name_and_role(client, app, auth):
    session, chars, user = new_member_session('imp_required')
    complete_character(chars[0])
    data = export_file(client, auth, session, chars[0])
    url = f'/api/sessions/{session.id}/characters/import'
    for change in (lambda d: d['character'].pop('display_name'),
                   lambda d: d['character'].__setitem__('display_name', '{{c:x}}'),
                   lambda d: d['character'].__setitem__('display_name', ''),
                   lambda d: d['character'].pop('role'),
                   lambda d: d['character'].__setitem__('role', {'id': 'astronaut', 'text': 'Astronaut'})):
        res = post_import(client, auth, url, mutated(data, change), user.id)
        assert res.status_code == 400, res.get_data(as_text=True)
    assert Character.query.filter_by(session_id=session.id, player_id=user.id).count() == 0
    ok = mutated(data, lambda d: d['character'].__setitem__('role', {'id': 'zzz', 'text': 'netrunner'}))
    assert post_import(client, auth, url, ok, user.id).status_code == 201


# --- dry run and include_text -----------------------------------------------------------------------------------------

def test_dry_run_writes_nothing_and_matches_the_real_run(client, app, auth):
    session, chars = create_ungenerated_session('imp_dry', ROLES5)
    complete_character(chars[0], offset=2)
    data = export_file(client, auth, session, chars[0])
    complete_character(chars[0], offset=5)       # the stored character now differs from the file
    db.session.expire_all()
    c = Character.query.get(chars[0].id)
    snapshot = {f: getattr(c, f) for f in AUTHORED}
    stamp = c.updated_at
    url = f'/api/sessions/{session.id}/characters/{c.id}/import'

    dry = post_import(client, auth, url, data, c.player_id, dry_run=1).json
    db.session.expire_all()
    c = Character.query.get(c.id)
    assert {f: getattr(c, f) for f in AUTHORED} == snapshot and c.updated_at == stamp
    assert dry['dry_run'] is True and dry['summary']['apply'] > 0

    real = post_import(client, auth, url, data, c.player_id).json
    assert real['dry_run'] is False
    strip = lambda r: {k: v for k, v in r.items() if k != 'dry_run'}
    assert strip(real) == strip(dry)
    db.session.expire_all()
    c = Character.query.get(c.id)
    assert c.personality == data['character']['lifepath']['shared']['personality']['id']
    again = post_import(client, auth, url, data, c.player_id).json
    assert again['summary']['apply'] == 0 and again['summary']['unchanged'] > 0


def test_include_text_zero_leaves_description_and_traits(client, app, auth):
    session, chars = create_ungenerated_session('imp_text', ROLES5)
    complete_character(chars[0])
    data = export_file(client, auth, session, chars[0])
    data['character']['description'] = 'FROM FILE'
    data['character']['traits'] = ['from-file']
    url = f'/api/sessions/{session.id}/characters/{chars[0].id}/import'
    res = post_import(client, auth, url, data, chars[0].player_id, include_text=0)
    assert res.status_code == 200
    assert not any(f['field'] in ('description', 'traits') for f in res.json['fields'])
    db.session.expire_all()
    c = Character.query.get(chars[0].id)
    assert c.description != 'FROM FILE' and c.traits == ['secretive', 'mirrorshades']

    other, _, user = new_member_session('imp_text2')
    res = post_import(client, auth, f'/api/sessions/{other.id}/characters/import', data, user.id, include_text=0)
    assert res.status_code == 201
    new = Character.query.get(res.json['character_id'])
    assert not new.description and not new.traits
