"""Shared, protagonist-POV objective ledger for player-facing adapters."""
from __future__ import annotations

from typing import Any


def player_objectives(view: dict[str, Any], protagonist: str | None) -> dict[str, Any]:
    """Return only the public goal and explicit LoreSystem task ledger.

    The caller supplies an already-redacted POV projection.  Dark lore, stages,
    provenance and private interpretations are deliberately absent from this
    compact read model.
    """
    systems = view.get("systems") if isinstance(view, dict) else {}
    systems = systems if isinstance(systems, dict) else {}
    graph = systems.get("ontology")
    goal: str | None = None
    if graph is not None and isinstance(protagonist, str):
        entity = graph.get_entity(protagonist)
        if entity is not None and isinstance(entity.attrs.get("goal"), str):
            goal = entity.attrs["goal"]
        for fact in graph.current_facts(protagonist):
            if fact.predicate in {"目标", "goal"} and isinstance(fact.value, str):
                goal = fact.value

    lines = ((systems.get("lore") or {}).get("lines", {})
             if isinstance(systems.get("lore"), dict) else {})
    active: list[dict[str, str]] = []
    completed: list[dict[str, str]] = []
    if isinstance(lines, dict):
        for quest_id, line in sorted(lines.items()):
            if not isinstance(quest_id, str) or not isinstance(line, dict):
                continue
            if line.get("state") == "明" and isinstance(line.get("summary"), str):
                active.append({"id": quest_id, "summary": line["summary"]})
                continue
            resolved = line.get("resolved")
            summary = resolved.get("summary") if isinstance(resolved, dict) else None
            summary = summary if isinstance(summary, str) and summary else line.get("summary")
            if (line.get("state") == "了结" and isinstance(line.get("surfaced_turn"), int)
                    and isinstance(summary, str)):
                completed.append({"id": quest_id, "summary": summary})
    return {"long_term_goal": goal, "active_quests": active, "completed_quests": completed}
