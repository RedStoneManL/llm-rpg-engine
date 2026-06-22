"""systems.scene — SceneSystem: owns the scene_advanced event.

Harness-authored only (no commit section). loop/turn.run_turn detects a scene
boundary (protagonist location changed OR day changed) after a turn and appends
a scene_advanced event carrying the next monotonic scene id, so the NEXT turn
opens the new scene.

meta.scene itself rides kernel projection (meta["scene"] = ev["scene"]); apply()
only tracks meta.scene_no (the int counter, for computing the next id) and
meta.scene_anchor (where/when the current scene began). Rewind-safe: both fold
from events, so /rewind that retracts scene_advanced events reverts the counter.
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem
from engine.log import get_logger

log = get_logger("systems.scene")


class SceneSystem(ContextSystem):
    """Owns scene_advanced. No commit section (harness-authored)."""

    name = "scene"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"scene_advanced"}

    def commit_sections(self) -> set[str]:
        return set()

    def empty_state(self) -> dict:
        return {}

    def apply(self, world: dict, event: dict) -> None:
        if event["type"] != "scene_advanced":
            return
        d = event.get("deltas", {})
        meta = world["meta"]
        # meta.scene is set by projection from event["scene"] (= the new id).
        meta["scene_no"] = d.get("scene_no", meta.get("scene_no") or 1)
        meta["scene_anchor"] = {"location": d.get("location"), "day": d.get("day")}
        log.debug("scene_advanced -> scene_no=%s anchor=%s",
                  meta["scene_no"], meta["scene_anchor"])
