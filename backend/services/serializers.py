"""One place that turns model rows into API dicts.

Information hiding lives here: players only ever get what is on their own sheet;
GM-only fields are added only when ``for_gm`` / ``viewer_is_gm`` is true.
"""
from extensions import db
from models import Character, Relationship, Secret, SecretKnowledge, Objective

SHARED_LIFEPATH_FIELDS = (
    'cultural_origin', 'personality', 'clothing_style', 'hairstyle', 'affectation',
    'value_most', 'feelings_about_people', 'most_valued_person', 'most_valued_possession',
    'family_background', 'childhood_environment', 'family_crisis', 'life_goals',
)
FACTION_FIELDS = (
    'friend_faction_type', 'friend_faction_name', 'friend_faction_feeling',
    'enemy_faction_type', 'enemy_faction_name', 'enemy_faction_feeling',
)


def serialize_objective(o, for_gm, char_names=None):
    """Players get only what is on their sheet. gm_hint, link info and the secret /
    relationship references are GM-only."""
    data = {
        'id': o.id,
        'text': o.text,
        'points': o.points,
        'kind': o.kind
    }
    if o.related_character_id:
        data['related_character_id'] = o.related_character_id
    if for_gm:
        char_names = char_names or {}
        data.update({
            'character_id': o.character_id,
            'character_name': char_names.get(o.character_id),
            'template_id': o.template_id,
            'gm_hint': o.gm_hint,
            'linked_objective_id': o.linked_objective_id,
            'link_type': o.link_type,
            'related_character_id': o.related_character_id,
            'related_secret_id': o.related_secret_id,
            'related_relationship_id': o.related_relationship_id,
            'subject_name': char_names.get(o.related_character_id) if o.related_character_id else None,
            'sort_order': o.sort_order
        })
    return data


def get_character_objectives(character, for_gm):
    rows = Objective.query.filter_by(character_id=character.id).order_by(Objective.sort_order, Objective.created_at).all()
    char_names = None
    if for_gm:
        char_names = {c.id: c.display_name for c in Character.query.filter_by(session_id=character.session_id).all()}
    return [serialize_objective(o, for_gm, char_names) for o in rows]


def get_character_secrets(character):
    """Secrets live in the Secret/SecretKnowledge tables. Returns the secrets this
    character holds: ones about themselves (text_subject) plus ones they know about
    others (text_knower)."""
    result = []
    for s in Secret.query.filter_by(subject_character_id=character.id).all():
        result.append({'id': s.id, 'template_id': s.template_id, 'kind': s.kind, 'text': s.text_subject, 'about_self': True})
    known = db.session.query(Secret, SecretKnowledge).join(
        SecretKnowledge, SecretKnowledge.secret_id == Secret.id
    ).filter(SecretKnowledge.knower_character_id == character.id).all()
    for s, k in known:
        if s.subject_character_id == character.id:
            continue
        result.append({'id': s.id, 'template_id': s.template_id, 'kind': s.kind, 'text': s.text_knower or s.text_subject,
                       'about_self': False, 'how_learned': k.how_learned})
    return result


def serialize_character(c, viewer_is_gm):
    """Full character sheet (own sheet, or any sheet for the GM)."""
    data = {
        'id': c.id,
        'player_id': c.player_id,
        'role_key': c.role_key,
        'display_name': c.display_name,
        'description': c.description,
        'traits': c.traits,
        'secrets': get_character_secrets(c),
        'objectives': get_character_objectives(c, viewer_is_gm),
        'is_killer': c.is_killer,
    }
    for field in SHARED_LIFEPATH_FIELDS + FACTION_FIELDS:
        data[field] = getattr(c, field)
    data['role_specific_lifepath'] = c.role_specific_lifepath
    data['lifepath_complete'] = c.lifepath_complete
    return data


def serialize_relationship(r):
    return {
        'id': r.id,
        'source_character_id': r.source_character_id,
        'target_character_id': r.target_character_id,
        'rel_type': r.rel_type,
        'description_source': r.description_source,
        'description_target': r.description_target,
        'is_symmetric': r.is_symmetric
    }


def serialize_secret_for_gm(s, char_map, knowers):
    return {
        'id': s.id,
        'template_id': s.template_id,
        'subject_character_id': s.subject_character_id,
        'subject_name': char_map.get(s.subject_character_id),
        'kind': s.kind,
        'category': s.category,
        'severity': s.severity,
        'is_killer_core': s.is_killer_core,
        'is_killer_trail': s.is_killer_trail,
        'text_subject': s.text_subject,
        'text_knower': s.text_knower,
        'knowers': [{'knower_character_id': k.knower_character_id, 'knower_name': char_map.get(k.knower_character_id)}
                    for k in knowers]
    }


def serialize_secret_for_subject(s, char_map):
    """The subject sees their own secret, but never who knows it or the knower wording."""
    return {
        'id': s.id,
        'template_id': s.template_id,
        'subject_character_id': s.subject_character_id,
        'subject_name': char_map.get(s.subject_character_id),
        'kind': s.kind,
        'category': s.category,
        'severity': s.severity,
        'is_killer_core': s.is_killer_core,
        'is_killer_trail': s.is_killer_trail,
        'text_subject': s.text_subject
    }


def serialize_secret_for_knower(s, char_map):
    """A knower sees only the knower wording: no text_subject, killer flags or other knowers."""
    return {
        'id': s.id,
        'template_id': s.template_id,
        'subject_character_id': s.subject_character_id,
        'subject_name': char_map.get(s.subject_character_id),
        'kind': s.kind,
        'category': s.category,
        'severity': s.severity,
        'text_knower': s.text_knower
    }
