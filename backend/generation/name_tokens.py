"""Name tokens decouple generated text from character names.

Generators store ``{{c:<character id>}}`` instead of a display name. Names are
substituted when the text is *served* (``resolve_names``), so renaming a character
updates every secret, relationship, objective and hint at once.
"""
import re

from services.errors import ApiError

# Character ids are not always 32-char hex (tests use ids like ``c_sess_0``).
TOKEN_ID_PATTERN = r'[A-Za-z0-9_-]+'
RAW_TOKEN_RE = re.compile(r'\{\{c:(' + TOKEN_ID_PATTERN + r')\}\}')
TOKEN_PREFIX = '{{c:'
UNKNOWN_NAME = 'Unknown'
UNNAMED = 'Unnamed'


def name_token(char_id):
    return '{{c:%s}}' % char_id


def resolve_names(text, names):
    """Single-pass substitution: a resolved name is never re-scanned, so a character
    named ``{{c:other}}`` cannot expand into somebody else's name."""
    if not text:
        return text
    return RAW_TOKEN_RE.sub(lambda m: names.get(m.group(1)) or UNKNOWN_NAME, text)


def contains_raw_token(text):
    return isinstance(text, str) and RAW_TOKEN_RE.search(text) is not None


def reject_token_input(value):
    """Validator helper for user-provided free text (400 on a reserved sequence)."""
    if isinstance(value, str) and TOKEN_PREFIX in value:
        raise ApiError('Text contains a reserved sequence')
    return value


def session_name_map(session_id):
    """{character id: display name} for one session (one query per request)."""
    from models import Character
    rows = Character.query.with_entities(Character.id, Character.display_name).filter_by(session_id=session_id).all()
    return {cid: (name or UNNAMED) for cid, name in rows}
