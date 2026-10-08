"""systems.scene — SceneSystem: owns the scene_advanced event.

Harness-authored only (no commit section). loop/turn.run_turn detects a scene
boundary (protagonist location changed OR day changed) after a turn and appends
a scene_advanced event carrying the next monotonic scene id, so the NEXT turn
opens the new scene.

After the first boundary, this system owns meta.scene as well as scene_no and
scene_anchor. Other event envelopes identify provenance, including historical
summaries; they cannot change the current scene. All three fields fold from
active events, so rewind restores the previous boundary (or legacy fallback).
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
        meta["scene"] = event["scene"]
        meta["scene_no"] = d.get("scene_no", meta.get("scene_no") or 1)
        meta["scene_anchor"] = {"location": d.get("location"), "day": d.get("day")}
        log.debug("scene_advanced -> scene_no=%s anchor=%s",
                  meta["scene_no"], meta["scene_anchor"])
