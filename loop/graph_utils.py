"""loop.graph_utils — shared graph-walk utilities.

Canonical contained_by-walk helper used by loop.density, loop.lore_disclosure,
and any future module that needs to walk up the place hierarchy.
"""
from __future__ import annotations


def ancestor_of_level(g, place_id: str, day: int, level: int) -> str | None:
    """Walk contained_by relations upward until we find a Place with attrs.level==level.

    Returns the ancestor's id, or None if not found or a cycle is detected.
    Uses a seen-set to guard against cycles in the containment graph.
    """
    cur, seen = place_id, set()
    while cur and cur not in seen:
        seen.add(cur)
        e = g.get_entity(cur)
        if e and e.attrs.get("level") == level:
            return cur
        parents = g.neighbors(cur, "contained_by", day)
        cur = parents[0] if parents else None
    return None
