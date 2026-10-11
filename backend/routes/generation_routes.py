import functools
import random
import secrets as stdlib_secrets  # not generation.secrets
from datetime import datetime

from flask import Blueprint, request, jsonify, g, current_app
from extensions import db
from models import Session, SessionMember, Character, Relationship, Secret, Objective
from auth import require_auth, is_session_gm
from services.cleanup import clear_generated, clear_generation_metadata
from services.errors import ApiError
from services.serializers import serialize_relationship
from generation.relationships import _generate_relationships, verify_session_graph
from generation.secrets import _generate_secrets, verify_secrets, get_session_report
from generation.report import store_report
from generation.secret_templates import load_all_secret_templates
from generation.objectives import (
    _generate_objectives, verify_objectives,
    get_objectives_report as get_cached_report,
)
from generation.objective_templates import load_all_objective_templates

bp = Blueprint('generation', __name__)


def _gated(allowed):
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not allowed():
                return jsonify({'error': 'Not found'}), 404
            return f(*args, **kwargs)
        return decorated
    return decorator


# The standalone generators skip the pipeline's single transaction, so they only exist in
# development and tests. The report endpoints expose generation internals: DEBUG only.
dev_only = _gated(lambda: current_app.debug or current_app.testing)
debug_only = _gated(lambda: current_app.debug)

@bp.route('/<session_id>/generate-relationships', methods=['POST'])
@require_auth
@dev_only
def generate_relationships(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can generate relationships'}), 403

    try:
        # Secrets and objectives reference relationships and are derived from them, so
        # regenerating relationships invalidates them. Clear them first (FK-safe order).
        if clear_generated(session_id):
            db.session.commit()
        rels = _generate_relationships(session)
        result = [serialize_relationship(r) for r in rels]
        return jsonify({'relationships': result}), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Generation failed for session %s', session_id)
        return jsonify({'error': 'Internal server error'}), 500

@bp.route('/<session_id>/generate-secrets', methods=['POST'])
@require_auth
@dev_only
def generate_secrets(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can generate secrets'}), 403

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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Generation failed for session %s', session_id)
        return jsonify({'error': 'Internal server error'}), 500

@bp.route('/<session_id>/secrets/report', methods=['GET'])
@require_auth
@debug_only
def get_secrets_report(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can view secrets report'}), 403

    characters = Character.query.filter_by(session_id=session_id).all()
    cached = get_session_report(session)

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

@bp.route('/<session_id>/generate-objectives', methods=['POST'])
@require_auth
@dev_only
def generate_objectives(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can generate objectives'}), 403

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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Generation failed for session %s', session_id)
        return jsonify({'error': 'Internal server error'}), 500

@bp.route('/<session_id>/objectives/report', methods=['GET'])
@require_auth
@debug_only
def get_objectives_report(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can view objectives report'}), 403

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

    cached = get_cached_report(session)
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

# --- Phase 5/6: single Generate pipeline (relationships -> secrets -> objectives) ---

def _requested_seed():
    """A client-chosen seed is honoured only in debug/testing; elsewhere it is ignored."""
    raw = request.args.get('seed')
    if raw is None or not (current_app.debug or current_app.testing):
        return None
    try:
        seed = int(raw)
    except ValueError:
        raise ApiError('seed must be an integer')
    if not 0 <= seed < 2 ** 32:
        raise ApiError('seed must be between 0 and 4294967295')
    return seed

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

    seed = _requested_seed()
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

    if seed is None:
        seed = stdlib_secrets.randbits(32)
    rng = random.Random(seed)  # one rng, passed through all three stages in order

    # One transaction: nothing below commits until all three stages have verified.
    # An existing is_killer assignment is kept; an auto-assigned one rolls back on failure.
    try:
        if has_existing:
            clear_generated(session_id, relationships=True)
        clear_generation_metadata(session)

        relationships = _generate_relationships(session, rng=rng, commit=False)
        verify_session_graph(session)

        _generate_secrets(session, rng=rng, commit=False)
        verify_secrets(session)

        objectives = _generate_objectives(session, rng=rng, commit=False)
        verify_objectives(session)

        store_report(session, 'seed', seed)
        session.generation_seed = seed
        session.generated_at = datetime.utcnow()
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
        return jsonify({'error': str(e)}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Generate pipeline failed for session %s', session_id)
        return jsonify({'error': 'Internal server error'}), 500

@bp.route('/<session_id>/reset', methods=['POST'])
@require_auth
def reset_session(session_id):
    """GM-only: back to a clean lobby. Deletes everything generated (including manual
    objective edits); keeps characters and their is_killer flags."""
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only GM can reset the game'}), 403

    if session.status in ('active', 'closed'):
        return jsonify({'error': f"Cannot reset while the session is '{session.status}'"}), 409

    try:
        counts = {
            'objectives': Objective.query.filter_by(session_id=session_id).count(),
            'secrets': Secret.query.filter_by(session_id=session_id).count(),
            'relationships': Relationship.query.filter_by(session_id=session_id).count(),
        }
        clear_generated(session_id, relationships=True)
        clear_generation_metadata(session)
        session.status = 'lobby'
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Reset failed for session %s', session_id)
        return jsonify({'error': 'Internal server error'}), 500

    return jsonify({'message': 'Game reset to a clean slate', 'deleted': counts}), 200
