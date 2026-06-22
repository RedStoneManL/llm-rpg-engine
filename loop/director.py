"""loop.director — the post-turn 暗骰 director hook.

run_director(registry, store, world, *, scene_ordinal=None) -> list[dict]
    1. Mark prior pending directives consumed (they were shown last turn).
    2. Derive pacing from the event stream (engine.director.compute_pacing).
    3. Backstop: never fire two turns in a row.
    4. Build a deterministic Oracle from world["meta"]["campaign_seed"] via
       engine.oracle.scene_seed(seed, scene_ordinal) — reproducible/rewind-safe.
    5. Run the pure engine.director.director_check; on a fire append an
       oracle_roll (audit) + director_fired (directive) event to the store.
    Returns the list of appended events (possibly empty). Never raises on a
    missing seed — falls back to 0 so a pre-B campaign degrades to quiet-ish.

All randomness is seeded; offline-deterministic. Mirrors the digest_fleet hook:
the caller wraps this in a tracer span + non-fatal try/except and re-projects.
"""
from __future__ import annotations

from engine.oracle import Oracle, load_table, scene_seed
from engine.director import compute_pacing, director_check, pick_thread_to_advance
from kernel.events import kernel_event
from engine.log import get_logger

log = get_logger("loop.director")

_SPEEDS = ("快", "中", "慢")
_MAX_THREADS = 5

_TABLES_CACHE: dict | None = None


def _tables() -> dict:
    global _TABLES_CACHE
    if _TABLES_CACHE is None:
        _TABLES_CACHE = {
            "event_types": load_table("event_types"),
            "twists": load_table("twists"),
        }
    return _TABLES_CACHE



def _last_turn(events: list[dict]) -> int:
    return max((e.get("turn") or 0 for e in events), default=0)


def _last_fire_turn(events: list[dict]) -> int:
    """Highest turn number carrying a director_fired event, or -1 if none."""
    return max((e.get("turn") or 0 for e in events if e["type"] == "director_fired"),
               default=-1)


def _handle_dormant(store, world, events, oracle, *, scene, day, turn):
    """dormant_thread outcome: advance a due active thread if one is overdue,
    else open a new dormant thread while under the 3-5 band. Returns the
    appended event or None."""
    threads = (world.get("systems", {}).get("director", {}) or {}).get("threads", {})
    # 1) advance a due, non-dormant thread (reuse the tested scheduler)
    due = pick_thread_to_advance(events, threads, oracle)
    if due is not None:
        ev = kernel_event("thread_advance", day=day, scene=scene,
                          summary=f"暗线推进:{due}",
                          deltas={"id": due, "last_advanced_scene": scene},
                          turn=turn)
        store.append(ev)
        return ev
    # 2) else open a new dormant thread if under the band
    if len(threads) < _MAX_THREADS:
        tables = {"thread_archetypes": load_table("thread_archetypes"),
                  "npc_traits": load_table("npc_traits")}
        # draw a single distinct thread (avoid existing archetypes/traits)
        existing_arch = {th.get("archetype") for th in threads.values()}
        existing_trait = {th.get("trait") for th in threads.values()}
        for _ in range(50):
            arch = oracle.draw(tables["thread_archetypes"])
            trait = oracle.draw(tables["npc_traits"])
            if arch["name"] in existing_arch or trait["name"] in existing_trait:
                continue
            tid = f"th_{len(threads) + 1}_{arch.get('type', 'thread')}"
            ev = kernel_event("thread_open", day=day, scene=scene,
                              summary=f"休眠暗线:{arch['name']}",
                              deltas={"id": tid, "status": "活跃",
                                      "speed": _SPEEDS[oracle.randint(0, 2)],
                                      "dormant": True, "trait": trait["name"],
                                      "archetype": arch["name"],
                                      "event_type": arch.get("type"),
                                      "last_advanced_scene": scene},
                              turn=turn)
            store.append(ev)
            return ev
    return None


def run_director(registry, store, world: dict, *, scene_ordinal: int | None = None) -> list[dict]:
    events = list(store.iter_events())

    # (1) Consume directives shown last turn via an event-sourced watermark.
    # Emitting a directive_consumed event (rather than mutating the in-memory
    # world) ensures that project() rebuilds consumed state from the event log,
    # guaranteeing replay-safety: a fresh project() after this call will still
    # honour the consumption watermark.
    slice_ = world.get("systems", {}).get("director")
    if isinstance(slice_, dict):
        pending = slice_.get("pending", [])
        if pending:
            through_turn = max(d.get("turn", 0) for d in pending)
            ev_day = events[-1]["day"] if events else 1
            ev_scene = events[-1]["scene"] if events else "scene"
            consumed_ev = kernel_event(
                "directive_consumed", day=ev_day, scene=ev_scene,
                summary=f"directive consumed through turn={through_turn}",
                deltas={"through_turn": through_turn},
                turn=through_turn,
            )
            store.append(consumed_ev)

    pacing = compute_pacing(events)
    ordinal = scene_ordinal if scene_ordinal is not None else pacing["scene_ordinal"]

    # (3) Backstop: never fire on two consecutive turns (belt over the scene
    # cooldown). A fire occupies its OWN turn slot (stamped at max+1), so once the
    # next player turn applies its events, that prior fire sits at last_turn-1 —
    # hence ">= last_turn - 1", not "== last_turn" (the interleaved player/director
    # turn counter means a player turn is always the max, never a fire).
    last_turn = _last_turn(events)
    last_fire = _last_fire_turn(events)
    if last_fire >= 0 and last_fire >= last_turn - 1:
        log.debug("run_director: skip — director fired recently (last_fire=%d last_turn=%d)",
                  last_fire, last_turn)
        return []

    # (4) Deterministic Oracle, seeded on (campaign_seed, scene_ordinal, salt=turn).
    # The salt=next_turn varies the roll per TURN, so the director is not frozen
    # when the scene id is static across turns (this engine has no scene
    # progression yet — that is a separate feature; without the salt the per-scene
    # seed is constant for a whole campaign and the director never changes its
    # mind). next_turn is derived from the event stream, so rolls still reproduce
    # exactly on replay (rewind-safe).
    next_turn = last_turn + 1
    campaign_seed = (world.get("meta", {}) or {}).get("campaign_seed", 0)
    seed_int = scene_seed(campaign_seed, ordinal, next_turn)
    out = director_check(pacing["scenes_since_event"], pacing["tension"],
                         Oracle(seed_int), tables=_tables())

    if not out["triggered"]:
        log.debug("run_director: quiet (ordinal=%d turn=%d prob=%.2f roll=%.2f)",
                  ordinal, next_turn, out["prob"], out["roll"])
        return []

    scene = pacing["current_scene"] or "scene"
    day = events[-1]["day"] if events else 1
    et = out["seed"]["event_type"]
    tw = out["seed"]["twist"]

    audit = kernel_event(
        "oracle_roll", day=day, scene=scene,
        summary=f"暗骰 roll={out['roll']:.2f} prob={out['prob']:.2f}",
        deltas={"prob": out["prob"], "roll": out["roll"],
                "scene_ordinal": ordinal, "campaign_seed": campaign_seed},
        turn=next_turn,
    )
    store.append(audit)
    appended = [audit]

    if out["type"] == "dormant_thread":
        ev = _handle_dormant(store, world, events, Oracle(seed_int + 1),
                             scene=scene, day=day, turn=next_turn)
        if ev is not None:
            appended.append(ev)
    else:  # front_stage / crit → a directive the next turn weaves in
        directive = kernel_event(
            "director_fired", day=day, scene=scene,
            summary=f"突发:{et['name']}（{tw['name']}）",
            deltas={
                "type": out["type"], "magnitude": out["magnitude"],
                "valence": out["valence"],
                "event_type": et["name"], "event_hint": et.get("hint"),
                "twist": tw["name"], "twist_hint": tw.get("hint"),
            },
            turn=next_turn,
        )
        store.append(directive)
        appended.append(directive)
    log.debug("run_director: FIRED type=%s mag=%s appended=%d",
              out["type"], out["magnitude"], len(appended))
    return appended
