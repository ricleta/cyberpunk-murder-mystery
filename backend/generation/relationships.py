"""
The role-affinity tag system biases the statistical likelihood and flavor of a relationship
forming between two characters based on their chosen Roles. It does not constrain player
roleplay. A player is free to build a character whose personality (via their Phase 2 Lifepath
choices) contradicts their Role's typical sociological position — e.g., a Netrunner who roleplays
as a buttoned-up corporate suit rather than a street-level hacker. The tag system only needs to
be reasonable on average across many generated sessions, not correct for every individual
character. All generated relationship text remains fully editable by the owning player and the
GM after generation, exactly as established in Phase 1's permission model.
"""

import os
import json
import random
import networkx as nx
import tracery
from tracery.modifiers import base_english
from models import db, Character, Relationship

def load_generation_data():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
    with open(os.path.join(base_dir, 'roleTags.json'), 'r') as f:
        role_tags = json.load(f)
    with open(os.path.join(base_dir, 'tagAffinity.json'), 'r') as f:
        tag_affinity = json.load(f)
    with open(os.path.join(base_dir, 'relationshipTypes.json'), 'r') as f:
        relationship_types = json.load(f)
    return role_tags, tag_affinity, relationship_types

def get_role_tags_flat(role_key: str, role_tags: dict) -> list[str]:
    tags = role_tags.get(role_key, {})
    return tags.get('sphere', []) + tags.get('disposition', []) + tags.get('transactional', [])

def compute_role_affinity(role_a_key: str, role_b_key: str, role_tags: dict, tag_affinity: dict) -> float:
    """
    Sum-then-clamp aggregation. Collects every tag from role_a's full tag set
    (sphere + disposition + transactional, flattened) paired against every tag
    from role_b's full tag set, looks up each pair's weight in tag_affinity
    (using the sorted 'tagX+tagY' key convention), sums all matches, and
    clamps the result to a ceiling of 0.95 (never a hard 1.0 -- no pairing
    is ever fully guaranteed, preserving narrative outlier possibility).

    Same-tag-to-itself pairs ARE included.
    """
    if not role_a_key or not role_b_key:
        return tag_affinity.get('defaultWeight', 0.15)
        
    tags_a = get_role_tags_flat(role_a_key, role_tags)
    tags_b = get_role_tags_flat(role_b_key, role_tags)
    
    pairs = tag_affinity.get('pairs', {})
    default_weight = tag_affinity.get('defaultWeight', 0.15)
    
    total_weight = 0.0
    for ta in tags_a:
        for tb in tags_b:
            pair_key = '+'.join(sorted([ta, tb]))
            total_weight += pairs.get(pair_key, default_weight)
            
    return min(total_weight, 0.95)

def edge_exists(graph: nx.Graph, source_id: str, target_id: str) -> bool:
    return graph.has_edge(source_id, target_id)

def build_relationship_graph(characters: list[Character], role_tags: dict, tag_affinity: dict) -> nx.Graph:
    """
    1. Create an nx.Graph with one node per character.
    2. Compute pairwise affinity for every possible character pair.
    3. Build a randomized spanning tree using weighted random sampling.
    4. Degree augmentation pass (degree >= 2).
    5. Add flavor edges.
    """
    G = nx.Graph()
    for c in characters:
        G.add_node(c.id, role_key=c.role_key)
        
    if len(characters) < 2:
        return G
        
    # Calculate all pair affinities
    nodes = list(G.nodes)
    affinities = {}
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            n1 = nodes[i]
            n2 = nodes[j]
            r1 = G.nodes[n1]['role_key']
            r2 = G.nodes[n2]['role_key']
            w = compute_role_affinity(r1, r2, role_tags, tag_affinity)
            affinities[(n1, n2)] = w
            affinities[(n2, n1)] = w
            
    # 3. Randomized spanning tree
    tree = nx.Graph()
    tree.add_node(nodes[0])
    
    unconnected = set(nodes[1:])
    while unconnected:
        candidates = []
        for u in tree.nodes:
            for v in unconnected:
                candidates.append((u, v, affinities[(u, v)]))
        
        if not candidates:
            break
            
        weights = [c[2] for c in candidates]
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        tree.add_edge(chosen[0], chosen[1])
        tree.add_node(chosen[1])
        unconnected.remove(chosen[1])
        
    G.add_edges_from(tree.edges())
    
    # 4. Degree augmentation pass (degree >= 2)
    if len(characters) >= 3:
        for n in list(G.nodes):
            if G.degree(n) < 2:
                candidates = []
                for other in G.nodes:
                    if other != n and not G.has_edge(n, other):
                        candidates.append((other, affinities[(n, other)]))
                
                if candidates:
                    weights = [c[1] for c in candidates]
                    chosen_other = random.choices(candidates, weights=weights, k=1)[0][0]
                    G.add_edge(n, chosen_other)
                    
    # 5. Flavor edges
    n_chars = len(characters)
    max_extra = n_chars // 3
    if max_extra > 0:
        extra_edges_added = 0
        possible_new_edges = []
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                if not G.has_edge(nodes[i], nodes[j]):
                    possible_new_edges.append((nodes[i], nodes[j], affinities[(nodes[i], nodes[j])]))
        
        while extra_edges_added < max_extra and possible_new_edges:
            weights = [c[2] for c in possible_new_edges]
            chosen = random.choices(possible_new_edges, weights=weights, k=1)[0]
            G.add_edge(chosen[0], chosen[1])
            possible_new_edges.remove(chosen)
            extra_edges_added += 1

    return G

def verify_graph(graph: nx.Graph, characters: list[Character]) -> None:
    if not characters:
        return
    if len(characters) != len(graph.nodes):
        raise ValueError("Graph node count does not match character count")
    if len(characters) >= 3:
        for n in graph.nodes:
            if graph.degree(n) < 2:
                raise ValueError(f"Node {n} has degree < 2")
    if len(characters) > 1 and not nx.is_connected(graph):
        raise ValueError("Graph is not fully connected")

def assign_relationship_types(graph: nx.Graph, characters_by_id: dict, relationship_types: dict, role_tags: dict) -> list[dict]:
    edges_info = []
    
    universal_types = relationship_types.get("universal", [])
    role_shaped_types = relationship_types.get("roleShaped", [])
    
    for u, v in graph.edges():
        c_u = characters_by_id[u]
        c_v = characters_by_id[v]
        
        tags_u = set(get_role_tags_flat(c_u.role_key, role_tags))
        tags_v = set(get_role_tags_flat(c_v.role_key, role_tags))
        union_tags = tags_u.union(tags_v)
        
        candidates = []
        weights = []
        
        for ut in universal_types:
            candidates.append(ut)
            weights.append(1.0)
            
        for rt in role_shaped_types:
            bias_tags = set(rt.get("biasTags", []))
            overlap = union_tags.intersection(bias_tags)
            if overlap:
                candidates.append(rt)
                weights.append(float(len(overlap)))
                
        chosen_type = random.choices(candidates, weights=weights, k=1)[0]
        
        edges_info.append({
            "source_id": u,
            "target_id": v,
            "rel_type": chosen_type["id"],
            "is_symmetric": chosen_type.get("symmetric", False),
            "chosen_type_data": chosen_type
        })
        
    return edges_info

def generate_relationship_text(rel_type_entry: dict, source_char: Character, target_char: Character) -> tuple[str, str]:
    flavor_list = [
        "though you rarely speak of it",
        "a bond neither of you takes lightly",
        "for better or worse",
        "it's complicated",
        "a well-kept secret"
    ]
    
    grammar_source = {
        "origin": rel_type_entry.get("templateSource", ""),
        "targetName": target_char.display_name or "Unknown",
        "sourceName": source_char.display_name or "Unknown",
        "flavor": flavor_list
    }
    
    flattened_source = tracery.Grammar(grammar_source).flatten("#origin#")
    
    if rel_type_entry.get("symmetric"):
        flattened_target = flattened_source
    else:
        grammar_target = {
            "origin": rel_type_entry.get("templateTarget", ""),
            "targetName": source_char.display_name or "Unknown",
            "sourceName": target_char.display_name or "Unknown",
            "flavor": flavor_list
        }
        flattened_target = tracery.Grammar(grammar_target).flatten("#origin#")
        
    return flattened_source, flattened_target

def _generate_relationships(session) -> list[Relationship]:
    characters = Character.query.filter_by(session_id=session.id).all()
    
    incomplete = [c.player_id for c in characters if not c.lifepath_complete or not c.role_key]
    if incomplete:
        raise ValueError("Not all characters have completed their lifepath or selected a role.")
        
    existing = Relationship.query.filter_by(session_id=session.id).all()
    for r in existing:
        db.session.delete(r)
    db.session.commit()
        
    role_tags, tag_affinity, relationship_types = load_generation_data()
    
    graph = build_relationship_graph(characters, role_tags, tag_affinity)
    verify_graph(graph, characters)
    
    char_by_id = {c.id: c for c in characters}
    edges_info = assign_relationship_types(graph, char_by_id, relationship_types, role_tags)
    
    created_relationships = []
    
    for edge in edges_info:
        source_char = char_by_id[edge["source_id"]]
        target_char = char_by_id[edge["target_id"]]
        
        # Check DB level existence just in case
        existing = Relationship.query.filter(
            ((Relationship.source_character_id == source_char.id) & (Relationship.target_character_id == target_char.id)) |
            ((Relationship.source_character_id == target_char.id) & (Relationship.target_character_id == source_char.id))
        ).first()
        
        if existing:
            continue
            
        desc_source, desc_target = generate_relationship_text(edge["chosen_type_data"], source_char, target_char)
        
        # Randomize direction for asymmetric relationships
        if not edge["is_symmetric"] and random.choice([True, False]):
            # Swap source and target
            source_char, target_char = target_char, source_char
            desc_source, desc_target = desc_target, desc_source
        
        rel = Relationship(
            session_id=session.id,
            source_character_id=source_char.id,
            target_character_id=target_char.id,
            rel_type=edge["rel_type"],
            is_symmetric=edge["is_symmetric"],
            description_source=desc_source,
            description_target=desc_target
        )
        db.session.add(rel)
        created_relationships.append(rel)
        
    db.session.commit()
    return created_relationships
