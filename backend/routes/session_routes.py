from flask import Blueprint, request, jsonify, g
import random
import string
from extensions import db
from models import Session, SessionMember, User, Character, Relationship, Secret, SecretKnowledge, Objective
from auth import require_auth, is_session_gm

def _delete_characters(char_ids):
    """Delete characters plus every row that references them (FK-safe order)."""
    if not char_ids:
        return
    secret_ids = [s.id for s in Secret.query.filter(
        (Secret.subject_character_id.in_(char_ids)) | (Secret.related_character_id.in_(char_ids))).all()]
    rel_ids = [r.id for r in Relationship.query.filter(
        (Relationship.source_character_id.in_(char_ids)) | (Relationship.target_character_id.in_(char_ids))).all()]
    # Secrets tied to relationships being removed must go too
    if rel_ids:
        secret_ids += [s.id for s in Secret.query.filter(Secret.related_relationship_id.in_(rel_ids)).all()]
    secret_ids = list(set(secret_ids))
    # Objectives: the removed characters' own objectives are deleted. Objectives that other
    # characters keep only lose their references to removed rows (their text is a snapshot).
    own_objective_ids = [o.id for o in Objective.query.filter(Objective.character_id.in_(char_ids)).all()]
    if own_objective_ids:
        Objective.query.filter(Objective.linked_objective_id.in_(own_objective_ids)).update(
            {Objective.linked_objective_id: None, Objective.link_type: None}, synchronize_session=False)
    Objective.query.filter(Objective.related_character_id.in_(char_ids)).update(
        {Objective.related_character_id: None}, synchronize_session=False)
    if secret_ids:
        Objective.query.filter(Objective.related_secret_id.in_(secret_ids)).update(
            {Objective.related_secret_id: None}, synchronize_session=False)
    if rel_ids:
        Objective.query.filter(Objective.related_relationship_id.in_(rel_ids)).update(
            {Objective.related_relationship_id: None}, synchronize_session=False)
    Objective.query.filter(Objective.character_id.in_(char_ids)).delete(synchronize_session=False)
    if secret_ids:
        SecretKnowledge.query.filter(SecretKnowledge.secret_id.in_(secret_ids)).delete(synchronize_session=False)
    SecretKnowledge.query.filter(SecretKnowledge.knower_character_id.in_(char_ids)).delete(synchronize_session=False)
    if secret_ids:
        Secret.query.filter(Secret.id.in_(secret_ids)).delete(synchronize_session=False)
    if rel_ids:
        Relationship.query.filter(Relationship.id.in_(rel_ids)).delete(synchronize_session=False)
    Character.query.filter(Character.id.in_(char_ids)).delete(synchronize_session=False)

def _clear_generated(session_id, relationships=False):
    """Delete a session's generated content in FK-safe order:
    objectives -> secret knowledge -> secrets (-> relationships). Does not commit.
    Returns True if anything was deleted."""
    deleted = Objective.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    secret_ids = [s.id for s in Secret.query.filter_by(session_id=session_id).all()]
    if secret_ids:
        SecretKnowledge.query.filter(SecretKnowledge.secret_id.in_(secret_ids)).delete(synchronize_session=False)
        deleted += Secret.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    if relationships:
        deleted += Relationship.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    return bool(deleted)

def _valid_points(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3

def serialize_objective(o, for_gm, char_names=None):
    """Players get only what is on their sheet. gm_hint, link info and the secret /
    relationship references are GM-only."""
    data = {
        'id': o.id,
        'text': o.text,
        'points': o.points,
        'kind': o.kind
    }
    if o.related_character_id:
        data['related_character_id'] = o.related_character_id
    if for_gm:
        char_names = char_names or {}
        data.update({
            'character_id': o.character_id,
            'character_name': char_names.get(o.character_id),
            'template_id': o.template_id,
            'gm_hint': o.gm_hint,
            'linked_objective_id': o.linked_objective_id,
            'link_type': o.link_type,
            'related_character_id': o.related_character_id,
            'related_secret_id': o.related_secret_id,
            'related_relationship_id': o.related_relationship_id,
            'subject_name': char_names.get(o.related_character_id) if o.related_character_id else None,
            'sort_order': o.sort_order
        })
    return data

def get_character_objectives(character, for_gm):
    rows = Objective.query.filter_by(character_id=character.id).order_by(Objective.sort_order, Objective.created_at).all()
    char_names = None
    if for_gm:
        char_names = {c.id: c.display_name for c in Character.query.filter_by(session_id=character.session_id).all()}
    return [serialize_objective(o, for_gm, char_names) for o in rows]

bp = Blueprint('sessions', __name__)

def generate_join_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@bp.route('', methods=['POST'])
@require_auth
def create_session():
    data = request.get_json() or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'Session name is required'}), 400

    # Generate unique join code
    while True:
        code = generate_join_code()
        if not Session.query.filter_by(join_code=code).first():
            break

    auto_relationships = data.get('auto_relationships', True)
    
    new_session = Session(
        gm_id=g.current_user_id,
        name=name,
        join_code=code,
        auto_relationships=auto_relationships
    )
    db.session.add(new_session)
    db.session.flush() # To get the session id if needed, though it's generated by default

    # GM is also a member
    gm_member = SessionMember(session_id=new_session.id, user_id=g.current_user_id)
    db.session.add(gm_member)
    db.session.commit()

    return jsonify({
        'id': new_session.id,
        'name': new_session.name,
        'join_code': new_session.join_code,
        'gm_id': new_session.gm_id,
        'status': new_session.status
    }), 201

@bp.route('/mine', methods=['GET'])
@require_auth
def get_my_sessions():
    memberships = SessionMember.query.filter_by(user_id=g.current_user_id).all()
    session_ids = [m.session_id for m in memberships]
    
    sessions = Session.query.filter(Session.id.in_(session_ids)).all()
    
    result = []
    for s in sessions:
        result.append({
            'id': s.id,
            'name': s.name,
            'join_code': s.join_code,
            'is_gm': s.gm_id == g.current_user_id,
            'status': s.status
        })
        
    return jsonify({'sessions': result}), 200

@bp.route('/<session_id>', methods=['GET'])
@require_auth
def get_session(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    member = SessionMember.query.filter_by(session_id=session_id, user_id=g.current_user_id).first()
    if not member and session.gm_id != g.current_user_id:
        return jsonify({'error': 'Not authorized to view this session'}), 403

    return jsonify({
        'id': session.id,
        'name': session.name,
        'join_code': session.join_code,
        'gm_id': session.gm_id,
        'status': session.status
    }), 200

@bp.route('/join', methods=['POST'])
@require_auth
def join_session():
    data = request.get_json() or {}
    join_code = data.get('join_code')
    if not join_code:
        return jsonify({'error': 'Join code is required'}), 400

    session = Session.query.filter_by(join_code=join_code.upper()).first()
    if not session:
        return jsonify({'error': 'Invalid join code'}), 404

    # Check if already a member
    existing_member = SessionMember.query.filter_by(session_id=session.id, user_id=g.current_user_id).first()
    if existing_member:
        return jsonify({'message': 'Already a member', 'session_id': session.id}), 200

    new_member = SessionMember(session_id=session.id, user_id=g.current_user_id)
    db.session.add(new_member)
    db.session.commit()

    return jsonify({
        'message': 'Successfully joined session',
        'session_id': session.id
    }), 200

@bp.route('/<session_id>/members', methods=['GET'])
@require_auth
def get_session_members(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    # Check authorization
    member = SessionMember.query.filter_by(session_id=session_id, user_id=g.current_user_id).first()
    if not member and session.gm_id != g.current_user_id:
        return jsonify({'error': 'Not authorized'}), 403

    members = db.session.query(SessionMember, User).join(User, SessionMember.user_id == User.id).filter(SessionMember.session_id == session_id).all()
    
    result = []
    for sm, u in members:
        result.append({
            'user_id': u.id,
            'display_name': u.username,
            'joined_at': sm.joined_at.isoformat()
        })

    return jsonify({'members': result}), 200

@bp.route('/<session_id>/members/<user_id>', methods=['DELETE'])
@require_auth
def remove_member(session_id, user_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    # Only GM can remove others. User can remove themselves.
    if user_id != g.current_user_id and session.gm_id != g.current_user_id:
        return jsonify({'error': 'Not authorized to remove this member'}), 403
        
    # Cannot remove GM (maybe? Let's allow self removal if they are not GM, or handle GM transfer later. For now, GM cannot be removed easily unless we close session, but let's just prevent GM from removing themselves as a regular member)
    if user_id == session.gm_id:
        return jsonify({'error': 'Cannot remove the GM from the session'}), 400

    member = SessionMember.query.filter_by(session_id=session_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'Member not found in session'}), 404

    chars = Character.query.filter_by(session_id=session_id, player_id=user_id).all()
    _delete_characters([c.id for c in chars])
    db.session.delete(member)
    db.session.commit()

    return jsonify({'message': 'Member removed successfully'}), 200

@bp.route('/<session_id>', methods=['DELETE'])
@require_auth
def delete_session(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if session.gm_id != g.current_user_id:
        return jsonify({'error': 'Only the GM can delete the session'}), 403

    _delete_characters([c.id for c in Character.query.filter_by(session_id=session_id).all()])
    Objective.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    Secret.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    Relationship.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    SessionMember.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    db.session.delete(session)
    db.session.commit()
    return jsonify({'message': 'Session deleted'}), 200

@bp.route('/<session_id>/lifepath-status', methods=['GET'])
@require_auth
def get_lifepath_status(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can view lifepath status'}), 403

    from models import Character
    members = db.session.query(SessionMember, User).join(User, SessionMember.user_id == User.id).filter(SessionMember.session_id == session_id).all()
    characters = Character.query.filter_by(session_id=session_id).all()
    char_map = {c.player_id: c for c in characters}

    all_complete = True
    result_members = []
    
    for sm, u in members:
        # GM doesn't need a character to be complete usually?
        # The prompt says "every session member having completed all required Lifepath fields". Let's assume GM is included if they have a character, or maybe GM is excluded? 
        # Usually GM doesn't play a character in the same way, but let's just list everyone.
        # Actually, let's just show status for everyone who has joined.
        char = char_map.get(u.id)
        if char:
            is_complete = char.lifepath_complete
            result_members.append({
                'character_id': char.id,
                'player_display_name': u.username,
                'role_key': char.role_key,
                'lifepath_complete': is_complete
            })
            if not is_complete:
                all_complete = False
        else:
            # If a member hasn't even created a character yet, they are definitely not complete
            # Wait, does the GM need to make a character? I'll assume they do if they want to play, or if they are just the GM maybe they shouldn't block.
            # Let's skip GM from completion blocking if they don't have a character, but if they do, track it.
            if u.id != session.gm_id:
                result_members.append({
                    'character_id': None,
                    'player_display_name': u.username,
                    'role_key': None,
                    'lifepath_complete': False
                })
                all_complete = False

    return jsonify({
        'allComplete': all_complete,
        'members': result_members
    }), 200

@bp.route('/<session_id>/generate-relationships', methods=['POST'])
@require_auth
def generate_relationships(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can generate relationships'}), 403

    from generation.relationships import _generate_relationships
    
    try:
        # Secrets and objectives reference relationships and are derived from them, so
        # regenerating relationships invalidates them. Clear them first (FK-safe order).
        if _clear_generated(session_id):
            db.session.commit()
        rels = _generate_relationships(session)
        result = []
        for r in rels:
            result.append({
                'id': r.id,
                'source_character_id': r.source_character_id,
                'target_character_id': r.target_character_id,
                'rel_type': r.rel_type,
                'description_source': r.description_source,
                'description_target': r.description_target,
                'is_symmetric': r.is_symmetric
            })
        return jsonify({'relationships': result}), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        db.session.rollback()
        return jsonify({'error': 'Internal server error: ' + traceback.format_exc()}), 500

@bp.route('/<session_id>/relationships', methods=['GET'])
@require_auth
def get_relationships(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    is_gm = is_session_gm(g.current_user_id, session)
    from models import Character, Relationship
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
                
        result.append({
            'id': r.id,
            'source_character_id': r.source_character_id,
            'target_character_id': r.target_character_id,
            'rel_type': r.rel_type,
            'description_source': r.description_source,
            'description_target': r.description_target,
            'is_symmetric': r.is_symmetric
        })

    return jsonify({'relationships': result}), 200

@bp.route('/<session_id>/generate-secrets', methods=['POST'])
@require_auth
def generate_secrets(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can generate secrets'}), 403

    from generation.secrets import _generate_secrets, verify_secrets
    from models import Secret

    existing = Secret.query.filter_by(session_id=session_id).first()
    if existing:
        return jsonify({'error': 'Secrets already exist for this session'}), 409

    try:
        _generate_secrets(session)
        verify_secrets(session)
        return jsonify({'message': 'Secrets generated successfully'}), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500

@bp.route('/<session_id>/secrets', methods=['GET'])
@require_auth
def get_secrets(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    from models import Secret, SecretKnowledge, Character
    
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
            result.append({
                'id': s.id,
                'template_id': s.template_id,
                'subject_character_id': s.subject_character_id,
                'subject_name': char_map.get(s.subject_character_id),
                'kind': s.kind,
                'category': s.category,
                'severity': s.severity,
                'is_killer_core': s.is_killer_core,
                'is_killer_trail': s.is_killer_trail,
                'text_subject': s.text_subject,
                'text_knower': s.text_knower,
                'knowers': [{'knower_character_id': k.knower_character_id, 'knower_name': char_map.get(k.knower_character_id)} for k in sk_list]
            })
            continue
            
        # For players
        if not my_char:
            continue
            
        # Is this my secret?
        if s.subject_character_id == my_char.id:
            result.append({
                'id': s.id,
                'template_id': s.template_id,
                'subject_character_id': s.subject_character_id,
                'subject_name': char_map.get(s.subject_character_id),
                'kind': s.kind,
                'category': s.category,
                'severity': s.severity,
                'is_killer_core': s.is_killer_core,
                'is_killer_trail': s.is_killer_trail,
                'text_subject': s.text_subject
                # Not returning knowers or text_knower to the subject
            })
            continue
            
        # Do I know this secret?
        my_knowledge = [k for k in knowledge if k.secret_id == s.id and k.knower_character_id == my_char.id]
        if my_knowledge:
            result.append({
                'id': s.id,
                'template_id': s.template_id,
                'subject_character_id': s.subject_character_id,
                'subject_name': char_map.get(s.subject_character_id),
                'kind': s.kind,
                'category': s.category,
                'severity': s.severity,
                'text_knower': s.text_knower
                # Not returning is_killer_core/trail, text_subject, or other knowers
            })

    return jsonify({'secrets': result}), 200

@bp.route('/<session_id>/secrets/report', methods=['GET'])
@require_auth
def get_secrets_report(session_id):
    from flask import current_app
    if not current_app.config.get('DEBUG'):
        return jsonify({'error': 'Not found'}), 404

    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can view secrets report'}), 403

    from generation.secrets import get_session_report
    from generation.secret_templates import load_all_secret_templates
    characters = Character.query.filter_by(session_id=session_id).all()
    cached = get_session_report(session_id)

    if cached:
        char_reports = {}
        for c in characters:
            char_reports[c.id] = {
                'character_id': c.id,
                'display_name': c.display_name,
                'role_key': c.role_key,
                'secrets': []
            }
        for entry in cached.get('characters', []):
            cid = entry['character_id']
            if cid in char_reports:
                char_reports[cid]['secrets'].append({
                    'kind': entry['kind'],
                    'template_id': entry['template_id'],
                    'category': entry['category'],
                    'pool': entry['pool'],
                    'is_relaxed': entry['is_relaxed'],
                    'final_weight': entry['selection_weight']
                })
        return jsonify({'characters': list(char_reports.values()), 'relaxed': cached.get('relaxed', [])}), 200

    # Fallback if cache is absent
    pools = load_all_secret_templates()
    secrets = Secret.query.filter_by(session_id=session_id).all()
    char_reports = {}
    for c in characters:
        char_reports[c.id] = {
            'character_id': c.id,
            'display_name': c.display_name,
            'role_key': c.role_key,
            'secrets': []
        }
    for s in secrets:
        cid = s.subject_character_id
        if cid in char_reports:
            pool = pools['template_pool_source'].get(s.template_id, 'universal')
            char_reports[cid]['secrets'].append({
                'kind': s.kind,
                'template_id': s.template_id,
                'category': s.category,
                'pool': pool,
                'is_relaxed': False,
                'final_weight': 1.0
            })
    return jsonify({'characters': list(char_reports.values()), 'relaxed': []}), 200

# --- Phase 5: Objectives ---

def _objective_editor_role(session, character):
    """'gm', 'owner', or None for the current user against one character's objectives."""
    if is_session_gm(g.current_user_id, session):
        return 'gm'
    if character and character.player_id == g.current_user_id:
        return 'owner'
    return None

@bp.route('/<session_id>/generate-objectives', methods=['POST'])
@require_auth
def generate_objectives(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can generate objectives'}), 403

    from generation.objectives import _generate_objectives, verify_objectives

    existing = Objective.query.filter_by(session_id=session_id).first()
    if existing:
        return jsonify({'error': 'Objectives already exist for this session'}), 409

    try:
        _generate_objectives(session)
        verify_objectives(session)
        return jsonify({'message': 'Objectives generated successfully'}), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500

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

    data = request.get_json() or {}
    character = Character.query.get(data.get('character_id')) if data.get('character_id') else None
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404

    role = _objective_editor_role(session, character)
    if not role:
        return jsonify({'error': 'Not authorized to add objectives to this character'}), 403

    text = data.get('text')
    if not isinstance(text, str) or not text.strip():
        return jsonify({'error': 'Objective text is required'}), 400
    if not _valid_points(data.get('points')):
        return jsonify({'error': 'points must be an integer from 1 to 3'}), 400
    if 'gm_hint' in data and role != 'gm':
        return jsonify({'error': 'Only GM can set gm_hint'}), 403

    last = Objective.query.filter_by(character_id=character.id).order_by(Objective.sort_order.desc()).first()
    objective = Objective(
        session_id=session_id,
        character_id=character.id,
        kind='manual',
        points=data['points'],
        text=text.strip(),
        gm_hint=data.get('gm_hint') if role == 'gm' else None,
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

    data = request.get_json() or {}
    if 'gm_hint' in data and role != 'gm':
        return jsonify({'error': 'Only GM can edit gm_hint'}), 403
    if 'text' in data and (not isinstance(data['text'], str) or not data['text'].strip()):
        return jsonify({'error': 'Objective text cannot be empty'}), 400
    if 'points' in data and not _valid_points(data['points']):
        return jsonify({'error': 'points must be an integer from 1 to 3'}), 400

    # Manual edits are free-form: only the 1-3 bound is enforced, budget rules are not re-run.
    if 'text' in data:
        objective.text = data['text'].strip()
    if 'points' in data:
        objective.points = data['points']
    if 'gm_hint' in data:
        objective.gm_hint = data['gm_hint']
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

@bp.route('/<session_id>/objectives/report', methods=['GET'])
@require_auth
def get_objectives_report(session_id):
    from flask import current_app
    if not current_app.config.get('DEBUG'):
        return jsonify({'error': 'Not found'}), 404

    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can view objectives report'}), 403

    from generation.objectives import get_objectives_report as get_cached_report
    from generation.objective_templates import load_all_objective_templates
    characters = Character.query.filter_by(session_id=session_id).all()
    char_reports = {}
    for c in characters:
        char_reports[c.id] = {
            'character_id': c.id,
            'display_name': c.display_name,
            'role_key': c.role_key,
            'is_killer': c.is_killer,
            'objectives': [],
            'point_adjustments': [],
            'linked_pairs': []
        }

    cached = get_cached_report(session_id)
    if cached:
        for entry in cached.get('objectives', []):
            cid = entry['character_id']
            if cid in char_reports:
                char_reports[cid]['objectives'].append({
                    'kind': entry['kind'],
                    'template_id': entry['template_id'],
                    'pool': entry['pool'],
                    'source': entry['source'],
                    'relaxed_step': entry['relaxed_step'],
                    'final_weight': entry['selection_weight'],
                    'points_initial': entry['points_initial'],
                    'points': entry['points']
                })
        for adj in cached.get('adjustments', []):
            if adj['character_id'] in char_reports:
                char_reports[adj['character_id']]['point_adjustments'].append({
                    'kind': adj['kind'], 'before': adj['before'], 'after': adj['after'], 'reason': adj['reason']
                })
        for pair in cached.get('linked_pairs', []):
            for cid, other in zip(pair['character_ids'], reversed(pair['character_ids'])):
                if cid in char_reports:
                    char_reports[cid]['linked_pairs'].append({
                        'link_type': pair['link_type'], 'kind': pair['kind'], 'with_character_id': other
                    })
        return jsonify({
            'characters': list(char_reports.values()),
            'relaxed': cached.get('relaxed', []),
            'fallbacks': cached.get('fallbacks', []),
            'linked_pairs': cached.get('linked_pairs', []),
            'scapegoat': cached.get('scapegoat'),
            'retries': cached.get('retries', 0),
            'failed_attempts': cached.get('failed_attempts', [])
        }), 200

    # Fallback if cache is absent (e.g. after a restart): rebuild what the rows still tell us
    pools = load_all_objective_templates()
    rows = Objective.query.filter_by(session_id=session_id).order_by(Objective.sort_order).all()
    by_id = {o.id: o for o in rows}
    linked_pairs = []
    scapegoat = None
    for o in rows:
        if o.character_id not in char_reports:
            continue
        char_reports[o.character_id]['objectives'].append({
            'kind': o.kind,
            'template_id': o.template_id,
            'pool': pools['template_pool_source'].get(o.template_id, 'manual'),
            'source': None,
            'relaxed_step': None,
            'final_weight': 1.0,
            'points_initial': o.points,
            'points': o.points
        })
        partner = by_id.get(o.linked_objective_id)
        if partner:
            char_reports[o.character_id]['linked_pairs'].append({
                'link_type': o.link_type, 'kind': o.kind, 'with_character_id': partner.character_id
            })
            if o.id < partner.id:
                linked_pairs.append({'link_type': o.link_type, 'kind': o.kind,
                                     'objective_ids': [o.id, partner.id],
                                     'character_ids': [o.character_id, partner.character_id]})
        if o.kind == 'frame':
            scapegoat = {'killer_id': o.character_id, 'chosen_character_id': o.related_character_id, 'candidates': []}
    return jsonify({
        'characters': list(char_reports.values()),
        'relaxed': [],
        'fallbacks': [],
        'linked_pairs': linked_pairs,
        'scapegoat': scapegoat,
        'retries': 0,
        'failed_attempts': []
    }), 200

# --- Phase 5: single Generate pipeline (relationships -> secrets -> objectives) ---

@bp.route('/<session_id>/generate', methods=['POST'])
@require_auth
def generate_all(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can generate the game'}), 403

    status = session.status or 'lobby'
    if status not in ('lobby', 'generated'):
        return jsonify({'error': f"Cannot generate while the session is '{status}'"}), 409

    regenerate = request.args.get('regenerate', '').lower() in ('true', '1')
    has_existing = bool(
        Relationship.query.filter_by(session_id=session_id).first()
        or Secret.query.filter_by(session_id=session_id).first()
        or Objective.query.filter_by(session_id=session_id).first()
    )
    if has_existing and not regenerate:
        return jsonify({'error': 'Generated content already exists for this session. Use regenerate=true to replace it.'}), 409

    # Gating: every player has a character and every lifepath is complete
    characters = Character.query.filter_by(session_id=session_id).all()
    char_player_ids = {c.player_id for c in characters}
    for member in SessionMember.query.filter_by(session_id=session_id).all():
        if member.user_id != session.gm_id and member.user_id not in char_player_ids:
            return jsonify({'error': 'Every player must create a character before generation.'}), 400
    if [c for c in characters if not c.lifepath_complete]:
        return jsonify({'error': 'Not all characters have completed their lifepath.'}), 400

    from generation.relationships import _generate_relationships, verify_session_graph
    from generation.secrets import _generate_secrets, verify_secrets, _secrets_report_cache
    from generation.objectives import _generate_objectives, verify_objectives, discard_objectives_report

    # One transaction: nothing below commits until all three stages have verified.
    # An existing is_killer assignment is kept; an auto-assigned one rolls back on failure.
    try:
        if has_existing:
            _clear_generated(session_id, relationships=True)

        relationships = _generate_relationships(session, commit=False)
        verify_session_graph(session)

        _generate_secrets(session, commit=False)
        verify_secrets(session)

        objectives = _generate_objectives(session, commit=False)
        verify_objectives(session)

        session.status = 'generated'
        db.session.commit()
        return jsonify({
            'message': 'Game generated successfully',
            'status': session.status,
            'relationships': len(relationships),
            'secrets': Secret.query.filter_by(session_id=session_id).count(),
            'objectives': len(objectives)
        }), 200
    except ValueError as e:
        db.session.rollback()
        _secrets_report_cache.pop(session_id, None)
        discard_objectives_report(session_id)
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        _secrets_report_cache.pop(session_id, None)
        discard_objectives_report(session_id)
        return jsonify({'error': 'Internal server error'}), 500
