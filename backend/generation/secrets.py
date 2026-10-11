"""
Phase 4.1 Secret Generation Engine.

Design principle:
Tag affinity should *bias* which secret a character gets, but it must not be the
only thing deciding fit. We use three layers:
1. Hard gates: a role may never get this (roles allowlist / excludeRoles denylist,
   plus requiresLifepath).
2. Weighted selection: sharpened affinity ((0.05 + affinity) ** 2) multiplied by
   template authoring weight and Lifepath boost multipliers.
3. Soft penalties: anti-repeat across the entire session (0.25 ** use_count).

Everything remains GM-editable afterward, exactly as before.
"""

import os
import json
import random
import logging
from models import db, Character, Relationship, Secret, SecretKnowledge
from generation.relationships import get_role_tags_flat
from generation.secret_templates import (
    load_all_secret_templates,
    get_valid_lifepath_options,
    resolve_template,
    pick_template,
    pick_template_strict,
    pick_template_relaxed,
    calculate_candidate_weight,
    weighted_choice,
    score_tags,
    SessionContext,
    get_base_data_dir
)

logger = logging.getLogger(__name__)

# Cache for dev report debugging aid (§8)
_secrets_report_cache = {}

def get_session_report(session_id):
    return _secrets_report_cache.get(session_id)

def _generate_secrets(session, rng=None):
    if rng is None:
        rng = random.Random()

    characters = Character.query.filter_by(session_id=session.id).all()
    if len(characters) < 3:
        raise ValueError("At least 3 characters are required in the session to generate secrets.")
    
    incomplete = [c.player_id for c in characters if not c.lifepath_complete]
    if incomplete:
        raise ValueError("Not all characters have completed their lifepath.")
        
    relationships = Relationship.query.filter_by(session_id=session.id).all()
    if not relationships:
        raise ValueError("At least one relationship row must exist for the session.")
        
    existing_secrets = Secret.query.filter_by(session_id=session.id).first()
    if existing_secrets:
        raise ValueError("Secrets already exist for this session.")
        
    killers = [c for c in characters if c.is_killer]
    if len(killers) > 1:
        raise ValueError("More than one killer found. Invalid state.")
        
    if len(killers) == 1:
        killer = killers[0]
    else:
        killer = rng.choice(characters)
        killer.is_killer = True
        db.session.commit()
        
    base_dir = get_base_data_dir()
    with open(os.path.join(base_dir, 'roleTags.json'), 'r', encoding='utf-8') as f:
        role_tags = json.load(f)
    with open(os.path.join(base_dir, 'scenarioPacks', session.scenario_pack_id + '.json'), 'r', encoding='utf-8') as f:
        scenario = json.load(f)
    with open(os.path.join(base_dir, 'relationshipTypes.json'), 'r', encoding='utf-8') as f:
        rel_types = json.load(f)
        rel_type_bias = {}
        for rt in rel_types.get("universal", []) + rel_types.get("roleShaped", []):
            rel_type_bias[rt["id"]] = rt.get("biasTags", [])

    # Load templates and lifepath metadata
    pools = load_all_secret_templates(base_dir)
    _, synonym_map = get_valid_lifepath_options(base_dir)

    char_by_id = {c.id: c for c in characters}
    char_tags = {c.id: set(get_role_tags_flat(c.role_key, role_tags)) for c in characters}
    
    # Adjacency list and edge mapping
    neighbors = {c.id: [] for c in characters}
    edges = {}
    for r in relationships:
        neighbors[r.source_character_id].append(r.target_character_id)
        neighbors[r.target_character_id].append(r.source_character_id)
        edges[(r.source_character_id, r.target_character_id)] = r
        edges[(r.target_character_id, r.source_character_id)] = r

    # Scenario values
    victim_name = scenario.get('victim_name') or scenario.get('victim', {}).get('name') or 'Director Kessler Voss'
    victim_title = scenario.get('victim_title') or scenario.get('victim', {}).get('roleInWorld') or 'Executive'
    locations = scenario.get('locations') or scenario.get('setting', {}).get('locations') or [scenario.get('setting', {}).get('locationName', 'The Voss Estate')]

    ctx = SessionContext()
    generated_secrets = []
    generated_knowledge = []

    # Per-character tracking for uniqueness and category exclusion (§3)
    char_assigned_ids = {c.id: set() for c in characters}
    char_assigned_categories = {c.id: set() for c in characters}

    def record_secret(subject, template, kind, text_sub, text_kno=None, is_core=False, is_trail=False, rel_char_id=None, rel_rel_id=None, selection_weight=1.0, is_relaxed=False):
        s = Secret(
            session_id=session.id,
            subject_character_id=subject.id,
            template_id=template["id"],
            kind=kind,
            category=template["category"],
            severity=template["severity"],
            text_subject=text_sub,
            text_knower=text_kno,
            is_killer_core=is_core,
            is_killer_trail=is_trail,
            related_character_id=rel_char_id,
            related_relationship_id=rel_rel_id
        )
        db.session.add(s)
        generated_secrets.append(s)
        ctx.use_count[template["id"]] += 1
        char_assigned_ids[subject.id].add(template["id"])
        char_assigned_categories[subject.id].add(template["category"])

        # Store in generation trace for report (§8)
        ctx.report_entries.append({
            "character_id": subject.id,
            "role_key": subject.role_key,
            "kind": kind,
            "template_id": template["id"],
            "category": template["category"],
            "pool": pools["template_pool_source"].get(template["id"], "universal"),
            "is_relaxed": is_relaxed,
            "selection_weight": round(selection_weight, 4)
        })
        return s

    def record_knowledge(secret, knower, how_learned=None):
        k = SecretKnowledge(
            secret_id=secret.id,
            knower_character_id=knower.id,
            how_learned=how_learned
        )
        db.session.add(k)
        generated_knowledge.append(k)
        return k

    # Phase A: Assign victim_tie, self, lie, and forced observation per character
    for c in characters:
        c_tags = char_tags[c.id]
        loc = rng.choice(locations)
        
        # 1. Victim tie
        min_sev = 2 if c.is_killer else 1
        vt_pool = [t for t in pools["victim_ties"] if t.get('severity', 1) >= min_sev]
        if not vt_pool:
            vt_pool = pools["victim_ties"]

        exclude_ids = set(char_assigned_ids[c.id])
        # Victim tie is exempt from category exclusion
        exclude_cats = set()

        cand_strict = pick_template_strict(vt_pool, c, c_tags, ctx, exclude_ids, exclude_cats, synonym_map)
        is_relaxed = False
        if cand_strict:
            t_vt = weighted_choice(cand_strict, rng=rng)
            w = calculate_candidate_weight(t_vt, c, c_tags, ctx, synonym_map)
        else:
            is_relaxed = True
            t_vt = pick_template_relaxed(vt_pool, c, c_tags, ctx, "victim_tie", exclude_ids, synonym_map, rng=rng)
            w = calculate_candidate_weight(t_vt, c, c_tags, ctx, synonym_map)

        vt_s = resolve_template(t_vt['templateSubject'], t_vt['id'], subject=c, knower=None, victim_name=victim_name, victim_title=victim_title, location=loc)
        vt_k = resolve_template(t_vt['templateKnower'], t_vt['id'], subject=c, knower=None, victim_name=victim_name, victim_title=victim_title, location=loc)
        record_secret(c, t_vt, 'victim_tie', vt_s, vt_k, selection_weight=w, is_relaxed=is_relaxed)

        # 2. Self secret (Two-stage draw: 60% role pool / 40% universal pool)
        exclude_ids = set(char_assigned_ids[c.id])
        exclude_cats = set(char_assigned_categories[c.id])

        role_self_pool = pools["roles_pool"].get(c.role_key, {}).get("self", [])
        univ_self_pool = pools["universal_self"]

        draw_role_first = (rng.random() < 0.60)
        first_pool = role_self_pool if draw_role_first else univ_self_pool
        second_pool = univ_self_pool if draw_role_first else role_self_pool

        cands = pick_template_strict(first_pool, c, c_tags, ctx, exclude_ids, exclude_cats, synonym_map)
        is_relaxed = False
        if cands:
            t_self = weighted_choice(cands, rng=rng)
            w = calculate_candidate_weight(t_self, c, c_tags, ctx, synonym_map)
        else:
            # Fall through to the other pool before relaxation ladder
            cands = pick_template_strict(second_pool, c, c_tags, ctx, exclude_ids, exclude_cats, synonym_map)
            if cands:
                t_self = weighted_choice(cands, rng=rng)
                w = calculate_candidate_weight(t_self, c, c_tags, ctx, synonym_map)
            else:
                # Relaxation ladder across combined pools
                is_relaxed = True
                combined_self = first_pool + second_pool
                t_self = pick_template_relaxed(combined_self, c, c_tags, ctx, "self", exclude_ids, synonym_map, rng=rng)
                w = calculate_candidate_weight(t_self, c, c_tags, ctx, synonym_map)

        sf_s = resolve_template(t_self['templateSubject'], t_self['id'], subject=c, knower=None, victim_name=victim_name, victim_title=victim_title, location=loc)
        sf_k = resolve_template(t_self['templateKnower'], t_self['id'], subject=c, knower=None, victim_name=victim_name, victim_title=victim_title, location=loc)
        record_secret(c, t_self, 'self', sf_s, sf_k, selection_weight=w, is_relaxed=is_relaxed)

        # 3. Lie secret (Two-stage draw: 40% role pool / 60% universal pool)
        exclude_ids = set(char_assigned_ids[c.id])
        exclude_cats = set(char_assigned_categories[c.id])

        role_lie_pool = pools["roles_pool"].get(c.role_key, {}).get("lie", [])
        univ_lie_pool = pools["universal_lie"]

        draw_role_first = (rng.random() < 0.40)
        first_pool = role_lie_pool if draw_role_first else univ_lie_pool
        second_pool = univ_lie_pool if draw_role_first else role_lie_pool

        cands = pick_template_strict(first_pool, c, c_tags, ctx, exclude_ids, exclude_cats, synonym_map)
        is_relaxed = False
        if cands:
            t_lie = weighted_choice(cands, rng=rng)
            w = calculate_candidate_weight(t_lie, c, c_tags, ctx, synonym_map)
        else:
            cands = pick_template_strict(second_pool, c, c_tags, ctx, exclude_ids, exclude_cats, synonym_map)
            if cands:
                t_lie = weighted_choice(cands, rng=rng)
                w = calculate_candidate_weight(t_lie, c, c_tags, ctx, synonym_map)
            else:
                is_relaxed = True
                combined_lie = first_pool + second_pool
                t_lie = pick_template_relaxed(combined_lie, c, c_tags, ctx, "lie", exclude_ids, synonym_map, rng=rng)
                w = calculate_candidate_weight(t_lie, c, c_tags, ctx, synonym_map)

        li_s = resolve_template(t_lie['templateSubject'], t_lie['id'], subject=c, knower=None, victim_name=victim_name, victim_title=victim_title, location=loc)
        li_k = resolve_template(t_lie['templateKnower'], t_lie['id'], subject=c, knower=None, victim_name=victim_name, victim_title=victim_title, location=loc)
        record_secret(c, t_lie, 'lie', li_s, li_k, selection_weight=w, is_relaxed=is_relaxed)

        # 4. Forced observation
        # Pick neighbor
        nbrs = neighbors[c.id]
        if nbrs:
            weights = []
            for n_id in nbrs:
                edge = edges[(c.id, n_id)]
                b_tags = rel_type_bias.get(edge.rel_type, [])
                overlap = set(b_tags).intersection({"trusted_confidant", "covert"})
                weights.append(2.0 if overlap else 1.0)
            neighbor_id = rng.choices(nbrs, weights=weights, k=1)[0]
            edge = edges[(c.id, neighbor_id)]
        else:
            other_ids = [o.id for o in characters if o.id != c.id]
            neighbor_id = rng.choice(other_ids)
            edge = None

        neighbor = char_by_id[neighbor_id]
        n_tags = char_tags[neighbor.id]

        # Select 3rd party for observation template if needed
        other_candidates = [o for o in characters if o.id not in (c.id, neighbor.id)]
        other = rng.choice(other_candidates) if other_candidates else None

        # Filter observations: if no 3rd party exists, cannot pick templates requiring #otherName#
        valid_obs_pool = pools["observations"]
        if not other:
            valid_obs_pool = [t for t in valid_obs_pool if '#otherName#' not in t.get('templateSubject', '') and '#otherName#' not in t.get('templateKnower', '')]

        # Prefer observation templates whose category matches one of the subject's own secret categories (§6)
        neighbor_cats = char_assigned_categories[neighbor.id]
        n_exclude_ids = set(char_assigned_ids[neighbor.id])

        # Score observation templates using subject's role
        obs_cands = []
        for t in valid_obs_pool:
            if t["id"] in n_exclude_ids:
                continue
            if neighbor.role_key in t.get("excludeRoles", []):
                continue
            if t.get("roles") and neighbor.role_key not in t["roles"]:
                continue
            w = calculate_candidate_weight(t, neighbor, n_tags, ctx, synonym_map)
            if t.get("category") in neighbor_cats:
                w *= 2.0  # Shared category preference
            obs_cands.append((t, w))

        is_relaxed = False
        if obs_cands:
            t_obs = weighted_choice(obs_cands, rng=rng)
            w = calculate_candidate_weight(t_obs, neighbor, n_tags, ctx, synonym_map)
        else:
            is_relaxed = True
            t_obs = pick_template_relaxed(valid_obs_pool, neighbor, n_tags, ctx, "observation", n_exclude_ids, synonym_map, rng=rng)
            w = calculate_candidate_weight(t_obs, neighbor, n_tags, ctx, synonym_map)

        ob_s = resolve_template(t_obs['templateSubject'], t_obs['id'], subject=neighbor, knower=c, victim_name=victim_name, victim_title=victim_title, location=loc, other=other)
        ob_k = resolve_template(t_obs['templateKnower'], t_obs['id'], subject=neighbor, knower=c, victim_name=victim_name, victim_title=victim_title, location=loc, other=other)

        rel_char_id = other.id if other else c.id
        rel_rel_id = edge.id if edge else None
        s_obs = record_secret(neighbor, t_obs, 'observation', ob_s, ob_k, rel_char_id=rel_char_id, rel_rel_id=rel_rel_id, selection_weight=w, is_relaxed=is_relaxed)

        db.session.flush()
        record_knowledge(s_obs, c)

        # Killer core
        if c.is_killer:
            core_choices = [t for t in pools["killer_cores"] if t["id"] not in char_assigned_ids[c.id]]
            if not core_choices:
                core_choices = pools["killer_cores"]
            t_core = rng.choice(core_choices)
            c_s = resolve_template(t_core['templateSubject'], t_core['id'], subject=c, knower=None, victim_name=victim_name, victim_title=victim_title, location=loc)
            record_secret(c, t_core, 'self', c_s, is_core=True, selection_weight=1.0)

    db.session.flush()

    # Phase B: Killer trail facts
    # Sample 2 distinct templates from killer_trails
    available_trails = [t for t in pools["killer_trails"] if t["id"] not in char_assigned_ids[killer.id]]
    if len(available_trails) < 2:
        available_trails = pools["killer_trails"]
    trail_choices = rng.sample(available_trails, 2)

    non_killer_chars = [c for c in characters if not c.is_killer]
    k_nbrs = neighbors[killer.id]
    weights = []
    for nk in non_killer_chars:
        w = 1.0
        if nk.id in k_nbrs:
            edge = edges[(killer.id, nk.id)]
            b_tags = rel_type_bias.get(edge.rel_type, [])
            if set(b_tags).intersection({"trusted_confidant", "covert"}):
                w = 2.0
            else:
                w = 1.5
        weights.append(w)

    trail_knowers = []
    idx1 = rng.choices(range(len(non_killer_chars)), weights=weights, k=1)[0]
    trail_knowers.append(non_killer_chars[idx1])

    weights2 = list(weights)
    weights2[idx1] = 0.0
    idx2 = rng.choices(range(len(non_killer_chars)), weights=weights2, k=1)[0]
    trail_knowers.append(non_killer_chars[idx2])

    for i in range(2):
        t_trail = trail_choices[i]
        loc = rng.choice(locations)
        tr_s = resolve_template(t_trail['templateSubject'], t_trail['id'], subject=killer, knower=trail_knowers[i], victim_name=victim_name, victim_title=victim_title, location=loc)
        tr_k = resolve_template(t_trail['templateKnower'], t_trail['id'], subject=killer, knower=trail_knowers[i], victim_name=victim_name, victim_title=victim_title, location=loc)
        s_tr = record_secret(killer, t_trail, 'observation', tr_s, tr_k, is_trail=True, selection_weight=1.0)
        db.session.flush()
        record_knowledge(s_tr, trail_knowers[i])

    # Phase C: Repair pass (ensure every character has at least one secret known by someone else)
    for c in characters:
        held = False
        c_secrets = [s for s in generated_secrets if s.subject_character_id == c.id and not s.is_killer_core and not s.is_killer_trail]
        for s in c_secrets:
            k = [sk for sk in generated_knowledge if sk.secret_id == s.id]
            if k:
                held = True
                break

        if not held:
            own_secrets = [s for s in c_secrets if s.kind in ('self', 'lie', 'victim_tie')]
            preferred = [s for s in own_secrets if s.kind in ('lie', 'self')]
            if preferred:
                chosen_s = rng.choice(preferred)
            elif own_secrets:
                chosen_s = rng.choice(own_secrets)
            else:
                continue

            nbr_ids = neighbors[c.id]
            if not nbr_ids:
                nbr_ids = [o.id for o in characters if o.id != c.id]

            nbr_known_counts = []
            for nid in nbr_ids:
                count = len([sk for sk in generated_knowledge if sk.knower_character_id == nid])
                nbr_known_counts.append((count, nid))

            nbr_known_counts.sort()
            chosen_knower_id = nbr_known_counts[0][1]
            chosen_knower = char_by_id[chosen_knower_id]
            record_knowledge(chosen_s, chosen_knower)

    db.session.commit()

    # Cache report trace for dev reporting (§8)
    _secrets_report_cache[session.id] = {
        "session_id": session.id,
        "relaxed": ctx.relaxed,
        "characters": ctx.report_entries
    }

def verify_secrets(session):
    """
    Validates all secrets generated for a session against gameplay and consistency invariants.
    """
    secrets = Secret.query.filter_by(session_id=session.id).all()
    knowledge = SecretKnowledge.query.join(Secret).filter(Secret.session_id == session.id).all()
    characters = Character.query.filter_by(session_id=session.id).all()
    
    killers = [c for c in characters if c.is_killer]
    if len(killers) != 1:
        raise ValueError("Exactly one character must have is_killer=True")
    killer = killers[0]
    
    core_secrets = [s for s in secrets if s.is_killer_core]
    if len(core_secrets) != 1:
        raise ValueError("Exactly one is_killer_core secret must exist")
    if core_secrets[0].subject_character_id != killer.id:
        raise ValueError("is_killer_core secret must belong to killer")
    if [k for k in knowledge if k.secret_id == core_secrets[0].id]:
        raise ValueError("is_killer_core secret must have zero knowers")
        
    trail_secrets = [s for s in secrets if s.is_killer_trail]
    if len(trail_secrets) != 2:
        raise ValueError("Exactly two is_killer_trail secrets must exist")
    for ts in trail_secrets:
        if ts.subject_character_id != killer.id:
            raise ValueError("trail secrets must belong to killer")
        ts_knowers = [k for k in knowledge if k.secret_id == ts.id]
        if len(ts_knowers) != 1:
            raise ValueError("each trail secret must have exactly one knower")
            
    knower1 = [k.knower_character_id for k in knowledge if k.secret_id == trail_secrets[0].id][0]
    knower2 = [k.knower_character_id for k in knowledge if k.secret_id == trail_secrets[1].id][0]
    if knower1 == knower2 or knower1 == killer.id or knower2 == killer.id:
        raise ValueError("trail knowers must be distinct from each other and from the killer")
        
    for c in characters:
        subj_secrets = [s for s in secrets if s.subject_character_id == c.id]
        if not subj_secrets:
            raise ValueError(f"Character {c.id} has no secrets where they're subject")
            
        held_by_others = False
        for s in subj_secrets:
            if s.is_killer_core or s.is_killer_trail:
                continue
            k_list = [k for k in knowledge if k.secret_id == s.id]
            if k_list:
                held_by_others = True
                break
        if not held_by_others:
            raise ValueError(f"Character {c.id} has no non-core/trail secret known by someone else")
            
        known_count = len([k for k in knowledge if k.knower_character_id == c.id])
        if known_count < 1:
            raise ValueError(f"Character {c.id} knows nothing")
            
    for k in knowledge:
        s = next((sec for sec in secrets if sec.id == k.secret_id), None)
        if s and k.knower_character_id == s.subject_character_id:
            raise ValueError(f"Character {k.knower_character_id} is knower of their own secret {s.id}")

    # --- Phase 4.1 Invariants (§7) ---

    # 1. No two Secret rows share (session_id, subject_character_id, template_id)
    seen_sub_tpl = set()
    for s in secrets:
        pair = (s.session_id, s.subject_character_id, s.template_id)
        if pair in seen_sub_tpl:
            raise ValueError(f"Duplicate secret template '{s.template_id}' assigned to subject '{s.subject_character_id}' in session '{session.id}'")
        seen_sub_tpl.add(pair)

    # 2. For each subject, no two visible secrets (excluding is_killer_core) have identical text_subject
    for c in characters:
        visible_texts = set()
        c_visible_secrets = [s for s in secrets if s.subject_character_id == c.id and not s.is_killer_core]
        for s in c_visible_secrets:
            if s.text_subject in visible_texts:
                raise ValueError(f"Subject '{c.display_name}' has duplicate secret text_subject: '{s.text_subject}'")
            visible_texts.add(s.text_subject)

    # 3. No two secrets for one subject share a category among kinds 'self' and 'lie'
    for c in characters:
        self_lie_cats = set()
        for s in secrets:
            if s.subject_character_id == c.id and s.kind in ('self', 'lie') and not s.is_killer_core:
                if s.category in self_lie_cats:
                    raise ValueError(f"Subject '{c.display_name}' has duplicate category '{s.category}' across self/lie secrets")
                self_lie_cats.add(s.category)

    # 4. No '#' characters remain in any text_subject or text_knower
    for s in secrets:
        if '#' in s.text_subject:
            raise ValueError(f"Unresolved Tracery placeholder in text_subject of secret '{s.id}' (template '{s.template_id}'): '{s.text_subject}'")
        if s.text_knower and '#' in s.text_knower:
            raise ValueError(f"Unresolved Tracery placeholder in text_knower of secret '{s.id}' (template '{s.template_id}'): '{s.text_knower}'")

    # 5. Every assigned template satisfies its own roles/excludeRoles against subject's role (unless relaxed)
    pools = load_all_secret_templates()
    cached_report = _secrets_report_cache.get(session.id, {})
    relaxed_char_ids = {r["character_id"] for r in cached_report.get("relaxed", [])}

    char_map = {c.id: c for c in characters}
    for s in secrets:
        tpl = pools["template_registry"].get(s.template_id)
        if not tpl:
            continue
        subj = char_map.get(s.subject_character_id)
        if not subj:
            continue

        # Check if subject was relaxed
        if subj.id in relaxed_char_ids:
            continue

        if subj.role_key in tpl.get("excludeRoles", []):
            raise ValueError(f"Subject '{subj.display_name}' has role '{subj.role_key}' which is in excludeRoles of template '{s.template_id}'")
        if tpl.get("roles") and subj.role_key not in tpl["roles"]:
            raise ValueError(f"Subject '{subj.display_name}' has role '{subj.role_key}' which is not in allowed roles {tpl['roles']} of template '{s.template_id}'")

    return True
