"""loop.lore — lore creation helper + the per-turn 暗骰 hook (L1).

create_lore_line: validate a pre-generated skeleton, append a lore_created event.
run_lore: post-apply fleet hook (mirrors loop.director.run_director). For each
active line, a seeded Oracle.d100() (rewind-safe via scene_seed) vs the line's
threshold; on a pass, advance one stage and emit lore_advanced carrying that
stage's clue. Cheap, no LLM, deterministic. Guarded by registry ownership so it
is a clean no-op when LoreSystem is not registered.
"""
from __future__ import annotations

from engine.oracle import Oracle, scene_seed
from kernel.events import kernel_event
from engine.log import get_logger
from llm.structured import complete_structured
from loop.graph_utils import ancestor_of_level

log = get_logger("loop.lore")

_REQUIRED = ("id", "complexity", "about", "anchor", "stages", "threshold",
             "description", "trigger", "l3_anchor")


def create_lore_line(store, skeleton: dict, *, day: int, scene: str, turn: int,
                     lifespan_days: int | None = None) -> dict:
    """Validate a skeleton and append a lore_created event. Returns the event.

    lifespan_days: if provided, overrides the default complexity-based lifespan
    (L3 hook — lets density generation pass an LLM-chosen lifespan). If not provided,
    LoreSystem.apply defaults by complexity (simple=3, medium=7, complex=20).
    """
    missing = [k for k in _REQUIRED if k not in skeleton]
    if missing:
        raise ValueError(f"lore skeleton missing required fields: {missing}")
    deltas = dict(skeleton)
    if lifespan_days is not None:
        deltas["lifespan_days"] = lifespan_days
    ev = kernel_event("lore_created", day=day, scene=scene,
                      summary=f"暗线生成:{skeleton['id']}",
                      deltas=deltas, turn=turn)
    store.append(ev)
    log.debug("create_lore_line id=%s complexity=%s lifespan_days=%s",
              skeleton["id"], skeleton.get("complexity"), lifespan_days)
    return ev


def fetch_lore(line: dict, depth: int) -> dict:
    """Graded disclosure of one lore line. depth 0=index, 1=current beat, 2=history+secret-edge."""
    out = {"id": line.get("id") if "id" in line else None,
           "description": line.get("description"),
           "trigger": line.get("trigger")}
    # line dicts in the slice are keyed by id elsewhere; carry id if present
    if out["id"] is None:
        out.pop("id")
    if depth >= 1:
        stages = line.get("stages", [])
        idx = line.get("stage_idx", -1)
        beat = (stages[idx].get("hint") if 0 <= idx < len(stages)
                and isinstance(stages[idx], dict) else None)
        clues = line.get("clues_dropped", [])
        out.update({"about": line.get("about"), "stage_idx": idx,
                    "beat": beat, "latest_clue": clues[-1] if clues else None})
    if depth >= 2:
        secret = line.get("secret") or ""
        out.update({"clues": list(line.get("clues_dropped", [])),
                    "anchor": line.get("anchor"),
                    # secret_edge = a deniable nudge toward (not a reveal of) the secret
                    "secret_edge": ("有迹象指向更深的隐情" if secret else None)})
    return out


def run_lore(registry, store, world: dict) -> list[dict]:
    """Per-turn seeded 暗骰: advance each active line whose roll passes threshold."""
    # Lazy import to avoid circular dependency: loop.lore ↔ loop.density ↔ loop.lore
    from loop.endgame import (  # noqa: PLC0415
        RESCUE_GRACE_STAGES,
        FINALE_RESCUE_CHANCE,
        roll_world_rescue,
        rescue_summary,
        build_catastrophe_events,
    )

    if registry.owner_of_event("lore_advanced") is None:
        return []  # LoreSystem not registered → clean no-op
    lines = (world.get("systems", {}).get("lore") or {}).get("lines", {})
    if not lines:
        return []

    events = list(store.iter_events())
    next_turn = max((e.get("turn") or 0 for e in events), default=0) + 1
    campaign_seed = (world.get("meta", {}) or {}).get("campaign_seed", 0)
    scene = (world.get("meta", {}) or {}).get("scene") or "scene"
    _md = (world.get("meta", {}) or {}).get("day")
    day = _md if _md is not None else (events[-1]["day"] if events else 1)

    already = {(e["deltas"].get("id"), e["deltas"].get("stage_idx"))
               for e in events if e["type"] == "lore_advanced"}

    # ---- Dormancy: resolve protagonist's current L2 town (★6) ----
    # simple/medium 暗 lines whose anchor != cur_town are FROZEN (no 暗骰 advance).
    # complex lines always brew. Expiry/finale still run for ALL lines.
    # cur_town = None when protagonist is off-graph; all simple/medium treated dormant.
    cur_town: str | None = None
    g = (world.get("systems") or {}).get("ontology")
    if g is not None:
        # Find protagonist: first tracked Person in the ontology graph
        protagonist_id: str | None = None
        for eid, e in g.entities.items():
            if getattr(e, "etype", None) == "Person" and getattr(e, "tier", None) == "tracked":
                protagonist_id = eid
                break
        if protagonist_id is not None:
            locs = g.neighbors(protagonist_id, "located_in", day)
            if locs:
                cur_l3 = locs[0]
                # Walk up contained_by edges to find the L2 ancestor (town)
                resolved = ancestor_of_level(g, cur_l3, day, 2)
                if resolved is not None:
                    cur_town = resolved
                else:
                    # Protagonist may already be at an L2 place directly
                    ent = g.get_entity(cur_l3)
                    if ent and getattr(ent, "attrs", {}).get("level") == 2:
                        cur_town = cur_l3
    log.debug("run_lore: protagonist cur_town=%s", cur_town)

    appended: list[dict] = []
    for lid, ln in lines.items():
        if ln.get("state") == "了结":
            continue
        if ln.get("state", "暗") != "暗":
            continue  # only roll 暗 lines; skip 明 / 了结

        # ---- B. Finale: pending_finale (set on a PRIOR turn by lifespan expiry) ----
        # Must be detected BEFORE the expiry block so it fires on subsequent turns,
        # not the same trip the expiry first sets pending_finale.
        if ln.get("complexity") == "complex" and ln.get("pending_finale"):
            oracle = Oracle(scene_seed(campaign_seed, f"finale:{lid}", day))
            if oracle.d100() <= FINALE_RESCUE_CHANCE:
                # Last-chance rescue succeeded — inescapable crisis quietly resolved
                fin_ev = kernel_event(
                    "quest_world_resolved", day=day, scene=scene,
                    summary=f"暗线终局救场:{lid}",
                    deltas={"id": lid, "by": "world_rescue:finale",
                            "summary": rescue_summary(ln)},
                    turn=next_turn,
                )
                store.append(fin_ev)
                appended.append(fin_ev)
                log.debug("run_lore: %s finale rescue SUCCESS → quest_world_resolved", lid)
            else:
                # Finale failed → catastrophe; emit quest_catastrophe + world_change
                emit_wc = registry.owner_of_event("world_change") is not None
                cat_evs = build_catastrophe_events(
                    ln, world, day=day, scene=scene, turn=next_turn,
                    emit_world_change=emit_wc,
                )
                for ev in cat_evs:
                    store.append(ev)
                    appended.append(ev)
                log.debug(
                    "run_lore: %s finale FAIL → catastrophe (emit_world_change=%s)",
                    lid, emit_wc,
                )
            continue  # line is now resolved; skip normal 暗骰 advance this trip

        # ---- Expiry check (lifespan, day-granular) ----
        born_day = ln.get("born_day")
        lifespan_days = ln.get("lifespan_days")
        if born_day is not None and lifespan_days is not None:
            if (day - born_day) >= lifespan_days:
                complexity = ln.get("complexity")
                if complexity == "complex":
                    # Guard: emit at most once
                    if not ln.get("pending_finale") and ln.get("state") != "了结":
                        finale_ev = kernel_event(
                            "quest_finale_due", day=day, scene=scene,
                            summary=f"暗线终局待决:{lid}",
                            deltas={"id": lid},
                            turn=next_turn,
                        )
                        store.append(finale_ev)
                        appended.append(finale_ev)
                        log.debug("run_lore: %s complex lifespan elapsed → quest_finale_due", lid)
                else:
                    # simple / medium: direct expiry
                    exp_ev = kernel_event(
                        "quest_expired", day=day, scene=scene,
                        summary=f"暗线到期了结:{lid}",
                        deltas={"id": lid},
                        turn=next_turn,
                    )
                    store.append(exp_ev)
                    appended.append(exp_ev)
                    log.debug("run_lore: %s (%s) lifespan elapsed → quest_expired", lid, complexity)
                continue  # skip 暗骰-advancing this line this trip
        # ---- End expiry check ----

        # ---- Dormancy gate (★6): freeze simple/medium 暗 lines when player is away ----
        # Expiry/finale above must NOT be gated — they already ran and continued or fell through.
        # Only the 暗骰 advance (and checkpoint rescue which only happens on advance) is frozen.
        complexity_now = ln.get("complexity")
        is_town_anchored = complexity_now in ("simple", "medium")
        if is_town_anchored:
            line_anchor = ln.get("anchor")
            dormant = (cur_town is None) or (line_anchor != cur_town)
            if dormant:
                log.debug(
                    "run_lore: %s (%s) DORMANT (cur_town=%s, anchor=%s) — skip 暗骰",
                    lid, complexity_now, cur_town, line_anchor,
                )
                continue  # freeze: skip 暗骰 advance + checkpoint rescue this turn
        # ---- End dormancy gate ----

        stages = ln.get("stages", [])
        idx = ln.get("stage_idx", -1)
        if idx + 1 >= len(stages):
            continue  # at last stage; resolution is L4, not L1
        new_idx = idx + 1
        if (lid, new_idx) in already:
            continue  # idempotency: this stage advance is already in the store
        seed = scene_seed(campaign_seed, f"lore:{lid}", next_turn)
        roll = Oracle(seed).d100()  # 1..100
        if roll <= ln.get("threshold", 50):
            st = stages[new_idx]
            hint = st.get("hint") if isinstance(st, dict) else str(st)
            ev = kernel_event("lore_advanced", day=day, scene=scene,
                              summary=f"暗线推进:{lid}→stage{new_idx}",
                              deltas={"id": lid, "stage_idx": new_idx, "hint": hint},
                              turn=next_turn)
            store.append(ev)
            appended.append(ev)
            log.debug("run_lore: %s advanced to stage %d (roll=%d<=%d)",
                      lid, new_idx, roll, ln.get("threshold", 50))

            # ---- A. Checkpoint world-rescue (暗骰酝酿期,渐进式) ----
            # Only for complex 暗 lines at stage >= RESCUE_GRACE_STAGES (no roll at stage 0).
            # Skip at the last stage — that uses the existing world-push surface path below.
            is_last_stage = (new_idx == len(stages) - 1)
            is_complex = ln.get("complexity") == "complex"
            if is_complex and new_idx >= RESCUE_GRACE_STAGES and not is_last_stage:
                rescue_oracle = Oracle(
                    scene_seed(campaign_seed, f"rescue:{lid}", new_idx)
                )
                if roll_world_rescue(rescue_oracle, new_idx, len(stages)):
                    res_ev = kernel_event(
                        "quest_world_resolved", day=day, scene=scene,
                        summary=f"暗线救场了结:{lid}",
                        deltas={"id": lid, "by": "world_rescue",
                                "summary": rescue_summary(ln)},
                        turn=next_turn,
                    )
                    store.append(res_ev)
                    appended.append(res_ev)
                    log.debug(
                        "run_lore: %s checkpoint rescue SUCCESS at stage %d → quest_world_resolved",
                        lid, new_idx,
                    )
                    continue  # resolved this trip; skip world-push surface

            # World-push surfacing: complex line reaching its LAST stage
            # erupts into 明 so it doesn't silently finish off-screen.
            # Simple/medium lines stay 暗 (they expire via lifespan in a later phase).
            if is_last_stage and is_complex:
                surface_ev = kernel_event(
                    "quest_surfaced", day=day, scene=scene,
                    summary=f"暗线爆点浮现:{lid}",
                    deltas={"id": lid, "by": "world"},
                    turn=next_turn,
                )
                store.append(surface_ev)
                appended.append(surface_ev)
                log.debug("run_lore: world-push surface %s (complex line at last stage %d)",
                          lid, new_idx)
    return appended


# ---------------------------------------------------------------------------
# jit_resequence — JIT-rewrite remaining stages after明→暗 demote
# ---------------------------------------------------------------------------

_JIT_SYSTEM = (
    '你是一个 RPG 世界的剧情设计师。给定一条暗线的基本信息和当前世界状态，'
    '请为该暗线从【当前现实】续写一组新的推进阶段（stages），'
    '代表【如果无人干预，该暗线会按此轨迹自然演化】。\n'
    '输出 JSON，格式：{"stages": [{"hint": "..."}]}\n'
    '每条 hint 简短（20字以内），纯中文，描述世界侧的变化迹象，不是玩家动作。'
)

_JIT_USER_TMPL = (
    "暗线主题：{about}\n"
    "隐情：{secret}\n"
    "已知线索（clues_dropped）：{clues}\n"
    "当前世界摘要（day={day}）：{world_summary}\n\n"
    "请续写该暗线从当前节点往后的默认走向（3-5个 stage），"
    "让暗骰能接着自走。只输出 JSON。"
)


def _jit_validate(obj):
    if not isinstance(obj, dict):
        return ['response must be a JSON object {"stages": [...]}']
    stages = obj.get("stages")
    if not isinstance(stages, list) or not stages:
        return ['"stages" must be a non-empty JSON array']
    errors: list[str] = []
    for i, s in enumerate(stages):
        if not isinstance(s, dict) or not isinstance(s.get("hint"), str) or not s["hint"].strip():
            errors.append(f'stage {i+1} must be an object with a non-empty string "hint"')
    return errors


def jit_resequence(line: dict, world: dict, provider) -> list[dict]:
    """JIT-rewrite: given a line whose pre-set stages no longer fit the current reality
    (e.g. after明→暗 demote), call complete_structured to get a new续写 stage list.

    Returns the new stages (caller emits an event to apply them).

    Defensive: if the provider response is malformed / missing stages, return the
    line's REMAINING original stages (line["stages"][stage_idx+1:]) as fallback.
    Never raises.
    """
    # Build fallback immediately (remaining original stages)
    idx = line.get("stage_idx", -1)
    remaining = list(line.get("stages", [])[idx + 1:])

    meta = world.get("meta") or {}
    day = meta.get("day", "?")
    # Build a minimal world summary (no deep serialisation; just meta + systems keys)
    systems_keys = list((world.get("systems") or {}).keys())
    world_summary = f"第{day}天；已有系统：{', '.join(systems_keys)}"

    clues = line.get("clues_dropped") or []
    clues_str = "；".join(clues) if clues else "无"

    user = _JIT_USER_TMPL.format(
        about=line.get("about", ""),
        secret=line.get("secret", ""),
        clues=clues_str,
        day=day,
        world_summary=world_summary,
    )

    try:
        obj, errors = complete_structured(
            provider,
            system=_JIT_SYSTEM,
            user=user,
            validate=_jit_validate,
            max_repairs=1,
            schema_reminder='Required: {"stages": [{"hint": "..."}, ...]} — at least one stage, each with a non-empty Chinese "hint".',
            log_label="jit",
        )
        if errors or not isinstance(obj, dict):
            log.debug("jit_resequence: malformed/empty stages → fallback for line %s",
                      line.get("id"))
            return remaining
        new_stages = obj["stages"]
        log.debug("jit_resequence: line %s got %d new stages", line.get("id"), len(new_stages))
        return new_stages
    except Exception as exc:  # noqa: BLE001
        log.warning("jit_resequence failed for line %s: %s → fallback", line.get("id"), exc)
        return remaining
