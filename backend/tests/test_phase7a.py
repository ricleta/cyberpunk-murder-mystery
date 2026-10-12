"""Phase 7A: player-view sheet, export, import."""
from models import Character
from conftest import create_ungenerated_session, generate_via_api

ROLES5 = ["solo", "exec", "netrunner", "fixer", "media"]
FORBIDDEN_SHEET_KEYS = {
    'template_id', 'category', 'is_killer', 'is_killer_core', 'is_killer_trail', 'gm_hint',
    'linked_objective_id', 'link_type', 'related_character_id', 'related_secret_id',
    'related_relationship_id', 'player_id', 'generation_seed', 'generated_at', 'generation_report',
    'character_id', 'character_name', 'subject_character_id', 'sort_order',
}


def walk_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk_keys(v)


def sheet(client, auth, session, char, user=None):
    return client.get(f'/api/sessions/{session.id}/characters/{char.id}/sheet', headers=auth(user or char.player_id))


# --- §4 sheet endpoint --------------------------------------------------------------------------

def test_sheet_before_generation_is_empty(client, app, auth):
    session, chars = create_ungenerated_session('sh_empty', ROLES5)
    res = sheet(client, auth, session, chars[0])
    assert res.status_code == 200
    body = res.json
    assert body['generated'] is False
    assert body['secrets'] == {'own': [], 'known': []}
    assert body['relationships'] == [] and body['objectives'] == []
    assert body['session'] == {'name': session.name, 'status': 'lobby'}
    assert body['character']['display_name'] == chars[0].display_name


def test_sheet_has_no_forbidden_keys_even_for_the_gm(client, app, auth):
    session, chars = create_ungenerated_session('sh_keys', ROLES5)
    generate_via_api(client, auth, session, seed=5)
    for c in chars:
        for user in (c.player_id, session.gm_id):
            res = sheet(client, auth, session, c, user)
            assert res.status_code == 200
            assert not (FORBIDDEN_SHEET_KEYS & set(walk_keys(res.json))), set(walk_keys(res.json))
            assert res.json['generated'] is True
            assert all(set(s) == {'id', 'kind', 'text', 'severity'} for s in res.json['secrets']['own'])
            assert all(set(s) == {'id', 'kind', 'text', 'subject_name'} for s in res.json['secrets']['known'])
            assert all(set(r) == {'other_name', 'rel_type', 'text'} for r in res.json['relationships'])
            assert all(set(o) == {'id', 'text', 'points', 'kind'} for o in res.json['objectives'])


def test_sheet_matches_what_the_owner_gets_elsewhere_and_gm_equals_owner(client, app, auth):
    session, chars = create_ungenerated_session('sh_same', ROLES5)
    generate_via_api(client, auth, session, seed=6)
    base = f'/api/sessions/{session.id}'
    for c in chars:
        owner = auth(c.player_id)
        body = sheet(client, auth, session, c).json
        full = client.get(f'{base}/characters/{c.id}', headers=owner).json
        assert [s['text'] for s in body['secrets']['own']] == [s['text'] for s in full['secrets'] if s['about_self']]
        assert [s['text'] for s in body['secrets']['known']] == [s['text'] for s in full['secrets'] if not s['about_self']]
        rels = client.get(f'{base}/relationships', headers=owner).json['relationships']
        expected = [r['description_source'] if r['source_character_id'] == c.id else r['description_target']
                    for r in rels if c.id in (r['source_character_id'], r['target_character_id'])]
        assert [r['text'] for r in body['relationships']] == expected
        objectives = client.get(f'{base}/objectives', headers=owner).json['objectives']
        assert [(o['text'], o['points']) for o in body['objectives']] == [(o['text'], o['points']) for o in objectives]
        assert sheet(client, auth, session, c, session.gm_id).json == body


def test_sheet_relationships_only_involve_this_character(client, app, auth):
    session, chars = create_ungenerated_session('sh_rels', ROLES5)
    generate_via_api(client, auth, session, seed=7)
    gm_rels = client.get(f'/api/sessions/{session.id}/relationships', headers=auth(session.gm_id)).json['relationships']
    for c in chars:
        mine = [r for r in gm_rels if c.id in (r['source_character_id'], r['target_character_id'])]
        body = sheet(client, auth, session, c).json
        assert len(body['relationships']) == len(mine)
        others = {r['source_name'] if r['target_character_id'] == c.id else r['target_name'] for r in mine}
        assert {r['other_name'] for r in body['relationships']} == others
        assert c.display_name not in {r['other_name'] for r in body['relationships']}


def test_sheet_authz(client, app, auth):
    session, chars = create_ungenerated_session('sh_authz', ROLES5)
    other, _ = create_ungenerated_session('sh_authz_other', ROLES5)
    url = f'/api/sessions/{session.id}/characters/{chars[0].id}/sheet'
    assert client.get(url).status_code == 401
    assert sheet(client, auth, session, chars[0], chars[1].player_id).status_code == 403
    assert client.get(f'/api/sessions/{session.id}/characters/missing/sheet', headers=auth(session.gm_id)).status_code == 404
    # a character that belongs to another session is a 404 even for that session's GM
    res = client.get(f'/api/sessions/{session.id}/characters/{_other_char_id(other)}/sheet', headers=auth(session.gm_id))
    assert res.status_code == 404


def _other_char_id(session):
    return Character.query.filter_by(session_id=session.id).first().id


# --- frontend static guard for the new modules ---------------------------------------------------------------------

def test_new_frontend_modules_never_touch_html_sinks():
    import os
    root = os.path.join(os.path.dirname(__file__), '..', '..', 'frontend')
    for rel in ('js/files.js', 'js/import-dialog.js', 'js/pages/print.js', 'js/sheet-render.js', 'print.html'):
        text = open(os.path.join(root, rel), encoding='utf-8').read()
        for sink in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'):
            assert sink not in text, f'{rel} uses {sink}'
