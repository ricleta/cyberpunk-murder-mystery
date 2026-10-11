"""
Phase 5 Objective Generation Engine.

Every character gets 4-5 point-valued objectives built from what they can
legitimately see: their own secrets, secrets they know about someone else, and
their own relationships. Objectives are woven into opposed/aligned pairs so the
table has something to fight over.

The whole sheet set is built in memory as ObjectiveDraft objects, checked against
the invariants, and only then persisted. A failed check retries with fresh
randomness, so a bad roll costs nothing and never needs a rollback.

Objective text is a snapshot: it is not re-rendered when a secret is edited later.
"""

import os
import json
import math
import random
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from models import db, Character, Relationship, Secret, SecretKnowledge, Objective, generate_uuid
from generation.relationships import get_role_tags_flat
from generation.report import store_report
from generation.name_tokens import name_token
from generation.secrets import verify_secrets
from generation.secret_templates import (
    get_valid_lifepath_options,
    pick_template_strict,
    pick_template_relaxed,
    calculate_candidate_weight,
    weighted_choice,
    SessionContext,
    get_base_data_dir
)
from generation.objective_templates import (
    load_all_objective_templates,
    resolve_objective_template,
    secret_label,
    OBJECTIVE_KINDS,
    KILLER_ONLY_KINDS,
    LINK_TYPES
)

logger = logging.getLogger(__name__)

MIN_CHARACTERS = 4      # at 3 both non-killers hold trail facts, so no legal scapegoat exists
MAX_ATTEMPTS = 25
FLAVOR_SLOT_P = 0.5
WEAVE_P = 0.7
OPPOSED_BOOST = 3.0     # weight multiplier for opposed relationship templates while under the tension target
MIN_TOTAL_POINTS = 8
MAX_TOTAL_POINTS = 12
MIN_OBJECTIVES = 4
MAX_OBJECTIVES = 5

SECRET_KINDS = ('protect_secret', 'exploit_secret')
FIXED_POINT_KINDS = ('find_killer', 'hide_evidence', 'frame')

# Slot order: the stable tie-break for sort_order
SLOT_PROTECT, SLOT_EXPLOIT, SLOT_RELATIONSHIP, SLOT_FLAVOR, SLOT_GENERIC, SLOT_FRAME = range(6)

def get_objectives_report(session):
    """The persisted objectives report of a session (None if nothing was generated)."""
    return (session.generation_report or {}).get('objectives')

@dataclass
class ObjectiveDraft:
    character_id: str
    kind: str
    slot: int
    template_id: str
    points: int
    text: str
    gm_hint: str = None
    related_character_id: str = None
    related_secret_id: str = None
    related_relationship_id: str = None
    linked_objective_id: str = None
    link_type: str = None
    severity: int = None   # severity of the referenced secret (budget tie-breaks)
    sort_order: int = 0
    id: str = field(default_factory=generate_uuid)
    meta: dict = field(default_factory=dict)   # report-only trace

class _World:
    """Read-only snapshot of a session. Everything is put in a canonical order so a
    seeded rng produces the same objectives regardless of row ids or query order."""

    def __init__(self, session):
        self.session_id = session.id
        self.characters = sorted(Character.query.filter_by(session_id=session.id).all(), key=lambda c: c.id)
        self.char_by_id = {c.id: c for c in self.characters}
        self.killers = [c for c in self.characters if c.is_killer]
        self.killer = self.killers[0] if len(self.killers) == 1 else None

        self.secrets = sorted(Secret.query.filter_by(session_id=session.id).all(),
                              key=lambda s: (s.subject_character_id, s.template_id))
        self.secret_by_id = {s.id: s for s in self.secrets}
        self.secrets_by_subject = defaultdict(list)
        for s in self.secrets:
            self.secrets_by_subject[s.subject_character_id].append(s)

        knowledge = SecretKnowledge.query.join(Secret).filter(Secret.session_id == session.id).all()
        self.knowers = defaultdict(list)    # secret id -> [knower character ids]
        for k in knowledge:
            self.knowers[k.secret_id].append(k.knower_character_id)
        for ids in self.knowers.values():
            ids.sort()
        self.known_by = defaultdict(list)   # character id -> [secrets they know]
        for s in self.secrets:
            for cid in self.knowers.get(s.id, []):
                self.known_by[cid].append(s)
        self.trail_knower_ids = {cid for s in self.secrets if s.is_killer_trail for cid in self.knowers.get(s.id, [])}

        self.relationships = sorted(Relationship.query.filter_by(session_id=session.id).all(),
                                    key=lambda r: (r.source_character_id, r.target_character_id))
        self.rel_by_id = {r.id: r for r in self.relationships}
        self.edges_by_char = defaultdict(list)
        for r in self.relationships:
            self.edges_by_char[r.source_character_id].append(r)
            self.edges_by_char[r.target_character_id].append(r)

    def name(self, char_id):
        c = self.char_by_id.get(char_id)
        return (c.display_name if c else None) or 'Unknown'

    def tension_target(self):
        return math.ceil(len(self.characters) / 3)

def _load_scenario(session):
    base_dir = get_base_data_dir()
    with open(os.path.join(base_dir, 'scenarioPacks', session.scenario_pack_id + '.json'), 'r', encoding='utf-8') as f:
        scenario = json.load(f)
    return {
        "victim_name": scenario.get('victim_name') or scenario.get('victim', {}).get('name') or 'Director Kessler Voss',
        "victim_title": scenario.get('victim_title') or scenario.get('victim', {}).get('roleInWorld') or 'Executive',
        "locations": scenario.get('locations') or scenario.get('setting', {}).get('locations') or [scenario.get('setting', {}).get('locationName', 'The Voss Estate')],
    }

def normalize_budget(drafts, is_killer):
    """
    Deterministic per-character point normalizer. Mutates draft.points and returns
    the list of adjustments made.
    1. At most one 3-pointer. The killer's frame keeps its 3; otherwise the 3 stays on
       the highest-severity secret objective. Every other 3 drops to 2.
    2. Total lands in 8-12. Below 8: promote the lowest-point objectives by +1 (never
       above 2 while a 3 exists; with no 3, one protect/exploit may go to 3).
       Above 12: demote the highest, never below 1.
    find_killer / hide_evidence / frame keep their fixed values.
    """
    adjustments = []
    ordered = sorted(drafts, key=lambda d: d.slot)

    def set_points(d, new_points, reason):
        adjustments.append({
            "objective_id": d.id, "character_id": d.character_id, "kind": d.kind,
            "before": d.points, "after": new_points, "reason": reason
        })
        d.points = new_points

    def total():
        return sum(d.points for d in ordered)

    threes = [d for d in ordered if d.points == 3]
    if len(threes) > 1:
        frames = [d for d in threes if d.kind == 'frame']
        if is_killer and frames:
            keeper = frames[0]
        else:
            keeper = max(threes, key=lambda d: (d.severity or 0, -d.slot))
        for d in threes:
            if d is not keeper:
                set_points(d, 2, "at_most_one_three_pointer")

    adjustable = [d for d in ordered if d.kind not in FIXED_POINT_KINDS]

    while total() < MIN_TOTAL_POINTS:
        low = [d for d in adjustable if d.points < 2]
        if low:
            d = min(low, key=lambda x: (x.points, x.slot))
            set_points(d, d.points + 1, "total_below_minimum")
            continue
        if not any(d.points == 3 for d in ordered):
            promotable = [d for d in adjustable if d.kind in SECRET_KINDS and d.points == 2]
            if promotable:
                d = max(promotable, key=lambda x: (x.severity or 0, -x.slot))
                set_points(d, 3, "total_below_minimum")
                continue
        break

    while total() > MAX_TOTAL_POINTS:
        high = [d for d in adjustable if d.points > 1]
        if not high:
            break
        d = max(high, key=lambda x: (x.points, x.slot))
        set_points(d, d.points - 1, "total_above_maximum")

    return adjustments

def _build_drafts(session, world, tpl, scenario, role_tags, synonym_map, rng):
    """One generation attempt, entirely in memory. Returns (drafts, report)."""
    ctx = SessionContext()
    killer = world.killer
    labels = tpl["category_labels"]
    char_tags = {c.id: set(get_role_tags_flat(c.role_key, role_tags)) for c in world.characters}

    drafts = []
    by_char = {c.id: [] for c in world.characters}
    used_templates = {c.id: set() for c in world.characters}
    fallbacks = []

    order = list(world.characters)
    rng.shuffle(order)
    flavor_slot = {c.id: rng.random() < FLAVOR_SLOT_P for c in order}

    def pick(pool, character, kind, exclude_ids=None, boost=None):
        """Standard strict-then-relaxed draw. Returns (template, trace)."""
        exclude_ids = used_templates[character.id] if exclude_ids is None else exclude_ids
        tags = char_tags[character.id]
        candidates = pick_template_strict(pool, character, tags, ctx, exclude_ids, set(), synonym_map)
        if boost:
            candidates = [(t, w * boost(t)) for t, w in candidates]
        relaxed_step = None
        if candidates:
            t = weighted_choice(candidates, rng=rng)
        else:
            before = len(ctx.relaxed)
            t = pick_template_relaxed(pool, character, tags, ctx, kind, exclude_ids, synonym_map, rng=rng)
            if len(ctx.relaxed) > before:
                relaxed_step = ctx.relaxed[-1]["step"]
        weight = calculate_candidate_weight(t, character, tags, ctx, synonym_map)
        ctx.use_count[t["id"]] += 1
        return t, {"selection_weight": round(weight, 4), "relaxed_step": relaxed_step}

    def render(template, field_name, kind, owner, target=None, label=None, location=None):
        return resolve_objective_template(
            template.get(field_name), template["id"], kind, owner, target=target,
            victim_name=scenario["victim_name"], victim_title=scenario["victim_title"],
            location=location, label=label)

    def add(owner, kind, slot, template, text, hint, points, trace, source, **extra):
        d = ObjectiveDraft(character_id=owner.id, kind=kind, slot=slot, template_id=template["id"],
                           points=points, text=text, gm_hint=hint, **extra)
        d.meta = dict(trace, source=source, pool=tpl["template_pool_source"].get(template["id"]), points_initial=points)
        drafts.append(d)
        by_char[owner.id].append(d)
        used_templates[owner.id].add(template["id"])
        return d

    def pick_secret_template(kind, owner, secret):
        """Category-specific template when one fits the secret, else a generic one."""
        fit = [t for t in tpl[kind] if secret.kind in t.get("matchSecretKinds", [])]
        specific = [t for t in fit if t.get("matchCategories") and secret.category in t["matchCategories"]]
        generic = [t for t in fit if not t.get("matchCategories")]
        tags = char_tags[owner.id]
        if specific and pick_template_strict(specific, owner, tags, ctx, used_templates[owner.id], set(), synonym_map):
            return pick(specific, owner, kind)
        if generic and pick_template_strict(generic, owner, tags, ctx, used_templates[owner.id], set(), synonym_map):
            return pick(generic, owner, kind)
        return pick(specific + generic, owner, kind)

    def add_simple(owner, kind, slot, source, target=None, **extra):
        """flavor / find_killer / hide_evidence / frame: fixed-point templates."""
        t, trace = pick(tpl[kind], owner, kind)
        location = rng.choice(scenario["locations"])
        text = render(t, "templateText", kind, owner, target=target, location=location)
        hint = render(t, "templateHint", kind, owner, target=target, location=location)
        return add(owner, kind, slot, t, text, hint, t["points"], trace, source, **extra)

    def link(a, b, link_type):
        a.linked_objective_id, b.linked_objective_id = b.id, a.id
        a.link_type = b.link_type = link_type

    # Pass 1: Protect. One of the character's own self/lie secrets, never core or trail.
    protect_by_char = {}
    for c in order:
        own = [s for s in world.secrets_by_subject[c.id]
               if s.kind in ('self', 'lie') and not s.is_killer_core and not s.is_killer_trail]
        if not own:
            raise ValueError(f"Character '{world.name(c.id)}' has no self or lie secret to protect")
        # Prefer secrets someone else knows: that is what makes protecting them a game.
        known = [s for s in own if world.knowers.get(s.id)]
        candidates = known or own
        secret = rng.choices(candidates, weights=[s.severity for s in candidates], k=1)[0]
        t, trace = pick_secret_template('protect_secret', c, secret)
        label = secret_label(secret.category, labels)
        location = rng.choice(scenario["locations"])
        # No target in the context: the subject cannot see who knows their secret.
        text = render(t, "templateText", 'protect_secret', c, label=label, location=location)
        hint = render(t, "templateHint", 'protect_secret', c, label=label, location=location)
        protect_by_char[c.id] = add(c, 'protect_secret', SLOT_PROTECT, t, text, hint, secret.severity, trace,
                                    "known_by_others" if known else "unknown_to_others",
                                    related_secret_id=secret.id, severity=secret.severity)

    exploit_by_char = {}

    def add_exploit(owner, secret, source):
        subject = world.char_by_id[secret.subject_character_id]
        t, trace = pick_secret_template('exploit_secret', owner, secret)
        label = secret_label(secret.category, labels)
        location = rng.choice(scenario["locations"])
        text = render(t, "templateText", 'exploit_secret', owner, target=subject, label=label, location=location)
        hint = render(t, "templateHint", 'exploit_secret', owner, target=subject, label=label, location=location)
        d = add(owner, 'exploit_secret', SLOT_EXPLOIT, t, text, hint, secret.severity, trace, source,
                related_secret_id=secret.id, related_character_id=subject.id, severity=secret.severity)
        exploit_by_char[owner.id] = d
        return d

    # Pass 2: Exploit weaving. A knower of a protected secret gets the opposing objective.
    for c in order:
        protect = protect_by_char[c.id]
        knowers = world.knowers.get(protect.related_secret_id, [])
        if not knowers or rng.random() >= WEAVE_P:
            continue
        # The killer has no exploit slot.
        open_knowers = [k for k in knowers if k != killer.id and k not in exploit_by_char]
        if not open_knowers:
            continue
        fewest = min(len(by_char[k]) for k in open_knowers)
        tied = [k for k in open_knowers if len(by_char[k]) == fewest]
        knower = world.char_by_id[rng.choice(tied)]
        exploit = add_exploit(knower, world.secret_by_id[protect.related_secret_id], "weave")
        link(protect, exploit, 'opposed')

    # Pass 3: Remaining exploit slots, from other secrets the character knows.
    # Core and trail secrets are excluded: an exploit on a trail fact would point
    # its holder straight at the killer.
    for c in order:
        if c.id == killer.id or c.id in exploit_by_char:
            continue
        eligible = [s for s in world.known_by.get(c.id, [])
                    if not s.is_killer_core and not s.is_killer_trail and s.subject_character_id != c.id]
        if eligible:
            secret = rng.choices(eligible, weights=[s.severity for s in eligible], k=1)[0]
            add_exploit(c, secret, "known_secret")
        else:
            add_simple(c, 'flavor', SLOT_EXPLOIT, "exploit_fallback")
            fallbacks.append({"character_id": c.id, "slot": "exploit_secret", "reason": "no eligible known secret"})

    # Pass 4: Relationship pairing. Greedy over shuffled edges, both endpoints open.
    rel_done = set()
    used_edges = set()
    opposed_pairs = sum(1 for d in drafts if d.link_type == 'opposed') // 2
    tension_target = world.tension_target()

    def add_relationship(owner, other, rel, template, side, trace, source):
        location = rng.choice(scenario["locations"])
        text = render(template, "template" + side, 'relationship', owner, target=other, location=location)
        hint = render(template, "hint" + side, 'relationship', owner, target=other, location=location)
        rel_done.add(owner.id)
        used_edges.add(rel.id)
        return add(owner, 'relationship', SLOT_RELATIONSHIP, template, text, hint, template["points" + side],
                   trace, source, related_character_id=other.id, related_relationship_id=rel.id)

    edges = list(world.relationships)
    rng.shuffle(edges)
    for rel in edges:
        if rel.source_character_id in rel_done or rel.target_character_id in rel_done:
            continue
        pool = [t for t in tpl["relationship"]
                if rel.rel_type in t["relTypes"] and t.get("templateSource") and t.get("templateTarget")]
        if not pool:
            continue
        source = world.char_by_id[rel.source_character_id]
        target = world.char_by_id[rel.target_character_id]
        # Gates and weights are scored against the source character; while the session is
        # short of opposed pairs, opposed templates are favored.
        boost = (lambda t: OPPOSED_BOOST if t["link"] == 'opposed' else 1.0) if opposed_pairs < tension_target else None
        t, trace = pick(pool, source, 'relationship',
                        exclude_ids=used_templates[source.id] | used_templates[target.id], boost=boost)
        d_source = add_relationship(source, target, rel, t, "Source", trace, "paired_edge")
        d_target = add_relationship(target, source, rel, t, "Target", trace, "paired_edge")
        if t["link"] in LINK_TYPES:
            link(d_source, d_target, t["link"])
            if t["link"] == 'opposed':
                opposed_pairs += 1

    # Anyone left takes a single-sided objective from an incident edge, unused edges first.
    for c in order:
        if c.id in rel_done:
            continue
        incident = list(world.edges_by_char.get(c.id, []))
        rng.shuffle(incident)
        incident.sort(key=lambda r: r.id in used_edges)   # stable: unused edges first
        placed = False
        for rel in incident:
            side = "Source" if rel.source_character_id == c.id else "Target"
            pool = [t for t in tpl["relationship"] if rel.rel_type in t["relTypes"] and t.get("template" + side)]
            if not pool:
                continue
            other_id = rel.target_character_id if side == "Source" else rel.source_character_id
            t, trace = pick(pool, c, 'relationship')
            add_relationship(c, world.char_by_id[other_id], rel, t, side, trace, "single_edge")
            placed = True
            break
        if not placed:
            add_simple(c, 'flavor', SLOT_RELATIONSHIP, "relationship_fallback")
            fallbacks.append({"character_id": c.id, "slot": "relationship", "reason": "no usable relationship edge"})

    # Pass 5: Flavor, where the slot rolled.
    for c in order:
        if flavor_slot[c.id]:
            add_simple(c, 'flavor', SLOT_FLAVOR, "flavor_slot")

    # Pass 6: Generic / killer objectives.
    scapegoat_report = None
    for c in order:
        if c.id != killer.id:
            add_simple(c, 'find_killer', SLOT_GENERIC, "generic")
            continue

        add_simple(c, 'hide_evidence', SLOT_GENERIC, "killer")

        # Scapegoat: a non-killer who does not hold a trail fact.
        candidates = [o for o in world.characters if o.id != killer.id and o.id not in world.trail_knower_ids]
        if not candidates:
            raise ValueError("No valid scapegoat: every non-killer holds a killer-trail fact")
        killer_neighbors = {r.target_character_id if r.source_character_id == killer.id else r.source_character_id
                            for r in world.edges_by_char.get(killer.id, [])}
        weights = []
        candidate_report = []
        for o in candidates:
            tie_severity = max([s.severity for s in world.secrets_by_subject[o.id] if s.kind == 'victim_tie'] or [1])
            shares_edge = o.id in killer_neighbors
            w = (tie_severity ** 2) * (1.5 if shares_edge else 1.0)
            weights.append(w)
            candidate_report.append({"character_id": o.id, "display_name": name_token(o.id),
                                     "victim_tie_severity": tie_severity, "shares_edge_with_killer": shares_edge,
                                     "weight": w})
        scapegoat = rng.choices(candidates, weights=weights, k=1)[0]
        add_simple(c, 'frame', SLOT_FRAME, "killer", target=scapegoat, related_character_id=scapegoat.id)
        scapegoat_report = {"killer_id": killer.id, "chosen_character_id": scapegoat.id,
                            "chosen_name": name_token(scapegoat.id), "candidates": candidate_report}

    # Pass 7: Budget normalization.
    adjustments = []
    for c in world.characters:
        adjustments.extend(normalize_budget(by_char[c.id], c.id == killer.id))

    # Pass 8: sort_order (points desc, then slot order).
    for c in world.characters:
        for idx, d in enumerate(sorted(by_char[c.id], key=lambda d: (-d.points, d.slot))):
            d.sort_order = idx

    by_id = {d.id: d for d in drafts}
    linked_pairs = []
    for d in drafts:
        if d.linked_objective_id and d.id < d.linked_objective_id:
            other = by_id[d.linked_objective_id]
            linked_pairs.append({"link_type": d.link_type, "kind": d.kind,
                                 "objective_ids": [d.id, other.id],
                                 "character_ids": [d.character_id, other.character_id]})

    report = {
        "session_id": session.id,
        "objectives": [{
            "objective_id": d.id,
            "character_id": d.character_id,
            "kind": d.kind,
            "template_id": d.template_id,
            "pool": d.meta.get("pool"),
            "source": d.meta.get("source"),
            "relaxed_step": d.meta.get("relaxed_step"),
            "selection_weight": d.meta.get("selection_weight"),
            "points_initial": d.meta.get("points_initial"),
            "points": d.points,
        } for d in drafts],
        "relaxed": ctx.relaxed,
        "fallbacks": fallbacks,
        "adjustments": adjustments,
        "linked_pairs": linked_pairs,
        "scapegoat": scapegoat_report,
    }
    return drafts, report

def _check_objectives(rows, world, killer_only_template_ids):
    """
    The §7 invariants. `rows` may be ObjectiveDraft objects or persisted Objective
    rows (they share attribute names), so the same check runs before and after
    persisting. Raises ValueError naming the first invariant that fails.
    """
    killer = world.killer
    if killer is None:
        raise ValueError("Exactly one character must have is_killer=True")

    by_id = {o.id: o for o in rows}
    by_char = defaultdict(list)
    for o in rows:
        if o.character_id not in world.char_by_id:
            raise ValueError(f"Objective '{o.id}' belongs to a character outside the session")
        by_char[o.character_id].append(o)

    for c in world.characters:
        name = world.name(c.id)
        objs = by_char.get(c.id, [])
        if not (MIN_OBJECTIVES <= len(objs) <= MAX_OBJECTIVES):
            raise ValueError(f"Character '{name}' has {len(objs)} objectives (expected {MIN_OBJECTIVES}-{MAX_OBJECTIVES})")
        for o in objs:
            if o.kind not in OBJECTIVE_KINDS:
                raise ValueError(f"Objective '{o.id}' on '{name}' has invalid kind '{o.kind}'")
            if not isinstance(o.points, int) or not (1 <= o.points <= 3):
                raise ValueError(f"Objective '{o.id}' on '{name}' has points {o.points!r} outside 1-3")
        total = sum(o.points for o in objs)
        if not (MIN_TOTAL_POINTS <= total <= MAX_TOTAL_POINTS):
            raise ValueError(f"Character '{name}' has a point total of {total} (expected {MIN_TOTAL_POINTS}-{MAX_TOTAL_POINTS})")
        if len([o for o in objs if o.points == 3]) > 1:
            raise ValueError(f"Character '{name}' has more than one 3-point objective")

        counts = defaultdict(int)
        for o in objs:
            counts[o.kind] += 1
        if c.id == killer.id:
            if counts['find_killer'] != 0:
                raise ValueError("The killer must not have a find_killer objective")
            if counts['hide_evidence'] != 1 or counts['frame'] != 1:
                raise ValueError("The killer must have exactly one hide_evidence and exactly one frame objective")
            frame = next(o for o in objs if o.kind == 'frame')
            scapegoat = world.char_by_id.get(frame.related_character_id)
            if scapegoat is None or scapegoat.id == killer.id:
                raise ValueError("The frame objective's scapegoat must be a non-killer character")
            if scapegoat.id in world.trail_knower_ids:
                raise ValueError("The frame objective's scapegoat must not be a trail knower")
        else:
            if counts['find_killer'] != 1:
                raise ValueError(f"Non-killer '{name}' must have exactly one find_killer objective")
            if counts['hide_evidence'] or counts['frame']:
                raise ValueError(f"Non-killer '{name}' has a killer-only objective")
            for o in objs:
                if o.template_id in killer_only_template_ids:
                    raise ValueError(f"Non-killer '{name}' has an objective from a killer-only pool ('{o.template_id}')")

        texts = set()
        keys = set()
        for o in objs:
            if o.text in texts:
                raise ValueError(f"Character '{name}' has two objectives with the same text: '{o.text}'")
            texts.add(o.text)
            if o.template_id:
                key = (o.template_id, o.related_character_id, o.related_secret_id)
                if key in keys:
                    raise ValueError(f"Character '{name}' has a duplicate objective for template '{o.template_id}'")
                keys.add(key)

    for o in rows:
        name = world.name(o.character_id)
        if not o.text:
            raise ValueError(f"Objective '{o.id}' on '{name}' has no text")
        if '#' in o.text or (o.gm_hint and '#' in o.gm_hint):
            raise ValueError(f"Unresolved placeholder in objective '{o.id}' (template '{o.template_id}')")

        if o.related_character_id:
            if o.related_character_id == o.character_id:
                raise ValueError(f"Objective '{o.id}' on '{name}' references its own owner as related character")
            if o.related_character_id not in world.char_by_id:
                raise ValueError(f"Objective '{o.id}' on '{name}' references a character outside the session")

        if o.kind in SECRET_KINDS and not o.related_secret_id:
            raise ValueError(f"Objective '{o.id}' on '{name}' is a {o.kind} without a secret")
        if o.related_secret_id:
            secret = world.secret_by_id.get(o.related_secret_id)
            if secret is None:
                raise ValueError(f"Objective '{o.id}' on '{name}' references a secret outside the session")
            if secret.is_killer_core or secret.is_killer_trail:
                raise ValueError(f"Objective '{o.id}' on '{name}' references a killer core/trail secret")
            if secret.subject_character_id == o.character_id:
                if o.kind != 'protect_secret':
                    raise ValueError(f"Objective '{o.id}' on '{name}' references the owner's own secret but is not a protect_secret")
            elif o.character_id in world.knowers.get(secret.id, []):
                if o.kind != 'exploit_secret':
                    raise ValueError(f"Objective '{o.id}' on '{name}' references a known secret but is not an exploit_secret")
                if o.related_character_id != secret.subject_character_id:
                    raise ValueError(f"Exploit objective '{o.id}' on '{name}' must reference the secret's subject")
            else:
                raise ValueError(f"Objective '{o.id}' on '{name}' references a secret its owner cannot see")

        if o.related_relationship_id:
            rel = world.rel_by_id.get(o.related_relationship_id)
            if rel is None:
                raise ValueError(f"Objective '{o.id}' on '{name}' references a relationship outside the session")
            endpoints = {rel.source_character_id, rel.target_character_id}
            if o.character_id not in endpoints:
                raise ValueError(f"Objective '{o.id}' on '{name}' references a relationship its owner is not part of")
            if o.related_character_id not in endpoints:
                raise ValueError(f"Objective '{o.id}' on '{name}' names a character outside its relationship")

        if o.kind == 'protect_secret':
            # The subject cannot see who knows their secret, and the objective must not leak it.
            if o.related_character_id:
                raise ValueError(f"Protect objective '{o.id}' on '{name}' must not reference another character")
            for knower_id in world.knowers.get(o.related_secret_id, []):
                knower_token = name_token(knower_id)
                if knower_token in (o.text or '') or knower_token in (o.gm_hint or ''):
                    raise ValueError(f"Protect objective '{o.id}' on '{name}' names a knower of the secret")

        if o.linked_objective_id:
            partner = by_id.get(o.linked_objective_id)
            if partner is None:
                raise ValueError(f"Objective '{o.id}' links to a missing objective")
            if partner.linked_objective_id != o.id:
                raise ValueError(f"Objective '{o.id}' has a link that is not mutual")
            if partner.character_id == o.character_id:
                raise ValueError(f"Objective '{o.id}' is linked to another objective on the same character")
            if o.link_type not in LINK_TYPES or partner.link_type != o.link_type:
                raise ValueError(f"Objective '{o.id}' has a link without a valid shared link_type")
        elif o.link_type:
            raise ValueError(f"Objective '{o.id}' has a link_type but no linked objective")

    opposed_pairs = len([o for o in rows if o.linked_objective_id and o.link_type == 'opposed']) // 2
    target = world.tension_target()
    if opposed_pairs < target:
        raise ValueError(f"Tension coverage: only {opposed_pairs} opposed pair(s), need at least {target}")

    return True

def _killer_only_template_ids(tpl):
    return {t["id"] for kind in KILLER_ONLY_KINDS for t in tpl[kind]}

def verify_objectives(session):
    """
    Validates the persisted objectives of a freshly generated session against the
    gameplay and knowledge-safety invariants. Manual edits are free-form, so this is
    only meaningful right after generation.
    """
    world = _World(session)
    tpl = load_all_objective_templates()
    rows = Objective.query.filter_by(session_id=session.id).all()
    return _check_objectives(rows, world, _killer_only_template_ids(tpl))

def _generate_objectives(session, rng=None, commit=True):
    if rng is None:
        rng = random.Random()

    characters = Character.query.filter_by(session_id=session.id).all()
    if len(characters) < MIN_CHARACTERS:
        raise ValueError(f"At least {MIN_CHARACTERS} characters are required in the session to generate objectives.")

    if [c for c in characters if not c.lifepath_complete]:
        raise ValueError("Not all characters have completed their lifepath.")

    if len([c for c in characters if c.is_killer]) != 1:
        raise ValueError("Exactly one killer is required to generate objectives. Generate secrets first.")

    if not Relationship.query.filter_by(session_id=session.id).first():
        raise ValueError("Relationships must be generated before objectives.")

    if not Secret.query.filter_by(session_id=session.id).first():
        raise ValueError("Secrets must be generated before objectives.")
    verify_secrets(session)

    if Objective.query.filter_by(session_id=session.id).first():
        raise ValueError("Objectives already exist for this session.")

    base_dir = get_base_data_dir()
    with open(os.path.join(base_dir, 'roleTags.json'), 'r', encoding='utf-8') as f:
        role_tags = json.load(f)
    scenario = _load_scenario(session)
    tpl = load_all_objective_templates(base_dir)
    _, synonym_map = get_valid_lifepath_options(base_dir)
    killer_only_ids = _killer_only_template_ids(tpl)

    world = _World(session)

    # Work in memory first: build, check, and only then persist.
    drafts = None
    report = None
    failures = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            attempt_drafts, attempt_report = _build_drafts(session, world, tpl, scenario, role_tags, synonym_map, rng)
            _check_objectives(attempt_drafts, world, killer_only_ids)
            drafts, report = attempt_drafts, attempt_report
            break
        except ValueError as e:
            failures.append({"attempt": attempt, "error": str(e)})

    if drafts is None:
        raise ValueError(f"Objective generation failed after {MAX_ATTEMPTS} attempts. "
                         f"Last failing invariant: {failures[-1]['error']}")

    rows = []
    for d in drafts:
        o = Objective(
            id=d.id,
            session_id=session.id,
            character_id=d.character_id,
            template_id=d.template_id,
            kind=d.kind,
            points=d.points,
            text=d.text,
            gm_hint=d.gm_hint,
            related_character_id=d.related_character_id,
            related_secret_id=d.related_secret_id,
            related_relationship_id=d.related_relationship_id,
            sort_order=d.sort_order
        )
        db.session.add(o)
        rows.append(o)
    db.session.flush()

    # Links are set once every row exists (self-referencing FK).
    for d, o in zip(drafts, rows):
        if d.linked_objective_id:
            o.linked_objective_id = d.linked_objective_id
            o.link_type = d.link_type
    db.session.flush()

    verify_objectives(session)

    if commit:
        db.session.commit()
    else:
        db.session.flush()

    # Persist the report trace (read by the DEBUG report endpoint)
    report["attempts"] = len(failures) + 1
    report["retries"] = len(failures)
    report["failed_attempts"] = failures
    store_report(session, "objectives", report)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return rows
