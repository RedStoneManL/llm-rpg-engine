"""Small, POV-safe physical outcomes for narration after modular repair.

These helpers never call a model or write events. Signatures compare declarations
(including rejected transfers), while narration receives only the validated
primary action's privately projected, observable outcome.
"""
from __future__ import annotations

import copy

from context.access import pov_world
from kernel.clock import advance
from kernel.projection import apply_event_metadata
from kernel.item_integrity import creation_first_sections, item_event_error
from systems.time import normalize_clock

_MAX_ROWS = 64
_NAME_FIELDS = ('name', '真名')
_PRIVATE = {'hidden', 'secret', 'undiscovered'}


def _freeze(value):
    """Preserve malformed physical values for comparison without interpreting them."""
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return (type(value).__name__, value)


def _rows(declaration, fields, *, default_op=None):
    if declaration is None:
        return ()
    if not isinstance(declaration, list):
        return ('invalid', _freeze(declaration))
    result = []
    for row in declaration:
        if not isinstance(row, dict):
            result.append(('invalid', _freeze(row)))
            continue
        values = {key: row.get(key) for key in fields}
        if default_op is not None:
            values['op'] = row.get('op', default_op)
        result.append(_freeze(values))
    return tuple(result)


def _clock(world):
    meta = world.get('meta', {})
    day = meta.get('day') if meta.get('day') is not None else 1
    band = meta.get('band') if meta.get('band') is not None else 0
    if type(day) is not int or day < 1 or type(band) is not int or not 0 <= band < 4:
        raise ValueError('Repair outcome requires a valid world clock')
    return day, band


def _endpoint(declaration, world):
    day, band = _clock(world)
    normalized = normalize_clock(declaration, world)
    if normalized and normalized[0].get('advance'):
        row = normalized[0]
        return advance(day, band, row.get('days', 0), row.get('bands', 0))
    return day, band


def physical_signature(commit, world):
    """Compare ordered physical declarations, not just their final state.

    In particular, dropping an invalid A-to-B transfer must differ even when B
    already holds the item. Cast goals and prose are outside this signature.
    Valid clock forms compare by absolute endpoint; clock reason is never part
    of the comparison, including when that is the only field needing repair.
    """
    sections = commit.sections
    clock = sections.get('clock')
    clock_physics = copy.deepcopy(clock)
    if isinstance(clock_physics, list):
        for row in clock_physics:
            if isinstance(row, dict):
                row['reason'] = 'physical signature'
    try:
        clock_signature = ('endpoint', _endpoint(clock_physics, world))
    except (AttributeError, KeyError, TypeError, ValueError):
        clock_signature = ('invalid', _rows(
            clock_physics, ('advance', 'days', 'bands', 'target')))
    relations = sections.get('relations') or []
    if isinstance(relations, list):
        relations = [row for row in relations if isinstance(row, dict)
                     and row.get('rel') in {'located_in', 'held_by'}]
    objects = sections.get('entities') or []
    if isinstance(objects, list):
        objects = [row for row in objects if isinstance(row, dict)
                   and row.get('etype') == 'Object']
    items = sections.get('items')
    if isinstance(items, list):
        items = [({
            'op': row.get('op', 'create'),
            **{key: row.get(key) for key in (
                ('id',) if row.get('op', 'create') == 'create'
                else ('item', 'from', 'to'))},
        } if isinstance(row, dict) else row) for row in items]
    return (
        _freeze(items or []) if isinstance(items, list) or items is None
        else ('invalid', _freeze(items)),
        _rows(sections.get('moves'), ('who', 'to', 'arrive_day')),
        clock_signature,
        _rows(relations, ('src', 'rel', 'dst')),
        _rows(objects, ('id', 'etype')),
    )


def _known_entity(graph, actor, entity, day):
    """Do not let co-location alone expose explicitly hidden identities."""
    if entity.id == actor or entity.attrs.get('visibility') not in _PRIVATE:
        return True
    if actor in (entity.attrs.get('discovered_by') or []):
        return True
    return any(fact.valid_at(day) and fact.predicate.startswith(f'knows:{entity.id}.')
               for fact in graph.current_facts(actor))


def _state(world, actor):
    """Whitelist local physical state from an independently filtered POV graph."""
    graph = world.get('systems', {}).get('ontology')
    entity = graph.get_entity(actor) if graph is not None else None
    if entity is None or entity.etype != 'Person':
        raise ValueError('Repair outcome requires an existing Person protagonist')
    day, band = _clock(world)
    locations = graph.neighbors(actor, 'located_in', day)
    if len(locations) > 1:
        raise ValueError('Repair outcome requires an unambiguous protagonist location')
    location = locations[0] if locations else None
    if location:
        place = graph.get_entity(location)
        if place is None or place.etype != 'Place':
            raise ValueError('Repair outcome protagonist location must be a Place')
    # Host scene metadata can be stale after a move. Both snapshots use the
    # original actor, canonical locations, and their own projected clock.
    scene = {'protagonist': actor, 'day': day, 'location': location, 'present': []}
    view = pov_world(world, scene, pov=actor)['systems']['ontology']
    allowed = {eid for eid, ent in view.entities.items()
               if _known_entity(graph, actor, ent, day)}
    # The actor knows their own canonical position, even at a hidden place.
    allowed.add(actor)
    if location:
        allowed.add(location)
    local_people = {actor}
    if location:
        local_people.update(eid for eid in allowed
                            if view.get_entity(eid).etype == 'Person'
                            and location in view.neighbors(eid, 'located_in', day))
    local_positions = set(local_people)
    if location:
        local_positions.update(eid for eid in allowed
                               if view.get_entity(eid).etype == 'Object'
                               and location in view.neighbors(eid, 'located_in', day))
    positions = []
    for who in sorted(local_positions):
        destinations = view.neighbors(who, 'located_in', day)
        if len(destinations) > 1:
            raise ValueError('Repair outcome contains ambiguous visible positions')
        if destinations and destinations[0] in allowed:
            positions.append({'who': who, 'location': destinations[0]})
    local_holders = local_people | ({location} if location else set())
    held_by = []
    for relation in view.relations:
        if (relation.rel != 'held_by' or not relation.valid_at(day)
                or relation.src not in allowed or relation.dst not in local_holders):
            continue
        obj, holder = view.get_entity(relation.src), view.get_entity(relation.dst)
        if obj.etype != 'Object' or holder.etype not in {'Person', 'Place'}:
            raise ValueError('Repair outcome contains an invalid visible item holder')
        canonical = graph.neighbors(relation.src, 'held_by', day)
        if len(canonical) != 1 or canonical[0] != relation.dst:
            raise ValueError('Repair outcome contains ambiguous visible item ownership')
        held_by.append({'item': relation.src, 'holder': relation.dst})
    held_by.sort(key=lambda row: (row['holder'] != actor, row['item'], row['holder']))
    if len(positions) > _MAX_ROWS or len(held_by) > _MAX_ROWS:
        raise ValueError('Repair outcome exceeds the bounded visible state')
    state = {'day': day, 'band': band, 'actor_location': location,
             'positions': positions, 'held_by': held_by}
    return state, view


def _names(view, state, actor):
    ids = {actor}
    if state['actor_location']:
        ids.add(state['actor_location'])
    for row in state['positions']:
        ids.update((row['who'], row['location']))
    for row in state['held_by']:
        ids.update((row['item'], row['holder']))
    result = {}
    for eid in sorted(ids):
        entity = view.get_entity(eid)
        if entity is None:
            raise ValueError('Repair outcome references a non-visible entity')
        entry = {'id': eid, 'type': entity.etype}
        for predicate in _NAME_FIELDS:
            value = view.value_at(eid, predicate, state['day'])
            if isinstance(value, str) and value.strip():
                # Only POV-filtered name facts, never attrs, goals, aliases, or
                # free ownership facts. Overlong values are not useful labels.
                if len(value) <= 160:
                    entry['name'] = value
                break
        result[eid] = entry
    return result


def _physical_transition(event, before, after):
    """Describe only observable edges actually produced by an approved event."""
    kind, data = event['type'], event.get('deltas', {})
    if kind == 'entity_moved' or (kind == 'relation_added' and data.get('rel') == 'located_in'):
        who = data.get('who') if kind == 'entity_moved' else data.get('src')
        previous = {row['who']: row['location'] for row in before['positions']}
        current = {row['who']: row['location'] for row in after['positions']}
        if who not in previous and who not in current:
            return None
        if who in previous and previous[who] == current.get(who):
            return None
        row = {'kind': 'move', 'who': who, 'day': event['day']}
        if who in previous:
            row['from'] = previous[who]
        if who in current:
            row['to'] = current[who]
        return row
    if kind == 'item_transferred':
        item = data.get('item')
        previous = {row['item']: row['holder'] for row in before['held_by']}
        current = {row['item']: row['holder'] for row in after['held_by']}
        if item not in current:
            return None
        row = {'kind': 'item_transfer', 'item': item, 'to': current[item], 'day': event['day']}
        if item in previous:
            row['from'] = previous[item]
        return row
    return None


def build_repair_outcome(registry, world, scene, commit, player_input):
    """Build a JSON-safe packet after validation and before any store write.

    Unknown/redacted relations stay unknown. Background hooks, resource values,
    NPC hearing, facts other than visible names, repair diagnostics, and the
    original prose never enter this packet. Preview errors reject the rewrite.
    """
    actor = scene.get('protagonist') if isinstance(scene, dict) else None
    if not isinstance(actor, str) or not actor.strip() or not isinstance(player_input, str):
        raise ValueError('Repair outcome requires the bound actor and exact player input')
    before, before_view = _state(world, actor)
    labels = _names(before_view, before, actor)
    preview = copy.deepcopy(world)
    sections = copy.deepcopy(commit.sections)
    if 'clock' in sections:
        sections['clock'] = normalize_clock(sections['clock'], world)
    day, _ = _endpoint(sections.get('clock'), world)
    turn = world.get('_action_turn')
    if type(turn) is not int or turn < 0:
        graph = world['systems']['ontology']
        turns = [row.ingest_turn for row in graph.facts + graph.relations]
        turn = max((value for value in turns if type(value) is int), default=0) + 1
    scene_id = scene.get('id') or scene.get('location') or 'scene'
    transitions, created = [], []
    physical_types = {'entity_moved', 'item_transferred', 'relation_added'}
    for section, declaration in creation_first_sections(sections):
        owner = registry.owner_of_section(section)
        if owner is None or not declaration:
            continue
        for event in owner.to_events(section, declaration, turn=turn, day=day, scene=scene_id):
            event_owner = registry.owner_of_event(event['type'])
            if event_owner is None:
                raise ValueError('Repair outcome cannot preview an unowned event')
            if item_event_error(preview, event):
                raise ValueError('Repair outcome contains an inconsistent item transition')
            physical = (event['type'] in physical_types and
                        (event['type'] != 'relation_added' or
                         event.get('deltas', {}).get('rel') == 'located_in'))
            prior, prior_view = _state(preview, actor) if physical else (None, None)
            apply_event_metadata(preview, event)
            event_owner.apply(preview, event)
            if physical:
                current, current_view = _state(preview, actor)
                transition = _physical_transition(event, prior, current)
                if transition is not None:
                    transitions.append(transition)
                    labels.update(_names(prior_view, prior, actor))
                    labels.update(_names(current_view, current, actor))
            data = event.get('deltas', {})
            if (event['type'] == 'object_created' or (
                    event['type'] == 'entity_created' and data.get('etype') == 'Object')):
                item = data.get('id')
                if world['systems']['ontology'].get_entity(item) is None and item not in {
                        row[0] for row in created}:
                    created.append((item, event['day']))
    after, after_view = _state(preview, actor)
    labels.update(_names(after_view, after, actor))
    final_local_items = {row['item'] for row in after['held_by']}
    transitions[:0] = [{'kind': 'item_created', 'item': item, 'day': event_day}
                       for item, event_day in created if item in final_local_items]
    if (before['day'], before['band']) != (after['day'], after['band']):
        transitions.append({'kind': 'clock',
                            'from': {'day': before['day'], 'band': before['band']},
                            'to': {'day': after['day'], 'band': after['band']}})
    if len(transitions) > _MAX_ROWS or len(labels) > _MAX_ROWS * 2:
        raise ValueError('Repair outcome exceeds the bounded physical transition packet')
    return {
        'scope': 'primary_turn_before_background_hooks',
        'player_intent': player_input,
        'actor_id': actor,
        'entities': [labels[eid] for eid in sorted(labels)],
        'before': before,
        'after': after,
        'transitions': transitions,
        'limits': ('Player input is intent, not evidence of success. Only listed physical transitions '
                   'are approved. Missing positions, holders, and transition endpoints are unknown, '
                   'not empty. Co-location does not establish hearing or knowledge. '
                   'Background hooks run later and are not covered.'),
    }
