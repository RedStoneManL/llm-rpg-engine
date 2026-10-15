"""Small current-scene character read model shared by player adapters.

The normal author already receives a deliberately narrow current-scene view:
only public, currently observable people and their observable sketch/mood/state.
Player UI must not independently reconstruct that set from canonical entities,
or it can drift into revealing a hidden co-located person.  This module strips
the author-only evidence from that existing projection and exposes only the
small presentation slice needed by CLI and dashboard orientation.
"""
from __future__ import annotations

from typing import Any

from context.native_authoring import native_authoring_context


_OBSERVABLE_LABELS = {"mood": "心情", "state": "状态"}


def player_present_people(world: dict[str, Any], scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Return currently observable people without author evidence or raw facts.

    ``native_authoring_context`` is already the receipt-safe, current-scene
    boundary used by normal player action handling.  Reusing it means the
    display and explicit-speech selector share the same public co-presence
    rule, while this DTO intentionally discards NPC knowledge, reports,
    arrival previews, route grants, and all private/canonical material.
    """
    people: list[dict[str, Any]] = []
    for person in native_authoring_context(world, scene).get("present_people", []):
        if not isinstance(person, dict) or not isinstance(person.get("id"), str):
            continue
        observable = person.get("observable") if isinstance(person.get("observable"), dict) else {}
        label = observable.get("sketch") if isinstance(observable.get("sketch"), str) else person["id"]
        details = [
            {"name": label_name, "value": value}
            for key, label_name in _OBSERVABLE_LABELS.items()
            if isinstance((value := observable.get(key)), str) and value
        ]
        people.append({"id": person["id"], "label": label, "observables": details})
    return people
