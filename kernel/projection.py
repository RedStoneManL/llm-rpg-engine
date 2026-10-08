from __future__ import annotations

from kernel.registry import Registry
from engine.log import get_logger

log = get_logger("kernel.projection")


def empty_world(registry: Registry) -> dict:
    return {
        "meta": {"day": None, "scene": None, "timeline": []},
        "systems": {s.name: s.empty_state() for s in registry.systems},
    }


def apply_event_metadata(world: dict, event: dict) -> None:
    """Fold event provenance without replacing an established active scene.

    SceneSystem creates scene_anchor when its first boundary is applied. Before
    that boundary, retain legacy envelope-based scene selection. Event day and
    timeline remain independent of active-scene ownership.
    """
    meta = world["meta"]
    if meta.get("day") is None or event["day"] >= meta["day"]:
        meta["day"] = event["day"]
        if "scene_anchor" not in meta:
            meta["scene"] = event["scene"]
    meta.setdefault("timeline", []).append({
        "day": event["day"], "scene": event["scene"], "summary": event["summary"]})


def project(registry: Registry, events, *, before_apply=None) -> dict:
    """Fold events into a world: kernel-level meta + each system's slice."""
    world = empty_world(registry)
    n = 0
    for ev in events:
        if ev.get("retracted"):
            continue
        apply_event_metadata(world, ev)
        owner = registry.owner_of_event(ev["type"])
        if owner is None:
            log.debug("no owner for event type=%s id=%s (ignored)", ev["type"], ev.get("id"))
            continue
        if before_apply is not None:
            before_apply(world, ev)
        owner.apply(world, ev)
        n += 1
    log.debug("project folded %d events across %d systems", n, len(registry.systems))
    return world
