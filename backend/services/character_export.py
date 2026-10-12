"""Character export file, schema version 1 (see docs/character-file-format.md).

A file holds only what a player authored: name, role, description, traits and Lifepath.
Never ids, the killer flag, secrets, relationships, objectives or generation data.
"""
from datetime import datetime

from services.character_updates import SHARED_FIELDS_MAP, option_text
from services.lifepath_data import get_role_labels, load_lifepath_data

FORMAT_CHARACTER = 'cyberpunk-murder-mystery/character'
FORMAT_BUNDLE = 'cyberpunk-murder-mystery/character-bundle'
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = {1}


def _option(table, value):
    """{'id', 'text'} for a stored option id, or None when unset."""
    if value in (None, ''):
        return None
    return {'id': value, 'text': option_text(table, value)}


def _now():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def character_to_dict(c):
    shared, faction_types, faction_feelings, roles_data = load_lifepath_data()
    labels = get_role_labels()

    role_tables = (roles_data.get(c.role_key) or {}).get('tables', {}) if c.role_key else {}
    picks = c.role_specific_lifepath or {}

    def faction(side):
        return {
            'type': _option(faction_types, getattr(c, f'{side}_faction_type')),
            'name': getattr(c, f'{side}_faction_name') or None,
            'feeling': _option(faction_feelings.get(side + 'Feelings'), getattr(c, f'{side}_faction_feeling')),
        }

    return {
        'display_name': c.display_name,
        'role': {'id': c.role_key, 'text': labels.get(c.role_key, c.role_key)} if c.role_key else None,
        'description': c.description or '',
        'traits': list(c.traits or []),
        'lifepath': {
            'shared': {field: _option(shared.get(json_key), getattr(c, field))
                       for field, json_key in SHARED_FIELDS_MAP.items()},
            'factions': {'friend': faction('friend'), 'enemy': faction('enemy')},
            'role_specific': {key: _option(table, picks.get(key)) for key, table in role_tables.items()},
        },
    }


def build_character_file(c):
    return {'format': FORMAT_CHARACTER, 'schema_version': SCHEMA_VERSION,
            'exported_at': _now(), 'character': character_to_dict(c)}


def build_bundle(session, characters):
    ordered = sorted(characters, key=lambda c: ((c.display_name or '').lower(), c.id))
    return {'format': FORMAT_BUNDLE, 'schema_version': SCHEMA_VERSION, 'exported_at': _now(),
            'session_name': session.name, 'characters': [character_to_dict(c) for c in ordered]}
