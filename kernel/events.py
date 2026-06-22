from __future__ import annotations

import uuid

from engine.store import EventStore


def kernel_event(type, *, day, scene, summary, actors=None, deltas=None,
                 thread_refs=None, chunk_ids=None, secrecy=None, roll=None,
                 turn=None, id=None) -> dict:
    """Build an event dict without the closed-set check (the store enforces the
    registry's allow-set instead). Same shape as engine.schema.make_event."""
    return {
        "id": id or f"ev_{uuid.uuid4().hex[:12]}",
        "type": type, "day": day, "scene": scene, "arc": None,
        "actors": list(actors or []), "summary": summary,
        "deltas": dict(deltas or {}), "thread_refs": list(thread_refs or []),
        "chunk_ids": list(chunk_ids or []), "secrecy": secrecy, "roll": roll,
        "turn": turn, "retracted": False,
    }


def open_store(db_path, jsonl_path, allowed_types) -> EventStore:
    """An EventStore that accepts exactly the registry's declared event-types."""
    return EventStore(db_path, jsonl_path, allowed_types=set(allowed_types))
