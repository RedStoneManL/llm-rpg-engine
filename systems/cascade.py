"""systems.cascade — CascadeSystem: owns cascade event types.

Phase C1: vertical world-cascade.

CascadeSystem owns three harness-authored event types:
  - place_evolved     : a sub-place's state changes due to a cascade
  - populace_shifted  : the populace mood in a sub-place shifts
  - world_change      : a significant world-level change (trigger for cascade)

It is harness-authored (no commit_sections), like DirectorSystem in Phase B.
Its slice in world["systems"]["cascade"]:
  {
    "queue":    [],        # C2: deferred remote-region hops
    "changes":  [],        # audit list of world_change records this session
    "consumed_through_turn": 0,
  }
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem, ValidationError
from kernel.events import kernel_event
from engine.log import get_logger

log = get_logger("systems.cascade")


class CascadeSystem(ContextSystem):
    """Owns place_evolved / populace_shifted / world_change event types.

    Harness-authored: no commit sections (like DirectorSystem).
    apply() writes to the shared FactGraph (world["systems"]["ontology"])
    and to the cascade slice (world["systems"]["cascade"]).
    """

    name = "cascade"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"place_evolved", "populace_shifted", "world_change"}

    def commit_sections(self) -> set[str]:
        return {"world"}

    def empty_state(self) -> dict:
        return {"queue": [], "changes": [], "consumed_through_turn": 0}

    def apply(self, world: dict, event: dict) -> None:
        """Project one cascade event into the shared graph + cascade slice.

        Defensive: any missing/dangling place id emits a warning and skips the
        fact write. Projection must never crash on a stored event (invariant 11).
        """
        g = world["systems"]["ontology"]
        d = event.get("deltas", {})
        t = event["type"]
        ev_id = event.get("id", "")
        day = event.get("day", 1)
        turn = event.get("turn") or 0

        if t == "place_evolved":
            pid = d.get("id")
            if not pid:
                log.warning(
                    "place_evolved event missing id in deltas; skipped. event_id=%s", ev_id
                )
                return
            entity = g.get_entity(pid)
            if entity is None:
                log.warning(
                    "place_evolved dangling id=%s; skipped. event_id=%s", pid, ev_id
                )
                return
            if d.get("state"):
                g.assert_fact(pid, "state", d["state"],
                              day=day, turn=turn, source_event=ev_id)
            # Always stamp last_cascade_turn (C1) and last_update (Phase D)
            entity.attrs["last_cascade_turn"] = turn
            entity.attrs["last_update"] = day
            log.debug("place_evolved applied id=%s state=%r", pid, d.get("state"))

        elif t == "populace_shifted":
            pid = d.get("id")
            if not pid:
                log.warning(
                    "populace_shifted event missing id; skipped. event_id=%s", ev_id
                )
                return
            entity = g.get_entity(pid)
            if entity is None:
                log.warning(
                    "populace_shifted dangling id=%s; skipped. event_id=%s", pid, ev_id
                )
                return
            if d.get("mood"):
                g.assert_fact(pid, "populace", d["mood"],
                              day=day, turn=turn, source_event=ev_id)
            log.debug("populace_shifted applied id=%s mood=%r", pid, d.get("mood"))

        elif t == "world_change":
            place = d.get("place")
            slice_ = world["systems"]["cascade"]

            # C2 Task 10: handle bookkeeping-only marker first (no audit/fact).
            # A world_change carrying deferred_consume_through advances the
            # consume watermark and does NOTHING else (no enqueue, no fact).
            if d.get("deferred_consume_through") is not None:
                new_through = int(d["deferred_consume_through"])
                slice_["consumed_through_turn"] = max(
                    slice_.get("consumed_through_turn", 0), new_through
                )
                log.debug(
                    "world_change consume watermark → consumed_through_turn=%d", new_through
                )
                return

            # Always record audit entry (even if place is None/dangling)
            if place:
                entry = {
                    "place": place,
                    "level": d.get("level", 1),
                    "summary": event.get("summary"),
                    "valence": d.get("valence"),
                    "turn": turn,
                }
                slice_["changes"].append(entry)
                entity = g.get_entity(place)
                if entity is None:
                    log.warning(
                        "world_change dangling place=%s; fact skipped. event_id=%s",
                        place, ev_id,
                    )
                else:
                    g.assert_fact(place, "world_change", event.get("summary", ""),
                                  day=day, turn=turn, source_event=ev_id)
                    log.debug("world_change applied place=%s level=%d",
                              place, d.get("level", 1))

                # C2 Task 10: if this is a deferral marker, enqueue it.
                # The queue is event-sourced: projection replays this branch on
                # every project() call so the queue is rebuilt deterministically
                # and survives rewind.
                if d.get("deferred"):
                    queue_entry = {
                        "region": place,
                        "level": d.get("level", 1),
                        "reason": d.get("reason"),
                        "depth": d.get("depth"),
                        "enqueue_turn": turn,
                        "consumed": False,
                    }
                    slice_["queue"].append(queue_entry)
                    log.debug(
                        "world_change deferral queued region=%s enqueue_turn=%d reason=%s",
                        place, turn, d.get("reason"),
                    )
            else:
                log.warning(
                    "world_change event missing place; audit skipped. event_id=%s", ev_id
                )

    # -------------------------------------------------------------------------
    # P1: LLM-authored `world` commit section
    # -------------------------------------------------------------------------

    _VALID_LEVELS = frozenset({1, 2, 3})

    def created_ids(self, section: str, decl) -> set:
        # The `world` section REFERENCES existing places; it never creates ids.
        return set()

    def validate(self, section: str, decl, world: dict) -> list:
        """Validate the LLM-authored `world` section (strict-gate path).

        Per item: areas non-empty list of existing Place ids (or same-commit
        stubs); level in 1..3; summary non-empty. Codes mirror PlaceSystem.
        """
        if section != "world":
            return []
        g = world.get("systems", {}).get("ontology")
        errs: list = []
        for i, item in enumerate(decl or []):
            areas = item.get("areas")
            if not isinstance(areas, list) or not areas:
                errs.append(ValidationError(
                    section="world", field=f"[{i}].areas", code="missing",
                    hint="world 段每项必须给出非空 areas（受影响地点 id 数组）"))
            else:
                for j, area in enumerate(areas):
                    if not isinstance(area, str) or not area:
                        errs.append(ValidationError(
                            section="world", field=f"[{i}].areas[{j}]", code="missing",
                            hint="areas 每个元素必须是非空的地点 id 字符串"))
                    elif g is not None and g.get_entity(area) is None:
                        errs.append(ValidationError(
                            section="world", field=f"[{i}].areas[{j}]", code="dangling_ref",
                            hint=f"受影响地点 '{area}' 不存在于图中"))
            level = item.get("level")
            if level is None:
                errs.append(ValidationError(
                    section="world", field=f"[{i}].level", code="missing",
                    hint="world 段每项必须给出 level（1/2/3）"))
            elif level not in self._VALID_LEVELS:
                errs.append(ValidationError(
                    section="world", field=f"[{i}].level", code="bad_enum",
                    hint=f"level 必须为 1/2/3，当前值: {level!r}"))
            summary = item.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                errs.append(ValidationError(
                    section="world", field=f"[{i}].summary", code="missing",
                    hint="world 段每项必须给出 summary（一句话事件描述）"))
        return errs

    def to_events(self, section: str, decl, *, turn: int, day: int, scene: str) -> list:
        """Explode the `world` section into one world_change event per area.

        Decision (DD1): one-world_change-per-area, so each area is a clean cascade
        root and reuses the existing world_change apply branch verbatim.
        """
        out: list = []
        if section != "world":
            return out
        for item in decl or []:
            summary = item.get("summary", "")
            level = item.get("level", 1)
            for area in item.get("areas") or []:
                out.append(kernel_event(
                    "world_change", day=day, scene=scene, summary=summary,
                    deltas={"place": area, "level": level, "summary": summary},
                    turn=turn,
                ))
        return out
