"""Values shared by proposal validation, materialization and player read models."""
from dataclasses import dataclass

from kernel.clock import advance


def turn_end(world, clock):
    meta = world.get('meta', {})
    day, band = meta.get('day') or 1, meta.get('band') or 0
    row = clock[0] if isinstance(clock, list) and clock else {}
    if not isinstance(row, dict) or row.get('advance') is not True:
        return day, band
    days, bands = row.get('days', 0), row.get('bands', 0)
    if type(days) is not int or type(bands) is not int or min(days, bands) < 0:
        return day, band  # The clock section owner supplies the structural error.
    return advance(day, band, days, bands)


@dataclass(frozen=True)
class ItemCustody:
    holder: str
    dropped_by: str | None = None


def possessions_at(graph, day):
    """Use the active held_by relation, including who placed it on the ground."""
    if graph is None:
        return {}
    return {r.src: ItemCustody(r.dst, r.attrs.get('dropped_by')) for r in graph.relations
            if r.rel == 'held_by' and r.valid_at(day) and graph.get_entity(r.src)
            and graph.get_entity(r.src).etype == 'Object'}


def transfer_sequence(graph, day, actor, location, targets, rows):
    """Simulate transfers in declaration order. Never infer another person's consent."""
    positions = possessions_at(graph, day)
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get('op') != 'transfer':
            yield index
            continue
        item, target = row.get('item'), row.get('to')
        held = positions.get(item) if isinstance(item, str) else None
        if held is None or not isinstance(target, str):
            yield index
        elif held.holder == actor and target in targets:
            positions[item] = ItemCustody(target, actor if target == location else None)
        elif held.holder == location and held.dropped_by == actor and target == actor:
            positions[item] = ItemCustody(actor)
        else:
            yield index
