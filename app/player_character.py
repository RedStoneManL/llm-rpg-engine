"""Small protagonist-POV character status read model for player adapters."""
from __future__ import annotations

import json
from typing import Any


def _safe_value(value: Any) -> str:
    """Render a bounded scalar-like value without passing structured state through."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)[:500]
    except (TypeError, ValueError):
        return str(value)[:500]


def player_character_facts(view: dict[str, Any], protagonist: str | None) -> list[dict[str, str]]:
    """Return only visible, non-secret current facts for the player protagonist.

    Callers must provide the protagonist's already-redacted POV projection. This
    keeps CLI and dashboard status panels out of canonical facts, raw events,
    and private interpretive-memory state.
    """
    systems = view.get("systems") if isinstance(view, dict) else {}
    systems = systems if isinstance(systems, dict) else {}
    graph = systems.get("ontology")
    if graph is None or not isinstance(protagonist, str):
        return []
    return [
        {"name": str(fact.predicate), "value": _safe_value(fact.value)}
        for fact in graph.current_facts(protagonist)
        if fact.predicate != "hidden" and fact.secrecy != "secret"
    ]
