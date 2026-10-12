import random

from flask import Blueprint, current_app, jsonify, g, request
from extensions import db
from generation.compose import compose_suggestion
from models import Session, SessionMember, Character
from auth import require_auth, is_session_gm, can_view_character, can_edit_character
from services import validation
from services.cleanup import delete_characters
from services.errors import ApiError
from services.lifepath_data import load_lifepath_data, is_valid_option, check_lifepath_complete
from services.serializers import serialize_character
from services.session_lock import is_locked, ensure_unlocked, locked_error

bp = Blueprint('characters', __name__)

SHARED_FIELDS_MAP = {
    'cultural_origin': 'culturalOrigin', 'personality': 'personality', 'clothing_style': 'clothingStyle',
    'hairstyle': 'hairstyle', 'affectation': 'affectation', 'value_most': 'valueMost',
    'feelings_about_people': 'feelingsAboutPeople', 'most_valued_person': 'mostValuedPerson',
    'most_valued_possession': 'mostValuedPossession', 'family_background': 'familyBackground',
    'childhood_environment': 'childhoodEnvironment', 'family_crisis': 'familyCrisis', 'life_goals': 'lifeGoals'
}


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


def _validated_option(data, field, table_data, message):
    """The submitted option id (or None to clear it), checked against its options table."""
    val = data[field]
    if val is not None and not is_valid_option(val, table_data):
        raise ApiError(message, 400)
    return val


@bp.route('/<session_id>/characters/<character_id>', methods=['PATCH'])
@require_auth
def update_character(session_id, character_id):
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    if not can_edit_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to edit this character'}), 403

    session = Session.query.get(session_id)
    is_gm = is_session_gm(g.current_user_id, session)
    data = validation.json_object()
    shared, faction_types, faction_feelings, roles_data = load_lifepath_data()

    # --- Validate everything first; nothing is written until every field passes ---
    free = {}      # always editable: name, description, traits
    locked = {}    # role / Lifepath / factions / killer: lobby only (see session_lock)

    if 'display_name' in data:
        free['display_name'] = validation.display_name(data['display_name'])
    if 'description' in data:
        free['description'] = validation.description(data['description'])
    if 'traits' in data:
        free['traits'] = validation.traits(data['traits'])
    # 'secrets' is no longer a Character column; secrets are generated in Phase 4.
    # 'objectives' is not accepted here; use the /objectives endpoints (Phase 5).

    new_role = character.role_key
    if 'role_key' in data:
        new_role = validation.role_key(data['role_key'])
        locked['role_key'] = new_role
    role_changed = new_role != character.role_key

    for db_field, json_key in SHARED_FIELDS_MAP.items():
        if db_field in data:
            locked[db_field] = _validated_option(data, db_field, shared.get(json_key), f'Invalid value for {db_field}')

    for side in ('friend', 'enemy'):
        if f'{side}_faction_type' in data:
            locked[f'{side}_faction_type'] = _validated_option(
                data, f'{side}_faction_type', faction_types, f'Invalid {side} faction type')
        if f'{side}_faction_name' in data:
            locked[f'{side}_faction_name'] = validation.faction_name(data[f'{side}_faction_name'], f'{side}_faction_name')
        if f'{side}_faction_feeling' in data:
            locked[f'{side}_faction_feeling'] = _validated_option(
                data, f'{side}_faction_feeling', faction_feelings.get(side + 'Feelings'), f'Invalid {side} feeling')

    if 'role_specific_lifepath' in data:
        val = data['role_specific_lifepath']
        if not isinstance(val, dict):
            raise ApiError('role_specific_lifepath must be an object')
        role_info = roles_data.get(new_role) if new_role else None
        if role_info:
            for k, v in val.items():
                if not (k in role_info.get('tables', {}) and is_valid_option(v, role_info['tables'][k])):
                    raise ApiError(f'Invalid value for role specific field {k}', 400)
        # A role change clears the old role's picks; then the submitted ones merge in.
        merged = {} if role_changed else dict(character.role_specific_lifepath or {})
        merged.update(val)
        locked['role_specific_lifepath'] = merged

    if 'is_killer' in data:
        new_killer = validation.boolean(data['is_killer'], 'is_killer')
        if is_gm:
            locked['is_killer'] = new_killer
        # Players cannot set the killer flag; it is ignored for them, as before.

    # --- Lobby lock: re-sending an identical value is a no-op, a change is a 409 ---
    if is_locked(session):
        for field, value in locked.items():
            if value != getattr(character, field):
                raise locked_error('The game has been generated, so role, Lifepath and killer cannot be '
                                   'changed. Ask the GM to reset the game in the lobby first.')

    # --- Apply ---
    for field, value in free.items():
        setattr(character, field, value)
    if role_changed:
        character.role_specific_lifepath = {}  # Clear on role change
    for field, value in locked.items():
        setattr(character, field, value)

    character.lifepath_complete = check_lifepath_complete(character, shared, roles_data)
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
