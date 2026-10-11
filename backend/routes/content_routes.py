from flask import Blueprint, request, jsonify, g
from extensions import db
from models import Session, Character, Relationship, Secret, SecretKnowledge, Objective
from auth import require_auth, is_session_gm
from services import validation
from services.serializers import (
    serialize_objective, serialize_relationship,
    serialize_secret_for_gm, serialize_secret_for_subject, serialize_secret_for_knower,
)

bp = Blueprint('content', __name__)

def _valid_points(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3

@bp.route('/<session_id>/relationships', methods=['GET'])
@require_auth
def get_relationships(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    is_gm = is_session_gm(g.current_user_id, session)
    my_char = Character.query.filter_by(session_id=session_id, player_id=g.current_user_id).first()

    rels = Relationship.query.filter_by(session_id=session_id).all()

    result = []
    for r in rels:
        # Filter if not GM
        if not is_gm:
            if not my_char:
                continue
            if r.source_character_id != my_char.id and r.target_character_id != my_char.id:
                continue

        result.append(serialize_relationship(r))

    return jsonify({'relationships': result}), 200

@bp.route('/<session_id>/secrets', methods=['GET'])
@require_auth
def get_secrets(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    is_gm = is_session_gm(g.current_user_id, session)
    my_char = Character.query.filter_by(session_id=session_id, player_id=g.current_user_id).first()

    secrets = Secret.query.filter_by(session_id=session_id).all()
    knowledge = SecretKnowledge.query.join(Secret).filter(Secret.session_id == session_id).all()

    characters = Character.query.filter_by(session_id=session_id).all()
    char_map = {c.id: c.display_name for c in characters}

    result = []

    for s in secrets:
        # GM sees everything
        if is_gm:
            sk_list = [k for k in knowledge if k.secret_id == s.id]
            result.append(serialize_secret_for_gm(s, char_map, sk_list))
            continue

        # For players
        if not my_char:
            continue

        # Is this my secret?
        if s.subject_character_id == my_char.id:
            result.append(serialize_secret_for_subject(s, char_map))
            continue

        # Do I know this secret?
        if any(k.secret_id == s.id and k.knower_character_id == my_char.id for k in knowledge):
            result.append(serialize_secret_for_knower(s, char_map))

    return jsonify({'secrets': result}), 200

# --- Phase 5: Objectives ---

def _objective_editor_role(session, character):
    """'gm', 'owner', or None for the current user against one character's objectives."""
    if is_session_gm(g.current_user_id, session):
        return 'gm'
    if character and character.player_id == g.current_user_id:
        return 'owner'
    return None

@bp.route('/<session_id>/objectives', methods=['GET'])
@require_auth
def get_objectives(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    is_gm = is_session_gm(g.current_user_id, session)
    query = Objective.query.filter_by(session_id=session_id)

    if is_gm:
        # Optional filter so the GM can open a single character's sheet
        character_id = request.args.get('character_id')
        if character_id:
            query = query.filter_by(character_id=character_id)
    else:
        # A player only ever gets their own character's objectives
        my_char = Character.query.filter_by(session_id=session_id, player_id=g.current_user_id).first()
        if not my_char:
            return jsonify({'objectives': []}), 200
        query = query.filter_by(character_id=my_char.id)

    rows = query.order_by(Objective.character_id, Objective.sort_order, Objective.created_at).all()
    char_names = None
    if is_gm:
        char_names = {c.id: c.display_name for c in Character.query.filter_by(session_id=session_id).all()}

    return jsonify({'objectives': [serialize_objective(o, is_gm, char_names) for o in rows]}), 200

@bp.route('/<session_id>/objectives', methods=['POST'])
@require_auth
def create_objective(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    data = validation.json_object()
    character_id = data.get('character_id')
    character = Character.query.get(character_id) if character_id and isinstance(character_id, str) else None
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    role = _objective_editor_role(session, character)
    if not role:
        return jsonify({'error': 'Not authorized to add objectives to this character'}), 403

    text = data.get('text')
    if not isinstance(text, str) or not text.strip():
        return jsonify({'error': 'Objective text is required'}), 400
    text = validation.objective_text(text)
    if not _valid_points(data.get('points')):
        return jsonify({'error': 'points must be an integer from 1 to 3'}), 400
    if 'gm_hint' in data and role != 'gm':
        return jsonify({'error': 'Only GM can set gm_hint'}), 403
    new_hint = validation.gm_hint(data['gm_hint']) if 'gm_hint' in data else None

    last = Objective.query.filter_by(character_id=character.id).order_by(Objective.sort_order.desc()).first()
    objective = Objective(
        session_id=session_id,
        character_id=character.id,
        kind='manual',
        points=data['points'],
        text=text,
        gm_hint=new_hint if role == 'gm' else None,
        sort_order=(last.sort_order or 0) + 1 if last else 0
    )
    db.session.add(objective)
    db.session.commit()

    return jsonify({'objective': serialize_objective(objective, role == 'gm')}), 201

@bp.route('/<session_id>/objectives/<objective_id>', methods=['PATCH'])
@require_auth
def update_objective(session_id, objective_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    objective = Objective.query.get(objective_id)
    if not objective or objective.session_id != session_id:
        return jsonify({'error': 'Objective not found'}), 404

    role = _objective_editor_role(session, Character.query.get(objective.character_id))
    if not role:
        return jsonify({'error': 'Not authorized to edit this objective'}), 403

    data = validation.json_object()
    if 'gm_hint' in data and role != 'gm':
        return jsonify({'error': 'Only GM can edit gm_hint'}), 403
    if 'text' in data and (not isinstance(data['text'], str) or not data['text'].strip()):
        return jsonify({'error': 'Objective text cannot be empty'}), 400
    if 'points' in data and not _valid_points(data['points']):
        return jsonify({'error': 'points must be an integer from 1 to 3'}), 400
    new_text = validation.objective_text(data['text']) if 'text' in data else None
    new_hint = validation.gm_hint(data['gm_hint']) if 'gm_hint' in data else None

    # Manual edits are free-form: only the 1-3 bound is enforced, budget rules are not re-run.
    if 'text' in data:
        objective.text = new_text
    if 'points' in data:
        objective.points = data['points']
    if 'gm_hint' in data:
        objective.gm_hint = new_hint
    db.session.commit()

    return jsonify({'objective': serialize_objective(objective, role == 'gm')}), 200

@bp.route('/<session_id>/objectives/<objective_id>', methods=['DELETE'])
@require_auth
def delete_objective(session_id, objective_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    objective = Objective.query.get(objective_id)
    if not objective or objective.session_id != session_id:
        return jsonify({'error': 'Objective not found'}), 404

    if not _objective_editor_role(session, Character.query.get(objective.character_id)):
        return jsonify({'error': 'Not authorized to delete this objective'}), 403

    # The partner objective survives, it just stops pointing at this one
    Objective.query.filter_by(linked_objective_id=objective.id).update(
        {Objective.linked_objective_id: None, Objective.link_type: None}, synchronize_session=False)
    db.session.delete(objective)
    db.session.commit()

    return jsonify({'message': 'Objective deleted'}), 200
