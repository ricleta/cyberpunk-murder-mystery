"""Compose from Lifepath (§8), the GM master endpoint (§9.1) and the frontend static guard (§4.1)."""
import json
import os
import re
import shutil

import pytest

from extensions import db
from models import Character, Secret, SecretKnowledge, Session
from conftest import create_ungenerated_session, generate_via_api
from generation import compose as compose_module
from generation.compose import validate_compose_templates, COMPOSE_FIELDS, SHARED_KEYS
from services.lifepath_data import load_lifepath_data

ROLES5 = ["solo", "exec", "netrunner", "fixer", "media"]


def fill_valid_lifepath(chars):
    shared = load_lifepath_data()[0]
    for i, c in enumerate(chars):
        for field in COMPOSE_FIELDS:
            options = shared[SHARED_KEYS[field]]['options']
            setattr(c, field, options[(i + 3) % len(options)]['id'])
    db.session.commit()


def compose(client, auth, session, char, user=None, seed=None):
    url = f'/api/sessions/{session.id}/characters/{char.id}/compose' + (f'?seed={seed}' if seed is not None else '')
    return client.post(url, headers=auth(user or char.player_id))


# --- compose ---------------------------------------------------------------------------------

def test_compose_returns_second_person_description_and_distinct_traits(client, app, auth):
    session, chars = create_ungenerated_session("cmp_ok", ROLES5)
    fill_valid_lifepath(chars)
    seen = set()
    for seed in range(40):
        res = compose(client, auth, session, chars[0], seed=seed)
        assert res.status_code == 200, res.json
        body = res.json
        assert set(body) == {'description', 'traits'}
        assert 4 <= len(body['traits']) <= 6
        assert len(set(body['traits'])) == len(body['traits'])
        assert all(isinstance(t, str) and 0 < len(t) <= 60 and len(t.split()) <= 3 for t in body['traits'])
        assert body['description'].startswith(('You', 'People', 'Everything', 'Up top', 'Between'))
        assert 3 <= len(re.findall(r'\.(?:\s|$)', body['description'])) <= 4
        assert '#' not in body['description'] and '{{' not in body['description']
        assert chars[0].display_name not in body['description']
        seen.add(body['description'])
    assert len(seen) > 10  # repeated presses give different prose


def test_compose_is_deterministic_with_a_seed_and_does_not_write(client, app, auth):
    session, chars = create_ungenerated_session("cmp_seed", ROLES5)
    fill_valid_lifepath(chars)
    before = (chars[0].description, chars[0].traits)
    first = compose(client, auth, session, chars[0], seed=5).json
    assert compose(client, auth, session, chars[0], seed=5).json == first
    assert compose(client, auth, session, chars[0], seed=6).json != first
    db.session.expire_all()
    c = Character.query.get(chars[0].id)
    assert (c.description, c.traits) == before


def test_compose_permissions_status_and_incomplete_lifepath(client, app, auth):
    session, chars = create_ungenerated_session("cmp_authz", ROLES5)
    fill_valid_lifepath(chars)
    assert client.post(f'/api/sessions/{session.id}/characters/{chars[0].id}/compose').status_code == 401
    assert compose(client, auth, session, chars[0], user=chars[1].player_id).status_code == 403
    assert compose(client, auth, session, chars[0], user=session.gm_id).status_code == 200
    assert client.post(f'/api/sessions/{session.id}/characters/missing/compose', headers=auth(session.gm_id)).status_code == 404

    # allowed in any session status, including after generation
    generate_via_api(client, auth, session)
    assert compose(client, auth, session, chars[0]).status_code == 200

    chars[2].lifepath_complete = False
    db.session.commit()
    res = compose(client, auth, session, chars[2])
    assert res.status_code == 400 and 'Lifepath' in res.json['error']


def test_compose_seed_is_ignored_outside_debug(client, app, auth):
    session, chars = create_ungenerated_session("cmp_prod", ROLES5)
    fill_valid_lifepath(chars)
    app.config['DEBUG'] = False
    app.config['TESTING'] = False
    try:
        outputs = {json.dumps(compose(client, auth, session, chars[0], seed=1).json, sort_keys=True) for _ in range(12)}
    finally:
        app.config['DEBUG'] = True
        app.config['TESTING'] = True
    assert len(outputs) > 1


def test_compose_data_covers_every_option_and_startup_validation_catches_gaps(tmp_path):
    assert validate_compose_templates()
    backend_data = compose_module.get_base_data_dir()
    broken = tmp_path / 'data'
    shutil.copytree(backend_data, broken)
    path = broken / 'compose' / 'composeTemplates.json'
    data = json.loads(path.read_text(encoding='utf-8'))

    def write(mutated):
        path.write_text(json.dumps(mutated), encoding='utf-8')

    # an option id without a phrase
    mutated = json.loads(json.dumps(data))
    del mutated['fields']['hairstyle']['mohawk']['phrase']
    write(mutated)
    with pytest.raises(ValueError, match="no 'phrase'"):
        validate_compose_templates(str(broken))
    # an option id without an entry at all
    mutated = json.loads(json.dumps(data))
    del mutated['fields']['affectation']['tattoos']
    write(mutated)
    with pytest.raises(ValueError, match='has no entry'):
        validate_compose_templates(str(broken))
    # a missing trait
    mutated = json.loads(json.dumps(data))
    mutated['fields']['personality']['shy_and_secretive']['trait'] = ''
    write(mutated)
    with pytest.raises(ValueError, match="no 'trait'"):
        validate_compose_templates(str(broken))
    # an unknown placeholder, and a modifier
    mutated = json.loads(json.dumps(data))
    mutated['descriptionGrammar']['sentence1'][0] = 'You are #nickname#.'
    write(mutated)
    with pytest.raises(ValueError, match='unknown placeholder'):
        validate_compose_templates(str(broken))
    mutated = json.loads(json.dumps(data))
    mutated['descriptionGrammar']['sentence1'][0] = 'You are #personality.capitalize#.'
    write(mutated)
    with pytest.raises(ValueError, match='modifier'):
        validate_compose_templates(str(broken))
    # an unrendered symbol would leave a '#'
    mutated = json.loads(json.dumps(data))
    mutated['descriptionGrammar']['sentence2'][0] = 'Broken #sentence9 here.'
    write(mutated)
    with pytest.raises(ValueError):
        validate_compose_templates(str(broken))


def test_frontend_and_backend_compose_data_are_identical():
    root = os.path.join(os.path.dirname(__file__), '..', '..')
    with open(os.path.join(root, 'frontend', 'data', 'compose', 'composeTemplates.json'), encoding='utf-8') as f:
        frontend = json.load(f)
    with open(os.path.join(root, 'backend', 'data', 'compose', 'composeTemplates.json'), encoding='utf-8') as f:
        backend = json.load(f)
    assert frontend == backend


# --- master endpoint ---------------------------------------------------------------------------

def test_master_is_gm_only_and_complete(client, app, auth):
    session, chars = create_ungenerated_session("mst_all", ROLES5)
    url = f'/api/sessions/{session.id}/master'
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth(chars[0].player_id)).status_code == 403
    assert client.get('/api/sessions/missing/master', headers=auth(session.gm_id)).status_code == 404

    # before generation the sheet still works (empty generated sections)
    res = client.get(url, headers=auth(session.gm_id))
    assert res.status_code == 200
    assert res.json['secrets'] == [] and res.json['objectives'] == [] and res.json['knowledge_matrix'] == {}

    generate_via_api(client, auth, session, seed=31)
    res = client.get(url, headers=auth(session.gm_id))
    body = res.json
    assert body['session']['generation_seed'] == 31 and body['session']['status'] == 'generated'
    assert len(body['characters']) == 5 and len(body['secrets']) > 5 and len(body['objectives']) >= 20
    char = body['characters'][0]
    assert {'id', 'display_name', 'role_key', 'is_killer', 'description', 'traits', 'lifepath'} <= set(char)
    assert {'shared', 'factions', 'role_specific'} == set(char['lifepath'])
    assert all({'label', 'value'} <= set(item) for item in char['lifepath']['shared'])
    secret = body['secrets'][0]
    assert {'subject_character_id', 'subject_name', 'kind', 'category', 'severity', 'is_killer_core',
            'is_killer_trail', 'text_subject', 'text_knower', 'knowers'} <= set(secret)
    for rel in body['relationships']:
        assert rel['source_name'] and rel['target_name'] and 'description_source' in rel
    assert all('gm_hint' in o and 'link_type' in o for o in body['objectives'])
    assert '{{c:' not in res.get_data(as_text=True)
    assert sum(c['is_killer'] for c in body['characters']) == 1


def test_master_knowledge_matrix_matches_the_database(client, app, auth):
    session, chars = create_ungenerated_session("mst_matrix", ROLES5)
    generate_via_api(client, auth, session, seed=32)
    body = client.get(f'/api/sessions/{session.id}/master', headers=auth(session.gm_id)).json

    expected = {}
    for k in SecretKnowledge.query.join(Secret).filter(Secret.session_id == session.id).all():
        subject = Secret.query.get(k.secret_id).subject_character_id
        expected.setdefault(k.knower_character_id, {}).setdefault(subject, []).append(k.secret_id)
    actual = {knower: {subj: sorted(ids) for subj, ids in subjects.items()}
              for knower, subjects in body['knowledge_matrix'].items()}
    assert actual == {kn: {sj: sorted(ids) for sj, ids in sjs.items()} for kn, sjs in expected.items()}
    # nobody knows their own secrets, and every secret's knowers agree with the matrix
    for knower, subjects in body['knowledge_matrix'].items():
        assert knower not in subjects
    for secret in body['secrets']:
        for k in secret['knowers']:
            assert secret['id'] in body['knowledge_matrix'][k['knower_character_id']][secret['subject_character_id']]


# --- frontend static guard (§4.1) ----------------------------------------------------------------

FRONTEND = os.path.join(os.path.dirname(__file__), '..', '..', 'frontend')
def frontend_sources():
    for folder, _, files in os.walk(FRONTEND):
        if os.path.basename(folder) == 'data' or os.sep + 'data' + os.sep in folder + os.sep:
            continue
        for name in files:
            if name.endswith(('.html', '.js')):
                yield os.path.join(folder, name)


SINK_RE = re.compile(r'(?:innerHTML|outerHTML)\s*\+?=(?!=)|insertAdjacentHTML\s*\(')


def scan_expression(text, start):
    """Reads the JS expression starting at `start` up to its terminating ';' (or the closing
    ')' of insertAdjacentHTML(...) at depth 0). Returns (expression, is_single_plain_literal)."""
    i, depth, quote = start, 0, None
    parts = []          # outside-string characters, ignoring whitespace
    literal_count = 0
    interpolated = False
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == chr(92):  # backslash
                i += 2
                continue
            if quote == '`' and text.startswith('${', i):
                interpolated = True
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'", '`'):
            quote = ch
            literal_count += 1
            parts.append('S')
        elif ch in '([{':
            depth += 1
            parts.append(ch)
        elif ch in ')]}':
            if depth == 0:
                break
            depth -= 1
            parts.append(ch)
        elif ch == ';' and depth == 0:
            break
        elif not ch.isspace():
            parts.append(ch)
        i += 1
    plain = (not interpolated) and ''.join(parts) in ('S', '')
    return text[start:i], plain


def test_no_html_sink_is_fed_interpolated_data():
    """innerHTML/outerHTML/insertAdjacentHTML may only receive a plain literal: no ${...},
    no '+' concatenation, no variable that could have been built from data."""
    offenders = []
    for path in frontend_sources():
        text = open(path, encoding='utf-8').read()
        for m in SINK_RE.finditer(text):
            expression, plain = scan_expression(text, m.end())
            if m.group(0).startswith('insertAdjacentHTML'):
                # (position, html): only the html argument matters
                _, _, html = expression.partition(',')
                _, plain = scan_expression(html + ';', 0)
            if not plain:
                line = text[:m.start()].count(chr(10)) + 1
                offenders.append(f'{os.path.relpath(path, FRONTEND)}:{line}: {m.group(0)} {expression.strip()[:70]!r}')
    assert not offenders, 'HTML sinks fed non-literal data:' + chr(10) + chr(10).join(offenders)


def test_static_guard_scanner_catches_the_known_bad_patterns():
    def flagged(code):
        m = SINK_RE.search(code)
        return not scan_expression(code, m.end())[1]
    assert flagged('el.innerHTML = `<b>${name}</b>`;')
    assert flagged("el.innerHTML = '<b>' + name + '</b>';")
    assert flagged('el.innerHTML += html;')
    assert flagged('el.outerHTML = `<p style="color: #999;">${x}</p>`;')
    assert not flagged("el.innerHTML = '';")
    assert not flagged('el.innerHTML = `<p style="color: #999;">No data.</p>`;')


def test_no_inline_event_handlers():
    offenders = []
    for path in frontend_sources():
        text = open(path, encoding='utf-8').read()
        for m in re.finditer(r'\son(?:click|change|input|submit|load|error|keydown|keyup)\s*=', text):
            offenders.append(f'{os.path.relpath(path, FRONTEND)}:{text[:m.start()].count(chr(10)) + 1}')
    assert not offenders, 'inline event handlers:\n' + '\n'.join(offenders)
