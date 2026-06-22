"""loop.time — time model helpers + catch-up hook (Phase D).

Public API:
    current_day(world) -> int
    detect_jump(this_turn_events, world, *, all_events) -> tuple[int, int, bool]
    stale_entering_scope(world, prev_scene, new_scene, *, now) -> list[str]
    run_catchup(registry, store, world, prev_scene, new_scene, *, provider,
                catchup_provider=None) -> list[dict]
"""
from __future__ import annotations

from kernel.events import kernel_event
from loop.cascade import lightweight_validate
from engine.log import get_logger
from llm.structured import complete_structured

log = get_logger("loop.time")

JUMP_THRESHOLD = 2
CATCHUP_BUDGET = 4


def current_day(world: dict) -> int:
    """Return current day from meta, defaulting to 1."""
    return (world.get("meta") or {}).get("day") or 1


def detect_jump(
    this_turn_events: list[dict],
    world: dict,
    *,
    all_events: list[dict],
) -> tuple[int, int, bool]:
    """Detect if this turn represents a time jump (delta >= JUMP_THRESHOLD).

    Returns (prev_day, now, jumped).
    prev_day = max day among events NOT in this turn's turn number.
    now      = current_day(world).
    jumped   = (now - prev_day) >= JUMP_THRESHOLD.
    """
    now = current_day(world)
    this_turns = {e.get("turn") for e in this_turn_events}
    prev_day = max(
        (e.get("day") or 1 for e in all_events if e.get("turn") not in this_turns),
        default=now,
    )
    jumped = (now - prev_day) >= JUMP_THRESHOLD
    return prev_day, now, jumped


def stale_entering_scope(
    world: dict,
    prev_scene: dict,
    new_scene: dict,
    *,
    now: int,
) -> list[str]:
    """Return sorted list of entity ids to catch up this turn.

    Selects tracked Person/Place entities that:
    1. Enter scope this turn (in new_scene["present"] but not in prev_scene["present"]
       and not the protagonist — protagonist is excluded, driven live).
    2. Are stale: last_update < now.
    3. Are not the protagonist.

    §14 断点3: only entities entering scope are caught up; off-screen entities
    keep their last_update. Conflict rule: last_update == now => skip.

    v1 note: Place scope from subtree is deferred; new_scope is the Person
    present set only.
    """
    g = world["systems"]["ontology"]
    prev_scope = {prev_scene.get("protagonist")} | set(prev_scene.get("present") or [])
    # Protagonist is driven live — exclude from catch-up candidates.
    new_scope = set(new_scene.get("present") or [])

    result = []
    for eid in sorted(new_scope - prev_scope):
        e = g.get_entity(eid)
        if e is None or e.tier != "tracked" or e.etype not in {"Person", "Place"}:
            continue
        lu = e.attrs.get("last_update")
        if lu is None or lu >= now:
            continue
        result.append(eid)
    return result


# ---------------------------------------------------------------------------
# Catch-up prompt / schema
# ---------------------------------------------------------------------------

_CATCHUP_SYSTEM = """\
你是 TRPG 世界引擎的角色/地点状态推演模块。
一个被追踪的角色或地点离开镜头一段时间后，请推演它在此期间发生的变化。
只输出一个 JSON 对象（不要任何散文/代码块）。
对于角色：predicate（属性名，如 mood/arc/状态）和 value（新值，中文）。
对于地点：state（地点新状态，中文）和 populace_mood（民众情绪，可选，中文）。
note 字段简短描述变化原因。changed:false 表示实质未变（只是时间流逝）。"""

_CATCHUP_SCHEMA = {
    "type": "object",
    "properties": {
        "id":             {"type": "string"},
        "changed":        {"type": "boolean"},
        "predicate":      {"type": "string"},
        "value":          {"type": "string"},
        "state":          {"type": "string"},
        "populace_mood":  {"type": "string"},
        "note":           {"type": "string"},
    },
    "required": ["changed"],
}


def _catchup_prompt(eid: str, kind: str, span: int, context: str) -> str:
    """Build the user prompt for one catch-up call.

    The entity id is embedded verbatim so a keyed fake (and any real model)
    can answer per-entity without relying on call order (D5 hazard mitigation).
    """
    return (
        f"实体 id：{eid}\n"
        f"类型：{kind}\n"
        f"离开镜头已有 {span} 天。\n"
        f"世界背景：{context}\n\n"
        f"请推演「{eid}」在这 {span} 天内的变化。"
        f"若有实质变化(changed:true)，给出相应字段；若无(changed:false)，"
        f"直接输出 {{\"changed\": false}}。"
    )


def _catchup_validate(kind: str):
    """Return a validator for catch-up responses, kind-dependent (Person or Place)."""
    def _v(obj):
        if not isinstance(obj, dict):
            return ['response must be a single JSON object']
        if not isinstance(obj.get("changed"), bool):
            return ['missing required boolean field "changed"']
        if not obj["changed"]:
            return []
        errs = []
        if kind == "Person":
            if not (isinstance(obj.get("predicate"), str) and obj["predicate"].strip()):
                errs.append('when "changed" is true for a character, provide a non-empty "predicate"')
            if not (isinstance(obj.get("value"), str) and obj["value"].strip()):
                errs.append('when "changed" is true for a character, provide a non-empty "value"')
        else:
            if not (isinstance(obj.get("state"), str) and obj["state"].strip()):
                errs.append('when "changed" is true for a place, provide a non-empty "state"')
        return errs
    return _v


def _next_turn(store) -> int:
    """Max turn in store + 1."""
    max_t = 0
    for ev in store.iter_events():
        t = ev.get("turn") or 0
        if t > max_t:
            max_t = t
    return max_t + 1


# ---------------------------------------------------------------------------
# run_catchup
# ---------------------------------------------------------------------------

def run_catchup(
    registry,
    store,
    world: dict,
    prev_scene: dict,
    new_scene: dict,
    *,
    provider,
    catchup_provider=None,
) -> list[dict]:
    """Post-cascade catch-up hook (Phase D).

    For each tracked entity entering scope this turn with last_update < now:
    - If changed: emit character_evolved or place_evolved (via lightweight_validate).
    - Always: emit time_advanced currency carrier so entity is not re-queried.

    Budget cap: CATCHUP_BUDGET calls max per turn.
    Returns list of appended events.
    """
    cp = catchup_provider or provider
    now = current_day(world)

    ids = stale_entering_scope(world, prev_scene, new_scene, now=now)
    if not ids:
        return []

    if len(ids) > CATCHUP_BUDGET:
        log.info(
            "catchup: budget %d hit; %d deferred: %s",
            CATCHUP_BUDGET, len(ids) - CATCHUP_BUDGET, ids[CATCHUP_BUDGET:],
        )
        ids = ids[:CATCHUP_BUDGET]

    g = world["systems"]["ontology"]
    turn = _next_turn(store)
    appended = []
    scene_id = new_scene.get("id") or new_scene.get("location") or "scene"
    context = new_scene.get("summary", "") or f"当前场景 {scene_id}"

    for eid in ids:
        e = g.get_entity(eid)
        kind = "Person" if e.etype == "Person" else "Place"
        span = now - e.attrs.get("last_update", now)

        obj, errors = complete_structured(
            cp,
            system=_CATCHUP_SYSTEM,
            user=_catchup_prompt(eid, kind, span, context),
            validate=_catchup_validate(kind),
            max_repairs=1,
            schema_reminder='Required: "changed" (boolean). If true — character: "predicate"+"value"; place: "state" (all Chinese). Optional: "populace_mood", "note".',
            log_label="catchup",
        )
        raw = obj if (isinstance(obj, dict) and not errors) else {}
        # Harness owns the id — override whatever the model returned
        raw = {**raw, "id": eid}

        if raw.get("changed") and lightweight_validate(raw, g, set()) is not None:
            if kind == "Person":
                ev = kernel_event(
                    "character_evolved",
                    day=now, scene=scene_id,
                    summary=f"{eid} 时移境迁",
                    deltas={
                        "id": eid,
                        "predicate": raw.get("predicate", "arc"),
                        "value": raw.get("value", ""),
                        "op": "evolve",
                    },
                    turn=turn,
                )
                store.append(ev)
                appended.append(ev)
                log.debug("catchup: character_evolved appended id=%s predicate=%s",
                          eid, raw.get("predicate"))
            else:
                ev = kernel_event(
                    "place_evolved",
                    day=now, scene=scene_id,
                    summary=f"{eid} 时移境迁",
                    deltas={
                        "id": eid,
                        "state": raw.get("state", ""),
                        "note": raw.get("note", ""),
                    },
                    turn=turn,
                )
                store.append(ev)
                appended.append(ev)
                pm = raw.get("populace_mood")
                if isinstance(pm, str) and pm.strip():
                    ev_mood = kernel_event(
                        "populace_shifted",
                        day=now, scene=scene_id,
                        summary=f"{eid} 民心变化",
                        deltas={
                            "id": eid,
                            "mood": pm.strip(),
                            "note": raw.get("note", ""),
                        },
                        turn=turn,
                    )
                    store.append(ev_mood)
                    appended.append(ev_mood)
        else:
            # changed:false or dropped → emit currency carrier so we don't re-ask
            ev_noop = kernel_event(
                "time_advanced",
                day=now, scene=scene_id,
                summary=f"{eid} currency",
                deltas={"id": eid, "to_day": now, "reason": "catchup-noop"},
                turn=turn,
            )
            store.append(ev_noop)
            appended.append(ev_noop)

        turn = _next_turn(store)  # refresh turn after each append

    log.debug("run_catchup: done appended=%d", len(appended))
    return appended
