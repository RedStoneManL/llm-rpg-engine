"""Checks for NEW item mutations, separate from permissive historical replay.

The source holder is a state precondition, not evidence of consent or authority.
"""
from __future__ import annotations

import copy

from kernel.contextsystem import ValidationError


def creation_first_sections(sections):
    """Resolve same-turn entity references independent of JSON key order.

    Move later creation-capable sections before the first items/links section.
    Preserve every declaration row and all other relative section ordering.
    """
    pairs = list(sections.items())
    promises = [pair for pair in pairs if pair[0] == 'promises']
    pairs = [pair for pair in pairs if pair[0] != 'promises']
    dependent = {'items', 'links'}
    if not any(sections.get(name) for name in dependent):
        return pairs + promises
    index = next(i for i, (name, rows) in enumerate(pairs) if name in dependent and rows)
    creators = {'entities', 'places', 'cast', 'factions'}
    later = pairs[index + 1:]
    return (pairs[:index] + [pair for pair in later if pair[0] in creators]
            + [pairs[index]] + [pair for pair in later if pair[0] not in creators] + promises)


def transfer_errors(graph, declaration, day, *, pending_types=False):
    """Return field/code/hint triples without disclosing a different holder."""
    errors = []
    item, target = declaration.get('item'), declaration.get('to')
    if not isinstance(item, str) or not item:
        errors.append(('item', 'missing', 'item 必须是非空物品 id 字符串'))
    if not isinstance(target, str) or not target:
        errors.append(('to', 'missing', 'to 必须是非空持有者 id 字符串'))
    if errors:
        return errors
    obj, holder = graph.get_entity(item), graph.get_entity(target)
    if obj is None:
        errors.append(('item', 'dangling_ref', '物品必须先声明为 Object 实体'))
    elif obj.etype != 'Object' and not (pending_types and obj.etype == '_pending'):
        errors.append(('item', 'item_type', '被转移实体必须是 Object，不能把角色或地点当作物品'))
    if holder is None:
        errors.append(('to', 'dangling_ref', '目标持有者必须先声明'))
    elif holder.etype not in {'Person', 'Place'} and not (pending_types and holder.etype == '_pending'):
        errors.append(('to', 'holder_type', '目标持有者必须是 Person 或 Place'))
    owners = graph.neighbors(item, 'held_by', day)
    source = declaration.get('from')
    if len(owners) > 1:
        errors.append(('from', 'ambiguous_holder', '物品当前持有者不唯一，不能猜测转移来源'))
    elif owners:
        if target == owners[0]:
            errors.append(('to', 'same_holder', '物品已经由该对象持有；没有实际转移时请删除这条 transfer'))
        if 'from' not in declaration:
            errors.append(('from', 'missing_source', '已有人持有的物品必须显式提供 from（转移前持有者 id）'))
        elif not isinstance(source, str) or source != owners[0]:
            errors.append(('from', 'stale_holder', 'from 与物品转移前持有者不符；核对可见物品记录，不得编造来源'))
        else:
            prior_holder = graph.get_entity(source)
            if prior_holder is None or (prior_holder.etype not in {'Person', 'Place'} and
                                        not (pending_types and prior_holder.etype == '_pending')):
                errors.append(('from', 'holder_type', '转移前持有者必须是已存在的 Person 或 Place'))
    elif source is not None:
        errors.append(('from', 'stale_holder', '尚无持有记录的物品，首次放置应省略 from 或使用 null'))
    return errors


def item_event_error(world, event):
    """Check one proposed event against the state immediately before applying it."""
    graph = world.get('systems', {}).get('ontology')
    if graph is None:
        return None
    kind, data = event['type'], event.get('deltas', {})
    if kind == 'relation_added' and data.get('rel') == 'held_by':
        return ('', 'item_route', 'held_by 必须通过 items 的 transfer 操作修改，并提供正确 from；删除这条 relations 声明')
    if kind == 'item_transferred':
        errors = transfer_errors(graph, data, event['day'])
        return errors[0] if errors else None
    # A generic declaration must not turn a person into an item (or vice versa)
    # to evade the type gate. This affects new events only, not legacy replay.
    types = {'object_created': 'Object', 'character_created': 'Person',
             'place_created': 'Place', 'faction_created': 'Faction'}
    entity_type = data.get('etype') if kind == 'entity_created' else types.get(kind)
    entity_id = data.get('id')
    old = graph.get_entity(entity_id) if isinstance(entity_id, str) else None
    if old and entity_type and old.etype != entity_type:
        if 'Object' in {old.etype, entity_type}:
            return ('id', 'item_type_change', '不能通过重复声明把已有角色/地点改成物品，或把已有物品改成其他类型')
        for relation in graph.relations_at(entity_id, 'held_by', event['day']):
            if entity_type != 'Object':
                return ('id', 'item_type_change', '持有关系中的物品必须保持 Object 类型')
        if entity_type not in {'Person', 'Place'} and any(
                r.rel == 'held_by' and r.dst == entity_id and r.valid_at(event['day'])
                for r in graph.relations):
            return ('id', 'holder_type', '现有物品持有者必须保持 Person 或 Place 类型')
    return None


def validate_item_commit(registry, commit, world):
    """Replay a structurally valid proposal privately to check cross-section order.

    The normal projection API remains unchanged for old saves. This preview
    resolves newly created entities to actual types rather than pending stubs.
    Passage operations share this ordered state preflight with item operations.
    """
    if (registry.owner_of_event('item_transferred') is None and
            registry.owner_of_event('place_linked') is None):
        return []
    relevant = bool(commit.sections.get('items') or commit.sections.get('promises')
                    or commit.sections.get('links')) or any(
        isinstance(row, dict) and row.get('rel') in {'held_by', 'adjacent_to'}
        for row in commit.sections.get('relations', []) or [])
    graph = world.get('systems', {}).get('ontology')
    if graph is not None:
        holders = {r.dst for r in graph.relations if r.rel == 'held_by' and r.is_current()}
        linked = {endpoint for r in graph.relations
                  if r.rel == 'adjacent_to' and r.is_current()
                  for endpoint in (r.src, r.dst)}
        relevant = relevant or any(
            isinstance(row, dict) and isinstance(row.get('id'), str) and (
                row.get('etype') == 'Object' or row['id'] in holders or row['id'] in linked or (
                    graph.get_entity(row['id']) is not None and
                    graph.get_entity(row['id']).etype == 'Object'))
            for section in ('entities', 'cast', 'places', 'factions')
            for row in commit.sections.get(section, []) or [])
    if not relevant:
        return []
    from kernel.clock import advance
    current = world.get('meta', {})
    day, band = current.get('day') or 1, current.get('band') or 0
    from systems.time import normalize_clock
    clock = normalize_clock(commit.sections.get('clock') or [], world)
    sections = {**commit.sections, 'clock': clock} if 'clock' in commit.sections else commit.sections
    if clock and clock[0].get('advance'):
        day, _ = advance(day, band, clock[0].get('days', 0), clock[0].get('bands', 0))
    preview = copy.deepcopy(world)
    action_turn = world.get('_action_turn')
    if type(action_turn) is not int or action_turn < 0:
        graph_turns = ([fact.ingest_turn for fact in graph.facts]
                       + [relation.ingest_turn for relation in graph.relations]) if graph is not None else []
        records = world.get('systems', {}).get('return_commitments', {}).get('records', {})
        turns = graph_turns + [record.get('created_turn', 0) for record in records.values()]
        action_turn = max((turn for turn in turns if type(turn) is int), default=0) + 1
    for section, declarations in creation_first_sections(sections):
        owner = registry.owner_of_section(section)
        if owner is None or not declarations:
            continue
        for index, declaration in enumerate(declarations):
            for event in owner.to_events(section, [declaration], turn=action_turn, day=day, scene='validation'):
                from kernel.place_integrity import place_event_error
                error = item_event_error(preview, event) or place_event_error(preview, event)
                if error:
                    field, code, hint = error
                    return [ValidationError(section, f'[{index}]' + ('.' + field if field else ''), code, hint)]
                event_owner = registry.owner_of_event(event['type'])
                if event_owner is not None:
                    try:
                        event_owner.apply(preview, event)
                    except ValueError as exc:
                        if section != 'promises':
                            raise
                        return [ValidationError(section, f'[{index}]', 'return_condition', str(exc))]
    return []
