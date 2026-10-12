import random

from flask import Blueprint, current_app, jsonify, g, request
from extensions import db
from generation.compose import compose_suggestion
from models import Session, SessionMember, Character
from auth import require_auth, is_session_gm, can_view_character, can_edit_character
from services import validation
from services.cleanup import delete_characters
from services.character_updates import apply_character_update
from services.serializers import serialize_character
from services.session_lock import ensure_unlocked

bp = Blueprint('characters', __name__)

@bp.route('/<session_id>/characters', methods=['POST'])
@require_auth
def create_character(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    # Any member can create their own character
    member = SessionMember.query.filter_by(session_id=session_id, user_id=g.current_user_id).first()
    if not member and session.gm_id != g.current_user_id:
        return jsonify({'error': 'Not a member of this session'}), 403

    ensure_unlocked(session, 'The game has been generated, so no new characters can be created. '
                             'Ask the GM to reset the game in the lobby first.')

    data = validation.json_object()
    player_id = g.current_user_id  # Players create their own characters
    role_key = data.get('role_key')
    display_name = data.get('display_name')

    if not role_key or not display_name:
        return jsonify({'error': 'role_key and display_name are required'}), 400
    role_key = validation.role_key(role_key)
    display_name = validation.display_name(display_name)

    # Check if character already exists for this player in this session
    existing_char = Character.query.filter_by(session_id=session_id, player_id=player_id).first()
    if existing_char:
        return jsonify({'error': 'Character already exists for this player'}), 409

    new_char = Character(
        session_id=session_id,
        player_id=player_id,
        role_key=role_key,
        display_name=display_name
    )
    db.session.add(new_char)
    db.session.commit()

    return jsonify({
        'id': new_char.id,
        'player_id': new_char.player_id,
        'role_key': new_char.role_key,
        'display_name': new_char.display_name
    }), 201


@bp.route('/<session_id>/characters', methods=['GET'])
@require_auth
def get_all_characters(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can view all characters'}), 403

    characters = Character.query.filter_by(session_id=session_id).all()
    result = []
    for c in characters:
        result.append({
            'id': c.id,
            'player_id': c.player_id,
            'role_key': c.role_key,
            'display_name': c.display_name,
            'is_killer': c.is_killer
        })

    return jsonify({'characters': result}), 200


@bp.route('/<session_id>/characters/mine', methods=['GET'])
@require_auth
def get_my_character(session_id):
    character = Character.query.filter_by(session_id=session_id, player_id=g.current_user_id).first()
    if not character:
        return jsonify({'error': 'Character not found for this user in this session'}), 404

    session = Session.query.get(character.session_id)
    return jsonify(serialize_character(character, bool(session and is_session_gm(g.current_user_id, session)))), 200


@bp.route('/<session_id>/characters/<character_id>', methods=['GET'])
@require_auth
def get_character(session_id, character_id):
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    if not can_view_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to view this character'}), 403

    session = Session.query.get(character.session_id)
    return jsonify(serialize_character(character, bool(session and is_session_gm(g.current_user_id, session)))), 200


@bp.route('/<session_id>/characters/<character_id>', methods=['PATCH'])
@require_auth
def update_character(session_id, character_id):
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    if not can_edit_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to edit this character'}), 403

    session = Session.query.get(session_id)
    data = validation.json_object()
    apply_character_update(character, session, is_session_gm(g.current_user_id, session), data)
    db.session.commit()

    return jsonify({
        'message': 'Character updated successfully',
        'character': {
            'role_specific_lifepath': character.role_specific_lifepath,
            'lifepath_complete': character.lifepath_complete
        }
    }), 200


@bp.route('/<session_id>/characters/<character_id>', methods=['DELETE'])
@require_auth
def delete_character(session_id, character_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can delete characters'}), 403

    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    ensure_unlocked(session, 'The game has been generated, so characters cannot be deleted. '
                             'Reset the game in the lobby first.')
    delete_characters([character.id])
    db.session.commit()

    return jsonify({'message': 'Character deleted successfully'}), 200


@bp.route('/<session_id>/characters/<character_id>/compose', methods=['POST'])
@require_auth
def compose_from_lifepath(session_id, character_id):
    """Suggests a description and traits from the Lifepath. Never writes to the database,
    so it is allowed in any session status."""
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    if not can_edit_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to edit this character'}), 403

    if not character.lifepath_complete:
        return jsonify({'error': 'Finish the Lifepath first: Compose needs every Lifepath choice.'}), 400

    rng = random.Random()
    raw_seed = request.args.get('seed')
    if raw_seed is not None and (current_app.debug or current_app.testing):
        try:
            rng = random.Random(int(raw_seed))
        except ValueError:
            return jsonify({'error': 'seed must be an integer'}), 400

    return jsonify(compose_suggestion(character, rng=rng)), 200
