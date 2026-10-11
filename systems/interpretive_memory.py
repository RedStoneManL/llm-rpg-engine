"""Opt-in, event-sourced private character interpretations.

These records are deliberately not facts, permissions, or prompt context.  A
host may opt in by registering this system in an isolated registry; the normal
application registry does not register it.  Reading is actor-scoped through
``records_for_actor`` and never uses ``inject`` or ``recall``.
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem
from engine.log import get_logger

log = get_logger("systems.interpretive_memory")


class InterpretiveMemorySystem(ContextSystem):
    """Private, replayable interpretations with no commit section."""

    name = "interpretive_memory"

    def event_types(self) -> set[str]:
        return {"character_interpretation_recorded"}

    def commit_sections(self) -> set[str]:
        # A model cannot publish these through a foreground TurnCommit.
        return set()

    def empty_state(self) -> dict:
        return {"by_actor": {}}

    def apply(self, world: dict, event: dict) -> None:
        data = event.get("deltas") or {}
        actor = data.get("actor")
        text = data.get("text")
        memory_id = data.get("memory_id") or event.get("id")
        if not isinstance(actor, str) or not actor or not isinstance(text, str) or not text:
            # Do not include private text in diagnostics.
            log.warning("interpretive memory event ignored: malformed actor/text (id=%s)", event.get("id"))
            return
        bucket = world["systems"][self.name]["by_actor"].setdefault(actor, [])
        if any(item["id"] == memory_id for item in bucket):
            return
        bucket.append({
            "id": memory_id,
            "text": text,
            "day": event["day"],
            "turn": event.get("turn"),
            "source_event": event.get("id"),
        })


def records_for_actor(world: dict, actor: str) -> list[dict]:
    """Return copies of only one actor's private interpretations."""
    state = world.get("systems", {}).get(InterpretiveMemorySystem.name) or {}
    return [dict(item) for item in state.get("by_actor", {}).get(actor, [])]
