from __future__ import annotations

from kernel.registry import Registry
from engine.log import get_logger

log = get_logger("kernel.projection")


def empty_world(registry: Registry) -> dict:
    return {
        "meta": {"day": None, "scene": None, "timeline": []},
        "systems": {s.name: s.empty_state() for s in registry.systems},
    }


def project(registry: Registry, events, *, before_apply=None) -> dict:
    """Fold events into a world: kernel-level meta + each system's slice."""
    world = empty_world(registry)
    n = 0
    for ev in events:
        if ev.get("retracted"):
            continue
        # Late historical annotations must not move the current scene/clock back.
        # System projections still receive every event and enforce their own
        # temporal invariants; a corrupt state transition is never skipped.
        current_day = world['meta'].get('day')
        if current_day is None or ev['day'] >= current_day:
            world["meta"]["day"] = ev["day"]
            world["meta"]["scene"] = ev["scene"]
        world["meta"]["timeline"].append(
            {"day": ev["day"], "scene": ev["scene"], "summary": ev["summary"]})
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
