"""Host-authored input provenance, never a declaration of world truth.

These records establish that a bound player submitted literal input and a
response committed. Optional introduction snapshots preserve that response's
published scene narration, without converting it to character facts or knowledge.
"""
from __future__ import annotations

import copy
import re

from kernel.events import kernel_event

OUTCOME = 'committed_response_not_proof_of_success'
SUMMARY = 'player input source recorded'
DELTA_KEYS = {'actor_id', 'input', 'requested_at', 'committed_at', 'outcome',
              'entity_refs', 'narration_ref', 'effect_refs'}
INTRO_MAX_CHARS = 2048
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


def valid_cast_introductions(value, actor_id, narration_ref, entity_refs):
    """Optional actor-owned scene source; missing historical fields stay unknown."""
    if not isinstance(value, dict) or set(value) != {'narration_ref', 'persons', 'span'}:
        return False
    if not _identifier(narration_ref) or value['narration_ref'] != narration_ref:
        return False
    span = value['span']
    if not isinstance(span, dict) or set(span) != {
            'text', 'start', 'end', 'original_length', 'truncated'}:
        return False
    if not (isinstance(span['text'], str) and 0 < len(span['text']) <= INTRO_MAX_CHARS
            and type(span['start']) is int and span['start'] == 0
            and type(span['end']) is int and span['end'] == len(span['text'])
            and type(span['original_length']) is int and span['original_length'] >= span['end']
            and type(span['truncated']) is bool
            and span['truncated'] == (span['end'] < span['original_length'])):
        return False
    persons = value['persons']
    if not isinstance(persons, list) or not persons or not isinstance(entity_refs, list):
        return False
    ids = []
    for person in persons:
        if not isinstance(person, dict) or set(person) not in (
                {'id'}, {'id', 'name'}, {'id', 'name', 'observed_identity'}):
            return False
        pid = person['id']
        if not _identifier(pid) or pid == actor_id or pid not in entity_refs:
            return False
        if 'name' in person and not (_identifier(person['name']) and person['name'] in span['text']):
            return False
        if 'observed_identity' in person:
            marker = person['observed_identity']
            if not (isinstance(marker, dict) and set(marker) == {'location', 'label', 'start', 'end'}
                    and _identifier(marker['location']) and marker['label'] == person['name']
                    and type(marker['start']) is int and type(marker['end']) is int
                    and 0 <= marker['start'] < marker['end'] <= len(span['text'])
                    and (marker['start'], marker['end']) == source_match_span(span['text'], person['name'])
                    and span['text'][marker['start']:marker['end']] == marker['label']):
                return False
        ids.append(pid)
    for person in persons:
        if 'observed_identity' in person and sum(
                other.get('name', '').strip().casefold() == person['name'].strip().casefold()
                for other in persons) != 1:
            return False
    return len(ids) == len(set(ids))


def valid_player_input(event):
    """Strict new-event contract; historical narrative events need no retrofit."""
    if not isinstance(event, dict) or event.get('type') != 'player_input_recorded':
        return False
    data = event.get('deltas')
    if not isinstance(data, dict) or set(data) not in (DELTA_KEYS, DELTA_KEYS | {'cast_introductions'}):
        return False
    if 'cast_introductions' in data and not valid_cast_introductions(
            data['cast_introductions'], data['actor_id'], data['narration_ref'], data['entity_refs']):
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
    if any(person.get('observed_identity', {}).get('location', committed['location'])
           != committed['location']
           for person in data.get('cast_introductions', {}).get('persons', [])):
        return False
    return (event.get('summary') == SUMMARY and event.get('actors') == []
            and type(event.get('day')) is int and event['day'] == committed['day']
            and event.get('scene') == committed['scene'])


def valid_narration_source(event):
    """Shared actor/source contract; opening observations are not player inputs."""
    try:
        if isinstance(event, dict) and event.get('type') == 'opening_observed':
            from systems.opening_sources import valid_opening_observation
            return valid_opening_observation(event)
        return valid_player_input(event)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def published_identity_bindings(world, actor_id):
    """Bound display references only: no current names, locations or profiles."""
    result = {}
    for pid, source in observed_identity_evidence(world, actor_id).items():
        label = source['person']['observed_identity']['label']
        if len(label) > 160:
            continue
        result[pid] = {'label': label, 'source_event_id': source['source_event_id'],
            'narration_ref': source['narration_ref'], 'turn': source['turn'],
            'category': 'published_display_binding'}
    return result


def introduction_sources(world, actor_id):
    """Yield validated actor-owned sources in one shape, without inventing input.

    No POV calls here: remembered identity is itself an input to POV filtering.
    """
    from systems.opening_sources import (
        DELTA_KEYS as OPENING_KEYS, SUMMARY as OPENING_SUMMARY, valid_opening_observation,
    )
    narrative = world.get('systems', {}).get('narrative')
    if not isinstance(narrative, dict) or not _identifier(actor_id):
        return
    for ledger_name, event_type, keys, summary, validator in (
            ('player_inputs', 'player_input_recorded', DELTA_KEYS | {'cast_introductions'},
             SUMMARY, valid_player_input),
            ('opening_observations', 'opening_observed', OPENING_KEYS,
             OPENING_SUMMARY, valid_opening_observation)):
        ledger = narrative.get(ledger_name)
        if not isinstance(ledger, list):
            continue
        for row in ledger:
            if not isinstance(row, dict) or row.get('actor_id') != actor_id:
                continue
            if set(row) != keys | {'source_event_id', 'turn'}:
                continue
            committed = row.get('committed_at')
            if not _context_valid(committed):
                continue
            event = {'type': event_type, 'id': row['source_event_id'],
                     'turn': row['turn'], 'day': committed['day'], 'scene': committed['scene'],
                     'summary': summary, 'actors': [], 'secrecy': 'private',
                     'deltas': {key: row[key] for key in keys}}
            if validator(event):
                yield {key: row[key] for key in (
                    'actor_id', 'turn', 'source_event_id', 'narration_ref',
                    'committed_at', 'entity_refs', 'cast_introductions')}


def observed_identity_evidence(world, actor_id):
    """Read explicit host markers; old scene associations confer no identity.

    This pure reader must not call POV/source-visibility helpers: it is also used
    to construct that view. Historical spans never authorize current graph facts.
    """
    graph = world.get('systems', {}).get('ontology')
    actor = graph.get_entity(actor_id) if graph and _identifier(actor_id) else None
    if actor is None or actor.etype != 'Person':
        return {}
    evidence = {}
    for row in introduction_sources(world, actor_id):
        snapshot = row['cast_introductions']
        for person in snapshot['persons']:
            entity = graph.get_entity(person['id'])
            if 'observed_identity' not in person or entity is None or entity.etype != 'Person':
                continue
            record = {key: copy.deepcopy(row[key]) for key in (
                'actor_id', 'turn', 'source_event_id', 'narration_ref', 'committed_at')}
            record.update(person=copy.deepcopy(person), span=copy.deepcopy(snapshot['span']))
            prior = evidence.get(person['id'])
            if prior is None or (record['turn'], record['source_event_id']) < (
                    prior['turn'], prior['source_event_id']):
                evidence[person['id']] = record
    return evidence


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
            'requested_at': source_context(world, actor_id), 'visible': view,
            # An already-existing but hidden Person is not a foreground creation.
            # IDs are host-only capture data and never enter the source event.
            'existing_ids': set(world['systems']['ontology'].entities)}


def _cast_introductions(captured, after, world, commit, active, narration_ref, committed):
    """Associate visible foreground creations with exact published scene prose."""
    if after is None or narration_ref is None or not commit.narration:
        return None
    text = commit.narration[:INTRO_MAX_CHARS]
    persons = {}
    graph = world['systems']['ontology']
    creations = {}
    for section, event_type in (('cast', 'character_created'), ('entities', 'entity_created')):
        for declaration in commit.sections.get(section) or []:
            if not isinstance(declaration, dict):
                continue
            if section == 'cast' and declaration.get('op', 'create') != 'create':
                continue
            pid = declaration.get('id')
            entity = graph.get_entity(pid) if isinstance(pid, str) else None
            if (entity is None or entity.etype != 'Person'
                    or pid in captured['existing_ids'] or pid == captured['actor_id']):
                continue
            if not any(event['type'] == event_type and event.get('deltas') == declaration
                       for event in active):
                continue
            name = declaration.get('name')
            creations.setdefault(pid, []).append(name)
            if after.get_entity(pid) is None:
                continue
            person = persons.setdefault(pid, {'id': pid})
            if _identifier(name) and name in text:
                person['name'] = name
    if not persons:
        return None
    # Same-place creation alone is insufficient: a unique creation label must
    # literally occur in the published, bounded passage. Unknown/off-scene and
    # duplicate labels keep only their legacy scene association, never a marker.
    labels = {}
    for pid, names in creations.items():
        for name in names:
            if _identifier(name):
                labels.setdefault(name.strip().casefold(), set()).add(pid)
    for view, day in ((captured['visible'], captured['requested_at']['day']),
                      (after, committed['day'])):
        for pid in captured['existing_ids']:
            entity = view.get_entity(pid)
            if entity is not None and entity.etype == 'Person':
                for label in visible_source_labels(view, pid, day):
                    labels.setdefault(label.strip().casefold(), set()).add(pid)
    for pid, source in observed_identity_evidence(world, captured['actor_id']).items():
        label = source['person']['observed_identity']['label']
        labels.setdefault(label.strip().casefold(), set()).add(pid)
    actor_locations = graph.neighbors(captured['actor_id'], 'located_in', committed['day'])
    location = committed['location']
    for pid, person in persons.items():
        name = person.get('name')
        match = source_match_span(text, name) if _identifier(name) else None
        if (match is None or text[match[0]:match[1]] != name or len(creations[pid]) != 1
                or labels.get(name.strip().casefold()) != {pid}
                or not _identifier(location) or actor_locations != [location]
                or graph.neighbors(pid, 'located_in', committed['day']) != [location]):
            continue
        start, end = match
        person['observed_identity'] = {'location': location, 'label': name,
                                       'start': start, 'end': end}
    return {'narration_ref': narration_ref, 'persons': [persons[pid] for pid in sorted(persons)],
            'span': {'text': text, 'start': 0, 'end': len(text),
                     'original_length': len(commit.narration),
                     'truncated': len(text) < len(commit.narration)}}


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
    introductions = _cast_introductions(captured, after, world, commit, active, narration_ref, committed)
    if introductions is not None:
        event['deltas']['cast_introductions'] = introductions
    if not valid_player_input(event):
        raise ValueError('Invalid host player input source')
    return event
