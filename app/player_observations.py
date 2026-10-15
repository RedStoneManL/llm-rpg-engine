"""Small player presentation DTO for durable, local place observations.

The author receives a richer ``public_place_observations`` structure so it can
retain event provenance while writing.  A player needs the observation itself,
not its source event, author identity, historical scene, or raw place id.  This
adapter intentionally strips those implementation details and preserves the
same current-location / legal-exit boundary from the context layer.
"""
from __future__ import annotations

from typing import Any

from context.capabilities import scene_capabilities
from context.place_observations import public_place_observations


_KIND_LABELS = {"stable_layout": "稳定布局", "single_observation": "现场观察"}


def player_place_observations(world: dict[str, Any], scene: dict[str, Any]) -> list[dict[str, str | bool]]:
    """Return only display-safe observations at the current local frontier.

    The source projection already verifies public tags, protagonist POV, and
    current-or-legal-exit locality.  Convert its internal place id and event
    provenance into a current-place marker or an already-visible place label;
    do not add a fallback canonical lookup.
    """
    capability = scene_capabilities(world, scene)
    labels = {place.id: place.label for place in capability.places}
    current = capability.location
    items: list[dict[str, str | bool]] = []
    for observation in public_place_observations(world, scene):
        place = observation["place"]
        label = labels.get(place)
        if label is None:
            # Defensive only: the source contract should already make this
            # impossible, but raw ids are never a player-facing fallback.
            continue
        kind = observation["kind"]
        items.append({
            "place": "这里" if place == current else label,
            "is_current": place == current,
            "kind": _KIND_LABELS.get(kind, "现场观察"),
            "range": observation["range"],
            "summary": observation["summary"],
        })
    return items
