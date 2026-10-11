"""GM master sheet payload: everything in the session, names resolved. GM-only."""
from collections import defaultdict

from models import Character, Relationship, Secret, SecretKnowledge, Objective, User
from generation.name_tokens import session_name_map
from services.lifepath_data import load_lifepath_data
from services.serializers import (
    SHARED_LIFEPATH_FIELDS, serialize_objective, serialize_relationship, serialize_secret_for_gm,
)

SHARED_JSON_KEYS = {
    'cultural_origin': 'culturalOrigin', 'personality': 'personality', 'clothing_style': 'clothingStyle',
    'hairstyle': 'hairstyle', 'affectation': 'affectation', 'value_most': 'valueMost',
    'feelings_about_people': 'feelingsAboutPeople', 'most_valued_person': 'mostValuedPerson',
    'most_valued_possession': 'mostValuedPossession', 'family_background': 'familyBackground',
    'childhood_environment': 'childhoodEnvironment', 'family_crisis': 'familyCrisis', 'life_goals': 'lifeGoals',
}


def _option_text(table, value):
    if not value or not table:
        return None
    for opt in table.get('options', []):
        if opt.get('id') == value:
            return opt.get('text')
    return value


def lifepath_summary(c):
    """The character's Lifepath as readable labels (option ids resolved to their text)."""
    shared, faction_types, faction_feelings, roles_data = load_lifepath_data()
    summary = {'shared': [], 'factions': [], 'role_specific': []}
    for field in SHARED_LIFEPATH_FIELDS:
        table = shared.get(SHARED_JSON_KEYS[field], {})
        summary['shared'].append({'field': field, 'label': table.get('label', field),
                                  'value': _option_text(table, getattr(c, field))})
    for side, feelings_key in (('friend', 'friendFeelings'), ('enemy', 'enemyFeelings')):
        summary['factions'].append({
            'side': side,
            'type': _option_text(faction_types, getattr(c, f'{side}_faction_type')),
            'name': getattr(c, f'{side}_faction_name'),
            'feeling': _option_text(faction_feelings.get(feelings_key), getattr(c, f'{side}_faction_feeling')),
        })
    tables = (roles_data.get(c.role_key) or {}).get('tables', {})
    picks = c.role_specific_lifepath or {}
    for key, table in tables.items():
        summary['role_specific'].append({'field': key, 'label': table.get('label', key),
                                         'value': _option_text(table, picks.get(key))})
    return summary


def build_master(session):
    names = session_name_map(session.id)
    characters = sorted(Character.query.filter_by(session_id=session.id).all(), key=lambda c: c.id)
    usernames = {u.id: u.username for u in User.query.filter(User.id.in_([c.player_id for c in characters])).all()} \
        if characters else {}

    secrets = Secret.query.filter_by(session_id=session.id).order_by(Secret.subject_character_id, Secret.template_id).all()
    knowledge = SecretKnowledge.query.join(Secret).filter(Secret.session_id == session.id).all()
    knowers_by_secret = defaultdict(list)
    matrix = defaultdict(lambda: defaultdict(list))
    subject_of = {s.id: s.subject_character_id for s in secrets}
    for k in knowledge:
        knowers_by_secret[k.secret_id].append(k)
        matrix[k.knower_character_id][subject_of[k.secret_id]].append(k.secret_id)

    relationships = Relationship.query.filter_by(session_id=session.id).order_by(
        Relationship.source_character_id, Relationship.target_character_id).all()
    rel_rows = []
    for r in relationships:
        row = serialize_relationship(r, names)
        row['source_name'] = names.get(r.source_character_id)
        row['target_name'] = names.get(r.target_character_id)
        rel_rows.append(row)

    objectives = Objective.query.filter_by(session_id=session.id).order_by(
        Objective.character_id, Objective.sort_order, Objective.created_at).all()

    return {
        'session': {
            'id': session.id,
            'name': session.name,
            'status': session.status,
            'join_code': session.join_code,
            'generation_seed': session.generation_seed,
            'generated_at': session.generated_at.isoformat() if session.generated_at else None,
        },
        'characters': [{
            'id': c.id,
            'player_id': c.player_id,
            'player_name': usernames.get(c.player_id),
            'display_name': names.get(c.id),
            'role_key': c.role_key,
            'is_killer': bool(c.is_killer),
            'description': c.description or '',
            'traits': c.traits or [],
            'lifepath_complete': bool(c.lifepath_complete),
            'lifepath': lifepath_summary(c),
        } for c in characters],
        'secrets': [serialize_secret_for_gm(s, names, knowers_by_secret.get(s.id, [])) for s in secrets],
        'relationships': rel_rows,
        'objectives': [serialize_objective(o, True, names) for o in objectives],
        'knowledge_matrix': {knower: dict(subjects) for knower, subjects in matrix.items()},
    }
