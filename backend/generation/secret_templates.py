import os
import json
import re
import random
import logging
from collections import defaultdict
import tracery
from tracery.modifiers import base_english

logger = logging.getLogger(__name__)

# Shared lifepath field mapping to lifepathShared.json keys
SHARED_LIFEPATH_MAP = {
    'cultural_origin': 'culturalOrigin',
    'personality': 'personality',
    'clothing_style': 'clothingStyle',
    'hairstyle': 'hairstyle',
    'affectation': 'affectation',
    'value_most': 'valueMost',
    'feelings_about_people': 'feelingsAboutPeople',
    'most_valued_person': 'mostValuedPerson',
    'most_valued_possession': 'mostValuedPossession',
    'family_background': 'familyBackground',
    'childhood_environment': 'childhoodEnvironment',
    'family_crisis': 'familyCrisis',
    'life_goals': 'lifeGoals'
}

class SessionContext:
    def __init__(self):
        self.use_count = defaultdict(int)
        self.relaxed = []  # list of dicts: {'character_id', 'kind', 'template_id', 'step'}
        self.report_entries = []  # list of dicts for §8 report

def get_base_data_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))

def load_all_secret_templates(data_dir=None):
    if not data_dir:
        data_dir = get_base_data_dir()
    secrets_dir = os.path.join(data_dir, 'secrets')
    roles_dir = os.path.join(secrets_dir, 'roles')

    with open(os.path.join(secrets_dir, 'selfSecretTemplates.json'), 'r', encoding='utf-8') as f:
        universal_self = json.load(f)
    with open(os.path.join(secrets_dir, 'lieSecretTemplates.json'), 'r', encoding='utf-8') as f:
        universal_lie = json.load(f)
    with open(os.path.join(secrets_dir, 'victimTieTemplates.json'), 'r', encoding='utf-8') as f:
        victim_ties = json.load(f)
    with open(os.path.join(secrets_dir, 'observationSecretTemplates.json'), 'r', encoding='utf-8') as f:
        observations = json.load(f)
    with open(os.path.join(secrets_dir, 'killerTrailTemplates.json'), 'r', encoding='utf-8') as f:
        killer_trails = json.load(f)

    killer_core_path = os.path.join(secrets_dir, 'killerCoreTemplates.json')
    if os.path.exists(killer_core_path):
        with open(killer_core_path, 'r', encoding='utf-8') as f:
            killer_cores = json.load(f)
    else:
        killer_cores = [
            {"id": "killer_core_1", "category": "murder_core", "severity": 3, "roles": [], "excludeRoles": [], "weight": 1.0, "templateSubject": "You killed #victimName# in #location#. There is no going back now.", "templateKnower": None},
            {"id": "killer_core_2", "category": "murder_core", "severity": 3, "roles": [], "excludeRoles": [], "weight": 1.0, "templateSubject": "You are directly responsible for #victimName#'s murder tonight in #location#.", "templateKnower": None},
            {"id": "killer_core_3", "category": "murder_core", "severity": 3, "roles": [], "excludeRoles": [], "weight": 1.0, "templateSubject": "You murdered #victimName# in #location# and must ensure you leave without being caught.", "templateKnower": None}
        ]

    roles_pool = {}
    if os.path.exists(roles_dir):
        for fname in os.listdir(roles_dir):
            if fname.endswith('.json'):
                role_key = fname[:-5]
                with open(os.path.join(roles_dir, fname), 'r', encoding='utf-8') as f:
                    content = json.load(f)
                    if isinstance(content, dict):
                        roles_pool[role_key] = {
                            "self": content.get("self", []),
                            "lie": content.get("lie", [])
                        }
                    elif isinstance(content, list):
                        roles_pool[role_key] = {
                            "self": [t for t in content if t.get("kind") == "self"],
                            "lie": [t for t in content if t.get("kind") == "lie"]
                        }

    # Build template registry
    template_registry = {}
    template_pool_source = {}

    def register(template_list, pool_name):
        for t in template_list:
            t_id = t["id"]
            template_registry[t_id] = t
            template_pool_source[t_id] = pool_name

    register(universal_self, "universal")
    register(universal_lie, "universal")
    register(victim_ties, "universal")
    register(observations, "observation")
    register(killer_trails, "trail")
    register(killer_cores, "core")

    for r_key, r_dict in roles_pool.items():
        register(r_dict.get("self", []), "role")
        register(r_dict.get("lie", []), "role")

    return {
        "universal_self": universal_self,
        "universal_lie": universal_lie,
        "victim_ties": victim_ties,
        "observations": observations,
        "killer_trails": killer_trails,
        "killer_cores": killer_cores,
        "roles_pool": roles_pool,
        "template_registry": template_registry,
        "template_pool_source": template_pool_source
    }

def get_valid_lifepath_options(data_dir=None):
    """Reads Phase 2 Lifepath JSON files directly and builds a mapping of valid values."""
    if not data_dir:
        data_dir = get_base_data_dir()

    with open(os.path.join(data_dir, 'lifepathShared.json'), 'r', encoding='utf-8') as f:
        shared = json.load(f)
    with open(os.path.join(data_dir, 'factionTypes.json'), 'r', encoding='utf-8') as f:
        f_types = json.load(f)
    with open(os.path.join(data_dir, 'factionFeelings.json'), 'r', encoding='utf-8') as f:
        f_feelings = json.load(f)
    with open(os.path.join(data_dir, 'rolesLifepath.json'), 'r', encoding='utf-8') as f:
        roles_lp = json.load(f)

    valid_options = {}  # field_name -> set of valid string representations (both id and text)
    synonym_map = {}    # field_name -> dict mapping id<->text

    for db_col, json_key in SHARED_LIFEPATH_MAP.items():
        table = shared.get(json_key, {})
        opts = table.get('options', [])
        valid_options[db_col] = set()
        synonym_map[db_col] = {}
        for opt in opts:
            oid = opt.get('id')
            otxt = opt.get('text')
            if oid:
                valid_options[db_col].add(oid)
            if otxt:
                valid_options[db_col].add(otxt)
            if oid and otxt:
                synonym_map[db_col][oid] = otxt
                synonym_map[db_col][otxt] = oid

    # Factions
    type_opts = f_types.get('options', [])
    for fld in ['friend_faction_type', 'enemy_faction_type']:
        valid_options[fld] = set()
        synonym_map[fld] = {}
        for opt in type_opts:
            oid = opt.get('id')
            otxt = opt.get('text')
            if oid: valid_options[fld].add(oid)
            if otxt: valid_options[fld].add(otxt)
            if oid and otxt:
                synonym_map[fld][oid] = otxt
                synonym_map[fld][otxt] = oid

    for fld, subkey in [('friend_faction_feeling', 'friendFeelings'), ('enemy_faction_feeling', 'enemyFeelings')]:
        opts = f_feelings.get(subkey, {}).get('options', [])
        valid_options[fld] = set()
        synonym_map[fld] = {}
        for opt in opts:
            oid = opt.get('id')
            otxt = opt.get('text')
            if oid: valid_options[fld].add(oid)
            if otxt: valid_options[fld].add(otxt)
            if oid and otxt:
                synonym_map[fld][oid] = otxt
                synonym_map[fld][otxt] = oid

    # Role specific lifepath tables
    for role_k, role_data in roles_lp.items():
        for tbl_k, tbl_val in role_data.get('tables', {}).items():
            opts = tbl_val.get('options', [])
            if tbl_k not in valid_options:
                valid_options[tbl_k] = set()
                synonym_map[tbl_k] = {}
            for opt in opts:
                oid = opt.get('id')
                otxt = opt.get('text')
                if oid: valid_options[tbl_k].add(oid)
                if otxt: valid_options[tbl_k].add(otxt)
                if oid and otxt:
                    synonym_map[tbl_k][oid] = otxt
                    synonym_map[tbl_k][otxt] = oid

    return valid_options, synonym_map

def load_template_validation_context(data_dir=None):
    """Lookups shared by every template validator: valid role keys, valid Lifepath
    option values per field, and the real Character column names."""
    if not data_dir:
        data_dir = get_base_data_dir()

    with open(os.path.join(data_dir, 'roles.json'), 'r', encoding='utf-8') as f:
        roles_data = json.load(f)
    valid_role_keys = {r['key'] for r in roles_data}

    valid_lifepath_options, _ = get_valid_lifepath_options(data_dir)

    # Real character lifepath fields
    from models import Character
    character_cols = {col.name for col in Character.__table__.columns}

    return valid_role_keys, valid_lifepath_options, character_cols

def validate_template_gates(t, file_name, valid_role_keys, valid_lifepath_options, character_cols):
    """Validates the gate fields shared by secret and objective templates:
    roles / excludeRoles and requiresLifepath / boostLifepath."""
    t_id = t.get("id")

    # Roles validation
    for r in t.get("roles", []):
        if r not in valid_role_keys:
            raise ValueError(f"Template '{t_id}' in {file_name} has invalid role_key in 'roles': '{r}'")
    for r in t.get("excludeRoles", []):
        if r not in valid_role_keys:
            raise ValueError(f"Template '{t_id}' in {file_name} has invalid role_key in 'excludeRoles': '{r}'")

    # Lifepath hooks validation
    req_lp = t.get("requiresLifepath", {}) or {}
    boost_lp = t.get("boostLifepath", {}) or {}

    for fld, allowed_vals in req_lp.items():
        if fld not in character_cols and fld not in valid_lifepath_options:
            raise ValueError(f"Template '{t_id}' in {file_name} has invalid Lifepath field in requiresLifepath: '{fld}'")
        if fld not in valid_lifepath_options:
            raise ValueError(f"Lifepath field '{fld}' in requiresLifepath of '{t_id}' has no options table in Lifepath data")
        if isinstance(allowed_vals, str):
            allowed_vals = [allowed_vals]
        for v in allowed_vals:
            if v not in valid_lifepath_options[fld]:
                raise ValueError(f"Template '{t_id}' requiresLifepath value '{v}' for field '{fld}' does not exist in Lifepath JSON tables")

    for fld, val_map in boost_lp.items():
        if fld not in character_cols and fld not in valid_lifepath_options:
            raise ValueError(f"Template '{t_id}' in {file_name} has invalid Lifepath field in boostLifepath: '{fld}'")
        if fld not in valid_lifepath_options:
            raise ValueError(f"Lifepath field '{fld}' in boostLifepath of '{t_id}' has no options table in Lifepath data")
        for v in val_map.keys():
            if v not in valid_lifepath_options[fld]:
                raise ValueError(f"Template '{t_id}' boostLifepath value '{v}' for field '{fld}' does not exist in Lifepath JSON tables")

def validate_secret_templates(data_dir=None):
    """
    Load-time validation: fail loudly at app startup.
    - Every roles / excludeRoles entry is a valid role_key.
    - Every field name in requiresLifepath / boostLifepath is a real Lifepath field on Character.
    - Every value in those maps is exactly a value present in the corresponding Lifepath table JSON.
    - No duplicate template id within or across pool files.
    - Every template renders cleanly against a dummy context.
    """
    if not data_dir:
        data_dir = get_base_data_dir()

    valid_role_keys, valid_lifepath_options, character_cols = load_template_validation_context(data_dir)

    pools = load_all_secret_templates(data_dir)
    seen_ids = set()

    pool_files_map = {
        "selfSecretTemplates.json": pools["universal_self"],
        "lieSecretTemplates.json": pools["universal_lie"],
        "victimTieTemplates.json": pools["victim_ties"],
        "observationSecretTemplates.json": pools["observations"],
        "killerTrailTemplates.json": pools["killer_trails"],
        "killerCoreTemplates.json": pools["killer_cores"]
    }
    for r_key, r_dict in pools["roles_pool"].items():
        pool_files_map[f"roles/{r_key}.json [self]"] = r_dict["self"]
        pool_files_map[f"roles/{r_key}.json [lie]"] = r_dict["lie"]

    dummy_context = {
        "subjectName": "DummySubject",
        "knowerName": "DummyKnower",
        "victimName": "DummyVictim",
        "victimTitle": "DummyVictimTitle",
        "location": "DummyLocation",
        "otherName": "DummyOther"
    }

    for file_name, t_list in pool_files_map.items():
        file_ids = set()
        for t in t_list:
            t_id = t.get("id")
            if not t_id:
                raise ValueError(f"Template missing 'id' in {file_name}: {t}")

            # 1. Uniqueness check across and within pools
            if t_id in file_ids:
                raise ValueError(f"Duplicate template id '{t_id}' within file {file_name}")
            if t_id in seen_ids:
                raise ValueError(f"Duplicate template id '{t_id}' found across multiple pool files (in {file_name})")
            file_ids.add(t_id)
            seen_ids.add(t_id)

            # 2-3. Roles and Lifepath hooks validation
            validate_template_gates(t, file_name, valid_role_keys, valid_lifepath_options, character_cols)

            # 4. Dummy render validation
            t_sub = t.get("templateSubject")
            t_kno = t.get("templateKnower")

            if t_sub:
                rendered_sub = render_grammar(t_sub, dummy_context)
                assert_clean_render(rendered_sub, t_id, "templateSubject")
            if t_kno:
                rendered_kno = render_grammar(t_kno, dummy_context)
                assert_clean_render(rendered_kno, t_id, "templateKnower")

    return True

def assert_clean_render(text, template_id, field_name):
    if '#' in text:
        unresolved = re.findall(r'#([^#]+)#', text)
        raise ValueError(f"Template '{template_id}' has unresolved placeholder(s) in {field_name}: {unresolved} (text: '{text}')")

def render_grammar(template_str, context):
    grammar_dict = {k: [v] if isinstance(v, str) else v for k, v in context.items()}
    grammar_dict["origin"] = template_str
    g = tracery.Grammar(grammar_dict)
    g.add_modifiers(base_english)
    return g.flatten("#origin#")

def resolve_template(template_str, template_id, subject, knower=None, victim_name=None, victim_title=None, location=None, other=None):
    """
    Build the Tracery grammar from a fully resolved context dict in Python before rendering.
    Required keys: subjectName, knowerName, victimName, victimTitle, location, otherName (only when secret has related_character_id).
    """
    if not template_str:
        return ""

    if '#otherName#' in template_str and not other:
        raise ValueError(f"Template '{template_id}' uses #otherName# but has no related_character_id")

    context = {
        "subjectName": subject.display_name if subject else "Someone",
        "knowerName": knower.display_name if knower else "Someone",
        "victimName": victim_name or "Director Kessler Voss",
        "victimTitle": victim_title or "Executive",
        "location": location or "Night City"
    }
    if other:
        context["otherName"] = other.display_name

    rendered = render_grammar(template_str, context)
    assert_clean_render(rendered, template_id, "rendered text")
    return rendered

def get_character_field_value(character, field_name):
    """Gets the value for a lifepath field from Character or role_specific_lifepath."""
    if hasattr(character, field_name):
        return getattr(character, field_name)
    if character.role_specific_lifepath and isinstance(character.role_specific_lifepath, dict):
        return character.role_specific_lifepath.get(field_name)
    return None

def lifepath_matches(requires_lifepath, character, synonym_map=None):
    if not requires_lifepath:
        return True
    for field_name, allowed_values in requires_lifepath.items():
        char_val = get_character_field_value(character, field_name)
        if not char_val:
            return False
        if isinstance(allowed_values, str):
            allowed_values = [allowed_values]
        # Match directly or via synonym
        matched = False
        field_synonyms = synonym_map.get(field_name, {}) if synonym_map else {}
        for allowed in allowed_values:
            if char_val == allowed or field_synonyms.get(char_val) == allowed or field_synonyms.get(allowed) == char_val:
                matched = True
                break
        if not matched:
            return False
    return True

def lifepath_boost(boost_lifepath, character, synonym_map=None):
    if not boost_lifepath:
        return 1.0
    multiplier = 1.0
    for field_name, val_map in boost_lifepath.items():
        char_val = get_character_field_value(character, field_name)
        if not char_val:
            continue
        field_synonyms = synonym_map.get(field_name, {}) if synonym_map else {}
        for target_val, mult in val_map.items():
            if char_val == target_val or field_synonyms.get(char_val) == target_val or field_synonyms.get(target_val) == char_val:
                multiplier *= mult
    return multiplier

def score_tags(character_tags: set[str], template_bias_tags: list[str]) -> float:
    """
    Score affinity based on role tags and template bias tags.
    Base weight 0.15 + 0.20 per overlapping tag, capped at 0.95.
    """
    base = 0.15
    overlap = character_tags.intersection(set(template_bias_tags))
    weight = base + len(overlap) * 0.20
    return min(weight, 0.95)

def weighted_choice(candidates, rng=None):
    """candidates: list of (template, weight)"""
    if not candidates:
        return None
    weights = [max(c[1], 1e-6) for c in candidates]
    templates = [c[0] for c in candidates]
    if rng:
        return rng.choices(templates, weights=weights, k=1)[0]
    return random.choices(templates, weights=weights, k=1)[0]

def calculate_candidate_weight(t, character, char_tags, ctx, synonym_map=None):
    affinity = score_tags(char_tags, t.get("biasTags", []))
    w = (0.05 + affinity) ** 2            # sharpen: low floor, squared
    w *= t.get("weight", 1.0)
    w *= lifepath_boost(t.get("boostLifepath"), character, synonym_map)
    w *= 0.25 ** ctx.use_count[t["id"]]   # soft anti-repeat across the whole session
    return w

def pick_template_strict(pool, character, char_tags, ctx, exclude_ids, exclude_categories, synonym_map=None):
    candidates = []
    for t in pool:
        if t["id"] in exclude_ids or t.get("category") in exclude_categories:
            continue
        if character.role_key in t.get("excludeRoles", []):
            continue
        if t.get("roles") and character.role_key not in t["roles"]:
            continue
        if not lifepath_matches(t.get("requiresLifepath"), character, synonym_map):
            continue
        w = calculate_candidate_weight(t, character, char_tags, ctx, synonym_map)
        candidates.append((t, w))
    return candidates

def pick_template_relaxed(pool, character, char_tags, ctx, kind, exclude_ids, synonym_map=None, rng=None):
    """
    Relaxation ladder, used only when strict pass yields nothing:
    1. Drop exclude_categories (keep exclude_ids).
    2. Drop the requiresLifepath gate.
    3. Drop the roles allowlist (keep excludeRoles).
    4. Last resort: ignore everything except exclude_ids.
    """
    # Step 1: Drop exclude_categories
    candidates = []
    for t in pool:
        if t["id"] in exclude_ids:
            continue
        if character.role_key in t.get("excludeRoles", []):
            continue
        if t.get("roles") and character.role_key not in t["roles"]:
            continue
        if not lifepath_matches(t.get("requiresLifepath"), character, synonym_map):
            continue
        w = calculate_candidate_weight(t, character, char_tags, ctx, synonym_map)
        candidates.append((t, w))

    if candidates:
        logger.warning(f"Relaxation step 1 (drop exclude_categories) used for character '{character.display_name}' ({character.role_key}), kind '{kind}'")
        ctx.relaxed.append({"character_id": character.id, "kind": kind, "step": "drop_exclude_categories"})
        return weighted_choice(candidates, rng=rng)

    # Step 2: Drop requiresLifepath gate
    candidates = []
    for t in pool:
        if t["id"] in exclude_ids:
            continue
        if character.role_key in t.get("excludeRoles", []):
            continue
        if t.get("roles") and character.role_key not in t["roles"]:
            continue
        w = calculate_candidate_weight(t, character, char_tags, ctx, synonym_map)
        candidates.append((t, w))

    if candidates:
        logger.warning(f"Relaxation step 2 (drop requiresLifepath) used for character '{character.display_name}' ({character.role_key}), kind '{kind}'")
        ctx.relaxed.append({"character_id": character.id, "kind": kind, "step": "drop_requiresLifepath"})
        return weighted_choice(candidates, rng=rng)

    # Step 3: Drop roles allowlist (keep excludeRoles)
    candidates = []
    for t in pool:
        if t["id"] in exclude_ids:
            continue
        if character.role_key in t.get("excludeRoles", []):
            continue
        w = calculate_candidate_weight(t, character, char_tags, ctx, synonym_map)
        candidates.append((t, w))

    if candidates:
        logger.warning(f"Relaxation step 3 (drop roles allowlist) used for character '{character.display_name}' ({character.role_key}), kind '{kind}'")
        ctx.relaxed.append({"character_id": character.id, "kind": kind, "step": "drop_roles_allowlist"})
        return weighted_choice(candidates, rng=rng)

    # Step 4: Last resort: ignore everything except exclude_ids
    candidates = []
    for t in pool:
        if t["id"] in exclude_ids:
            continue
        w = calculate_candidate_weight(t, character, char_tags, ctx, synonym_map)
        candidates.append((t, w))

    if candidates:
        logger.warning(f"Relaxation step 4 (drop all constraints except exclude_ids) used for character '{character.display_name}' ({character.role_key}), kind '{kind}'")
        ctx.relaxed.append({"character_id": character.id, "kind": kind, "step": "drop_all_except_exclude_ids"})
        return weighted_choice(candidates, rng=rng)

    raise ValueError(f"Template pool is exhausted for character '{character.display_name}' (session size too large for pool)")

def pick_template(pool, character, char_tags, ctx, kind, exclude_ids, exclude_categories, synonym_map=None, rng=None):
    candidates = pick_template_strict(pool, character, char_tags, ctx, exclude_ids, exclude_categories, synonym_map)
    if not candidates:
        return pick_template_relaxed(pool, character, char_tags, ctx, kind, exclude_ids, synonym_map, rng=rng)
    return weighted_choice(candidates, rng=rng)
