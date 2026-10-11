"""Regression guard for the generators.

Seeded runs of relationships -> secrets -> objectives must keep producing the same
*structure* (roles, secret and objective templates, kinds, points, knowledge pairs by
character index). Text is deliberately not compared. If a generator change is meant to
alter outputs, regenerate the fixture with:

    python tests/test_golden.py --update
"""
import json
import os
import random
import sys

import pytest

HERE = os.path.dirname(__file__)
FIXTURE = os.path.join(HERE, 'fixtures', 'golden_structure.json')
ROLES = ["solo", "netrunner", "tech", "medtech", "media", "lawman", "exec", "fixer", "nomad", "rockerboy", "solo"]
PARTY_SIZES = (4, 6, 8, 11)
SEEDS = range(1, 6)


def build_snapshot(app):
    from extensions import db
    from models import Character, Secret, SecretKnowledge, Objective
    from conftest import create_test_session
    from generation.relationships import _generate_relationships
    from generation.secrets import _generate_secrets
    from generation.objectives import _generate_objectives

    out = {}
    with app.app_context():
        db.create_all()
        for n in PARTY_SIZES:
            for seed in SEEDS:
                sid = f"g{n}_{seed}"
                session, _ = create_test_session(sid, ROLES[:n])
                rng = random.Random(seed)
                _generate_relationships(session, rng=rng)
                _generate_secrets(session, rng=rng)
                _generate_objectives(session, rng=rng)
                chars = sorted(Character.query.filter_by(session_id=sid).all(), key=lambda c: c.id)
                idx = {c.id: i for i, c in enumerate(chars)}
                secrets = Secret.query.filter_by(session_id=sid).all()
                by_id = {s.id: s for s in secrets}
                res = {}
                for c in chars:
                    res[str(idx[c.id])] = {
                        "role": c.role_key, "killer": bool(c.is_killer),
                        "secrets": sorted([s.kind, s.template_id, s.category, s.severity]
                                          for s in secrets if s.subject_character_id == c.id),
                        "objectives": sorted([o.kind, o.template_id, o.points]
                                             for o in Objective.query.filter_by(character_id=c.id).all()),
                    }
                know = sorted([idx[k.knower_character_id], idx[by_id[k.secret_id].subject_character_id],
                               by_id[k.secret_id].template_id]
                              for k in SecretKnowledge.query.join(Secret).filter(Secret.session_id == sid).all())
                out[sid] = {"chars": res, "knowledge": know}
    return json.loads(json.dumps(out))  # normalise tuples to lists


def make_app():
    from app import create_app
    from config import Config

    class Cfg(Config):
        TESTING = True
        DEBUG = True
        SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
        JWT_SECRET_KEY = 'test-secret'
    return create_app(Cfg)


def test_seeded_generation_structure_is_unchanged():
    with open(FIXTURE, encoding='utf-8') as f:
        expected = json.load(f)
    actual = build_snapshot(make_app())
    assert set(actual) == set(expected)
    for sid in expected:
        assert actual[sid] == expected[sid], f"structure changed for {sid}"


if __name__ == '__main__':
    if '--update' not in sys.argv:
        sys.exit('pass --update to rewrite the golden fixture')
    sys.path.insert(0, os.path.join(HERE, '..'))
    sys.path.insert(0, HERE)
    snapshot = build_snapshot(make_app())
    with open(FIXTURE, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, indent=1, sort_keys=True)
    print('updated', FIXTURE)
