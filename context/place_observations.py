"""Public, place-scoped observation context derived from durable events.

This is deliberately a projection, not a new source of world authority.  It
selects only explicitly tagged public observations from ``world_change``
events, then applies the same player-POV and action-capability boundaries used
by the UI.  Secret/global cascade records therefore cannot become native-author
grounding merely because they happen to mention a known place.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from context.access import pov_world
from context.capabilities import scene_capabilities


_OBSERVATION_KINDS = frozenset({"stable_layout", "single_observation"})
_MAX_SINGLE_OBSERVATIONS_PER_PLACE = 3


def public_place_observations(world: dict[str, Any], scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Return bounded, player-safe observations for the current local frontier.

    An entry needs all of the following to cross this boundary:

    * it was explicitly emitted as a public observation rather than a generic
      world/cascade event;
    * its place is visible in a redacted player POV; and
    * its place is the current location or a currently legal public exit.

    Stable layout records coalesce by ``(place, range)`` to the newest durable
    statement.  One-time observations retain only the newest small window per
    place.  This survives recap/history trimming without replaying an unbounded
    narration transcript, while keeping observations distinct from facts,
    discovery permissions, or global world claims.
    """
    actor = scene.get("protagonist")
    if not isinstance(actor, str) or not actor:
        return []
    view = pov_world(world, scene, pov=actor, redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    if graph is None:
        return []
    capability = scene_capabilities(world, scene)
    relevant_places = set(capability.allowed_move_ids)
    if capability.location:
        relevant_places.add(capability.location)

    raw_changes = (world.get("systems", {}).get("cascade", {}) or {}).get("changes", [])
    eligible: list[dict[str, Any]] = []
    for item in raw_changes:
        if not isinstance(item, dict):
            continue
        place = item.get("place")
        kind = item.get("observation_kind")
        summary = item.get("summary")
        observation_range = item.get("observation_range")
        if (
            item.get("public_observation") is not True
            or item.get("visibility") != "public"
            or kind not in _OBSERVATION_KINDS
            or not isinstance(place, str)
            or place not in relevant_places
            or graph.get_entity(place) is None
            or not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(observation_range, str)
            or not observation_range.strip()
        ):
            continue
        eligible.append({
            "source_event_id": str(item.get("event_id") or ""),
            "source": str(item.get("source") or "author_world"),
            "place": place,
            "day": int(item.get("day") or 0),
            "turn": int(item.get("turn") or 0),
            "scene": str(item.get("scene") or ""),
            "kind": kind,
            "range": observation_range.strip(),
            "summary": summary.strip(),
        })

    stable: dict[tuple[str, str], dict[str, Any]] = {}
    transient: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        if item["kind"] == "stable_layout":
            stable[(item["place"], item["range"])] = item
        else:
            transient[item["place"]].append(item)

    out = list(stable.values())
    for place, rows in transient.items():
        del place
        out.extend(rows[-_MAX_SINGLE_OBSERVATIONS_PER_PLACE:])
    kind_order = {"stable_layout": 0, "single_observation": 1}
    return sorted(out, key=lambda item: (
        item["place"], kind_order[item["kind"]], item["day"], item["turn"], item["source_event_id"],
    ))
