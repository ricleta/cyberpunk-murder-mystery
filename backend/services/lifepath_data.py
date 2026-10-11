import json
import os

from generation.secret_templates import get_base_data_dir

_REQUIRED_FILES = ('lifepathShared.json', 'factionTypes.json', 'factionFeelings.json', 'rolesLifepath.json', 'roles.json')
_cache = {}


def _read(name):
    with open(os.path.join(get_base_data_dir(), name), 'r', encoding='utf-8') as f:
        return json.load(f)


def load_lifepath_data(reload=False):
    """(shared, faction_types, faction_feelings, roles_lifepath), read once from
    backend/data and cached. Raises if a file is missing: the app calls this at
    startup so a bad deploy fails loudly instead of rejecting every Lifepath PATCH."""
    if reload or 'lifepath' not in _cache:
        for name in _REQUIRED_FILES:
            path = os.path.join(get_base_data_dir(), name)
            if not os.path.isfile(path):
                raise RuntimeError(f"Required data file is missing: {path} (run copy_data.py from backend/)")
        _cache['lifepath'] = (_read('lifepathShared.json'), _read('factionTypes.json'),
                              _read('factionFeelings.json'), _read('rolesLifepath.json'))
        _cache['role_keys'] = frozenset(r['key'] for r in _read('roles.json'))
    return _cache['lifepath']


def get_role_keys():
    """Valid role keys (the keys of roles.json)."""
    load_lifepath_data()
    return _cache['role_keys']


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
