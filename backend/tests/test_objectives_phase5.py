import json
import math
import os
import random
import re
import shutil
import pytest
from app import create_app
from extensions import db
from models import Session, SessionMember, User, Character, Relationship, Secret, SecretKnowledge, Objective
from config import Config
from conftest import create_test_session
from generation.relationships import _generate_relationships
from generation.secrets import _generate_secrets, verify_secrets
from generation.secret_templates import get_base_data_dir, render_grammar
from generation.objectives import (
    _generate_objectives,
    verify_objectives,
    normalize_budget,
    ObjectiveDraft,
    SLOT_PROTECT,
    SLOT_EXPLOIT,
    SLOT_RELATIONSHIP,
    SLOT_GENERIC,
    SLOT_FRAME
)
from generation.objective_templates import load_all_objective_templates, validate_objective_templates
import jwt
from datetime import datetime, timedelta

class TestConfig(Config):
    TESTING = True
    DEBUG = True
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

ROLES = ["solo", "netrunner", "tech", "medtech", "media", "lawman", "exec", "fixer", "nomad", "rockerboy"]
PLAYER_FIELDS = {'id', 'text', 'points', 'kind', 'related_character_id'}
GM_ONLY_FIELDS = {'gm_hint', 'linked_objective_id', 'link_type', 'related_secret_id', 'related_relationship_id'}

def auth(token):
    return {'Authorization': f'Bearer {token}'}

def generate_full(session_id, roles, seed):
    """Relationships -> secrets -> objectives with one seeded rng."""
    session, chars = create_test_session(session_id, roles)
    rng = random.Random(seed)
    _generate_relationships(session, rng=rng)
    _generate_secrets(session, rng=rng)
    verify_secrets(session)
    _generate_objectives(session, rng=rng)
    return session, chars

def create_ungenerated_session(session_id, roles):
    """A lobby session with complete characters and nothing generated yet."""
    session, chars = create_test_session(session_id, roles)
    Relationship.query.filter_by(session_id=session.id).delete()
    db.session.commit()
    return session, chars

def generated_counts(session_id):
    return {
        'relationships': Relationship.query.filter_by(session_id=session_id).count(),
        'secrets': Secret.query.filter_by(session_id=session_id).count(),
        'knowledge': SecretKnowledge.query.join(Secret).filter(Secret.session_id == session_id).count(),
        'objectives': Objective.query.filter_by(session_id=session_id).count(),
    }

def load_state(session):
    chars = {c.id: c for c in Character.query.filter_by(session_id=session.id).all()}
    secrets = {s.id: s for s in Secret.query.filter_by(session_id=session.id).all()}
    knowers = {}
    for k in SecretKnowledge.query.join(Secret).filter(Secret.session_id == session.id).all():
        knowers.setdefault(k.secret_id, set()).add(k.knower_character_id)
    rels = {r.id: r for r in Relationship.query.filter_by(session_id=session.id).all()}
    objectives = Objective.query.filter_by(session_id=session.id).all()
    return chars, secrets, knowers, rels, objectives

def mentions(text, name):
    """Whole-name match: the fixture names share prefixes (Char_solo_1 / Char_solo_10)."""
    return re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text or '') is not None

def assert_knowledge_safety(session):
    """The §6 rules, checked independently of verify_objectives."""
    chars, secrets, knowers, rels, objectives = load_state(session)
    pools = load_all_objective_templates()
    killer = [c for c in chars.values() if c.is_killer][0]
    killer_only_ids = {t["id"] for t in pools["hide_evidence"] + pools["frame"]}
    trail_knowers = {cid for s in secrets.values() if s.is_killer_trail for cid in knowers.get(s.id, set())}
    victim_name = "Director Kessler Voss"
    find_killer_texts = {
        render_grammar(t["templateText"], {"victimName": victim_name}) for t in pools["find_killer"]
    }

    for o in objectives:
        owner = chars[o.character_id]

        # 1/3. Only things the owner can legitimately see
        if o.related_secret_id:
            s = secrets[o.related_secret_id]
            assert not s.is_killer_core and not s.is_killer_trail
            if o.kind == 'protect_secret':
                assert s.subject_character_id == owner.id
            else:
                assert o.kind == 'exploit_secret'
                assert owner.id in knowers.get(s.id, set())
                assert o.related_character_id == s.subject_character_id
                # never reference the other knowers
                for other_id in knowers.get(s.id, set()) - {owner.id}:
                    assert not mentions(o.text, chars[other_id].display_name)
            # never embed the secret's own text
            assert s.text_subject not in o.text
            assert not s.text_knower or s.text_knower not in o.text
        if o.related_relationship_id:
            r = rels[o.related_relationship_id]
            assert owner.id in (r.source_character_id, r.target_character_id)
        assert o.related_character_id != owner.id

        # 2. A protect objective never names the knowers
        if o.kind == 'protect_secret':
            assert o.related_character_id is None
            for knower_id in knowers.get(o.related_secret_id, set()):
                name = chars[knower_id].display_name
                assert not mentions(o.text, name), f"protect text names knower {name}: {o.text}"
                assert not mentions(o.gm_hint, name)

        # 4. Killer-only content stays on the killer's sheet
        if owner.id != killer.id:
            assert o.kind not in ('hide_evidence', 'frame')
            assert o.template_id not in killer_only_ids

        # 5. find_killer is never personalized
        if o.kind == 'find_killer':
            assert owner.id != killer.id
            assert o.points == 2
            assert o.text in find_killer_texts
            assert o.related_character_id is None and o.related_secret_id is None

        assert '#' not in o.text and '#' not in (o.gm_hint or '')
        assert '((' not in o.text and '((' not in (o.gm_hint or '')

    # Killer sheet
    killer_objs = [o for o in objectives if o.character_id == killer.id]
    assert [o.kind for o in killer_objs].count('find_killer') == 0
    assert [o.kind for o in killer_objs].count('hide_evidence') == 1
    frames = [o for o in killer_objs if o.kind == 'frame']
    assert len(frames) == 1
    scapegoat = chars[frames[0].related_character_id]
    assert not scapegoat.is_killer
    assert scapegoat.id not in trail_knowers
    assert mentions(frames[0].gm_hint, killer.display_name) and mentions(frames[0].gm_hint, scapegoat.display_name)

    # Weaving: links are mutual and there are enough opposed pairs
    by_id = {o.id: o for o in objectives}
    for o in objectives:
        if o.linked_objective_id:
            partner = by_id[o.linked_objective_id]
            assert partner.linked_objective_id == o.id
            assert partner.character_id != o.character_id
            assert o.link_type in ('opposed', 'aligned') and partner.link_type == o.link_type
        else:
            assert o.link_type is None
    opposed = len([o for o in objectives if o.linked_objective_id and o.link_type == 'opposed']) // 2
    assert opposed >= math.ceil(len(chars) / 3)

    # Budget
    for c in chars.values():
        objs = [o for o in objectives if o.character_id == c.id]
        assert 4 <= len(objs) <= 5
        assert all(1 <= o.points <= 3 for o in objs)
        assert 8 <= sum(o.points for o in objs) <= 12
        assert len([o for o in objs if o.points == 3]) <= 1

def snapshot(session):
    """Id-independent description of a session's objectives."""
    chars, secrets, knowers, rels, objectives = load_state(session)
    by_id = {o.id: o for o in objectives}
    rows = []
    for o in objectives:
        s = secrets.get(o.related_secret_id)
        r = rels.get(o.related_relationship_id)
        partner = by_id.get(o.linked_objective_id)
        rows.append((
            o.character_id, o.sort_order, o.kind, o.template_id, o.points, o.text, o.gm_hint,
            o.related_character_id,
            (s.subject_character_id, s.template_id) if s else None,
            (r.source_character_id, r.target_character_id) if r else None,
            (partner.character_id, partner.template_id) if partner else None,
            o.link_type
        ))
    return sorted(rows, key=repr)

# --- 1. Template validation ---
def test_objective_templates_validate_at_startup(app):
    with app.app_context():
        assert validate_objective_templates() is True

def _copy_data_dir(tmp_path):
    data_dir = os.path.join(str(tmp_path), 'data')
    shutil.copytree(get_base_data_dir(), data_dir)
    return data_dir

def _edit_json(data_dir, name, mutate):
    path = os.path.join(data_dir, 'objectives', name)
    with open(path, 'r', encoding='utf-8') as f:
        content = json.load(f)
    mutate(content)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(content, f)

def test_broken_objective_templates_fail_validation(app, tmp_path):
    with app.app_context():
        # Duplicate id across files
        data_dir = _copy_data_dir(tmp_path / 'dup')
        _edit_json(data_dir, 'flavorTemplates.json', lambda c: c[0].update(id='protect_generic_public'))
        with pytest.raises(ValueError, match="Duplicate template id"):
            validate_objective_templates(data_dir)

        # Unknown category
        data_dir = _copy_data_dir(tmp_path / 'cat')
        _edit_json(data_dir, 'protectTemplates.json', lambda c: c[3].update(matchCategories=['not_a_category']))
        with pytest.raises(ValueError, match="unknown category"):
            validate_objective_templates(data_dir)

        # Unresolved placeholder
        data_dir = _copy_data_dir(tmp_path / 'ph')
        _edit_json(data_dir, 'findKillerTemplates.json', lambda c: c[0].update(templateText='Find #bogusName# tonight.'))
        with pytest.raises(ValueError, match="unresolved placeholder"):
            validate_objective_templates(data_dir)

        # #targetName# where no target exists, #secretLabel# outside protect/exploit
        data_dir = _copy_data_dir(tmp_path / 'target')
        _edit_json(data_dir, 'protectTemplates.json', lambda c: c[0].update(templateText='Hide it from #targetName#.'))
        with pytest.raises(ValueError, match="unresolved placeholder"):
            validate_objective_templates(data_dir)

        data_dir = _copy_data_dir(tmp_path / 'label')
        _edit_json(data_dir, 'flavorTemplates.json', lambda c: c[0].update(templateText='Talk about #secretLabel#.'))
        with pytest.raises(ValueError, match="unresolved placeholder"):
            validate_objective_templates(data_dir)

        # Unknown relationship type and invalid role
        data_dir = _copy_data_dir(tmp_path / 'rel')
        _edit_json(data_dir, 'relationshipObjectiveTemplates.json', lambda c: c[0].update(relTypes=['frenemies']))
        with pytest.raises(ValueError, match="unknown relationship type"):
            validate_objective_templates(data_dir)

        data_dir = _copy_data_dir(tmp_path / 'role')
        _edit_json(data_dir, 'flavorTemplates.json', lambda c: c[0].update(roles=['wizard']))
        with pytest.raises(ValueError, match="invalid role_key"):
            validate_objective_templates(data_dir)

# --- 2. Property test: every invariant over many seeds and sizes ---
@pytest.mark.parametrize("size", [4, 6, 8, 11, 15])
def test_objectives_property_all_invariants(app, size):
    with app.app_context():
        for seed in range(100):
            role_rng = random.Random(seed * 1000 + size)
            roles = [role_rng.choice(ROLES) for _ in range(size)]
            session, chars = generate_full(f"prop_{size}_{seed}", roles, seed=seed * 1000 + size)
            assert verify_objectives(session) is True
            assert_knowledge_safety(session)

# --- 3. Determinism ---
def test_same_seed_gives_identical_objectives(app):
    with app.app_context():
        roles = ["solo", "exec", "netrunner", "fixer", "media", "lawman", "nomad"]
        session, _ = generate_full("determinism_sess", roles, seed=2077)
        first = snapshot(session)

        db.session.remove()
        db.drop_all()
        db.create_all()

        session, _ = generate_full("determinism_sess", roles, seed=2077)
        assert snapshot(session) == first

        db.session.remove()
        db.drop_all()
        db.create_all()

        session, _ = generate_full("determinism_sess", roles, seed=2078)
        assert snapshot(session) != first

# --- 4. Killer sheet / core and trail secrets ---
def test_killer_sheet_and_no_core_or_trail_references(app):
    with app.app_context():
        pools = load_all_objective_templates()
        killer_only_ids = {t["id"] for t in pools["hide_evidence"] + pools["frame"]}
        for seed in range(20):
            session, chars = generate_full(f"killer_sess_{seed}", ["solo", "exec", "netrunner", "fixer", "media", "tech"], seed=seed)
            killer = [c for c in chars if c.is_killer][0]
            objectives = Objective.query.filter_by(session_id=session.id).all()
            killer_kinds = [o.kind for o in objectives if o.character_id == killer.id]
            assert 'find_killer' not in killer_kinds
            assert 'exploit_secret' not in killer_kinds
            assert killer_kinds.count('hide_evidence') == 1
            assert killer_kinds.count('frame') == 1

            trail_ids = [s.id for s in Secret.query.filter_by(session_id=session.id, is_killer_trail=True).all()]
            trail_knowers = {k.knower_character_id for k in SecretKnowledge.query.filter(SecretKnowledge.secret_id.in_(trail_ids)).all()}
            frame = [o for o in objectives if o.kind == 'frame'][0]
            assert frame.related_character_id != killer.id
            assert frame.related_character_id not in trail_knowers

            for o in objectives:
                if o.character_id != killer.id:
                    assert o.template_id not in killer_only_ids
                if o.related_secret_id:
                    s = Secret.query.get(o.related_secret_id)
                    assert not s.is_killer_core and not s.is_killer_trail

def test_three_characters_are_rejected(app):
    with app.app_context():
        session, chars = create_test_session("three_sess", ["solo", "exec", "netrunner"])
        _generate_secrets(session, rng=random.Random(1))
        with pytest.raises(ValueError, match="At least 4 characters"):
            _generate_objectives(session, rng=random.Random(1))
        assert Objective.query.filter_by(session_id=session.id).count() == 0

def test_generation_preconditions(app):
    with app.app_context():
        session, chars = create_test_session("precond_sess", ["solo", "exec", "netrunner", "fixer"])
        with pytest.raises(ValueError, match="Exactly one killer"):
            _generate_objectives(session, rng=random.Random(1))
        chars[0].is_killer = True
        db.session.commit()
        with pytest.raises(ValueError, match="Secrets must be generated"):
            _generate_objectives(session, rng=random.Random(1))
        _generate_secrets(session, rng=random.Random(1))
        _generate_objectives(session, rng=random.Random(1))
        with pytest.raises(ValueError, match="already exist"):
            _generate_objectives(session, rng=random.Random(1))

# --- 5. Budget normalizer edge cases ---
def _draft(kind, slot, points, severity=None):
    return ObjectiveDraft(character_id="c", kind=kind, slot=slot, template_id=f"t_{kind}", points=points,
                          text=kind, severity=severity)

def test_budget_two_three_pointers_keeps_one(app):
    protect = _draft('protect_secret', SLOT_PROTECT, 3, severity=3)
    exploit = _draft('exploit_secret', SLOT_EXPLOIT, 3, severity=3)
    drafts = [protect, exploit, _draft('relationship', SLOT_RELATIONSHIP, 2), _draft('find_killer', SLOT_GENERIC, 2)]
    adjustments = normalize_budget(drafts, is_killer=False)
    assert [d.points for d in drafts] == [3, 2, 2, 2]
    assert adjustments == [{
        "objective_id": exploit.id, "character_id": "c", "kind": "exploit_secret",
        "before": 3, "after": 2, "reason": "at_most_one_three_pointer"
    }]

def test_budget_killer_frame_keeps_the_three(app):
    protect = _draft('protect_secret', SLOT_PROTECT, 3, severity=3)
    frame = _draft('frame', SLOT_FRAME, 3)
    drafts = [protect, _draft('relationship', SLOT_RELATIONSHIP, 1), _draft('hide_evidence', SLOT_GENERIC, 2), frame]
    normalize_budget(drafts, is_killer=True)
    assert frame.points == 3
    assert protect.points == 2
    assert sum(d.points for d in drafts) == 8

def test_budget_low_total_is_promoted(app):
    drafts = [_draft('protect_secret', SLOT_PROTECT, 1, severity=1), _draft('exploit_secret', SLOT_EXPLOIT, 1, severity=1),
              _draft('relationship', SLOT_RELATIONSHIP, 1), _draft('find_killer', SLOT_GENERIC, 2)]
    adjustments = normalize_budget(drafts, is_killer=False)
    assert [d.points for d in drafts] == [2, 2, 2, 2]
    assert len(adjustments) == 3
    assert all(a["reason"] == "total_below_minimum" and a["before"] == 1 and a["after"] == 2 for a in adjustments)

def test_budget_promotion_never_passes_two_while_a_three_exists(app):
    drafts = [_draft('protect_secret', SLOT_PROTECT, 3, severity=3), _draft('exploit_secret', SLOT_EXPLOIT, 1, severity=1),
              _draft('relationship', SLOT_RELATIONSHIP, 1), _draft('find_killer', SLOT_GENERIC, 2)]
    normalize_budget(drafts, is_killer=False)
    assert [d.points for d in drafts] == [3, 2, 1, 2]
    assert len([d for d in drafts if d.points == 3]) == 1

def test_budget_high_total_is_demoted(app):
    drafts = [_draft('flavor', i, 3) for i in range(5)]
    normalize_budget(drafts, is_killer=False)
    assert len([d for d in drafts if d.points == 3]) == 1
    assert 8 <= sum(d.points for d in drafts) <= 12
    assert min(d.points for d in drafts) >= 1

def test_budget_is_untouched_when_already_valid(app):
    drafts = [_draft('protect_secret', SLOT_PROTECT, 2, severity=2), _draft('exploit_secret', SLOT_EXPLOIT, 3, severity=3),
              _draft('relationship', SLOT_RELATIONSHIP, 2), _draft('find_killer', SLOT_GENERIC, 2)]
    assert normalize_budget(drafts, is_killer=False) == []

# --- 6. Endpoints and authz ---
@pytest.fixture
def generated(app):
    with app.app_context():
        session, chars = generate_full("api_sess", ["solo", "exec", "netrunner", "fixer", "media"], seed=7)
        yield {
            'session': session.id,
            'gm': session.gm_id,
            'players': [c.player_id for c in chars],
            'chars': [c.id for c in chars],
        }

def test_generate_objectives_authz_and_conflict(client, app, generate_token):
    with app.app_context():
        session, chars = create_test_session("gen_api_sess", ["solo", "exec", "netrunner", "fixer"])
        _generate_secrets(session, rng=random.Random(3))
        url = f'/api/sessions/{session.id}/generate-objectives'

        assert client.post(url).status_code == 401
        assert client.post(url, headers=auth(generate_token(chars[0].player_id))).status_code == 403
        assert client.post('/api/sessions/nope/generate-objectives', headers=auth(generate_token(session.gm_id))).status_code == 404

        res = client.post(url, headers=auth(generate_token(session.gm_id)))
        assert res.status_code == 200, res.json
        assert Objective.query.filter_by(session_id=session.id).count() >= 16

        assert client.post(url, headers=auth(generate_token(session.gm_id))).status_code == 409

def test_generate_objectives_bad_state_is_400(client, app, generate_token):
    with app.app_context():
        session, chars = create_test_session("gen_400_sess", ["solo", "exec", "netrunner", "fixer"])
        res = client.post(f'/api/sessions/{session.id}/generate-objectives', headers=auth(generate_token(session.gm_id)))
        assert res.status_code == 400
        assert Objective.query.filter_by(session_id=session.id).count() == 0

def test_get_objectives_player_sees_only_own_restricted_shape(client, generated, generate_token):
    url = f'/api/sessions/{generated["session"]}/objectives'

    res = client.get(url, headers=auth(generate_token(generated['gm'])))
    assert res.status_code == 200
    gm_rows = res.json['objectives']
    assert {o['character_id'] for o in gm_rows} == set(generated['chars'])
    for o in gm_rows:
        assert GM_ONLY_FIELDS <= set(o.keys())
        assert 'subject_name' in o
    assert any(o['gm_hint'] for o in gm_rows)
    assert any(o['linked_objective_id'] for o in gm_rows)

    own_ids = {o['id'] for o in gm_rows if o['character_id'] == generated['chars'][0]}
    res = client.get(url, headers=auth(generate_token(generated['players'][0])))
    assert res.status_code == 200
    player_rows = res.json['objectives']
    assert {o['id'] for o in player_rows} == own_ids
    for o in player_rows:
        assert set(o.keys()) <= PLAYER_FIELDS
        assert not (GM_ONLY_FIELDS & set(o.keys()))

    # A player cannot widen the query with the GM's filter
    res = client.get(url + f'?character_id={generated["chars"][1]}', headers=auth(generate_token(generated['players'][0])))
    assert {o['id'] for o in res.json['objectives']} == own_ids

    # The GM filter narrows to one character
    res = client.get(url + f'?character_id={generated["chars"][1]}', headers=auth(generate_token(generated['gm'])))
    assert {o['character_id'] for o in res.json['objectives']} == {generated['chars'][1]}

def test_character_serialization_uses_objective_table(client, app, generated, generate_token):
    url = f'/api/sessions/{generated["session"]}/characters/{generated["chars"][0]}'

    res = client.get(url, headers=auth(generate_token(generated['players'][0])))
    assert res.status_code == 200
    objectives = res.json['objectives']
    assert 4 <= len(objectives) <= 5
    for o in objectives:
        assert {'id', 'text', 'points'} <= set(o.keys())
        assert not (GM_ONLY_FIELDS & set(o.keys()))

    res = client.get(url, headers=auth(generate_token(generated['gm'])))
    assert all('gm_hint' in o and 'link_type' in o for o in res.json['objectives'])

    res = client.get(f'/api/sessions/{generated["session"]}/characters/mine', headers=auth(generate_token(generated['players'][0])))
    assert [o['id'] for o in res.json['objectives']] == [o['id'] for o in objectives]

    # The deprecated JSON field is no longer writable through the character PATCH
    res = client.patch(url, json={'objectives': ['old style objective']}, headers=auth(generate_token(generated['players'][0])))
    assert res.status_code == 200
    with app.app_context():
        assert not Character.query.get(generated['chars'][0]).objectives
        assert Objective.query.filter_by(character_id=generated['chars'][0]).count() == len(objectives)

def test_patch_objective_permissions_and_validation(client, app, generated, generate_token):
    owner = auth(generate_token(generated['players'][0]))
    other = auth(generate_token(generated['players'][1]))
    gm = auth(generate_token(generated['gm']))
    with app.app_context():
        objective = Objective.query.filter_by(character_id=generated['chars'][0]).first()
        objective_id, old_hint = objective.id, objective.gm_hint
    url = f'/api/sessions/{generated["session"]}/objectives/{objective_id}'

    res = client.patch(url, json={'text': 'My own wording', 'points': 1}, headers=owner)
    assert res.status_code == 200
    assert res.json['objective']['text'] == 'My own wording'
    assert res.json['objective']['points'] == 1
    assert 'gm_hint' not in res.json['objective']

    # Owner cannot touch gm_hint
    assert client.patch(url, json={'gm_hint': 'free points'}, headers=owner).status_code == 403
    with app.app_context():
        assert Objective.query.get(objective_id).gm_hint == old_hint

    # Points must be an integer 1-3
    for bad in (0, 4, -1, '2', 2.5, True, None):
        assert client.patch(url, json={'points': bad}, headers=owner).status_code == 400, bad
    assert client.patch(url, json={'text': '   '}, headers=owner).status_code == 400
    with app.app_context():
        assert Objective.query.get(objective_id).points == 1

    # Another player cannot edit or delete
    assert client.patch(url, json={'text': 'hijacked'}, headers=other).status_code == 403
    assert client.delete(url, headers=other).status_code == 403

    # GM can edit everything, including gm_hint
    res = client.patch(url, json={'gm_hint': 'Award generously', 'points': 3}, headers=gm)
    assert res.status_code == 200
    assert res.json['objective']['gm_hint'] == 'Award generously'
    with app.app_context():
        row = Objective.query.get(objective_id)
        assert row.text == 'My own wording' and row.points == 3

    assert client.patch(f'/api/sessions/{generated["session"]}/objectives/missing', json={'points': 1}, headers=gm).status_code == 404

def test_create_manual_objective(client, app, generated, generate_token):
    owner = auth(generate_token(generated['players'][0]))
    other = auth(generate_token(generated['players'][1]))
    gm = auth(generate_token(generated['gm']))
    url = f'/api/sessions/{generated["session"]}/objectives'
    body = {'character_id': generated['chars'][0], 'text': 'Steal the centerpiece', 'points': 2}

    res = client.post(url, json=body, headers=owner)
    assert res.status_code == 201
    assert res.json['objective']['kind'] == 'manual'
    assert not (GM_ONLY_FIELDS & set(res.json['objective'].keys()))
    with app.app_context():
        row = Objective.query.get(res.json['objective']['id'])
        assert row.kind == 'manual' and row.template_id is None and row.points == 2

    assert client.post(url, json=dict(body, gm_hint='x'), headers=owner).status_code == 403
    assert client.post(url, json=body, headers=other).status_code == 403
    assert client.post(url, json=dict(body, points=5), headers=owner).status_code == 400
    assert client.post(url, json=dict(body, text=''), headers=owner).status_code == 400
    assert client.post(url, json=dict(body, character_id='missing'), headers=gm).status_code == 404

    res = client.post(url, json=dict(body, gm_hint='GM note'), headers=gm)
    assert res.status_code == 201
    assert res.json['objective']['gm_hint'] == 'GM note'

    # The new rows show up on the player's sheet, without the hint
    res = client.get(url, headers=owner)
    manual = [o for o in res.json['objectives'] if o['kind'] == 'manual']
    assert len(manual) == 2
    assert all('gm_hint' not in o for o in manual)

def test_delete_objective_nulls_partner_link(client, app, generated, generate_token):
    with app.app_context():
        linked = Objective.query.filter(Objective.session_id == generated['session'],
                                        Objective.linked_objective_id.isnot(None)).first()
        linked_id, partner_id = linked.id, linked.linked_objective_id
        owner_player = Character.query.get(linked.character_id).player_id

    res = client.delete(f'/api/sessions/{generated["session"]}/objectives/{linked_id}', headers=auth(generate_token(owner_player)))
    assert res.status_code == 200
    with app.app_context():
        assert Objective.query.get(linked_id) is None
        partner = Objective.query.get(partner_id)
        assert partner is not None
        assert partner.linked_objective_id is None and partner.link_type is None

def test_objectives_report_endpoint(client, app, generated, generate_token):
    url = f'/api/sessions/{generated["session"]}/objectives/report'
    gm = auth(generate_token(generated['gm']))

    res = client.get(url, headers=gm)
    assert res.status_code == 200
    data = res.json
    assert len(data['characters']) == 5
    assert 'retries' in data and 'linked_pairs' in data
    assert data['scapegoat']['candidates'] and all('weight' in c for c in data['scapegoat']['candidates'])
    for char_rep in data['characters']:
        assert 'point_adjustments' in char_rep and 'linked_pairs' in char_rep
        assert 4 <= len(char_rep['objectives']) <= 5
        for o in char_rep['objectives']:
            for key in ('kind', 'template_id', 'pool', 'source', 'relaxed_step', 'points'):
                assert key in o

    assert client.get(url, headers=auth(generate_token(generated['players'][0]))).status_code == 403

    # Without the in-memory cache the report is rebuilt from the rows
    from generation.objectives import discard_objectives_report
    discard_objectives_report(generated['session'])
    res = client.get(url, headers=gm)
    assert res.status_code == 200
    assert len(res.json['characters']) == 5

    app.config["DEBUG"] = False
    assert client.get(url, headers=gm).status_code == 404
    app.config["DEBUG"] = True

# --- 7. Single Generate pipeline ---
def test_pipeline_success_sets_status_generated(client, app, generate_token):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_ok", ["solo", "exec", "netrunner", "fixer", "media"])
        url = f'/api/sessions/{session.id}/generate'

        assert client.post(url, headers=auth(generate_token(chars[0].player_id))).status_code == 403

        res = client.post(url, headers=auth(generate_token(session.gm_id)))
        assert res.status_code == 200, res.json
        assert res.json['status'] == 'generated'

        db.session.expire_all()
        session = Session.query.get("pipe_ok")
        assert session.status == 'generated'
        counts = generated_counts(session.id)
        assert all(v > 0 for v in counts.values())
        assert res.json['objectives'] == counts['objectives']
        assert len([c for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer]) == 1
        verify_secrets(session)
        verify_objectives(session)
        assert_knowledge_safety(session)

def test_pipeline_regenerate_replaces_everything(client, app, generate_token):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_regen", ["solo", "exec", "netrunner", "fixer", "media"])
        url = f'/api/sessions/{session.id}/generate'
        gm = auth(generate_token(session.gm_id))

        assert client.post(url, headers=gm).status_code == 200
        db.session.expire_all()
        killer_id = [c.id for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer][0]
        old_objective_ids = {o.id for o in Objective.query.filter_by(session_id=session.id).all()}
        old_secret_ids = {s.id for s in Secret.query.filter_by(session_id=session.id).all()}
        old_rel_ids = {r.id for r in Relationship.query.filter_by(session_id=session.id).all()}

        # Existing content without the flag is a conflict and changes nothing
        res = client.post(url, headers=gm)
        assert res.status_code == 409
        assert {o.id for o in Objective.query.filter_by(session_id=session.id).all()} == old_objective_ids

        res = client.post(url + '?regenerate=true', headers=gm)
        assert res.status_code == 200, res.json
        db.session.expire_all()
        assert not ({o.id for o in Objective.query.filter_by(session_id=session.id).all()} & old_objective_ids)
        assert not ({s.id for s in Secret.query.filter_by(session_id=session.id).all()} & old_secret_ids)
        assert not ({r.id for r in Relationship.query.filter_by(session_id=session.id).all()} & old_rel_ids)
        # The killer assignment survives a regenerate
        assert [c.id for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer] == [killer_id]
        session = Session.query.get("pipe_regen")
        assert session.status == 'generated'
        verify_objectives(session)

def test_pipeline_refuses_active_and_closed(client, app, generate_token):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_active", ["solo", "exec", "netrunner", "fixer"])
        gm = auth(generate_token(session.gm_id))
        for status in ('active', 'closed'):
            session.status = status
            db.session.commit()
            assert client.post(f'/api/sessions/{session.id}/generate', headers=gm).status_code == 409
            assert client.post(f'/api/sessions/{session.id}/generate?regenerate=true', headers=gm).status_code == 409
        assert generated_counts(session.id) == {'relationships': 0, 'secrets': 0, 'knowledge': 0, 'objectives': 0}

def test_pipeline_failure_in_objectives_stage_persists_nothing(client, app, generate_token, monkeypatch):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_fail", ["solo", "exec", "netrunner", "fixer", "media"])
        gm = auth(generate_token(session.gm_id))

        def boom(*args, **kwargs):
            raise ValueError("forced objectives failure")
        monkeypatch.setattr("routes.generation_routes._generate_objectives", boom)

        res = client.post(f'/api/sessions/{session.id}/generate', headers=gm)
        assert res.status_code == 400
        assert res.json['error'] == "forced objectives failure"

        db.session.expire_all()
        assert generated_counts(session.id) == {'relationships': 0, 'secrets': 0, 'knowledge': 0, 'objectives': 0}
        # The auto-assigned killer was rolled back with everything else
        assert not [c for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer]
        assert Session.query.get("pipe_fail").status == 'lobby'

def test_pipeline_unexpected_error_is_500_and_rolls_back(client, app, generate_token, monkeypatch):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_500", ["solo", "exec", "netrunner", "fixer"])

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected")
        monkeypatch.setattr("routes.generation_routes._generate_objectives", boom)

        res = client.post(f'/api/sessions/{session.id}/generate', headers=auth(generate_token(session.gm_id)))
        assert res.status_code == 500
        db.session.expire_all()
        assert generated_counts(session.id) == {'relationships': 0, 'secrets': 0, 'knowledge': 0, 'objectives': 0}
        assert not [c for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer]

def test_pipeline_failed_regenerate_keeps_previous_game(client, app, generate_token, monkeypatch):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_keep", ["solo", "exec", "netrunner", "fixer", "media"])
        gm = auth(generate_token(session.gm_id))
        assert client.post(f'/api/sessions/{session.id}/generate', headers=gm).status_code == 200
        db.session.expire_all()
        before = snapshot(session)
        counts = generated_counts(session.id)

        def boom(*args, **kwargs):
            raise ValueError("forced objectives failure")
        monkeypatch.setattr("routes.generation_routes._generate_objectives", boom)

        assert client.post(f'/api/sessions/{session.id}/generate?regenerate=true', headers=gm).status_code == 400
        db.session.expire_all()
        assert generated_counts(session.id) == counts
        assert snapshot(session) == before

def test_pipeline_three_players_fails_cleanly(client, app, generate_token):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_three", ["solo", "exec", "netrunner"])
        res = client.post(f'/api/sessions/{session.id}/generate', headers=auth(generate_token(session.gm_id)))
        assert res.status_code == 400
        assert "At least 4 characters" in res.json['error']
        db.session.expire_all()
        assert generated_counts(session.id) == {'relationships': 0, 'secrets': 0, 'knowledge': 0, 'objectives': 0}
        assert not [c for c in Character.query.filter_by(session_id=session.id).all() if c.is_killer]

def test_pipeline_gating(client, app, generate_token):
    with app.app_context():
        session, chars = create_ungenerated_session("pipe_gate", ["solo", "exec", "netrunner", "fixer"])
        gm = auth(generate_token(session.gm_id))
        url = f'/api/sessions/{session.id}/generate'

        chars[0].lifepath_complete = False
        db.session.commit()
        res = client.post(url, headers=gm)
        assert res.status_code == 400 and 'lifepath' in res.json['error']
        chars[0].lifepath_complete = True

        late = User(id="late_joiner", username="late_joiner", password_hash="hash")
        db.session.add(late)
        db.session.add(SessionMember(session_id=session.id, user_id=late.id))
        db.session.commit()
        res = client.post(url, headers=gm)
        assert res.status_code == 400 and 'character' in res.json['error']
        assert generated_counts(session.id) == {'relationships': 0, 'secrets': 0, 'knowledge': 0, 'objectives': 0}

def test_standalone_relationship_regeneration_clears_objectives(client, app, generated, generate_token):
    res = client.post(f'/api/sessions/{generated["session"]}/generate-relationships', headers=auth(generate_token(generated['gm'])))
    assert res.status_code == 200
    with app.app_context():
        counts = generated_counts(generated['session'])
        assert counts['objectives'] == 0 and counts['secrets'] == 0 and counts['relationships'] > 0

# --- 8. Character delete cleanup ---
def test_character_delete_cleans_up_objectives(client, app, generate_token):
    with app.app_context():
        session, chars = generate_full("delete_sess", ["solo", "exec", "netrunner", "fixer", "media", "tech"], seed=11)
        # Delete someone other characters point at
        referenced = {o.related_character_id for o in Objective.query.filter_by(session_id=session.id).all() if o.related_character_id}
        victim_id = sorted(referenced)[0]
        others_before = Objective.query.filter(Objective.session_id == session.id, Objective.character_id != victim_id).count()
        gm = auth(generate_token(session.gm_id))

        res = client.delete(f'/api/sessions/{session.id}/characters/{victim_id}', headers=gm)
        assert res.status_code == 200
        db.session.expire_all()

        objectives = Objective.query.filter_by(session_id=session.id).all()
        ids = {o.id for o in objectives}
        secret_ids = {s.id for s in Secret.query.filter_by(session_id=session.id).all()}
        rel_ids = {r.id for r in Relationship.query.filter_by(session_id=session.id).all()}

        # Their own objectives are gone; everyone else keeps theirs
        assert not [o for o in objectives if o.character_id == victim_id]
        assert len(objectives) == others_before
        # No reference to the removed character or to rows removed along with them
        for o in objectives:
            assert o.related_character_id != victim_id
            assert o.linked_objective_id is None or o.linked_objective_id in ids
            assert o.linked_objective_id is not None or o.link_type is None
            assert o.related_secret_id is None or o.related_secret_id in secret_ids
            assert o.related_relationship_id is None or o.related_relationship_id in rel_ids

def test_session_delete_removes_objectives(client, app, generated, generate_token):
    res = client.delete(f'/api/sessions/{generated["session"]}', headers=auth(generate_token(generated['gm'])))
    assert res.status_code == 200
    with app.app_context():
        assert Objective.query.filter_by(session_id=generated['session']).count() == 0
