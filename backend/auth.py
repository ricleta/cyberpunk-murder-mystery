import functools
from flask import request, jsonify, g
import jwt
from models import Session

from flask import current_app

def decode_token(token):
    try:
        secret = current_app.config['JWT_SECRET_KEY']
        payload = jwt.decode(token, secret, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid token'}), 401
        
        token = auth_header.split(' ')[1]
        payload = decode_token(token)
        if not payload:
            return jsonify({'error': 'Token expired or invalid'}), 401
        
        g.current_user_id = payload.get('user_id')
        return f(*args, **kwargs)
    return decorated

def is_session_gm(user_id, session):
    return session.gm_id == user_id

def can_view_character(user_id, character):
    if character.player_id == user_id:
        return True
    session = Session.query.get(character.session_id)
    if session and is_session_gm(user_id, session):
        return True
    return False

def can_edit_character(user_id, character):
    # Same rule as can_view_character for Phase 1
    return can_view_character(user_id, character)
