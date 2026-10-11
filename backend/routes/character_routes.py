from flask import Blueprint, request, jsonify, g
from extensions import db
from models import Session, Character, User, Secret, SecretKnowledge
from auth import require_auth, is_session_gm, can_view_character, can_edit_character
import json
import os

def load_lifepath_data():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'data'))
    try:
        with open(os.path.join(base_dir, 'lifepathShared.json'), 'r', encoding='utf-8') as f:
            shared = json.load(f)
        with open(os.path.join(base_dir, 'factionTypes.json'), 'r', encoding='utf-8') as f:
            faction_types = json.load(f)
        with open(os.path.join(base_dir, 'factionFeelings.json'), 'r', encoding='utf-8') as f:
            faction_feelings = json.load(f)
        with open(os.path.join(base_dir, 'rolesLifepath.json'), 'r', encoding='utf-8') as f:
            roles = json.load(f)
        return shared, faction_types, faction_feelings, roles
    except Exception as e:
        print(f"Error loading lifepath data: {e}")
        return {}, {}, {}, {}

def is_valid_option(value, table_data):
    if not value or not table_data or 'options' not in table_data: return False
    return any(opt['id'] == value for opt in table_data['options'])

def check_lifepath_complete(character, shared, roles_data):
    if not character.role_key: return False
    
    # Check shared fields
    shared_fields = [
        'cultural_origin', 'personality', 'clothing_style', 'hairstyle', 'affectation',
        'value_most', 'feelings_about_people', 'most_valued_person', 'most_valued_possession',
        'family_background', 'childhood_environment', 'family_crisis', 'life_goals'
    ]
    for field in shared_fields:
        if not getattr(character, field): return False
        
    # Check faction fields
    if not character.friend_faction_type or not character.friend_faction_feeling or not character.friend_faction_name: return False
    if not character.enemy_faction_type or not character.enemy_faction_feeling or not character.enemy_faction_name: return False
    
    # Check role specific fields
    role_info = roles_data.get(character.role_key)
    if not role_info or 'tables' not in role_info: return False
    
    specific_data = character.role_specific_lifepath or {}
    for table_key in role_info['tables'].keys():
        if table_key not in specific_data or not specific_data[table_key]:
            return False
            
    return True

def get_character_secrets(character):
    """Secrets now live in the Secret/SecretKnowledge tables (Phase 4), not on Character.
    Returns the secrets this character holds: ones about themselves (text_subject)
    plus ones they know about others (text_knower)."""
    result = []
    for s in Secret.query.filter_by(subject_character_id=character.id).all():
        result.append({'id': s.id, 'template_id': s.template_id, 'kind': s.kind, 'text': s.text_subject, 'about_self': True})
    known = db.session.query(Secret, SecretKnowledge).join(
        SecretKnowledge, SecretKnowledge.secret_id == Secret.id
    ).filter(SecretKnowledge.knower_character_id == character.id).all()
    for s, k in known:
        if s.subject_character_id == character.id:
            continue
        result.append({'id': s.id, 'template_id': s.template_id, 'kind': s.kind, 'text': s.text_knower or s.text_subject,
                       'about_self': False, 'how_learned': k.how_learned})
    return result

def get_objectives_for(character):
    """Objectives live in the Objective table (Phase 5), not in the deprecated
    Character.objectives JSON column. gm_hint and link fields are GM-only."""
    from routes.session_routes import get_character_objectives
    session = Session.query.get(character.session_id)
    return get_character_objectives(character, bool(session and is_session_gm(g.current_user_id, session)))

bp = Blueprint('characters', __name__)

@bp.route('/<session_id>/characters', methods=['POST'])
@require_auth
def create_character(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
        
    # Any member can create their own character
    from models import SessionMember
    member = SessionMember.query.filter_by(session_id=session_id, user_id=g.current_user_id).first()
    if not member and session.gm_id != g.current_user_id:
        return jsonify({'error': 'Not a member of this session'}), 403

    data = request.get_json() or {}
    player_id = g.current_user_id  # Players create their own characters
    role_key = data.get('role_key')
    display_name = data.get('display_name')

    if not role_key or not display_name:
        return jsonify({'error': 'role_key and display_name are required'}), 400

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

    return jsonify({
        'id': character.id,
        'player_id': character.player_id,
        'role_key': character.role_key,
        'display_name': character.display_name,
        'description': character.description,
        'traits': character.traits,
        'secrets': get_character_secrets(character),
        'objectives': get_objectives_for(character),
        'is_killer': character.is_killer,
        'cultural_origin': character.cultural_origin,
        'personality': character.personality,
        'clothing_style': character.clothing_style,
        'hairstyle': character.hairstyle,
        'affectation': character.affectation,
        'value_most': character.value_most,
        'feelings_about_people': character.feelings_about_people,
        'most_valued_person': character.most_valued_person,
        'most_valued_possession': character.most_valued_possession,
        'family_background': character.family_background,
        'childhood_environment': character.childhood_environment,
        'family_crisis': character.family_crisis,
        'life_goals': character.life_goals,
        'friend_faction_type': character.friend_faction_type,
        'friend_faction_name': character.friend_faction_name,
        'friend_faction_feeling': character.friend_faction_feeling,
        'enemy_faction_type': character.enemy_faction_type,
        'enemy_faction_name': character.enemy_faction_name,
        'enemy_faction_feeling': character.enemy_faction_feeling,
        'role_specific_lifepath': character.role_specific_lifepath,
        'lifepath_complete': character.lifepath_complete
    }), 200

@bp.route('/<session_id>/characters/<character_id>', methods=['GET'])
@require_auth
def get_character(session_id, character_id):
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    if not can_view_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to view this character'}), 403

    return jsonify({
        'id': character.id,
        'player_id': character.player_id,
        'role_key': character.role_key,
        'display_name': character.display_name,
        'description': character.description,
        'traits': character.traits,
        'secrets': get_character_secrets(character),
        'objectives': get_objectives_for(character),
        'is_killer': character.is_killer,
        'cultural_origin': character.cultural_origin,
        'personality': character.personality,
        'clothing_style': character.clothing_style,
        'hairstyle': character.hairstyle,
        'affectation': character.affectation,
        'value_most': character.value_most,
        'feelings_about_people': character.feelings_about_people,
        'most_valued_person': character.most_valued_person,
        'most_valued_possession': character.most_valued_possession,
        'family_background': character.family_background,
        'childhood_environment': character.childhood_environment,
        'family_crisis': character.family_crisis,
        'life_goals': character.life_goals,
        'friend_faction_type': character.friend_faction_type,
        'friend_faction_name': character.friend_faction_name,
        'friend_faction_feeling': character.friend_faction_feeling,
        'enemy_faction_type': character.enemy_faction_type,
        'enemy_faction_name': character.enemy_faction_name,
        'enemy_faction_feeling': character.enemy_faction_feeling,
        'role_specific_lifepath': character.role_specific_lifepath,
        'lifepath_complete': character.lifepath_complete
    }), 200

@bp.route('/<session_id>/characters/<character_id>', methods=['PATCH'])
@require_auth
def update_character(session_id, character_id):
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    if not can_edit_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to edit this character'}), 403

    data = request.get_json() or {}
    
    # Update fields if provided
    if 'display_name' in data:
        character.display_name = data['display_name']
    if 'description' in data:
        character.description = data['description']
    if 'traits' in data:
        character.traits = data['traits']
    # 'secrets' is no longer a Character column; secrets are generated in Phase 4.
    # 'objectives' is no longer accepted here; use the /objectives endpoints (Phase 5).
        
    # Role Key (editable by GM and Player now)
    if 'role_key' in data:
        new_role = data['role_key']
        if new_role != character.role_key:
            character.role_key = new_role
            character.role_specific_lifepath = {} # Clear on role change

    shared, faction_types, faction_feelings, roles_data = load_lifepath_data()

    # Shared Lifepath Fields
    shared_fields_map = {
        'cultural_origin': 'culturalOrigin', 'personality': 'personality', 'clothing_style': 'clothingStyle',
        'hairstyle': 'hairstyle', 'affectation': 'affectation', 'value_most': 'valueMost',
        'feelings_about_people': 'feelingsAboutPeople', 'most_valued_person': 'mostValuedPerson',
        'most_valued_possession': 'mostValuedPossession', 'family_background': 'familyBackground',
        'childhood_environment': 'childhoodEnvironment', 'family_crisis': 'familyCrisis', 'life_goals': 'lifeGoals'
    }
    for db_field, json_key in shared_fields_map.items():
        if db_field in data:
            val = data[db_field]
            if val is not None and not is_valid_option(val, shared.get(json_key)):
                return jsonify({'error': f'Invalid value for {db_field}'}), 400
            setattr(character, db_field, val)

    # Faction Fields
    if 'friend_faction_type' in data:
        val = data['friend_faction_type']
        if val is not None and not is_valid_option(val, faction_types): return jsonify({'error': 'Invalid friend faction type'}), 400
        character.friend_faction_type = val
    if 'friend_faction_name' in data:
        character.friend_faction_name = data['friend_faction_name']
    if 'friend_faction_feeling' in data:
        val = data['friend_faction_feeling']
        if val is not None and not is_valid_option(val, faction_feelings.get('friendFeelings')): return jsonify({'error': 'Invalid friend feeling'}), 400
        character.friend_faction_feeling = val

    if 'enemy_faction_type' in data:
        val = data['enemy_faction_type']
        if val is not None and not is_valid_option(val, faction_types): return jsonify({'error': 'Invalid enemy faction type'}), 400
        character.enemy_faction_type = val
    if 'enemy_faction_name' in data:
        character.enemy_faction_name = data['enemy_faction_name']
    if 'enemy_faction_feeling' in data:
        val = data['enemy_faction_feeling']
        if val is not None and not is_valid_option(val, faction_feelings.get('enemyFeelings')): return jsonify({'error': 'Invalid enemy feeling'}), 400
        character.enemy_faction_feeling = val

    # Role Specific Lifepath
    if 'role_specific_lifepath' in data:
        val = data['role_specific_lifepath']
        if character.role_key and val:
            role_info = roles_data.get(character.role_key)
            if role_info:
                # Validate keys
                for k, v in val.items():
                    if k in role_info.get('tables', {}) and is_valid_option(v, role_info['tables'][k]):
                        pass # valid
                    else:
                        return jsonify({'error': f'Invalid value for role specific field {k}'}), 400
        # Merge dict
        current_specific = dict(character.role_specific_lifepath or {})
        current_specific.update(data.get('role_specific_lifepath', {}))
        character.role_specific_lifepath = current_specific

    character.lifepath_complete = check_lifepath_complete(character, shared, roles_data)
    
    # GM only fields
    session = Session.query.get(session_id)
    if is_session_gm(g.current_user_id, session):
        if 'is_killer' in data:
            character.is_killer = data['is_killer']

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

    from routes.session_routes import _delete_characters
    _delete_characters([character.id])
    db.session.commit()

    return jsonify({'message': 'Character deleted successfully'}), 200
