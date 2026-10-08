"""Checks for NEW passage mutations, separate from historical projection.

An adjacency change describes world state, not knowledge or permission to move.
"""
from __future__ import annotations


def link_errors(graph, row, day, *, pending_types=False, check_state=True):
    """Return field/code/hint triples for one ordered links declaration.

    Structural validation permits same-commit pending entity stubs and defers
    state checks. The ordered event preview supplies concrete endpoint types
    and the state produced by preceding rows.
    """
    if not isinstance(row, dict):
        return [('', 'bad_shape', 'links 声明必须是对象')]
    errors = []
    op = row.get('op', 'open')
    if op not in ('open', 'close'):
        errors.append(('op', 'bad_enum', 'links 的 op 只能是 open 或 close；省略时表示 open'))

    a, b = row.get('a'), row.get('b')
    endpoints_valid = True
    for field, value in (('a', a), ('b', b)):
        if not isinstance(value, str) or not value.strip():
            errors.append((field, 'missing', '通道端点必须是非空地点 id 字符串'))
            endpoints_valid = False
            continue
        if graph is not None:
            entity = graph.get_entity(value)
            if entity is None:
                errors.append((field, 'dangling_ref', '通道端点必须先声明为 Place 实体'))
            elif entity.etype != 'Place' and not (pending_types and entity.etype == '_pending'):
                errors.append((field, 'place_type', '通道端点必须是 Place，不能把角色或物品作为地点'))
    if endpoints_valid and a == b:
        errors.append(('b', 'same_place', '通道两端必须是不同地点'))

    if op == 'open':
        cost = row.get('travel_cost', 1)
        if type(cost) is not int or cost < 0:
            errors.append(('travel_cost', 'bad_value', 'travel_cost 必须是非负整数（不能是布尔值）；省略时为 1'))
    elif op == 'close' and 'travel_cost' in row:
        errors.append(('travel_cost', 'forbidden', '关闭通道时必须省略 travel_cost；关闭不会修改历史成本'))

    if errors or not check_state or graph is None:
        return errors
    current = [relation for relation in graph.relations
               if relation.rel == 'adjacent_to' and relation.is_current()
               and ((relation.src == a and relation.dst == b)
                    or (relation.src == b and relation.dst == a))]
    if any(relation.event_time_start > day for relation in current):
        errors.append(('', 'link_time', '通道变更日期不能早于现有通道记录；不能倒写历史'))
    elif op == 'close' and not current:
        errors.append(('', 'link_absent', '只能关闭当前存在的通道；没有通道变化时请删除这条 links 声明'))
    return errors


def place_event_error(world, event):
    """Check one NEW event against the state immediately before applying it."""
    graph = world.get('systems', {}).get('ontology')
    kind, data = event['type'], event.get('deltas', {})
    if kind == 'relation_added' and data.get('rel') == 'adjacent_to':
        return ('', 'place_route', 'adjacent_to 必须通过 links 的 open/close 操作修改；删除这条 relations 声明')
    if kind in ('place_linked', 'place_unlinked'):
        op = 'open' if kind == 'place_linked' else 'close'
        if 'op' in data and data['op'] != op:
            return ('op', 'bad_enum', '通道事件类型必须与 links 的 open/close 操作一致')
        errors = link_errors(graph, {**data, 'op': op}, event['day'])
        return errors[0] if errors else None
    if graph is None:
        return None

    # Re-declaring a linked endpoint must not evade the Place type gate. Keep
    # this on the new-write path; existing saves remain replayable as stored.
    types = {'object_created': 'Object', 'character_created': 'Person',
             'place_created': 'Place', 'faction_created': 'Faction'}
    entity_type = data.get('etype') if kind == 'entity_created' else types.get(kind)
    entity_id = data.get('id')
    old = graph.get_entity(entity_id) if isinstance(entity_id, str) else None
    if old and old.etype == 'Place' and entity_type and entity_type != 'Place':
        if any(relation.rel == 'adjacent_to' and relation.is_current()
               and entity_id in (relation.src, relation.dst)
               for relation in graph.relations):
            return ('id', 'place_type_change', '现有通道端点必须保持 Place 类型；不能通过重复声明改变类型')
    return None
