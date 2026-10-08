"""Active scene history for event consumers, independent of event provenance."""
from kernel.projection import apply_event_metadata


def iter_scene_history(events):
    """Yield (event, active_scene, ordinal) for each surviving event.

    Match projection's day-gated legacy fallback until the first surviving
    scene_advanced. Thereafter only explicit boundaries can change the scene.
    Ordinals count visits, not distinct labels or numeric scene-id suffixes.
    """
    world = {"meta": {"day": None, "scene": None}}
    previous = object()
    ordinal = 0
    for event in events:
        if event.get("retracted"):
            continue
        apply_event_metadata(world, event)
        meta = world["meta"]
        # Timeline entries are provenance, not needed by this streaming fold.
        meta["timeline"].clear()
        if event["type"] == "scene_advanced":
            meta["scene"] = event["scene"]
            meta["scene_anchor"] = True
        scene = meta["scene"]
        if scene != previous:
            ordinal += 1
            previous = scene
        yield event, scene, ordinal
