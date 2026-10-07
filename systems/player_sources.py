"""Host-authored input provenance, never a declaration of world truth.

These records establish only that a bound player submitted literal input and a
response committed. They establish neither success nor speech/hearing/knowledge.
"""
from __future__ import annotations

import copy
import re

from kernel.events import kernel_event

OUTCOME = 'committed_response_not_proof_of_success'
SUMMARY = 'player input source recorded'
DELTA_KEYS = {'actor_id', 'input', 'requested_at', 'committed_at', 'outcome',
              'entity_refs', 'narration_ref', 'effect_refs'}
CONTEXT_KEYS = {'day', 'band', 'scene', 'location'}
LABEL_FIELDS = {'name', 'display_name', '真名', '别名', 'alias', 'aliases'}
# Only typed foreground references, never arbitrary prose, values, or bystanders.
SECTION_REFS = {
    'entities': ('id',), 'facts': ('subject',), 'relations': ('src', 'dst'),
    'places': ('id', 'parent'), 'moves': ('who', 'to'), 'links': ('a', 'b'),
    'materialize': ('id',), 'cast': ('id', 'toward'),
    'items': ('id', 'item', 'from', 'to'),
    'factions': ('id', 'person', 'faction'), 'knowledge': ('knower',),
    'quests': ('anchor', 'l3_anchor'), 'world': ('areas',),
}


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _context_valid(value):
    return (isinstance(value, dict) and set(value) == CONTEXT_KEYS
            and type(value['day']) is int and value['day'] >= 0
            and type(value['band']) is int and 0 <= value['band'] < 4
            and _identifier(value['scene'])
            and (value['location'] is None or _identifier(value['location'])))


def _identifiers(value):
    return (isinstance(value, list) and all(_identifier(item) for item in value)
            and len(value) == len(set(value)))


def valid_player_input(event):
    """Strict new-event contract; historical narrative events need no retrofit."""
    if not isinstance(event, dict) or event.get('type') != 'player_input_recorded':
        return False
    data = event.get('deltas')
    if not isinstance(data, dict) or set(data) != DELTA_KEYS:
        return False
    if not (_identifier(event.get('id')) and type(event.get('turn')) is int
            and event['turn'] > 0 and _identifier(data['actor_id'])
            and isinstance(data['input'], str)
            and _context_valid(data['requested_at'])
            and _context_valid(data['committed_at'])
            and data['outcome'] == OUTCOME
            and _identifiers(data['entity_refs'])
            and data['actor_id'] not in data['entity_refs']
            and _identifiers(data['effect_refs'])
            and (data['narration_ref'] is None or _identifier(data['narration_ref']))):
        return False
    committed = data['committed_at']
    return (event.get('summary') == SUMMARY and event.get('actors') == []
            and type(event.get('day')) is int and event['day'] == committed['day']
            and event.get('scene') == committed['scene'])


def source_context(world, actor_id):
    """Capture authoritative clock and physical location, not scene hints."""
    meta = world.get('meta', {})
    day = meta.get('day') if meta.get('day') is not None else 1
    graph = world.get('systems', {}).get('ontology')
    locations = graph.neighbors(actor_id, 'located_in', day) if graph else []
    return {'day': day, 'band': meta.get('band') or 0,
            'scene': meta.get('scene') or 'scene',
            'location': locations[0] if locations else None}


def visible_source_entities(world, actor_id):
    """Independent actor POV graph using canonical co-presence, never listeners."""
    from context.access import pov_world
    graph = world.get('systems', {}).get('ontology')
    entity = graph.get_entity(actor_id) if graph and isinstance(actor_id, str) else None
    if entity is None or entity.etype != 'Person':
        return None
    context = source_context(world, actor_id)
    scene = {**context, 'protagonist': actor_id, 'present': []}
    return pov_world(world, scene, pov=actor_id)['systems']['ontology']


def source_match_span(text, label):
    """Case-insensitive literal match with offsets into the original Unicode text."""
    if not isinstance(text, str) or not isinstance(label, str) or not label:
        return None
    folded, positions = [], []
    for index, char in enumerate(text):
        fragment = char.casefold()
        folded.append(fragment)
        positions.extend([index] * len(fragment))
    haystack, needle = ''.join(folded), label.casefold()
    latin = re.fullmatch(r"[\w -]+", label, re.ASCII) is not None
    offset = 0
    while (found := haystack.find(needle, offset)) >= 0:
        stop = found + len(needle)
        # Never select only half of an expanded character such as ß -> ss.
        aligned = ((found == 0 or positions[found - 1] != positions[found])
                   and (stop == len(positions) or positions[stop - 1] != positions[stop]))
        start, end = positions[found], positions[stop - 1] + 1
        bounded = (not latin or (
            (start == 0 or not re.match(r"\w", text[start - 1]))
            and (end == len(text) or not re.match(r"\w", text[end]))))
        if aligned and bounded:
            return start, end
        offset = found + 1
    return None


def source_mentions(text, label):
    """Use the same literal matching semantics for recording and retrieval."""
    return source_match_span(text, label) is not None


def visible_source_labels(graph, entity_id, day):
    """Only current POV-visible name facets are literal-match candidates."""
    entity = graph.get_entity(entity_id)
    # Entity attrs have no facet-level secrecy boundary. A visible person may
    # still carry a secret true name there; only the filtered facts are safe.
    values = [fact.value for fact in graph.current_facts(entity_id)
              if fact.predicate in LABEL_FIELDS and fact.valid_at(day)]
    labels = {entity_id} if entity else set()
    for value in values:
        for label in value if isinstance(value, list) else [value]:
            if _identifier(label):
                labels.add(label.strip())
    return labels


def capture_player_input(world, scene, player_input):
    """Capture before proposals; unknown/bootstrap actors are deliberately absent."""
    actor_id = scene.get('protagonist')
    view = visible_source_entities(world, actor_id)
    if view is None:
        return None
    if not isinstance(player_input, str):
        raise ValueError('Player input source must be a string')
    return {'actor_id': actor_id, 'input': player_input,
            'requested_at': source_context(world, actor_id), 'visible': view}


def player_input_event(captured, world, commit, events, *, turn):
    """Build one private source after all validated effects and final guards."""
    actor_id, text = captured['actor_id'], captured['input']
    committed = source_context(world, actor_id)
    before = captured['visible']
    after = visible_source_entities(world, actor_id)
    relevant, visible = set(), set()
    for graph, day in ((before, captured['requested_at']['day']),
                       (after, committed['day'])):
        if graph is None:
            continue
        visible.update(graph.entities)
        for entity_id in graph.entities:
            if any(source_mentions(text, label)
                   for label in visible_source_labels(graph, entity_id, day)):
                relevant.add(entity_id)
    for section, fields in SECTION_REFS.items():
        for row in commit.sections.get(section) or []:
            if isinstance(row, dict):
                for field in fields:
                    value = row.get(field)
                    values = value if isinstance(value, list) else [value]
                    relevant.update(item for item in values if isinstance(item, str) and item in visible)
    # actor/co-presence are provenance or visibility, never relevance signals.
    relevant.discard(actor_id)
    active = [event for event in events if event.get('turn') == turn and not event.get('retracted')]
    narration_ref = next((event['id'] for event in active
                          if event['type'] == 'narration_recorded'
                          and event.get('deltas', {}).get('text') == commit.narration), None)
    event = kernel_event('player_input_recorded', day=committed['day'],
        scene=committed['scene'], turn=turn, summary=SUMMARY, secrecy='private',
        deltas={'actor_id': actor_id, 'input': text,
                'requested_at': copy.deepcopy(captured['requested_at']),
                'committed_at': committed, 'outcome': OUTCOME,
                'entity_refs': sorted(relevant), 'narration_ref': narration_ref,
                'effect_refs': [event['id'] for event in active
                                if event['type'] not in {'narration_recorded', 'player_input_recorded'}]})
    if not valid_player_input(event):
        raise ValueError('Invalid host player input source')
    return event
