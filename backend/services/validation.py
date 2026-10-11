"""Request-body validation shared by the routes. Every helper raises ApiError(400)."""
import re

from flask import request

from generation.name_tokens import reject_token_input
from services.errors import ApiError
from services.lifepath_data import get_role_keys

DISPLAY_NAME_MAX = 50
DESCRIPTION_MAX = 4000
TRAITS_MAX_COUNT = 20
TRAIT_MAX = 60
FACTION_NAME_MAX = 60
OBJECTIVE_TEXT_MAX = 500
GM_HINT_MAX = 500
SESSION_NAME_MAX = 80
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,32}$')
PASSWORD_MIN = 8


def json_object():
    """The request body as a dict. An empty body is {}; anything that is not a JSON
    object is a 400 (never a 500 from calling .get on a list/str)."""
    if not request.get_data():
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError('Request body must be a JSON object')
    return data


def _string(value, field, max_len, min_len=0, strip=True):
    if not isinstance(value, str):
        raise ApiError(f'{field} must be a string')
    if strip:
        value = value.strip()
    if len(value) < min_len:
        raise ApiError(f'{field} is required' if min_len == 1 else f'{field} must be at least {min_len} characters')
    if len(value) > max_len:
        raise ApiError(f'{field} must be at most {max_len} characters')
    return reject_token_input(value)


def display_name(value):
    return _string(value, 'display_name', DISPLAY_NAME_MAX, min_len=1)


def description(value):
    return _string(value, 'description', DESCRIPTION_MAX, strip=False)


def traits(value):
    if not isinstance(value, list):
        raise ApiError('traits must be a list of strings')
    if len(value) > TRAITS_MAX_COUNT:
        raise ApiError(f'traits may contain at most {TRAITS_MAX_COUNT} entries')
    return [_string(t, 'each trait', TRAIT_MAX) for t in value]


def faction_name(value, field):
    if value is None:
        return None
    return _string(value, field, FACTION_NAME_MAX)


def role_key(value):
    if not isinstance(value, str) or value not in get_role_keys():
        raise ApiError('Invalid role_key')
    return value


def boolean(value, field):
    if not isinstance(value, bool):
        raise ApiError(f'{field} must be true or false')
    return value


def session_name(value):
    return _string(value, 'Session name', SESSION_NAME_MAX, min_len=1)


def objective_text(value):
    return _string(value, 'Objective text', OBJECTIVE_TEXT_MAX, min_len=1)


def gm_hint(value):
    if value is None:
        return None
    return _string(value, 'gm_hint', GM_HINT_MAX)


def username(value):
    if not isinstance(value, str) or not USERNAME_RE.match(value):
        raise ApiError('Username must be 3-32 characters: letters, numbers, underscore, dot or hyphen')
    return value


def password(value):
    if not isinstance(value, str) or len(value) < PASSWORD_MIN:
        raise ApiError(f'Password must be at least {PASSWORD_MIN} characters')
    return value
