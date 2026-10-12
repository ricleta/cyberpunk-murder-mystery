"""Import of a character file (schema version 1). The file is untrusted input.

Structural problems (size, JSON, format, version) are 4xx errors. Anything wrong with an
individual field becomes a ``skipped`` entry in the report and the rest still applies,
because every value goes through the same validators as ``PATCH /characters/<id>``
(``apply_character_update`` in lenient mode). ``is_killer`` and ids in a file are ignored.
"""
import json

from flask import request

from models import Character
from services import validation
from services.character_export import FORMAT_BUNDLE, FORMAT_CHARACTER, SUPPORTED_SCHEMA_VERSIONS
from services.character_updates import (
    FACTION_LABELS, SHARED_FIELDS_MAP, apply_character_update, field_label, option_text, preview,
)
from services.errors import ApiError
from services.lifepath_data import get_role_labels, load_lifepath_data

MAX_IMPORT_BYTES = 64 * 1024
TOP_LEVEL_KEYS = {'format', 'schema_version', 'exported_at', 'character'}
CHARACTER_KEYS = {'display_name', 'role', 'description', 'traits', 'lifepath'}
LIFEPATH_KEYS = {'shared', 'factions', 'role_specific'}
NO_LONGER_EXISTS = 'No longer exists in the current Lifepath tables'


# --- Parsing the request ------------------------------------------------------------------------------

def parse_import_request():
    """The decoded, structurally valid file object. Raises ApiError otherwise."""
    if request.content_length is not None and request.content_length > MAX_IMPORT_BYTES:
        raise ApiError('That file is too large to be a character file (limit 64 KB).', 413)
    raw = request.get_data()
    if len(raw) > MAX_IMPORT_BYTES:
        raise ApiError('That file is too large to be a character file (limit 64 KB).', 413)
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise ApiError('That file is not valid JSON.')
    if not isinstance(data, dict):
        raise ApiError('A character file must be a JSON object.')

    fmt = data.get('format')
    if fmt == FORMAT_BUNDLE:
        raise ApiError('This file holds several characters. Choose one character from the bundle.')
    if fmt != FORMAT_CHARACTER:
        raise ApiError('This is not a character file from this app.')

    version = data.get('schema_version')
    if isinstance(version, bool) or not isinstance(version, int):
        raise ApiError('The file has no valid schema_version.')
    if version > max(SUPPORTED_SCHEMA_VERSIONS):
        raise ApiError('This file was created by a newer version of the app. Update the app and try again.')
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ApiError(f'Unsupported schema_version {version}.')

    if not isinstance(data.get('character'), dict):
        raise ApiError('The file has no character object.')
    return data


# --- Option matching ---------------------------------------------------------------------------------------

def match_option(value, table):
    """Option id for a file value ({id, text} or a plain string): by id, else by exact
    case-insensitive text. None when nothing matches."""
    if isinstance(value, dict):
        candidate_id, candidate_text = value.get('id'), value.get('text')
    else:
        candidate_id = candidate_text = value
    options = (table or {}).get('options', [])
    if isinstance(candidate_id, str):
        for opt in options:
            if opt.get('id') == candidate_id:
                return opt['id']
    if isinstance(candidate_text, str):
        wanted = candidate_text.strip().lower()
        for opt in options:
            if isinstance(opt.get('text'), str) and opt['text'].strip().lower() == wanted:
                return opt['id']
    return None


def _offered_text(value):
    if isinstance(value, dict):
        value = value.get('text') or value.get('id')
    return preview(value if isinstance(value, (str, int, float)) else None)


def _skip(label, field, value, current_text, reason=NO_LONGER_EXISTS):
    return {'field': field, 'label': label, 'status': 'skipped', 'before': current_text,
            'after': _offered_text(value), 'reason': reason, 'code': None}


# --- Turning the file into an update ---------------------------------------------------------------------------

def resolve_role(character_data):
    """(role key or None, role-present flag, skip row or None)."""
    if 'role' not in character_data:
        return None, False, None
    value = character_data['role']
    if value is None:
        return None, True, None
    labels = get_role_labels()
    table = {'options': [{'id': k, 'text': t} for k, t in labels.items()]}
    key = match_option(value, table)
    return key, True, None if key else _skip('Role', 'role_key', value, None)


def build_update(character, file_character, include_text, *, current_role_key):
    """(data for apply_character_update, pre-skipped rows, warnings)."""
    shared, faction_types, faction_feelings, roles_data = load_lifepath_data()
    data, skipped, warnings = {}, [], []

    for key in file_character:
        if key not in CHARACTER_KEYS:
            warnings.append(f'Ignored: {key}')

    def missing(label):
        warnings.append(f'Missing from file: {label} (left unchanged)')

    if 'display_name' in file_character:
        data['display_name'] = file_character['display_name']
    else:
        missing('character name')

    role_key, role_present, role_skip = resolve_role(file_character)
    if role_skip:
        skipped.append(role_skip)
    elif role_present and role_key:
        data['role_key'] = role_key
    elif not role_present:
        missing('role')
    effective_role = data.get('role_key', current_role_key)

    if include_text:
        for key in ('description', 'traits'):
            if key in file_character:
                data[key] = file_character[key]
            else:
                missing(key)

    lifepath = file_character.get('lifepath')
    if lifepath is None:
        missing('Lifepath')
        return data, skipped, warnings
    if not isinstance(lifepath, dict):
        skipped.append(_skip('Lifepath', 'lifepath', None, None, 'Must be an object'))
        return data, skipped, warnings
    for key in lifepath:
        if key not in LIFEPATH_KEYS:
            warnings.append(f'Ignored: lifepath.{key}')

    def resolve_into(field, value, table, label):
        """null clears; a matching option sets; anything else is pre-skipped."""
        if value is None:
            data[field] = None
            return
        option_id = match_option(value, table)
        if option_id is None:
            skipped.append(_skip(label, field, value, option_text(table, getattr(character, field, None))))
        else:
            data[field] = option_id

    group = lifepath.get('shared')
    if group is None:
        missing('shared Lifepath')
    elif not isinstance(group, dict):
        skipped.append(_skip('Shared Lifepath', 'lifepath.shared', None, None, 'Must be an object'))
    else:
        for key in group:
            if key not in SHARED_FIELDS_MAP:
                warnings.append(f'Ignored: lifepath.shared.{key}')
        for field, json_key in SHARED_FIELDS_MAP.items():
            if field in group:
                resolve_into(field, group[field], shared.get(json_key), field_label(field, shared))
            else:
                missing(field_label(field, shared))

    factions = lifepath.get('factions')
    if factions is None:
        missing('factions')
    elif not isinstance(factions, dict):
        skipped.append(_skip('Factions', 'lifepath.factions', None, None, 'Must be an object'))
    else:
        for side in ('friend', 'enemy'):
            entry = factions.get(side)
            if entry is None:
                missing(f'{side} faction')
                continue
            if not isinstance(entry, dict):
                skipped.append(_skip(f'{side.title()} faction', f'lifepath.factions.{side}', None, None, 'Must be an object'))
                continue
            type_f, name_f, feel_f = f'{side}_faction_type', f'{side}_faction_name', f'{side}_faction_feeling'
            if 'type' in entry:
                resolve_into(type_f, entry['type'], faction_types, FACTION_LABELS[type_f])
            if 'name' in entry:
                data[name_f] = entry['name']
            if 'feeling' in entry:
                resolve_into(feel_f, entry['feeling'], faction_feelings.get(side + 'Feelings'), FACTION_LABELS[feel_f])

    specific = lifepath.get('role_specific')
    if specific is None:
        missing('role-specific Lifepath')
    elif not isinstance(specific, dict):
        skipped.append(_skip('Role-specific Lifepath', 'lifepath.role_specific', None, None, 'Must be an object'))
    else:
        tables = (roles_data.get(effective_role) or {}).get('tables', {}) if effective_role else {}
        resolved = {}
        for key, value in specific.items():
            if key not in tables:
                skipped.append(_skip(key, f'role_specific.{key}', value, None, 'Not a choice for this role'))
            elif value is not None:       # null simply leaves the pick cleared (the dict is replaced)
                option_id = match_option(value, tables[key])
                if option_id is None:
                    skipped.append(_skip(tables[key].get('label', key), f'role_specific.{key}', value, None))
                else:
                    resolved[key] = option_id
        data['role_specific_lifepath'] = resolved

    return data, skipped, warnings


# --- Running an import --------------------------------------------------------------------------------------------

def _finish(report, skipped, warnings, *, dry_run, created, character_id):
    fields = report['fields'] + skipped
    summary = {'apply': 0, 'unchanged': 0, 'skipped': 0}
    for f in fields:
        summary[f['status']] += 1
    return {'dry_run': dry_run, 'created': created, 'character_id': character_id, 'fields': fields,
            'summary': summary, 'warnings': warnings, 'lifepath_complete': report['lifepath_complete']}


def import_into_existing(character, session, is_gm, file_data, *, dry_run, include_text):
    data, skipped, warnings = build_update(character, file_data['character'], include_text,
                                           current_role_key=character.role_key)
    warnings += [f'Ignored: {k}' for k in file_data if k not in TOP_LEVEL_KEYS]
    report = apply_character_update(character, session, is_gm, data, mode='lenient',
                                    replace_role_specific=True, dry_run=dry_run)
    return _finish(report, skipped, warnings, dry_run=dry_run, created=False, character_id=character.id)


def import_as_new(session, user_id, is_gm, file_data, *, dry_run, include_text, db):
    """Creates the character (lobby only; the caller has already checked) from the file."""
    file_character = file_data['character']
    # A character cannot exist without a valid name and role
    if 'display_name' not in file_character:
        raise ApiError('The file has no character name.')
    validation.display_name(file_character['display_name'])
    role_key, _, role_skip = resolve_role(file_character)
    if role_skip or not role_key:
        raise ApiError('The role in this file no longer exists, so a character cannot be created from it.')

    bare = Character(session_id=session.id, player_id=user_id)
    data, skipped, warnings = build_update(bare, file_character, include_text, current_role_key=None)
    warnings += [f'Ignored: {k}' for k in file_data if k not in TOP_LEVEL_KEYS]

    if dry_run:
        report = apply_character_update(bare, session, is_gm, data, mode='lenient',
                                        replace_role_specific=True, dry_run=True)
        return _finish(report, skipped, warnings, dry_run=True, created=False, character_id=None)

    db.session.add(bare)
    db.session.flush()
    report = apply_character_update(bare, session, is_gm, data, mode='lenient', replace_role_specific=True)
    return _finish(report, skipped, warnings, dry_run=False, created=True, character_id=bare.id)
