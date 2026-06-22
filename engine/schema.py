import uuid

EVENT_TYPES = frozenset({
    "action", "dialogue_beat", "relationship_change", "character_reveal",
    "character_development", "thread_open", "thread_advance", "thread_resolve",
    "promise_made", "promise_kept", "world_fact", "combat_result",
    "item_change", "level_change", "location_change", "villain_knowledge_gain",
    "player_choice", "landmark", "oracle_roll", "director_fired",
})

# Required fields (actors may be empty list; day may be 0)
_REQUIRED = ("id", "type", "day", "scene", "actors", "summary")

def make_event(type, day, scene, actors, summary, *, arc=None, deltas=None,
               thread_refs=None, chunk_ids=None, secrecy=None, roll=None, turn=None, id=None):
    ev = {
        "id": id or f"ev_{uuid.uuid4().hex[:12]}",
        "type": type, "day": day, "scene": scene, "arc": arc,
        "actors": list(actors), "summary": summary,
        "deltas": dict(deltas or {}), "thread_refs": list(thread_refs or []),
        "chunk_ids": list(chunk_ids or []), "secrecy": secrecy, "roll": roll,
        "turn": turn,
        "retracted": False,
    }
    validate_event(ev)
    return ev

def validate_event(ev, allowed_types=None):
    types = EVENT_TYPES if allowed_types is None else allowed_types
    for k in _REQUIRED:
        if k not in ev:
            raise ValueError(f"event missing required field: {k}")
    if ev["type"] not in types:
        raise ValueError(f"unknown event type: {ev['type']!r}")
    if not isinstance(ev["day"], int):
        raise ValueError("day must be an int")
    if not isinstance(ev["actors"], list):
        raise ValueError("actors must be a list")
    if not str(ev.get("summary", "")).strip():
        raise ValueError("summary must be a non-empty string")
