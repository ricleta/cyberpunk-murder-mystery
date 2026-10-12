"""Character export (backup) and import (restore). Nothing is stored server-side: files are
produced on request and read from the request body."""
from flask import Blueprint, jsonify, g, request

from auth import can_edit_character, can_view_character, is_session_gm, require_auth
from extensions import db
from models import Character, Session, SessionMember
from services.character_export import build_bundle, build_character_file
from services.character_import import import_as_new, import_into_existing, parse_import_request
from services.session_lock import ensure_unlocked

bp = Blueprint('transfer', __name__)


def _flag(name, default):
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes')


def _no_store(body, status=200):
    response = jsonify(body)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


# Registered before the <character_id> routes in the URL map, but werkzeug prefers the
# static segment anyway: /characters/export is never read as a character id.
@bp.route('/<session_id>/characters/export', methods=['GET'])
@require_auth
def export_all_characters(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if not is_session_gm(g.current_user_id, session):
        return jsonify({'error': 'Only the GM can export every character'}), 403
    characters = Character.query.filter_by(session_id=session_id).all()
    return _no_store(build_bundle(session, characters))


@bp.route('/<session_id>/characters/<character_id>/export', methods=['GET'])
@require_auth
def export_character(session_id, character_id):
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404
    if not can_view_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to export this character'}), 403
    return _no_store(build_character_file(character))


@bp.route('/<session_id>/characters/<character_id>/import', methods=['POST'])
@require_auth
def import_into_character(session_id, character_id):
    character = Character.query.get(character_id)
    if not character or character.session_id != session_id:
        return jsonify({'error': 'Character not found'}), 404
    if not can_edit_character(g.current_user_id, character):
        return jsonify({'error': 'Not authorized to edit this character'}), 403

    session = Session.query.get(session_id)
    file_data = parse_import_request()
    dry_run = _flag('dry_run', False)
    report = import_into_existing(character, session, is_session_gm(g.current_user_id, session), file_data,
                                  dry_run=dry_run, include_text=_flag('include_text', True))
    if not dry_run:
        db.session.commit()
    return _no_store(report)


@bp.route('/<session_id>/characters/import', methods=['POST'])
@require_auth
def import_new_character(session_id):
    session = Session.query.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    # Same membership rule as creating a character by hand
    member = SessionMember.query.filter_by(session_id=session_id, user_id=g.current_user_id).first()
    if not member and session.gm_id != g.current_user_id:
        return jsonify({'error': 'Not a member of this session'}), 403

    file_data = parse_import_request()
    ensure_unlocked(session, 'The game has been generated, so no new characters can be created. '
                             'Ask the GM to reset the game in the lobby first.')
    if Character.query.filter_by(session_id=session_id, player_id=g.current_user_id).first():
        return jsonify({'error': 'You already have a character in this session; import into it instead'}), 409

    dry_run = _flag('dry_run', False)
    try:
        report = import_as_new(session, g.current_user_id, is_session_gm(g.current_user_id, session), file_data,
                               dry_run=dry_run, include_text=_flag('include_text', True), db=db)
        if not dry_run:
            db.session.commit()
    except Exception:
        db.session.rollback()   # one transaction: nothing is left half-created
        raise
    return _no_store(report, 200 if dry_run else 201)
