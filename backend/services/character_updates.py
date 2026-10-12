"""One place that validates and applies edits to a character.

Used by the PATCH route (``mode='strict'``: first problem is a 400/409) and by import
(``mode='lenient'``: every problem becomes a ``skipped`` entry and the rest still applies),
so an imported file can never do anything a normal edit could not.

Free fields (name, description, traits) are always editable. Locked fields (role,
Lifepath, factions, killer flag) only change while the session is in the lobby; sending
the stored value again is a no-op everywhere.
"""
from types import SimpleNamespace

from services import validation
from services.errors import ApiError
from services.lifepath_data import (
    check_lifepath_complete, get_role_labels, is_valid_option, load_lifepath_data,
)
from services.session_lock import LOCK_CODE, is_locked

SHARED_FIELDS_MAP = {
    'cultural_origin': 'culturalOrigin', 'personality': 'personality', 'clothing_style': 'clothingStyle',
    'hairstyle': 'hairstyle', 'affectation': 'affectation', 'value_most': 'valueMost',
    'feelings_about_people': 'feelingsAboutPeople', 'most_valued_person': 'mostValuedPerson',
    'most_valued_possession': 'mostValuedPossession', 'family_background': 'familyBackground',
    'childhood_environment': 'childhoodEnvironment', 'family_crisis': 'familyCrisis', 'life_goals': 'lifeGoals'
}
FREE_FIELDS = ('display_name', 'description', 'traits')
LOCK_MESSAGE = ('The game has been generated, so role, Lifepath and killer cannot be '
                'changed. Ask the GM to reset the game in the lobby first.')
IMPORT_LOCK_REASON = 'The game has been generated; ask the GM to reset it to restore Lifepath.'
PREVIEW_MAX = 80

FACTION_LABELS = {
    'friend_faction_type': 'Friend faction type', 'friend_faction_name': 'Friend faction name',
    'friend_faction_feeling': 'Friend faction feeling', 'enemy_faction_type': 'Enemy faction type',
    'enemy_faction_name': 'Enemy faction name', 'enemy_faction_feeling': 'Enemy faction feeling',
}
BASE_LABELS = {'display_name': 'Character name', 'description': 'Description', 'traits': 'Traits',
               'role_key': 'Role', 'is_killer': 'Killer'}


def option_text(table, value):
    """Readable text for an option id (the id itself when it is not in the table)."""
    if value in (None, ''):
        return None
    for opt in (table or {}).get('options', []):
        if opt.get('id') == value:
            return opt.get('text')
    return value


def preview(value):
    if isinstance(value, list):
        value = ', '.join(value)
    if value in (None, ''):
        return None
    value = str(value)
    return value if len(value) <= PREVIEW_MAX else value[:PREVIEW_MAX - 3] + '...'


def field_label(field, shared):
    if field in BASE_LABELS:
        return BASE_LABELS[field]
    if field in FACTION_LABELS:
        return FACTION_LABELS[field]
    if field in SHARED_FIELDS_MAP:
        return (shared.get(SHARED_FIELDS_MAP[field]) or {}).get('label', field)
    return field


def _row(field, label, status, before=None, after=None, reason=None, code=None):
    return {'field': field, 'label': label, 'status': status, 'before': before, 'after': after,
            'reason': reason, 'code': code}


def _projection(character, values, role_specific):
    """The character as it would look after the update (for lifepath_complete)."""
    attrs = {name: getattr(character, name) for name in
             ('role_key', *SHARED_FIELDS_MAP, *FACTION_LABELS)}
    attrs.update(values)
    attrs['role_specific_lifepath'] = role_specific
    return SimpleNamespace(**attrs)


def apply_character_update(character, session, is_gm, data, *, mode='strict',
                           replace_role_specific=False, dry_run=False):
    """Validate ``data`` and apply it to ``character`` (no commit). Returns a report:
    ``{'fields': [...], 'summary': {...}, 'lifepath_complete': bool, 'changed': bool}``."""
    strict = mode == 'strict'
    shared, faction_types, faction_feelings, roles_data = load_lifepath_data()
    role_labels = get_role_labels()
    locked_session = is_locked(session)

    rows = []
    free = {}        # field -> new value
    locked = {}      # field -> new value (only entries that passed validation)

    def attempt(field, label, validate, *, locked_field):
        """Runs one validator. Strict: errors propagate. Lenient: becomes a skipped row."""
        try:
            return True, validate()
        except ApiError as e:
            if strict:
                raise
            rows.append(_row(field, label, 'skipped', reason=e.message))
            return False, None

    # --- Validate everything first; nothing is written until every field passes ---
    if 'display_name' in data:
        ok, v = attempt('display_name', 'Character name', lambda: validation.display_name(data['display_name']), locked_field=False)
        if ok: free['display_name'] = v
    if 'description' in data:
        ok, v = attempt('description', 'Description', lambda: validation.description(data['description']), locked_field=False)
        if ok: free['description'] = v
    if 'traits' in data:
        ok, v = attempt('traits', 'Traits', lambda: validation.traits(data['traits']), locked_field=False)
        if ok: free['traits'] = v

    new_role = character.role_key
    if 'role_key' in data:
        ok, v = attempt('role_key', 'Role', lambda: validation.role_key(data['role_key']), locked_field=True)
        if ok:
            new_role = v
            locked['role_key'] = v
    role_changed = new_role != character.role_key

    def option_checker(field, table_data, message):
        def check():
            val = data[field]
            if val is not None and not is_valid_option(val, table_data):
                raise ApiError(message, 400)
            return val
        return check

    tables = {}   # field -> options table, for readable before/after
    for db_field, json_key in SHARED_FIELDS_MAP.items():
        if db_field in data:
            tables[db_field] = shared.get(json_key)
            ok, v = attempt(db_field, field_label(db_field, shared),
                            option_checker(db_field, shared.get(json_key), f'Invalid value for {db_field}'), locked_field=True)
            if ok: locked[db_field] = v

    for side in ('friend', 'enemy'):
        type_f, name_f, feel_f = f'{side}_faction_type', f'{side}_faction_name', f'{side}_faction_feeling'
        if type_f in data:
            tables[type_f] = faction_types
            ok, v = attempt(type_f, FACTION_LABELS[type_f],
                            option_checker(type_f, faction_types, f'Invalid {side} faction type'), locked_field=True)
            if ok: locked[type_f] = v
        if name_f in data:
            ok, v = attempt(name_f, FACTION_LABELS[name_f], lambda f=name_f: validation.faction_name(data[f], f), locked_field=True)
            if ok: locked[name_f] = v
        if feel_f in data:
            tables[feel_f] = faction_feelings.get(side + 'Feelings')
            ok, v = attempt(feel_f, FACTION_LABELS[feel_f],
                            option_checker(feel_f, faction_feelings.get(side + 'Feelings'), f'Invalid {side} feeling'), locked_field=True)
            if ok: locked[feel_f] = v

    # Role-specific picks. A role change clears the old role's picks first.
    base_specific = {} if role_changed else dict(character.role_specific_lifepath or {})
    new_tables = ((roles_data.get(new_role) or {}).get('tables', {})) if new_role else {}
    specific_rows = []
    new_specific = None
    if 'role_specific_lifepath' in data:
        val = data['role_specific_lifepath']
        if not isinstance(val, dict):
            if strict:
                raise ApiError('role_specific_lifepath must be an object')
            rows.append(_row('role_specific_lifepath', 'Role-specific Lifepath', 'skipped', reason='Must be an object'))
        else:
            accepted = {}
            for k, v in val.items():
                if new_role and roles_data.get(new_role):
                    table = new_tables.get(k)
                    if not (table is not None and is_valid_option(v, table)):
                        if strict:
                            raise ApiError(f'Invalid value for role specific field {k}', 400)
                        label = (table or {}).get('label', k)
                        rows.append(_row(f'role_specific.{k}', label, 'skipped',
                                         reason='Not a valid choice for this role'))
                        continue
                accepted[k] = v
            new_specific = dict(accepted) if replace_role_specific else {**base_specific, **accepted}
            if replace_role_specific and new_role and roles_data.get(new_role):
                keys = list(new_tables)
            else:
                keys = list(accepted)
            for k in keys:
                table = new_tables.get(k) or {}
                current = (character.role_specific_lifepath or {}).get(k) if not role_changed else None
                new_value = new_specific.get(k)
                specific_rows.append((k, table, current, new_value))
            locked['role_specific_lifepath'] = new_specific

    if 'is_killer' in data:
        ok, v = attempt('is_killer', 'Killer', lambda: validation.boolean(data['is_killer'], 'is_killer'), locked_field=True)
        if ok and is_gm:   # players cannot set the killer flag; it is ignored for them
            locked['is_killer'] = v

    # --- Lobby lock: identical value is a no-op, a change is a 409 (strict) or skipped (lenient) ---
    if locked_session:
        for field in list(locked):
            if locked[field] == getattr(character, field):
                continue
            if strict:
                raise ApiError(LOCK_MESSAGE, status=409, code=LOCK_CODE)
            if field == 'role_specific_lifepath':
                for k, table, current, new_value in specific_rows:
                    if current != new_value:
                        rows.append(_row(f'role_specific.{k}', table.get('label', k), 'skipped',
                                         option_text(table, current), option_text(table, new_value),
                                         IMPORT_LOCK_REASON, LOCK_CODE))
                specific_rows = []
            else:
                rows.append(_row(field, field_label(field, shared), 'skipped', _before(character, field, tables, role_labels),
                                 _after(field, locked[field], tables, role_labels), IMPORT_LOCK_REASON, LOCK_CODE))
            del locked[field]
        if 'role_key' not in locked:
            role_changed = False
            new_role = character.role_key
            if 'role_specific_lifepath' not in locked:
                base_specific = dict(character.role_specific_lifepath or {})

    # --- Report rows for everything that will be applied or is unchanged ---
    for field, value in free.items():
        same = value == getattr(character, field)
        rows.append(_row(field, BASE_LABELS[field], 'unchanged' if same else 'apply',
                         preview(getattr(character, field)), preview(value)))
    for field, value in locked.items():
        if field == 'role_specific_lifepath':
            for k, table, current, new_value in specific_rows:
                rows.append(_row(f'role_specific.{k}', table.get('label', k),
                                 'unchanged' if current == new_value else 'apply',
                                 option_text(table, current), option_text(table, new_value)))
            continue
        same = value == getattr(character, field)
        rows.append(_row(field, field_label(field, shared), 'unchanged' if same else 'apply',
                         _before(character, field, tables, role_labels), _after(field, value, tables, role_labels)))

    # --- lifepath_complete for the resulting state ---
    final_values = {f: v for f, v in locked.items() if f != 'role_specific_lifepath' and f != 'is_killer'}
    final_specific = locked.get('role_specific_lifepath', base_specific)
    projected = _projection(character, final_values, final_specific)
    complete = check_lifepath_complete(projected, shared, roles_data)

    # --- Apply ---
    if not dry_run:
        for field, value in free.items():
            setattr(character, field, value)
        if role_changed:
            character.role_specific_lifepath = {}  # Clear on role change
        for field, value in locked.items():
            setattr(character, field, value)
        character.lifepath_complete = complete

    summary = {'apply': 0, 'unchanged': 0, 'skipped': 0}
    for r in rows:
        summary[r['status']] += 1
    return {'fields': rows, 'summary': summary, 'lifepath_complete': complete,
            'changed': summary['apply'] > 0}


def _before(character, field, tables, role_labels):
    value = getattr(character, field)
    if field == 'role_key':
        return role_labels.get(value, value)
    if field in tables:
        return option_text(tables[field], value)
    return value


def _after(field, value, tables, role_labels):
    if field == 'role_key':
        return role_labels.get(value, value)
    if field in tables:
        return option_text(tables[field], value)
    return value
