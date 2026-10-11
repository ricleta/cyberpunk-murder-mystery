from flask import Blueprint, jsonify
from services.lifepath_data import load_lifepath_data

bp = Blueprint('lifepath', __name__)

@bp.route('/shared', methods=['GET'])
def get_shared_lifepath():
    shared, _, _, _ = load_lifepath_data()
    return jsonify(shared), 200

@bp.route('/factions', methods=['GET'])
def get_factions():
    _, faction_types, faction_feelings, _ = load_lifepath_data()
    return jsonify({
        'types': faction_types,
        'feelings': faction_feelings
    }), 200

@bp.route('/roles/<role_key>', methods=['GET'])
def get_role_lifepath(role_key):
    _, _, _, roles = load_lifepath_data()
    if role_key in roles:
        return jsonify(roles[role_key]), 200
    return jsonify({'error': 'Role not found'}), 404
