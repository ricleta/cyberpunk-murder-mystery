import os
import json
import random
import tracery
from models import db, Character, Relationship, Secret, SecretKnowledge
from generation.relationships import get_role_tags_flat

def compute_affinity(character_tags: set[str], template_bias_tags: list[str]) -> float:
    # Simpler affinity: 0.15 base + 0.20 per overlapping tag, max 0.95
    base = 0.15
    overlap = character_tags.intersection(set(template_bias_tags))
    weight = base + len(overlap) * 0.20
    return min(weight, 0.95)

def pick_template(character_tags: set[str], templates: list[dict], min_severity: int = 1) -> dict:
    valid_templates = [t for t in templates if t.get('severity', 1) >= min_severity]
    if not valid_templates:
        valid_templates = templates
    
    weights = []
    for t in valid_templates:
        bias_tags = t.get('biasTags', [])
        weights.append(compute_affinity(character_tags, bias_tags))
    return random.choices(valid_templates, weights=weights, k=1)[0]

def resolve_template(template_str: str, subject_name: str, knower_name: str, victim_name: str, other_name: str = None, locations: list[str] = None) -> str:
    grammar = {
        "origin": template_str,
        "subjectName": subject_name or "Someone",
        "knowerName": knower_name or "Someone",
        "victimName": victim_name or "The Victim"
    }
    if other_name:
        grammar["otherName"] = other_name
    if locations:
        grammar["location"] = locations
    return tracery.Grammar(grammar).flatten("#origin#")

def _generate_secrets(session):
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
        killer = random.choice(characters)
        killer.is_killer = True
        db.session.commit()
        
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
    with open(os.path.join(base_dir, 'roleTags.json'), 'r', encoding='utf-8') as f:
        role_tags = json.load(f)
    with open(os.path.join(base_dir, 'scenarioPacks', session.scenario_pack_id + '.json'), 'r', encoding='utf-8') as f:
        scenario = json.load(f)
    with open(os.path.join(base_dir, 'secrets', 'victimTieTemplates.json'), 'r', encoding='utf-8') as f:
        victim_tie_templates = json.load(f)
    with open(os.path.join(base_dir, 'secrets', 'selfSecretTemplates.json'), 'r', encoding='utf-8') as f:
        self_secret_templates = json.load(f)
    with open(os.path.join(base_dir, 'secrets', 'lieSecretTemplates.json'), 'r', encoding='utf-8') as f:
        lie_secret_templates = json.load(f)
    with open(os.path.join(base_dir, 'secrets', 'observationSecretTemplates.json'), 'r', encoding='utf-8') as f:
        obs_secret_templates = json.load(f)
    with open(os.path.join(base_dir, 'secrets', 'killerTrailTemplates.json'), 'r', encoding='utf-8') as f:
        killer_trail_templates = json.load(f)
    with open(os.path.join(base_dir, 'relationshipTypes.json'), 'r', encoding='utf-8') as f:
        rel_types = json.load(f)
        rel_type_bias = {}
        for rt in rel_types.get("universal", []) + rel_types.get("roleShaped", []):
            rel_type_bias[rt["id"]] = rt.get("biasTags", [])

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

    generated_secrets = []
    generated_knowledge = []
    
    def create_secret(subject, kind, category, severity, text_sub, text_kno=None, is_core=False, is_trail=False, rel_char_id=None, rel_rel_id=None):
        s = Secret(
            session_id=session.id,
            subject_character_id=subject.id,
            kind=kind,
            category=category,
            severity=severity,
            text_subject=text_sub,
            text_knower=text_kno,
            is_killer_core=is_core,
            is_killer_trail=is_trail,
            related_character_id=rel_char_id,
            related_relationship_id=rel_rel_id
        )
        db.session.add(s)
        generated_secrets.append(s)
        return s

    def create_knowledge(secret, knower, how_learned=None):
        k = SecretKnowledge(
            secret_id=secret.id,
            knower_character_id=knower.id,
            how_learned=how_learned
        )
        db.session.add(k)
        generated_knowledge.append(k)
        return k

    victim_name = scenario.get('victim_name', 'The Victim')
    locations = scenario.get('locations', ['somewhere'])
    
    # Phase A
    for c in characters:
        c_tags = char_tags[c.id]
        
        # 1. Victim tie
        min_sev = 2 if c.is_killer else 1
        t_vt = pick_template(c_tags, victim_tie_templates, min_sev)
        vt_s = resolve_template(t_vt['templateSubject'], c.display_name, None, victim_name, locations=locations)
        vt_k = resolve_template(t_vt['templateKnower'], c.display_name, None, victim_name, locations=locations)
        create_secret(c, 'victim_tie', t_vt['category'], t_vt['severity'], vt_s, vt_k)
        
        # 2. Self
        t_self = pick_template(c_tags, self_secret_templates)
        sf_s = resolve_template(t_self['templateSubject'], c.display_name, None, victim_name, locations=locations)
        sf_k = resolve_template(t_self['templateKnower'], c.display_name, None, victim_name, locations=locations)
        create_secret(c, 'self', t_self['category'], t_self['severity'], sf_s, sf_k)
        
        # 3. Lie
        t_lie = pick_template(c_tags, lie_secret_templates)
        li_s = resolve_template(t_lie['templateSubject'], c.display_name, None, victim_name, locations=locations)
        li_k = resolve_template(t_lie['templateKnower'], c.display_name, None, victim_name, locations=locations)
        create_secret(c, 'lie', t_lie['category'], t_lie['severity'], li_s, li_k)
        
        # 4. Forced observation
        nbrs = neighbors[c.id]
        if nbrs:
            weights = []
            for n_id in nbrs:
                edge = edges[(c.id, n_id)]
                b_tags = rel_type_bias.get(edge.rel_type, [])
                overlap = set(b_tags).intersection({"trusted_confidant", "covert"})
                weights.append(2.0 if overlap else 1.0)
            neighbor_id = random.choices(nbrs, weights=weights, k=1)[0]
            edge = edges[(c.id, neighbor_id)]
        else:
            other_ids = [o.id for o in characters if o.id != c.id]
            neighbor_id = random.choice(other_ids)
            edge = None
            
        neighbor = char_by_id[neighbor_id]
        n_tags = char_tags[neighbor.id]
        t_obs = pick_template(n_tags, obs_secret_templates)
        
        # We might need a 3rd party for observation template if it uses #otherName#
        other_candidates = [o for o in characters if o.id not in (c.id, neighbor.id)]
        other = random.choice(other_candidates) if other_candidates else c
        
        ob_s = resolve_template(t_obs['templateSubject'], neighbor.display_name, c.display_name, victim_name, other.display_name, locations=locations)
        ob_k = resolve_template(t_obs['templateKnower'], neighbor.display_name, c.display_name, victim_name, other.display_name, locations=locations)
        
        rel_rel_id = edge.id if edge else None
        s_obs = create_secret(neighbor, 'observation', t_obs['category'], t_obs['severity'], ob_s, ob_k, rel_char_id=c.id, rel_rel_id=rel_rel_id)
        
        db.session.flush() # Need secret ID to create knowledge
        create_knowledge(s_obs, c)
        
        # Killer core
        if c.is_killer:
            core_templates = [
                {"text": "You killed #victimName#. There is no going back now."},
                {"text": "You are responsible for #victimName#'s death tonight."},
                {"text": "You murdered #victimName# and must ensure you aren't caught."}
            ]
            t_core = random.choice(core_templates)
            c_s = resolve_template(t_core['text'], c.display_name, None, victim_name, locations=locations)
            create_secret(c, 'self', 'murder_core', 3, c_s, is_core=True)

    db.session.flush()
    
    # Phase B: Killer trail facts
    trail_choices = random.sample(killer_trail_templates, 2)
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
    # Pick two distinct
    idx1 = random.choices(range(len(non_killer_chars)), weights=weights, k=1)[0]
    trail_knowers.append(non_killer_chars[idx1])
    
    weights2 = list(weights)
    weights2[idx1] = 0.0
    idx2 = random.choices(range(len(non_killer_chars)), weights=weights2, k=1)[0]
    trail_knowers.append(non_killer_chars[idx2])
    
    for i in range(2):
        t_trail = trail_choices[i]
        tr_s = resolve_template(t_trail['templateSubject'], killer.display_name, None, victim_name, locations=locations)
        tr_k = resolve_template(t_trail['templateKnower'], killer.display_name, None, victim_name, locations=locations)
        s_tr = create_secret(killer, 'observation', t_trail['category'], t_trail['severity'], tr_s, tr_k, is_trail=True)
        db.session.flush()
        create_knowledge(s_tr, trail_knowers[i])
        
    # Phase C: Repair pass
    for c in characters:
        # Check if anyone holds a secret where c is the subject (excluding core/trail)
        held = False
        c_secrets = [s for s in generated_secrets if s.subject_character_id == c.id and not s.is_killer_core and not s.is_killer_trail]
        for s in c_secrets:
            k = [sk for sk in generated_knowledge if sk.secret_id == s.id]
            if k:
                held = True
                break
                
        if not held:
            # Pick a secret to give to someone
            own_secrets = [s for s in c_secrets if s.kind in ('self', 'lie', 'victim_tie')]
            # Prefer lie or self
            preferred = [s for s in own_secrets if s.kind in ('lie', 'self')]
            if preferred:
                chosen_s = random.choice(preferred)
            elif own_secrets:
                chosen_s = random.choice(own_secrets)
            else:
                continue # Should not happen based on generation A
                
            # Pick a neighbor to be the knower (prefer fewer things known)
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
            
            create_knowledge(chosen_s, chosen_knower)

    db.session.commit()
    
def verify_secrets(session):
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
