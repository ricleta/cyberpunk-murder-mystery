import pytest
import random
import logging
from app import create_app
from extensions import db
from models import User, Session, SessionMember, Character, Relationship, Secret, SecretKnowledge
from config import Config
from generation.secrets import _generate_secrets, verify_secrets
from generation.secret_templates import (
    load_all_secret_templates,
    validate_secret_templates,
    resolve_template,
    pick_template_relaxed,
    calculate_candidate_weight,
    score_tags,
    SessionContext
)
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

from conftest import create_test_session

# --- 1. Uniqueness Tests ---
def test_secrets_uniqueness_across_session_sizes(app):
    with app.app_context():
        # Test 10+ sessions of sizes 4, 8, and 12, including duplicate roles (e.g. 3 Solos)
        test_specs = [
            (4, ["solo", "solo", "solo", "netrunner"]),
            (4, ["exec", "media", "lawman", "fixer"]),
            (4, ["tech", "medtech", "nomad", "rockerboy"]),
            (8, ["solo", "solo", "solo", "exec", "exec", "netrunner", "fixer", "tech"]),
            (8, ["media", "media", "lawman", "medtech", "nomad", "rockerboy", "solo", "exec"]),
            (8, ["solo", "netrunner", "tech", "medtech", "media", "exec", "lawman", "fixer"]),
            (12, ["solo", "solo", "solo", "exec", "exec", "netrunner", "netrunner", "tech", "medtech", "media", "lawman", "fixer"]),
            (12, ["rockerboy", "rockerboy", "nomad", "nomad", "solo", "solo", "exec", "media", "lawman", "fixer", "tech", "medtech"]),
            (12, ["solo", "exec", "netrunner", "tech", "medtech", "media", "lawman", "fixer", "nomad", "rockerboy", "solo", "exec"]),
            (12, ["fixer", "fixer", "fixer", "solo", "solo", "netrunner", "tech", "medtech", "media", "exec", "lawman", "nomad"])
        ]

        for s_idx, (size, roles) in enumerate(test_specs):
            session_id = f"uniq_sess_{s_idx}_{size}"
            session, chars = create_test_session(session_id, roles)
            rng = random.Random(1000 + s_idx)
            _generate_secrets(session, rng=rng)
            verify_secrets(session)

            # Assert no repeated (subject, template_id)
            secrets = Secret.query.filter_by(session_id=session.id).all()
            subject_templates = set()
            for s in secrets:
                pair = (s.subject_character_id, s.template_id)
                assert pair not in subject_templates, f"Duplicate template {s.template_id} on character {s.subject_character_id}"
                subject_templates.add(pair)

            # Assert no repeated visible text_subject per subject
            for c in chars:
                c_secs = [s for s in secrets if s.subject_character_id == c.id and not s.is_killer_core]
                texts = [s.text_subject for s in c_secs]
                assert len(texts) == len(set(texts)), f"Duplicate text_subject on character {c.display_name}: {texts}"

# --- 2. Hard Gates Tests ---
def test_hard_gates_enforcement(app):
    with app.app_context():
        # Run multiple seeded generations and assert no disallowed role receives a gated template
        pools = load_all_secret_templates()
        all_templates = pools["template_registry"]

        roles = ["solo", "netrunner", "tech", "medtech", "media", "lawman", "exec", "fixer", "nomad", "rockerboy"]
        for iteration in range(15):
            session_id = f"gate_sess_{iteration}"
            # Randomize role assignment
            rng = random.Random(5000 + iteration)
            chosen_roles = [rng.choice(roles) for _ in range(6)]
            session, chars = create_test_session(session_id, chosen_roles)
            _generate_secrets(session, rng=rng)
            verify_secrets(session)

            char_map = {c.id: c for c in chars}
            secrets = Secret.query.filter_by(session_id=session.id).all()
            for s in secrets:
                t = all_templates.get(s.template_id)
                if not t:
                    continue
                c = char_map[s.subject_character_id]
                allowed_roles = t.get("roles", [])
                exclude_roles = t.get("excludeRoles", [])

                if allowed_roles:
                    assert c.role_key in allowed_roles, f"Template {t['id']} requires {allowed_roles} but given to {c.role_key}"
                if exclude_roles:
                    assert c.role_key not in exclude_roles, f"Template {t['id']} excludes {exclude_roles} but given to {c.role_key}"

# --- 3. Statistical Fit Tests ---
def test_statistical_role_fit_ratios(app):
    with app.app_context():
        # Over 500 seeded draws of self and lie secrets, assert role pool usage matches configured rates
        pools = load_all_secret_templates()
        role_key = "solo"
        role_self = pools["roles_pool"][role_key]["self"]
        univ_self = pools["universal_self"]
        role_lie = pools["roles_pool"][role_key]["lie"]
        univ_lie = pools["universal_lie"]

        from generation.secret_templates import pick_template_strict

        dummy_char = Character(role_key=role_key, display_name="TestSolo", id="test_c")
        char_tags = {"street", "violent", "sells_labor"}

        self_role_count = 0
        lie_role_count = 0
        total_trials = 500

        for seed in range(total_trials):
            rng = random.Random(seed)
            ctx = SessionContext()

            # Self draw (target ~60% role pool)
            draw_role = (rng.random() < 0.60)
            first_pool = role_self if draw_role else univ_self
            second_pool = univ_self if draw_role else role_self
            cands = pick_template_strict(first_pool, dummy_char, char_tags, ctx, set(), set())
            if not cands:
                cands = pick_template_strict(second_pool, dummy_char, char_tags, ctx, set(), set())
            chosen_self = rng.choices([c[0] for c in cands], weights=[c[1] for c in cands], k=1)[0]
            if chosen_self in role_self:
                self_role_count += 1

            # Lie draw (target ~40% role pool)
            draw_role_lie = (rng.random() < 0.40)
            first_pool_lie = role_lie if draw_role_lie else univ_lie
            second_pool_lie = univ_lie if draw_role_lie else role_lie
            cands_lie = pick_template_strict(first_pool_lie, dummy_char, char_tags, ctx, set(), set())
            if not cands_lie:
                cands_lie = pick_template_strict(second_pool_lie, dummy_char, char_tags, ctx, set(), set())
            chosen_lie = rng.choices([c[0] for c in cands_lie], weights=[c[1] for c in cands_lie], k=1)[0]
            if chosen_lie in role_lie:
                lie_role_count += 1

        self_rate = self_role_count / total_trials
        lie_rate = lie_role_count / total_trials

        # Assert with loose tolerance (e.g. 50%-70% for self, 30%-50% for lie)
        assert 0.50 <= self_rate <= 0.70, f"Expected self rate ~0.60, got {self_rate}"
        assert 0.30 <= lie_rate <= 0.50, f"Expected lie rate ~0.40, got {lie_rate}"

# --- 4. Session-wide Spread Tests ---
def test_session_wide_spread_penalty(app):
    with app.app_context():
        # In an 8-player session, assert no single universal template is assigned to more than 3 characters
        roles = ["solo", "exec", "netrunner", "tech", "medtech", "media", "lawman", "fixer"]
        session, chars = create_test_session("spread_sess_1", roles)
        rng = random.Random(777)
        _generate_secrets(session, rng=rng)
        verify_secrets(session)

        secrets = Secret.query.filter_by(session_id=session.id).all()
        pools = load_all_secret_templates()

        template_counts = {}
        for s in secrets:
            template_counts[s.template_id] = template_counts.get(s.template_id, 0) + 1

        # Check universal templates specifically
        univ_ids = {t["id"] for t in pools["universal_self"] + pools["universal_lie"] + pools["victim_ties"]}
        for t_id, count in template_counts.items():
            if t_id in univ_ids:
                assert count <= 3, f"Universal template {t_id} assigned {count} times (max allowed is 3)"

# --- 5. Placeholders and Victim Name Tests ---
def test_placeholders_and_scenario_victim_name(app):
    with app.app_context():
        # Startup validation renders all templates against dummy context and ensures no unresolved '#'
        assert validate_secret_templates() is True

        # Assert the scenario pack's victim_name appears in at least one rendered victim tie
        roles = ["exec", "solo", "netrunner", "lawman"]
        session, chars = create_test_session("victim_name_sess", roles)
        _generate_secrets(session, rng=random.Random(123))
        verify_secrets(session)

        victim_tie_secrets = Secret.query.filter_by(session_id=session.id, kind='victim_tie').all()
        assert len(victim_tie_secrets) > 0

        # Scenario pack arasaka_soiree_v1 has victim "Director Kessler Voss"
        found_victim = any("Director Kessler Voss" in s.text_subject for s in victim_tie_secrets)
        assert found_victim, "Expected 'Director Kessler Voss' to appear in rendered victim tie text_subject"

        # Assert no unresolved '#' remains in any secret
        all_secrets = Secret.query.filter_by(session_id=session.id).all()
        for s in all_secrets:
            assert '#' not in s.text_subject, f"Unresolved placeholder in {s.template_id}: {s.text_subject}"
            if s.text_knower:
                assert '#' not in s.text_knower, f"Unresolved placeholder in {s.template_id}: {s.text_knower}"

# --- 6. Relaxation Ladder Tests ---
def test_relaxation_ladder_steps(app, caplog):
    with app.app_context():
        # Build artificial tiny pools to force each relaxation step
        char = Character(role_key="solo", display_name="TestSolo", id="rel_c")
        char_tags = {"street", "violent"}

        # Step 1: Force drop exclude_categories
        # Only template in pool has a category that is in exclude_categories
        pool_step1 = [
            {"id": "t1", "category": "cat_a", "severity": 1, "roles": ["solo"], "excludeRoles": [], "requiresLifepath": {}, "weight": 1.0}
        ]
        ctx1 = SessionContext()
        with caplog.at_level(logging.WARNING):
            res1 = pick_template_relaxed(pool_step1, char, char_tags, ctx1, "self", exclude_ids=set(), synonym_map=None, rng=random.Random(1))
            assert res1["id"] == "t1"
            assert any("drop_exclude_categories" in r["step"] for r in ctx1.relaxed)

        # Step 2: Force drop requiresLifepath
        # Template requires a lifepath value that the character does not have
        pool_step2 = [
            {"id": "t2", "category": "cat_b", "severity": 1, "roles": ["solo"], "excludeRoles": [], "requiresLifepath": {"life_goals": ["impossible_goal"]}, "weight": 1.0}
        ]
        ctx2 = SessionContext()
        res2 = pick_template_relaxed(pool_step2, char, char_tags, ctx2, "self", exclude_ids=set(), synonym_map=None, rng=random.Random(1))
        assert res2["id"] == "t2"
        assert any("drop_requiresLifepath" in r["step"] for r in ctx2.relaxed)

        # Step 3: Force drop roles allowlist (keep excludeRoles)
        # Template allows only 'netrunner', but character is 'solo'
        pool_step3 = [
            {"id": "t3", "category": "cat_c", "severity": 1, "roles": ["netrunner"], "excludeRoles": [], "requiresLifepath": {}, "weight": 1.0}
        ]
        ctx3 = SessionContext()
        res3 = pick_template_relaxed(pool_step3, char, char_tags, ctx3, "self", exclude_ids=set(), synonym_map=None, rng=random.Random(1))
        assert res3["id"] == "t3"
        assert any("drop_roles_allowlist" in r["step"] for r in ctx3.relaxed)

        # Step 4: Force drop all gates (only exclude_ids kept)
        # Template has 'excludeRoles': ['solo'], character is 'solo'
        pool_step4 = [
            {"id": "t4", "category": "cat_d", "severity": 1, "roles": [], "excludeRoles": ["solo"], "requiresLifepath": {}, "weight": 1.0}
        ]
        ctx4 = SessionContext()
        res4 = pick_template_relaxed(pool_step4, char, char_tags, ctx4, "self", exclude_ids=set(), synonym_map=None, rng=random.Random(1))
        assert res4["id"] == "t4"
        assert any("drop_all_except_exclude_ids" in r["step"] for r in ctx4.relaxed)

        # Exhaustion raises ValueError
        with pytest.raises(ValueError, match="Template pool is exhausted"):
            pick_template_relaxed(pool_step4, char, char_tags, ctx4, "self", exclude_ids={"t4"}, synonym_map=None, rng=random.Random(1))

# --- 7. Killer Invariants Tests ---
def test_killer_invariants_preserved(app):
    with app.app_context():
        roles = ["solo", "exec", "netrunner", "fixer"]
        session, chars = create_test_session("killer_inv_sess", roles)
        _generate_secrets(session, rng=random.Random(999))
        verify_secrets(session)

        killer = [c for c in chars if c.is_killer][0]

        core_secrets = Secret.query.filter_by(session_id=session.id, is_killer_core=True).all()
        assert len(core_secrets) == 1
        assert core_secrets[0].subject_character_id == killer.id
        # Zero knowers
        core_knowledge = SecretKnowledge.query.filter_by(secret_id=core_secrets[0].id).all()
        assert len(core_knowledge) == 0

        trail_secrets = Secret.query.filter_by(session_id=session.id, is_killer_trail=True).all()
        assert len(trail_secrets) == 2
        trail_knowers = []
        for ts in trail_secrets:
            assert ts.subject_character_id == killer.id
            knowers = SecretKnowledge.query.filter_by(secret_id=ts.id).all()
            assert len(knowers) == 1
            trail_knowers.append(knowers[0].knower_character_id)

        assert len(trail_knowers) == 2
        assert trail_knowers[0] != trail_knowers[1]
        assert killer.id not in trail_knowers

# --- 8. Dev-only Debugging Report Endpoint Tests ---
def test_dev_report_endpoint(client, app, generate_token):
    with app.app_context():
        roles = ["solo", "exec", "netrunner", "tech"]
        session, chars = create_test_session("report_sess", roles)
        _generate_secrets(session, rng=random.Random(42))

        gm_token = generate_token(session.gm_id)
        player_token = generate_token(chars[0].player_id)

        # 1. GM access in DEBUG=True -> 200
        res = client.get(f'/api/sessions/{session.id}/secrets/report', headers={'Authorization': f'Bearer {gm_token}'})
        assert res.status_code == 200
        data = res.json
        assert 'characters' in data
        assert len(data['characters']) == 4
        for char_rep in data['characters']:
            assert 'role_key' in char_rep
            assert 'secrets' in char_rep
            for sec in char_rep['secrets']:
                assert 'template_id' in sec
                assert 'kind' in sec
                assert 'category' in sec
                assert 'pool' in sec
                assert 'is_relaxed' in sec
                assert 'final_weight' in sec

        # 2. Player access -> 403
        res_player = client.get(f'/api/sessions/{session.id}/secrets/report', headers={'Authorization': f'Bearer {player_token}'})
        assert res_player.status_code == 403

        # 3. DEBUG=False -> 404
        app.config["DEBUG"] = False
        res_nodebug = client.get(f'/api/sessions/{session.id}/secrets/report', headers={'Authorization': f'Bearer {gm_token}'})
        assert res_nodebug.status_code == 404
        app.config["DEBUG"] = True
