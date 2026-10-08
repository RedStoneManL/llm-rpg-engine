"""Host-only sources for an approved, published opening's display bindings.

The caller supplies audited person/label bindings after publishing the text.
This module neither mines names nor establishes legal names, knowledge or goals.
"""
from __future__ import annotations

import copy

from kernel.events import kernel_event
from systems.player_sources import (
    INTRO_MAX_CHARS, _context_valid, _identifier, _identifiers,
    observed_identity_evidence, source_match_span, valid_cast_introductions, visible_source_labels,
)

SUMMARY = 'opening observation source recorded'
DELTA_KEYS = {'actor_id', 'committed_at', 'narration_ref', 'entity_refs',
              'cast_introductions'}
_MAX_PERSONS = 64


def valid_opening_observation(event):
    """Validate the durable source shape without relying on present-day state."""
    if not isinstance(event, dict) or event.get('type') != 'opening_observed':
        return False
    data = event.get('deltas')
    if not isinstance(data, dict) or set(data) != DELTA_KEYS:
        return False
    committed = data['committed_at']
    if not (_identifier(event.get('id')) and type(event.get('turn')) is int
            and event['turn'] == 0 and _identifier(data['actor_id'])
            and _context_valid(committed) and committed['day'] >= 1
            and _identifier(committed['location'])
            and _identifier(data['narration_ref'])
            and event['id'] != data['narration_ref']
            and _identifiers(data['entity_refs'])
            and 0 < len(data['entity_refs']) <= _MAX_PERSONS
            and data['actor_id'] not in data['entity_refs']
            and event.get('summary') == SUMMARY and event.get('actors') == []
            and event.get('secrecy') == 'private'
            and type(event.get('day')) is int and event['day'] == committed['day']
            and event.get('scene') == committed['scene']):
        return False
    if any(event.get(key) not in (None, []) for key in ('thread_refs', 'chunk_ids')):
        return False
    if event.get('arc') is not None or event.get('roll') is not None:
        return False
    snapshot = data['cast_introductions']
    if not valid_cast_introductions(
            snapshot, data['actor_id'], data['narration_ref'], data['entity_refs']):
        return False
    persons, span = snapshot['persons'], snapshot['span']
    return (span['end'] == min(span['original_length'], INTRO_MAX_CHARS)
            and data['entity_refs'] == sorted(person['id'] for person in persons)
            and all(set(person) == {'id', 'name', 'observed_identity'}
                    and person['observed_identity']['location'] == committed['location']
                    for person in persons))


def _opening_state(world, actor_id):
    """Canonical unique location plus the existing POV/hidden-identity boundary."""
    # Lazy imports keep access -> player_sources -> opening_sources acyclic.
    from context.access import pov_world
    from loop.repair_outcome import _clock, _known_entity

    graph = world.get('systems', {}).get('ontology')
    actor = graph.get_entity(actor_id) if graph and _identifier(actor_id) else None
    if actor is None or actor.etype != 'Person':
        raise ValueError('Opening observation requires an existing Person actor')
    day, band = _clock(world)
    locations = graph.neighbors(actor_id, 'located_in', day)
    if len(locations) != 1:
        raise ValueError('Opening observation requires one canonical actor location')
    location = locations[0]
    place = graph.get_entity(location)
    if place is None or place.etype != 'Place':
        raise ValueError('Opening observation actor location must be a Place')
    committed = {'day': day, 'band': band, 'scene': world.get('meta', {}).get('scene'),
                 'location': location}
    if not _context_valid(committed):
        raise ValueError('Opening observation requires an exact scene context')
    scene = {**committed, 'protagonist': actor_id, 'present': []}
    view = pov_world(world, scene, pov=actor_id)['systems']['ontology']
    people = {pid for pid, person in view.entities.items()
              if person.etype == 'Person'
              and _known_entity(graph, actor_id, graph.get_entity(pid), day)}
    return graph, view, people, committed


def opening_observation_event(world, narration_event, bindings, *, actor_id):
    """Build a private source after publication; return None for no safe binding.

    Bindings are explicit, prevalidated ``{'id': ..., 'label': ...}`` records.
    A label is a published display binding, not proof of a canonical true name.
    The caller must append the narration before this event for replay validation.
    """
    if not isinstance(bindings, list) or len(bindings) > _MAX_PERSONS:
        raise ValueError('Opening bindings must be a bounded list')
    if any(not isinstance(row, dict) or set(row) != {'id', 'label'}
           or not _identifier(row['id']) or not _identifier(row['label']) for row in bindings):
        raise ValueError('Opening bindings require explicit person IDs and labels')
    graph, view, people, committed = _opening_state(world, actor_id)
    source = narration_event
    data = source.get('deltas') if isinstance(source, dict) else None
    if not (isinstance(source, dict) and source.get('type') == 'narration_recorded'
            and _identifier(source.get('id')) and type(source.get('turn')) is int
            and source['turn'] == 0 and not source.get('retracted')
            and type(source.get('day')) is int and source['day'] == committed['day']
            and source.get('scene') == committed['scene']
            and isinstance(data, dict) and data.get('scene') == committed['scene']
            and isinstance(data.get('text'), str) and data['text'].strip()):
        raise ValueError('Opening observation requires matching published narration')
    full_text = data['text']
    text = full_text[:INTRO_MAX_CHARS]
    labels, counts = {}, {}
    for row in bindings:
        labels.setdefault(row['label'].strip().casefold(), set()).add(row['id'])
        counts[row['id']] = counts.get(row['id'], 0) + 1
    for pid in people:
        for label in visible_source_labels(view, pid, committed['day']):
            labels.setdefault(label.strip().casefold(), set()).add(pid)
    for pid, prior in observed_identity_evidence(world, actor_id).items():
        label = prior['person']['observed_identity']['label']
        labels.setdefault(label.strip().casefold(), set()).add(pid)
    persons = []
    for row in bindings:
        pid, label = row['id'], row['label']
        # Match against the full publication first: cutting a longer name at the
        # source limit must not manufacture a different person's identity.
        match = source_match_span(full_text, label)
        if (pid == actor_id or pid not in people or counts[pid] != 1
                or graph.neighbors(pid, 'located_in', committed['day']) != [committed['location']]
                or view.neighbors(pid, 'located_in', committed['day']) != [committed['location']]
                or labels.get(label.strip().casefold()) != {pid}
                or match is None or match[1] > len(text)
                or full_text[match[0]:match[1]] != label
                or source_match_span(text, label) != match):
            continue
        persons.append({'id': pid, 'name': label, 'observed_identity': {
            'location': committed['location'], 'label': label,
            'start': match[0], 'end': match[1]}})
    if not persons:
        return None
    persons.sort(key=lambda person: person['id'])
    event = kernel_event('opening_observed', day=committed['day'],
        scene=committed['scene'], turn=0, summary=SUMMARY, secrecy='private',
        deltas={'actor_id': actor_id, 'committed_at': committed,
                'narration_ref': source['id'], 'entity_refs': [person['id'] for person in persons],
                'cast_introductions': {'narration_ref': source['id'], 'persons': persons,
                    'span': {'text': text, 'start': 0, 'end': len(text),
                             'original_length': len(full_text),
                             'truncated': len(text) < len(full_text)}}})
    if not valid_opening_observation(event):
        raise ValueError('Invalid host opening observation source')
    return event


def validate_opening_replay(world, event):
    """Fail closed unless the source matches its earlier narration and state."""
    if not valid_opening_observation(event):
        raise ValueError('Invalid opening observation source event')
    data = event['deltas']
    matches = []
    for bucket in world['systems']['narrative'].get('scenes', []):
        raw, sources = bucket.get('raw', []), bucket.get('narration_sources', [])
        if not isinstance(raw, list) or not isinstance(sources, list) or len(raw) != len(sources):
            continue
        for text, source in zip(raw, sources):
            if isinstance(source, dict) and source.get('id') == data['narration_ref']:
                matches.append({'type': 'narration_recorded', **copy.deepcopy(source),
                                'deltas': {'scene': bucket.get('scene'), 'text': text}})
    if len(matches) != 1:
        raise ValueError('Opening observation requires one earlier narration source')
    expected = opening_observation_event(world, matches[0], [
        {'id': person['id'], 'label': person['name']}
        for person in data['cast_introductions']['persons']], actor_id=data['actor_id'])
    if expected is None or expected['deltas'] != data:
        raise ValueError('Opening observation disagrees with its published source or context')
