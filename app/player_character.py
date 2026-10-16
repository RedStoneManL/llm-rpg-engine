"""Small protagonist-POV character status read model for player adapters."""
from __future__ import annotations

import json
from typing import Any

from context.access import PLAYER_HIDDEN_FACT_SECRECIES

_MAX_DISPLAY_CHARS = 500


def _bounded_text(text: str) -> str:
    """Keep a player DTO field bounded without splitting its visible cue."""
    if len(text) <= _MAX_DISPLAY_CHARS:
        return text
    return text[:_MAX_DISPLAY_CHARS - 1] + "…"


def _safe_value(value: Any) -> str:
    """Render a bounded scalar-like value without passing structured state through."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _bounded_text(str(value))
    try:
        return _bounded_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return _bounded_text(str(value))


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
        {"name": _bounded_text(str(fact.predicate)), "value": _safe_value(fact.value)}
        for fact in graph.current_facts(protagonist)
        if fact.predicate != "hidden" and fact.secrecy not in PLAYER_HIDDEN_FACT_SECRECIES
    ]
