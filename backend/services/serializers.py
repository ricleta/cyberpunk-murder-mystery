"""One place that turns model rows into API dicts.

Information hiding lives here: players only ever get what is on their own sheet;
GM-only fields are added only when ``for_gm`` / ``viewer_is_gm`` is true.

Generated text stores ``{{c:<id>}}`` name tokens. Every serializer resolves them with a
name map built for the response (``session_name_map``), so a renamed character shows up
everywhere at once and no raw token ever leaves the API.
"""
from extensions import db
from models import Relationship, Secret, SecretKnowledge, Objective
from generation.name_tokens import resolve_names, session_name_map

SHARED_LIFEPATH_FIELDS = (
    'cultural_origin', 'personality', 'clothing_style', 'hairstyle', 'affectation',
    'value_most', 'feelings_about_people', 'most_valued_person', 'most_valued_possession',
    'family_background', 'childhood_environment', 'family_crisis', 'life_goals',
)
FACTION_FIELDS = (
    'friend_faction_type', 'friend_faction_name', 'friend_faction_feeling',
    'enemy_faction_type', 'enemy_faction_name', 'enemy_faction_feeling',
)


def resolve_deep(value, names):
    """resolve_names over every string inside a JSON-like structure (reports)."""
    if isinstance(value, str):
        return resolve_names(value, names)
    if isinstance(value, list):
        return [resolve_deep(v, names) for v in value]
    if isinstance(value, dict):
        return {k: resolve_deep(v, names) for k, v in value.items()}
    return value


def serialize_objective(o, for_gm, names):
    """Players get only what is on their sheet. gm_hint, link info and the secret /
    relationship references are GM-only."""
    data = {
        'id': o.id,
        'text': resolve_names(o.text, names),
        'points': o.points,
        'kind': o.kind
    }
    if o.related_character_id:
        data['related_character_id'] = o.related_character_id
    if for_gm:
        data.update({
            'character_id': o.character_id,
            'character_name': names.get(o.character_id),
            'template_id': o.template_id,
            'gm_hint': resolve_names(o.gm_hint, names),
            'linked_objective_id': o.linked_objective_id,
            'link_type': o.link_type,
            'related_character_id': o.related_character_id,
            'related_secret_id': o.related_secret_id,
            'related_relationship_id': o.related_relationship_id,
            'subject_name': names.get(o.related_character_id) if o.related_character_id else None,
            'sort_order': o.sort_order
        })
    return data


def get_character_objectives(character, for_gm, names=None):
    names = names if names is not None else session_name_map(character.session_id)
    rows = Objective.query.filter_by(character_id=character.id).order_by(Objective.sort_order, Objective.created_at).all()
    return [serialize_objective(o, for_gm, names) for o in rows]


def get_character_secrets(character, names=None, for_gm=False):
    """Secrets live in the Secret/SecretKnowledge tables. Returns the secrets this
    character holds: ones about themselves (text_subject) plus ones they know about
    others (text_knower).

    Players get a deliberately lean shape. The killer's trail secrets are the only
    observations with severity 3 and category murder_*, and their template ids say
    what they are, so a knower must never receive template_id / category / severity
    (they could confirm "this person is the killer"). Own secrets keep severity only.
    The GM payload keeps every field."""
    names = names if names is not None else session_name_map(character.session_id)
    result = []
    for s in Secret.query.filter_by(subject_character_id=character.id).all():
        item = {'id': s.id, 'kind': s.kind, 'text': resolve_names(s.text_subject, names),
                'about_self': True, 'severity': s.severity}
        if for_gm:
            item.update({'template_id': s.template_id, 'category': s.category})
        result.append(item)
    known = db.session.query(Secret, SecretKnowledge).join(
        SecretKnowledge, SecretKnowledge.secret_id == Secret.id
    ).filter(SecretKnowledge.knower_character_id == character.id).all()
    for s, k in known:
        if s.subject_character_id == character.id:
            continue
        item = {'id': s.id, 'kind': s.kind, 'text': resolve_names(s.text_knower or s.text_subject, names),
                'about_self': False, 'how_learned': resolve_names(k.how_learned, names),
                'subject_character_id': s.subject_character_id,
                'subject_name': names.get(s.subject_character_id)}
        if for_gm:
            item.update({'template_id': s.template_id, 'category': s.category, 'severity': s.severity})
        result.append(item)
    return result


def serialize_character(c, viewer_is_gm):
    """Full character sheet (own sheet, or any sheet for the GM)."""
    names = session_name_map(c.session_id)
    data = {
        'id': c.id,
        'player_id': c.player_id,
        'role_key': c.role_key,
        'display_name': c.display_name,
        'description': c.description,
        'traits': c.traits,
        'secrets': get_character_secrets(c, names, for_gm=viewer_is_gm),
        'objectives': get_character_objectives(c, viewer_is_gm, names),
        'is_killer': c.is_killer,
    }
    for field in SHARED_LIFEPATH_FIELDS + FACTION_FIELDS:
        data[field] = getattr(c, field)
    data['role_specific_lifepath'] = c.role_specific_lifepath
    data['lifepath_complete'] = c.lifepath_complete
    return data


def serialize_relationship(r, names):
    return {
        'id': r.id,
        'source_character_id': r.source_character_id,
        'target_character_id': r.target_character_id,
        'rel_type': r.rel_type,
        'description_source': resolve_names(r.description_source, names),
        'description_target': resolve_names(r.description_target, names),
        'is_symmetric': r.is_symmetric,
        'source_name': names.get(r.source_character_id),
        'target_name': names.get(r.target_character_id)
    }


def serialize_secret_for_gm(s, names, knowers):
    return {
        'id': s.id,
        'template_id': s.template_id,
        'subject_character_id': s.subject_character_id,
        'subject_name': names.get(s.subject_character_id),
        'kind': s.kind,
        'category': s.category,
        'severity': s.severity,
        'is_killer_core': s.is_killer_core,
        'is_killer_trail': s.is_killer_trail,
        'text_subject': resolve_names(s.text_subject, names),
        'text_knower': resolve_names(s.text_knower, names),
        'knowers': [{'knower_character_id': k.knower_character_id, 'knower_name': names.get(k.knower_character_id)}
                    for k in knowers]
    }


def serialize_secret_for_subject(s, names):
    """The subject sees their own secret, but never who knows it, the knower wording,
    or template_id / category."""
    return {
        'id': s.id,
        'subject_character_id': s.subject_character_id,
        'subject_name': names.get(s.subject_character_id),
        'kind': s.kind,
        'severity': s.severity,
        'is_killer_core': s.is_killer_core,
        'is_killer_trail': s.is_killer_trail,
        'text_subject': resolve_names(s.text_subject, names)
    }


def serialize_secret_for_knower(s, names):
    """A knower sees only the knower wording and who it is about. No template_id,
    category, severity or killer flags: those would single out the killer's trail secrets."""
    return {
        'id': s.id,
        'subject_character_id': s.subject_character_id,
        'subject_name': names.get(s.subject_character_id),
        'kind': s.kind,
        'text_knower': resolve_names(s.text_knower, names)
    }


def serialize_player_sheet(c, session):
    """Exactly what the player sees on their own sheet, for the owner AND for the GM
    (so the GM can print handouts without leaking anything GM-only). Built from the lean
    player shapes: no template_id / category, severity on own secrets only, no killer
    flag, GM hints, link or related_* fields, player ids or generation data."""
    names = session_name_map(c.session_id)
    secrets = get_character_secrets(c, names, for_gm=False)
    relationships = []
    for r in Relationship.query.filter_by(session_id=c.session_id).all():
        if r.source_character_id == c.id:
            relationships.append({'other_name': names.get(r.target_character_id), 'rel_type': r.rel_type,
                                  'text': resolve_names(r.description_source, names)})
        elif r.target_character_id == c.id:
            relationships.append({'other_name': names.get(r.source_character_id), 'rel_type': r.rel_type,
                                  'text': resolve_names(r.description_target, names)})
    generated = (session.status or 'lobby') != 'lobby' or bool(relationships or secrets) or \
        Secret.query.filter_by(session_id=c.session_id).first() is not None
    return {
        'session': {'name': session.name, 'status': session.status},
        'generated': generated,
        'character': {'id': c.id, 'display_name': c.display_name, 'role_key': c.role_key,
                      'description': c.description or '', 'traits': c.traits or []},
        'secrets': {
            'own': [{'id': s['id'], 'kind': s['kind'], 'text': s['text'], 'severity': s['severity']}
                    for s in secrets if s['about_self']],
            'known': [{'id': s['id'], 'kind': s['kind'], 'text': s['text'], 'subject_name': s['subject_name']}
                      for s in secrets if not s['about_self']],
        },
        'relationships': relationships,
        'objectives': [{'id': o['id'], 'text': o['text'], 'points': o['points'], 'kind': o['kind']}
                       for o in get_character_objectives(c, False, names)],
    }
