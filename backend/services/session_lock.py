"""The lobby-only lock rule lives here and nowhere else.

While ``session.status != 'lobby'`` the game has been generated from each character's
role, Lifepath and killer flag, so those can no longer change until the GM resets.
"""
from services.errors import ApiError

LOCK_CODE = 'session_locked'
LOCK_MESSAGE = ('The game has been generated, so this is locked. '
                'Ask the GM to reset the game in the lobby first.')


def is_locked(session):
    return (session.status or 'lobby') != 'lobby'


def locked_error(message=None):
    return ApiError(message or LOCK_MESSAGE, status=409, code=LOCK_CODE)


def ensure_unlocked(session, message=None):
    """Raise the 409 session_locked error unless the session is still in the lobby."""
    if is_locked(session):
        raise locked_error(message)
