"""Compose from Lifepath: suggest a description and traits from a character's Lifepath.

Pure suggestion code: nothing here touches the database. The description is written in
the second person ("You ..."), so it never contains the character's name and a later
rename cannot make it stale.
"""
import json
import os
import random
import re

import tracery

from generation.secret_templates import get_base_data_dir, assert_clean_render

COMPOSE_FIELDS = ('personality', 'clothing_style', 'hairstyle', 'affectation',
                  'value_most', 'life_goals', 'childhood_environment')
# Character column -> key in lifepathShared.json
SHARED_KEYS = {
    'personality': 'personality', 'clothing_style': 'clothingStyle', 'hairstyle': 'hairstyle',
    'affectation': 'affectation', 'value_most': 'valueMost', 'life_goals': 'lifeGoals',
    'childhood_environment': 'childhoodEnvironment',
}
SENTENCE_SLOTS = ('sentence1', 'sentence2', 'sentence3', 'sentence4')
MIN_TRAITS, MAX_TRAITS = 4, 6
_PLACEHOLDER_RE = re.compile(r'#([^#]*)#')


def load_compose_templates(data_dir=None):
    path = os.path.join(data_dir or get_base_data_dir(), 'compose', 'composeTemplates.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _placeholders(text):
    return [m for m in _PLACEHOLDER_RE.findall(text)]


def validate_compose_templates(data_dir=None):
    """Startup validation (fail loudly): every option id of the seven fields has a phrase
    and a trait, the grammar only uses known placeholders, and every sentence variant
    renders cleanly."""
    data_dir = data_dir or get_base_data_dir()
    templates = load_compose_templates(data_dir)
    with open(os.path.join(data_dir, 'lifepathShared.json'), 'r', encoding='utf-8') as f:
        shared = json.load(f)

    fields = templates.get('fields') or {}
    seen_traits = {}
    for field in COMPOSE_FIELDS:
        entries = fields.get(field)
        if not isinstance(entries, dict):
            raise ValueError(f"composeTemplates.json is missing the '{field}' table")
        option_ids = [o['id'] for o in shared[SHARED_KEYS[field]]['options']]
        for oid in option_ids:
            entry = entries.get(oid)
            if not isinstance(entry, dict):
                raise ValueError(f"composeTemplates.json: option '{oid}' of '{field}' has no entry")
            for key in ('phrase', 'trait'):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    raise ValueError(f"composeTemplates.json: option '{oid}' of '{field}' has no '{key}'")
            phrase, trait = entry['phrase'], entry['trait']
            if '#' in phrase or '{{' in phrase:
                raise ValueError(f"composeTemplates.json: phrase of '{field}.{oid}' contains a reserved character")
            if not (1 <= len(trait.split()) <= 3) or len(trait) > 60 or '#' in trait:
                raise ValueError(f"composeTemplates.json: trait of '{field}.{oid}' must be 1-3 words: '{trait}'")
            key = trait.lower()
            if key in seen_traits:
                raise ValueError(f"composeTemplates.json: trait '{trait}' is used by both "
                                 f"'{seen_traits[key]}' and '{field}.{oid}'")
            seen_traits[key] = f'{field}.{oid}'
        extra = set(entries) - set(option_ids)
        if extra:
            raise ValueError(f"composeTemplates.json: '{field}' has entries for unknown options: {sorted(extra)}")

    grammar = templates.get('descriptionGrammar')
    if not isinstance(grammar, dict) or not grammar.get('origin'):
        raise ValueError("composeTemplates.json: descriptionGrammar needs an 'origin' rule")
    known = set(COMPOSE_FIELDS) | set(SENTENCE_SLOTS)
    for symbol, rules in grammar.items():
        if symbol != 'origin' and symbol not in SENTENCE_SLOTS:
            raise ValueError(f"composeTemplates.json: unknown grammar symbol '{symbol}'")
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"composeTemplates.json: grammar symbol '{symbol}' needs a list of variants")
        for rule in rules:
            if rule.count('#') % 2:
                raise ValueError(f"composeTemplates.json: unbalanced '#' in '{rule}'")
            for ph in _placeholders(rule):
                if '.' in ph:
                    raise ValueError(f"composeTemplates.json: modifier in '{rule}' (not supported)")
                if ph not in known:
                    raise ValueError(f"composeTemplates.json: unknown placeholder '#{ph}#' in '{rule}'")
    for slot in SENTENCE_SLOTS:
        if len(grammar.get(slot, [])) < 2:
            raise ValueError(f"composeTemplates.json: '{slot}' needs at least 2 variants")

    # Render every sentence variant and origin with dummy phrases
    dummy = {field: ['DummyPhrase'] for field in COMPOSE_FIELDS}
    for slot in SENTENCE_SLOTS:
        for variant in grammar[slot]:
            g = tracery.Grammar({**dummy, 'origin': variant})
            rendered = g.flatten('#origin#')
            assert_clean_render(rendered, slot, 'description sentence')
            if not rendered.strip().endswith('.'):
                raise ValueError(f"composeTemplates.json: sentence variant must end with a period: '{variant}'")
    for origin in grammar['origin']:
        count = len([ph for ph in _placeholders(origin) if ph in SENTENCE_SLOTS])
        if not 3 <= count <= 4:
            raise ValueError(f"composeTemplates.json: an origin must use 3-4 sentences: '{origin}'")
    return True


def compose_suggestion(character, rng=None, templates=None):
    """{'description': str, 'traits': [str, ...]} for one character. A seeded rng makes
    the result reproducible (variants are picked here, not by Tracery's global random)."""
    rng = rng or random.Random()
    templates = templates or load_compose_templates()
    fields = templates['fields']
    grammar = templates['descriptionGrammar']

    entries = {}
    for field in COMPOSE_FIELDS:
        table = fields[field]
        # An unknown stored value (legacy data) falls back to a random option instead of failing
        entries[field] = table.get(getattr(character, field) or '') or table[rng.choice(sorted(table))]

    rules = {field: [entries[field]['phrase']] for field in COMPOSE_FIELDS}
    for slot in SENTENCE_SLOTS:
        rules[slot] = [rng.choice(grammar[slot])]
    rules['origin'] = [rng.choice(grammar['origin'])]
    description = tracery.Grammar(rules).flatten('#origin#').strip()
    assert_clean_render(description, 'compose', 'description')

    count = rng.randint(MIN_TRAITS, MAX_TRAITS)
    chosen = rng.sample(COMPOSE_FIELDS, count if count <= len(COMPOSE_FIELDS) else len(COMPOSE_FIELDS))
    traits = []
    for field in chosen:
        trait = entries[field]['trait']
        if trait not in traits:
            traits.append(trait)
    return {'description': description, 'traits': traits}
