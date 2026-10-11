"""
Phase 5 objective templates: loading, load-time validation and rendering.

Objective templates share their gate/weight fields (roles, excludeRoles,
requiresLifepath, boostLifepath, biasTags, weight) with secret templates so the
selection helpers in secret_templates.py work on them unchanged. The render
context is deliberately separate from the secret context: an objective never
embeds a secret's text, only a short #secretLabel# derived from its category.
"""

import os
import json
import re
from generation.secret_templates import (
    get_base_data_dir,
    load_all_secret_templates,
    load_template_validation_context,
    validate_template_gates,
    render_grammar,
    assert_clean_render,
)

OBJECTIVE_KINDS = ('protect_secret', 'exploit_secret', 'relationship', 'flavor',
                   'find_killer', 'hide_evidence', 'frame', 'manual')
KILLER_ONLY_KINDS = ('hide_evidence', 'frame')
LINK_TYPES = ('opposed', 'aligned')

BASE_PLACEHOLDERS = {'ownerName', 'victimName', 'victimTitle', 'location'}
# #targetName# is only legal where a target exists; #secretLabel# only for secret objectives.
ALLOWED_PLACEHOLDERS = {
    'protect_secret': BASE_PLACEHOLDERS | {'secretLabel'},
    'exploit_secret': BASE_PLACEHOLDERS | {'secretLabel', 'targetName'},
    'relationship': BASE_PLACEHOLDERS | {'targetName'},
    'flavor': BASE_PLACEHOLDERS,
    'find_killer': BASE_PLACEHOLDERS,
    'hide_evidence': BASE_PLACEHOLDERS,
    'frame': BASE_PLACEHOLDERS | {'targetName'},
}
MATCH_SECRET_KINDS = {
    'protect_secret': {'self', 'lie'},
    'exploit_secret': {'self', 'lie', 'observation', 'victim_tie'},
}
FIXED_POINTS = {'find_killer': (2, 2), 'hide_evidence': (2, 2), 'frame': (3, 3), 'flavor': (1, 2)}

_PLACEHOLDER_RE = re.compile(r'#([^#]*)#')

def _load_json(objectives_dir, name):
    with open(os.path.join(objectives_dir, name), 'r', encoding='utf-8') as f:
        return json.load(f)

def load_all_objective_templates(data_dir=None):
    if not data_dir:
        data_dir = get_base_data_dir()
    objectives_dir = os.path.join(data_dir, 'objectives')

    killer = _load_json(objectives_dir, 'killerObjectiveTemplates.json')
    pools = {
        "protect_secret": _load_json(objectives_dir, 'protectTemplates.json'),
        "exploit_secret": _load_json(objectives_dir, 'exploitTemplates.json'),
        "relationship": _load_json(objectives_dir, 'relationshipObjectiveTemplates.json'),
        "flavor": _load_json(objectives_dir, 'flavorTemplates.json'),
        "find_killer": _load_json(objectives_dir, 'findKillerTemplates.json'),
        "hide_evidence": killer.get("hide_evidence", []),
        "frame": killer.get("frame", []),
    }

    template_registry = {}
    template_pool_source = {}
    for kind, t_list in pools.items():
        for t in t_list:
            if t.get("id"):
                template_registry[t["id"]] = t
                template_pool_source[t["id"]] = kind

    pools["category_labels"] = _load_json(objectives_dir, 'categoryLabels.json')
    pools["template_registry"] = template_registry
    pools["template_pool_source"] = template_pool_source
    return pools

def load_relationship_type_ids(data_dir=None):
    if not data_dir:
        data_dir = get_base_data_dir()
    with open(os.path.join(data_dir, 'relationshipTypes.json'), 'r', encoding='utf-8') as f:
        rel_types = json.load(f)
    return [rt["id"] for rt in rel_types.get("universal", []) + rel_types.get("roleShaped", [])]

def secret_label(category, category_labels):
    """Short label used for #secretLabel#. Falls back to the category name for
    categories that predate categoryLabels.json (e.g. hand-edited secrets)."""
    return category_labels.get(category) or "the " + str(category).replace('_', ' ')

def _assert_placeholders(text, template_id, field_name, allowed):
    """Every #placeholder# must be one this kind may use. Tracery renders unknown
    symbols as ((name)) rather than leaving a '#', so check the names explicitly."""
    for raw in _PLACEHOLDER_RE.findall(text):
        name = raw.split('.')[0]
        if name not in allowed:
            raise ValueError(f"Template '{template_id}' has unresolved placeholder(s) in {field_name}: ['{raw}'] (text: '{text}')")

def _validate_text(t, field_name, kind, dummy_context, required=True):
    text = t.get(field_name)
    if not text:
        if required:
            raise ValueError(f"Template '{t['id']}' is missing '{field_name}'")
        return
    allowed = ALLOWED_PLACEHOLDERS[kind]
    _assert_placeholders(text, t["id"], field_name, allowed)
    rendered = render_grammar(text, {k: v for k, v in dummy_context.items() if k in allowed})
    assert_clean_render(rendered, t["id"], field_name)
    if '((' in rendered:
        raise ValueError(f"Template '{t['id']}' has unresolved placeholder(s) in {field_name} (text: '{rendered}')")

def _validate_points(t, field_name, low, high):
    p = t.get(field_name)
    if not isinstance(p, int) or isinstance(p, bool) or not (low <= p <= high):
        raise ValueError(f"Template '{t['id']}' has invalid '{field_name}': {p!r} (expected {low}-{high})")

def validate_objective_templates(data_dir=None):
    """
    Load-time validation: fail loudly at app startup.
    - Template ids are unique across every objective file.
    - roles / excludeRoles / requiresLifepath / boostLifepath are valid (same rules as secrets).
    - matchCategories exist in categoryLabels.json; categoryLabels covers every secret category.
    - relTypes are real relationship type ids.
    - Every text and hint renders cleanly; #targetName# and #secretLabel# only where legal.
    - Every pool can always produce a template (generic fallbacks, both sides per rel type).
    """
    if not data_dir:
        data_dir = get_base_data_dir()

    valid_role_keys, valid_lifepath_options, character_cols = load_template_validation_context(data_dir)
    pools = load_all_objective_templates(data_dir)
    labels = pools["category_labels"]
    rel_type_ids = load_relationship_type_ids(data_dir)

    secret_categories = {t["category"] for t in load_all_secret_templates(data_dir)["template_registry"].values()}
    unlabeled = sorted(secret_categories - set(labels))
    if unlabeled:
        raise ValueError(f"categoryLabels.json is missing labels for secret categories: {unlabeled}")
    for cat, label in labels.items():
        if not isinstance(label, str) or not label.strip() or '#' in label:
            raise ValueError(f"categoryLabels.json has an invalid label for '{cat}': {label!r}")

    dummy_context = {
        "ownerName": "DummyOwner",
        "targetName": "DummyTarget",
        "victimName": "DummyVictim",
        "victimTitle": "DummyVictimTitle",
        "location": "DummyLocation",
        "secretLabel": "the dummy secret",
    }

    pool_files_map = {
        "protectTemplates.json": "protect_secret",
        "exploitTemplates.json": "exploit_secret",
        "relationshipObjectiveTemplates.json": "relationship",
        "flavorTemplates.json": "flavor",
        "findKillerTemplates.json": "find_killer",
        "killerObjectiveTemplates.json [hide_evidence]": "hide_evidence",
        "killerObjectiveTemplates.json [frame]": "frame",
    }

    seen_ids = set()
    for file_name, kind in pool_files_map.items():
        t_list = pools[kind]
        if not t_list:
            raise ValueError(f"Objective template pool {file_name} is empty")
        for t in t_list:
            t_id = t.get("id")
            if not t_id:
                raise ValueError(f"Template missing 'id' in {file_name}: {t}")
            if t_id in seen_ids:
                raise ValueError(f"Duplicate template id '{t_id}' found across objective template files (in {file_name})")
            seen_ids.add(t_id)

            validate_template_gates(t, file_name, valid_role_keys, valid_lifepath_options, character_cols)

            if kind in MATCH_SECRET_KINDS:
                kinds = t.get("matchSecretKinds") or []
                if not kinds or not set(kinds) <= MATCH_SECRET_KINDS[kind]:
                    raise ValueError(f"Template '{t_id}' in {file_name} has invalid matchSecretKinds: {kinds}")
                for cat in t.get("matchCategories") or []:
                    if cat not in labels:
                        raise ValueError(f"Template '{t_id}' in {file_name} has unknown category in matchCategories: '{cat}'")
                if "points" in t:
                    raise ValueError(f"Template '{t_id}' in {file_name} must not define 'points' (points come from secret severity)")
                _validate_text(t, "templateText", kind, dummy_context)
                _validate_text(t, "templateHint", kind, dummy_context)

            elif kind == "relationship":
                types = t.get("relTypes") or []
                if not types:
                    raise ValueError(f"Template '{t_id}' in {file_name} has no relTypes")
                for rt in types:
                    if rt not in rel_type_ids:
                        raise ValueError(f"Template '{t_id}' in {file_name} has unknown relationship type in relTypes: '{rt}'")
                link = t.get("link")
                if link not in LINK_TYPES + ('independent',):
                    raise ValueError(f"Template '{t_id}' in {file_name} has invalid link: {link!r}")
                has_side = False
                for side in ("Source", "Target"):
                    if t.get("template" + side):
                        has_side = True
                        _validate_text(t, "template" + side, kind, dummy_context)
                        _validate_text(t, "hint" + side, kind, dummy_context)
                        _validate_points(t, "points" + side, 1, 2)
                if not has_side:
                    raise ValueError(f"Template '{t_id}' in {file_name} defines neither templateSource nor templateTarget")
                if link in LINK_TYPES and not (t.get("templateSource") and t.get("templateTarget")):
                    raise ValueError(f"Template '{t_id}' in {file_name} is '{link}' but only one side has text")

            else:
                _validate_text(t, "templateText", kind, dummy_context)
                _validate_text(t, "templateHint", kind, dummy_context)
                _validate_points(t, "points", *FIXED_POINTS[kind])
                if kind == "frame":
                    # The GM must always be told who the killer is framing.
                    for ph in ("#ownerName#", "#targetName#"):
                        if ph not in t["templateHint"]:
                            raise ValueError(f"Template '{t_id}' in {file_name} must name {ph} in templateHint")
                if kind == "find_killer" and (t.get("roles") or t.get("requiresLifepath")):
                    raise ValueError(f"Template '{t_id}' in {file_name} must not be role- or Lifepath-gated")

    # Pool coverage: generation must never dead-end on a missing template.
    for kind, secret_kinds in MATCH_SECRET_KINDS.items():
        for sk in secret_kinds:
            if not any(sk in t["matchSecretKinds"] and not t.get("matchCategories") and not t.get("roles")
                       and not t.get("requiresLifepath") for t in pools[kind]):
                raise ValueError(f"No generic {kind} template covers secret kind '{sk}'")
    for rt in rel_type_ids:
        if not any(rt in t["relTypes"] and t.get("templateSource") and t.get("templateTarget")
                   for t in pools["relationship"]):
            raise ValueError(f"No relationship objective template covers both sides of '{rt}'")

    return True

def resolve_objective_template(template_str, template_id, kind, owner, target=None, victim_name=None,
                               victim_title=None, location=None, label=None):
    """
    Renders one objective text or hint. The context only contains what this kind may
    reference, so a template can never pull in a name the owner should not see.
    """
    if not template_str:
        return ""

    if '#targetName#' in template_str and not target:
        raise ValueError(f"Template '{template_id}' uses #targetName# but the objective has no target")
    if '#secretLabel#' in template_str and not label:
        raise ValueError(f"Template '{template_id}' uses #secretLabel# but the objective has no secret")

    context = {
        "ownerName": owner.display_name or "Someone",
        "victimName": victim_name or "Director Kessler Voss",
        "victimTitle": victim_title or "Executive",
        "location": location or "Night City",
    }
    if target:
        context["targetName"] = target.display_name or "Someone"
    if label:
        context["secretLabel"] = label

    allowed = ALLOWED_PLACEHOLDERS[kind]
    _assert_placeholders(template_str, template_id, "rendered text", allowed)
    rendered = render_grammar(template_str, {k: v for k, v in context.items() if k in allowed})
    assert_clean_render(rendered, template_id, "rendered text")
    return rendered
