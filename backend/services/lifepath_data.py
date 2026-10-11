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
