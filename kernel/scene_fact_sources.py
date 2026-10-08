"""Bounded, authenticated current-Place facts for actor-visible consumers.

Source records retain the original materialization wire format and digest
version: persisted item origins must continue to authenticate during replay.
Only a before-world record is positive continuity evidence. A candidate's
same-slot value can signal change, but cannot establish a new historical fact.
"""
from __future__ import annotations

import hashlib
import json


VERSION = 'item_materialization_v1'
MAX_SOURCES = 24
MAX_SOURCE_CHARS = 2048


def _identifier(value, maximum=160):
    return (isinstance(value, str) and bool(value.strip())
            and value == value.strip() and len(value) <= maximum)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _context(world, actor_id):
    """Use host-bound actor/time and actual visible location, never JSON hints."""
    if not isinstance(world, dict):
        return None
    bound = world.get('_materialization_actor')
    actor = bound if actor_id is None else actor_id
    if (not _identifier(actor) or (bound is not None and actor != bound)
            or type(world.get('_action_turn')) is not int or world['_action_turn'] <= 0):
        return None
    graph = world.get('systems', {}).get('ontology')
    if graph is None:
        return None
    person = graph.get_entity(actor)
    day = world.get('meta', {}).get('day')
    if person is None or person.etype != 'Person' or type(day) is not int or day < 0:
        return None
    locations = graph.neighbors(actor, 'located_in', day)
    if len(locations) != 1 or not _identifier(locations[0]):
        return None
    place = graph.get_entity(locations[0])
    if place is None or place.etype != 'Place':
        return None
    from context.access import pov_world
    scene = {'protagonist': actor, 'location': place.id, 'present': [], 'day': day}
    view = pov_world(world, scene, pov=actor)['systems']['ontology']
    visible_place = view.get_entity(place.id)
    if (visible_place is None or visible_place.etype != 'Place'
            or view.neighbors(actor, 'located_in', day) != [place.id]):
        return None
    return {'actor': actor, 'day': day, 'turn': world['_action_turn'],
            'place': place.id, 'graph': graph, 'view': view}


def _current_place_slots(context):
    # Count before filtering: a hidden/invalid duplicate must not make an
    # otherwise ambiguous slot appear uniquely supported.
    slots = {}
    for fact in context['graph'].facts:
        if fact.subject == context['place'] and fact.is_current() and fact.valid_at(context['day']):
            slots.setdefault((fact.subject, fact.predicate), []).append(fact)
    return slots


def _visible_fact(context, versions, *, include_action_turn=False):
    """Authenticate one complete value, rejecting ambiguity and POV beliefs."""
    if len(versions) != 1:
        return None
    fact = versions[0]
    upper_turn = context['turn'] + int(include_action_turn)
    if (not _identifier(fact.predicate) or fact.predicate.startswith('knows:')
            or not _identifier(fact.source_event)
            or fact.secrecy not in {None, 'public'}
            or type(fact.ingest_turn) is not int
            or not 0 <= fact.ingest_turn < upper_turn
            or type(fact.event_time_start) is not int
            or not 0 <= fact.event_time_start <= context['day']
            or not isinstance(fact.value, str) or not fact.value.strip()
            or len(fact.value) > MAX_SOURCE_CHARS):
        return None
    visible = [candidate for candidate in context['view'].facts
               if candidate.subject == fact.subject and candidate.predicate == fact.predicate
               and candidate.is_current() and candidate.valid_at(context['day'])]
    if (len(visible) != 1 or visible[0].source_event != fact.source_event
            or visible[0].ingest_turn != fact.ingest_turn
            or visible[0].event_time_start != fact.event_time_start
            or visible[0].value != fact.value):
        return None
    return fact


def _source_rows(context, exclude_slots=()):
    excluded = set(exclude_slots)
    rows = []
    for slot, versions in _current_place_slots(context).items():
        if slot in excluded:
            continue
        fact = _visible_fact(context, versions)
        if fact is None:
            continue
        provenance = {'source_event_id': fact.source_event,
                      'subject': fact.subject, 'predicate': fact.predicate}
        try:
            row = {'source_ref': 'fact:' + _hash(provenance),
                   **provenance, 'actor_id': context['actor'], 'text': fact.value,
                   'source_visibility': 'public' if fact.secrecy == 'public' else 'actor_only',
                   'turn': fact.ingest_turn, 'day': fact.event_time_start}
            row['source_digest'] = _hash({'version': VERSION, **row})
        except (TypeError, ValueError, UnicodeError):
            continue
        rows.append(row)
    rows.sort(key=lambda row: (-row['turn'], -row['day'], row['source_event_id'],
                               row['subject'], row['predicate']))
    return rows[:MAX_SOURCES]


def current_place_fact_sources(world, actor_id, *, exclude_slots=()):
    """Return at most 24 complete, authenticated prior-ingest Place facts.

    This is a bounded subset, not an exhaustive search. Newest eligible facts
    come first, with stable tie-breaking. Values over 2048 characters are
    omitted rather than truncated. Exclusions apply before the source cap.
    Item consumption is a caller's policy and does not consume descriptive
    truth; this reader does not inspect the materialization ledger.
    """
    context = _context(world, actor_id)
    return [] if context is None else _source_rows(context, exclude_slots)


def scene_fact_continuity_sources(before_world, after_world, actor_id):
    """Pair old positive evidence with a bounded after-world change signal.

    Static continuity only applies within the same unique actor Place and
    day/band. Unavailable after values never expose text or provenance. New
    same-slot assertions may veto old evidence, including on this action's
    turn, but candidate-only slots never become positive evidence. Random
    preview event IDs are reduced to a boolean comparison and never emitted.
    """
    before = _context(before_world, actor_id)
    after = _context(after_world, actor_id)
    if (before is None or after is None or before['actor'] != after['actor']
            or before['place'] != after['place']
            or before['day'] != after['day']):
        return []
    before_band = before_world.get('meta', {}).get('band', 0)
    after_band = after_world.get('meta', {}).get('band', 0)
    if (type(before_band) is not int or not 0 <= before_band <= 3
            or type(after_band) is not int or before_band != after_band):
        return []
    slots = _current_place_slots(after)
    rows = []
    for source in _source_rows(before):
        versions = slots.get((source['subject'], source['predicate']), [])
        fact = _visible_fact(after, versions, include_action_turn=True)
        signal = {'status': 'unavailable', 'same_source_event': False}
        if fact is not None:
            same_event = fact.source_event == source['source_event_id']
            visibility = 'public' if fact.secrecy == 'public' else 'actor_only'
            unchanged = (same_event and fact.value == source['text']
                         and fact.ingest_turn == source['turn']
                         and fact.event_time_start == source['day']
                         and visibility == source['source_visibility'])
            signal = {'status': 'unchanged' if unchanged else 'changed',
                      'text': fact.value, 'same_source_event': same_event}
        rows.append({'source': source, 'after': signal})
    return rows
