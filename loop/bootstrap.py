"""loop.bootstrap — world-genesis orchestrator: bootstrap_world + reroll.

The per-step content generators (gen_frame ... gen_opening) live in
loop.genesis_steps and are re-exported here for back-compat (tests and
callers import them from loop.bootstrap)."""
from __future__ import annotations
import copy
from functools import wraps
from uuid import uuid4

from engine.store import EventBatch
from kernel.projection import project
from llm.generation_guard import collect_failures, WatchedProvider
from llm.provider import FakeLLMProvider

from engine.oracle import Oracle, scene_seed, load_table  # noqa: F401
from engine.log import get_logger
from kernel.events import kernel_event
from kernel.observability import get_tracer
from llm.structured import complete_structured  # noqa: F401

from loop.genesis import (  # noqa: F401  (re-exported for back-compat)
    _draw_distinct, _empty_str,
    _roll_world_seeds, _roll_protagonist_seeds, _protagonist_seed_block,
    _COMPLEXITY_TABLE, _SPEED_TABLE, _STAGE_COUNT, _SYSTEM_GEN_OPENING,
    gen_frame, gen_regions, gen_local_map, gen_protagonist,
    gen_factions, gen_npcs, gen_codex, gen_threads, gen_opening,
)

from loop.flavor import stored_flavor

log = get_logger("loop.bootstrap")


class GenesisError(RuntimeError):
    """Generation did not produce a complete world; the prior save is intact."""


def _atomic_genesis(function):
    @wraps(function)
    def run(engine, *args, **kwargs):
        if isinstance(engine.store, EventBatch):
            return function(engine, *args, **kwargs)
        batch = EventBatch(engine.store, turn=0, preflight=lambda events: project(engine.registry, events))
        staged = copy.copy(engine)
        staged.store = batch
        offline = isinstance(engine.provider, FakeLLMProvider) or getattr(engine.provider, 'is_offline', False)
        staged._offline_genesis = offline
        staged.provider = WatchedProvider(engine.provider)
        if engine.cascade_provider is not None:
            staged.cascade_provider = WatchedProvider(engine.cascade_provider)
        with collect_failures() as failures:
            try:
                result = function(staged, *args, **kwargs)
            except Exception as exc:
                escaped = [f for f in failures if f.get('exception') is exc]
                if offline or not escaped:
                    raise
                stages = ', '.join(sorted({f['stage'] for f in escaped}))
                raise GenesisError(
                    '世界生成未完成，原存档保持不变；可重试。失败阶段：' + stages
                ) from exc
        if failures and not offline:
            stages = ', '.join(sorted({f['stage'] for f in failures}))
            raise GenesisError('世界生成未完成，原存档保持不变；可重试。失败阶段：' + stages)
        receipt = batch.publish(action_id='genesis:' + str(uuid4()))
        engine.world = project(engine.registry, engine.store.iter_events())
        engine.world['_revision'] = receipt['revision']
        return result
    return run


# ---------------------------------------------------------------------------
# bootstrap_world — Task 9: orchestrator (steps 1-9) + reroll helpers
# ---------------------------------------------------------------------------

@_atomic_genesis
def bootstrap_world(engine, pitch: str = "", *, spec=None, attempt: int = 0, progress=None, flavor: str = "classic") -> dict:
    """Run all generation steps + protagonist creation; return a rich result dict.

    Orchestration order (all genesis events: turn=0, day=1, scene="genesis"):
        1. campaign_seeded
        2. gen_frame
        3. gen_regions
        4. gen_local_map
        5. gen_protagonist  (authored protagonist to fit world)
        6. protagonist character_created + fact_asserted events + entity_moved
        7. gen_factions
        8. gen_npcs
        9. gen_threads  → create_lore_line per skeleton
        10. gen_opening
        then project

    Args:
        engine:   The wired engine.
        pitch:    The world background keyword string.
        attempt:  Reroll attempt counter (0 for first run).
        progress: Optional callback(step_index, total_steps, label) called before
                  each generation step. Default None → no-op (existing callers unaffected).

    Returns:
        {
          "summary": {...display fields...},
          "_state": {frame, regions_summary, local_map, factions_summary, protagonist,
                     protagonist_authored, pitch, attempts:{step:attempt}},
          "_boundaries": {step: first_seq_of_that_step},
        }

    Real-provider failure raises GenesisError without publishing partial/stub
    events. Explicit offline providers may use deterministic stub generators.
    """
    from kernel.projection import project as _project
    from loop.lore import create_lore_line
    from app.engine import _PROTAGONIST_ID

    store = engine.store
    provider = engine.provider
    campaign_seed = engine.campaign_seed

    # Resolve the spec: normalize, then seed world_premise.genre from `pitch`
    # when the spec does not already set it. With spec=None + a pitch this is
    # byte-identical to the legacy pitch-only path.
    from loop.genesis_spec import normalize as _normalize_spec
    spec = _normalize_spec(spec)
    wp = dict(spec.get("world_premise") or {})
    if not wp.get("genre") and pitch:
        wp["genre"] = pitch
    if wp:
        spec = {**spec, "world_premise": wp}
    genre = wp.get("genre", pitch)

    boundaries: dict[str, int] = {}

    def _seed(step: str) -> Oracle:
        return Oracle(scene_seed(campaign_seed, f"genesis:{step}", attempt))

    def _progress(idx: int, total: int, label: str) -> None:
        if progress is not None:
            try:
                progress(idx, total, label)
            except Exception:
                pass  # progress callback errors must never abort genesis

    _TOTAL_STEPS = 8

    with get_tracer().span("genesis"):
        # -----------------------------------------------------------------------
        # Step 1: campaign_seeded (mirrors new_game)
        # -----------------------------------------------------------------------
        ev_seed = kernel_event(
            "campaign_seeded",
            turn=0, day=1, scene="genesis",
            summary=f"campaign seed = {campaign_seed}",
            deltas={"campaign_seed": campaign_seed, "flavor": flavor},
        )
        boundaries["campaign_seeded"] = store.append(ev_seed)

        # -----------------------------------------------------------------------
        # Step 2: gen_frame
        # -----------------------------------------------------------------------
        _progress(1, _TOTAL_STEPS, "世界框架")
        with get_tracer().span("gen_frame", step="frame"):
            frame_evs, frame = gen_frame(provider, _seed("frame"), genre,
                                         provided=spec.get("world_premise"), flavor=flavor)
        boundaries["frame"] = store.append(frame_evs[0])
        for ev in frame_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 3: gen_regions
        # -----------------------------------------------------------------------
        _progress(2, _TOTAL_STEPS, "宏观区域")
        # I9: route the bulk structure-filling steps (regions/factions/npcs/codex)
        # through the fast cascade model when configured, to keep genesis snappy —
        # the oracle seeds carry the distinctness, so content quality holds. Falls
        # back to the main provider when RPG_CASCADE_MODEL is unset.
        bulk_provider = getattr(engine, "cascade_provider", None) or provider
        region_evs, regions_summary = gen_regions(bulk_provider, _seed("regions"), frame,
                                                   provided=spec.get("regions"), flavor=flavor)
        boundaries["regions"] = store.append(region_evs[0])
        for ev in region_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 4: gen_local_map
        # -----------------------------------------------------------------------
        _progress(3, _TOTAL_STEPS, "本地地图")
        local_map_evs, local_map = gen_local_map(provider, _seed("local_map"), frame, regions_summary,
                                                  provided=spec.get("local_map"), flavor=flavor)
        boundaries["local_map"] = store.append(local_map_evs[0])
        for ev in local_map_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 5: gen_protagonist — author protagonist to fit the world
        # -----------------------------------------------------------------------
        _progress(4, _TOTAL_STEPS, "主角")
        _, protagonist_authored = gen_protagonist(provider, _seed("protagonist"), frame, local_map,
                                                  provided=spec.get("protagonist"), flavor=flavor)

        # -----------------------------------------------------------------------
        # Step 5b: create protagonist (tracked) + public facts + move to first venue
        # -----------------------------------------------------------------------
        protagonist = _PROTAGONIST_ID
        first_venue = local_map["venues"][0]

        ev_char = kernel_event(
            "character_created",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 主角登场（{protagonist_authored['name']}）",
            deltas={
                "id": protagonist,
                "tier": "tracked",
                "sketch": protagonist_authored["origin"],
                "goal": protagonist_authored["goal"],
            },
        )
        boundaries["protagonist"] = store.append(ev_char)

        # fact_asserted for name (公开 — DM 和主角都知道自己叫什么)
        store.append(kernel_event(
            "fact_asserted",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 名字：{protagonist_authored['name']}",
            deltas={
                "subject": protagonist,
                "predicate": "真名",
                "value": protagonist_authored["name"],
                "secrecy": "public",
            },
        ))

        # fact_asserted for starting objective (公开 — player knows what to do)
        store.append(kernel_event(
            "fact_asserted",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 当前目标：{protagonist_authored['objective']}",
            deltas={
                "subject": protagonist,
                "predicate": "目标",
                "value": protagonist_authored["objective"],
                "secrecy": "public",
            },
        ))

        ev_move = kernel_event(
            "entity_moved",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 抵达 {first_venue}",
            deltas={"who": protagonist, "to": first_venue},
        )
        store.append(ev_move)

        # Opt-in host rules; values/types remain flavor data. The resulting
        # configuration is event-sourced, so resumed saves need no environment.
        import os
        from engine.oracle import load_pack_manifest
        resources = load_pack_manifest(flavor).get('resources', {})
        if resources and os.environ.get('RPG_RESOURCE_RULES', '0') == '1':
            store.append(kernel_event('resources_configured', turn=0, day=1, scene='genesis',
                summary='scenario resource rules configured',
                deltas={'subject':protagonist, 'resources':resources}))

        # -----------------------------------------------------------------------
        # Step 6: gen_factions
        # -----------------------------------------------------------------------
        _progress(5, _TOTAL_STEPS, "势力")
        faction_evs, factions_summary = gen_factions(bulk_provider, _seed("factions"), frame, regions_summary,
                                                      provided=spec.get("factions"))
        boundaries["factions"] = store.append(faction_evs[0])
        for ev in faction_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 7: gen_npcs
        # -----------------------------------------------------------------------
        _progress(6, _TOTAL_STEPS, "NPC")
        npc_evs, npcs_summary = gen_npcs(bulk_provider, _seed("npcs"), frame, local_map, factions_summary,
                                         provided=spec.get("npcs"), flavor=flavor)
        boundaries["npcs"] = store.append(npc_evs[0])
        for ev in npc_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 6b: gen_codex — world setting text blocks (实力等级/编年史/势力志).
        # Uses the fast cascade model when configured so it doesn't balloon genesis
        # (one LLM call; deterministic stub on failure). (I6-P3b / I9)
        # -----------------------------------------------------------------------
        codex_provider = getattr(engine, "cascade_provider", None) or provider
        with get_tracer().span("gen_codex", step="codex"):
            codex_evs = gen_codex(codex_provider, _seed("codex"), frame, factions_summary,
                                  provided=spec.get("codex"))
        if codex_evs:
            boundaries["codex"] = store.append(codex_evs[0])
            for ev in codex_evs[1:]:
                store.append(ev)

        # -----------------------------------------------------------------------
        # Step 8: gen_threads → create_lore_line per skeleton
        # -----------------------------------------------------------------------
        _progress(7, _TOTAL_STEPS, "暗线")
        with get_tracer().span("gen_threads", step="threads"):
            skeletons, threads_summary = gen_threads(
                provider, _seed("threads"), frame, local_map, protagonist,
                provided=spec.get("threads"), flavor=flavor
            )
        # Append each skeleton via create_lore_line; boundary recovered via SQL below.
        for sk in skeletons:
            create_lore_line(store, sk, day=1, scene="genesis", turn=0)
        boundaries["threads"] = _find_first_lore_seq(store, skeletons)

        # -----------------------------------------------------------------------
        # Step 9: gen_opening
        # -----------------------------------------------------------------------
        _progress(8, _TOTAL_STEPS, "开场")
        with get_tracer().span("gen_opening", step="opening"):
            world_summary = _build_world_summary(frame, regions_summary, local_map, npcs_summary, threads_summary)
            # Resolve the first venue's human-readable name so the prompt never shows an id
            first_venue_name = local_map.get("venue_names", {}).get(first_venue, first_venue)
            from loop.genesis_opening import publish_opening
            opening_seq, narration = publish_opening(engine, frame=frame, pitch=pitch,
                provided=spec.get("opening"), legacy_summary=world_summary)
        boundaries["opening"] = opening_seq

        # -----------------------------------------------------------------------
        # Project world
        # -----------------------------------------------------------------------
        engine.world = _project(engine.registry, store.iter_events())

    # -----------------------------------------------------------------------
    # Build result
    # -----------------------------------------------------------------------
    n_lore = len(threads_summary.get("threads", []))
    n_factions_actual = len(factions_summary.get("factions", []))
    n_npcs_actual = len(npcs_summary.get("npcs", []))

    summary = {
        "world_name": frame["world_name"],
        "tone": frame["tone"],
        "central_conflict": frame["central_conflict"],
        "n_regions": frame["n_regions"],
        "n_factions": n_factions_actual,
        "n_npcs": n_npcs_actual,
        "n_lore": n_lore,
        "narration_excerpt": narration[:120] if narration else "",
        "opening": narration or "",
        "protagonist_name": protagonist_authored["name"],
        "protagonist_origin": protagonist_authored["origin"],
        "protagonist_goal": protagonist_authored["goal"],
        "objective": protagonist_authored["objective"],
    }

    return {
        "summary": summary,
        "_state": {
            "frame": frame,
            "regions_summary": regions_summary,
            "local_map": local_map,
            "factions_summary": factions_summary,
            "npcs_summary": npcs_summary,
            "threads_summary": threads_summary,
            "protagonist": protagonist,
            "protagonist_authored": protagonist_authored,
            "pitch": pitch,
            "spec": spec,
            "attempts": {step: attempt for step in boundaries},
        },
        "_boundaries": boundaries,
    }


def _find_first_lore_seq(store, skeletons: list[dict]) -> int:
    """Return the seq of the first lore_created event (for the thread boundaries).

    create_lore_line does not expose the seq via its return value, so we fall back
    to a direct SQL query on the underlying SQLite connection (store._conn).
    """
    if not skeletons:
        return _read_last_seq(store) or 1
    # Direct SQL: cheapest way to find the first lore_created seq without
    # intercepting create_lore_line's internal store.append call.
    seq = next((ev['seq'] for ev in store.iter_events() if ev['type'] == 'lore_created'), None)
    if seq is not None:
        return seq
    return _read_last_seq(store) or 1


def _read_last_seq(store) -> int | None:
    """Return the highest seq in the store (non-retracted), or None if empty."""
    return max((ev['seq'] for ev in store.iter_events()), default=None)


def _build_world_summary(frame: dict, regions_summary: dict, local_map: dict,
                          npcs_summary: dict, threads_summary: dict) -> str:
    """Build a compact world summary string for gen_opening's world_summary arg."""
    region_names = ", ".join(r["name"] for r in regions_summary.get("regions", []))

    # Resolve start town NAME from l2 list (not raw id)
    start_town_id = local_map.get("start_town", "town_0")
    town_name = start_town_id
    for entry in local_map.get("l2", []):
        if entry.get("id") == start_town_id:
            town_name = entry.get("name", start_town_id)
            break

    # Use venue NAMES (not ids) for the world summary
    venue_ids = local_map.get("venues", [])
    venue_names_map = local_map.get("venue_names", {})
    venue_display = ", ".join(
        venue_names_map.get(vid, vid) for vid in venue_ids
    )

    npc_sketches = ", ".join(n["sketch"] for n in npcs_summary.get("npcs", []))
    thread_abouts = "; ".join(
        t["about"] for t in threads_summary.get("threads", [])
    )
    return (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n"
        f"大区域：{region_names}\n"
        f"起始小镇：{town_name}，场所：{venue_display}\n"
        f"开场NPC：{npc_sketches}\n"
        f"暗线：{thread_abouts}"
    )


@_atomic_genesis
def reroll_all(engine, prev_result: dict, *, progress=None) -> dict:
    """Retract all genesis events and run a fresh bootstrap_world.

    The previous attempt's overall counter is bumped by 1.
    """
    # Determine previous attempt number (use the 'frame' step attempt as proxy)
    prev_attempts = prev_result.get("_state", {}).get("attempts", {})
    prev_attempt = prev_attempts.get("frame", 0)
    new_attempt = prev_attempt + 1

    # Retract all turn-0 events
    flavor = stored_flavor(engine.store)
    engine.store.retract_from_turn(0)

    pitch = prev_result["_state"]["pitch"]
    prev_spec = prev_result.get("_state", {}).get("spec")
    return bootstrap_world(engine, pitch, spec=prev_spec, attempt=new_attempt,
                           progress=progress, flavor=flavor)


@_atomic_genesis
def reroll_step(engine, prev_result: dict, step: str, *, progress=None) -> dict:
    """Retract from step's boundary and re-run from that step to end.

    Only leaf steps are supported: 'factions', 'npcs', 'threads'.
    For map/region reroll, callers should use reroll_all.

    Preserves upstream summaries from prev_result._state.
    """
    from kernel.projection import project as _project
    from loop.lore import create_lore_line
    from app.engine import _PROTAGONIST_ID

    _LEAF_STEPS = {"factions", "npcs", "threads"}
    if step not in _LEAF_STEPS:
        raise ValueError(f"reroll_step: '{step}' is not a leaf step; use reroll_all for map/region reroll")

    boundaries = prev_result["_boundaries"]
    state = prev_result["_state"]
    frame = state["frame"]
    regions_summary = state["regions_summary"]
    local_map = state["local_map"]
    protagonist = state["protagonist"]
    protagonist_authored = state.get("protagonist_authored", {
        "name": "无名旅者",
        "origin": "来历不明的旅人，只知道自己踏上了这条路。",
        "goal": "找到属于自己的答案",
        "objective": "在起始小镇打听线索，寻找下一步的方向",
    })
    pitch = state["pitch"]
    spec = state.get("spec") or {}
    campaign_seed = engine.campaign_seed
    store = engine.store
    provider = engine.provider
    flavor = stored_flavor(store)   # reroll uses the campaign's persisted flavor

    # Bump this step's attempt; downstream steps keep their own prior attempt counters
    prev_attempts = state.get("attempts", {})
    step_attempt = prev_attempts.get(step, 0) + 1

    def _seed(s: str) -> Oracle:
        # The retracted step uses its newly bumped attempt; every downstream step
        # that was NOT retracted uses its own unchanged prior attempt counter so
        # that npcs/threads oracle rolls are a function of (step, its own attempt)
        # and are not aliased to the triggering step's attempt trajectory.
        if s == step:
            sa = step_attempt
        else:
            sa = prev_attempts.get(s, 0)
        return Oracle(scene_seed(campaign_seed, f"genesis:{s}", sa))

    # Retract from this step's boundary (drops step + everything after)
    store.retract_from_seq(boundaries[step])

    new_boundaries = dict(boundaries)
    new_state = dict(state)

    # Re-run factions if needed
    if step == "factions":
        faction_evs, factions_summary = gen_factions(provider, _seed("factions"), frame, regions_summary,
                                                      provided=spec.get("factions"))
        new_boundaries["factions"] = store.append(faction_evs[0])
        for ev in faction_evs[1:]:
            store.append(ev)
        new_state["factions_summary"] = factions_summary
    else:
        factions_summary = state["factions_summary"]

    # Re-run npcs if needed
    if step in ("factions", "npcs"):
        npc_evs, npcs_summary = gen_npcs(provider, _seed("npcs"), frame, local_map, factions_summary,
                                         provided=spec.get("npcs"), flavor=flavor)
        new_boundaries["npcs"] = store.append(npc_evs[0])
        for ev in npc_evs[1:]:
            store.append(ev)
        new_state["npcs_summary"] = npcs_summary
    else:
        npcs_summary = state.get("npcs_summary", {"npcs": []})

    # Re-run threads (always, since we retract from factions/npcs/threads boundary)
    skeletons, threads_summary = gen_threads(
        provider, _seed("threads"), frame, local_map, protagonist,
        provided=spec.get("threads"), flavor=flavor
    )
    for sk in skeletons:
        create_lore_line(store, sk, day=1, scene="genesis", turn=0)
    new_boundaries["threads"] = _find_first_lore_seq(store, skeletons)
    new_state["threads_summary"] = threads_summary

    # Re-run opening
    world_summary = _build_world_summary(frame, regions_summary, local_map, npcs_summary, threads_summary)
    first_venue = local_map["venues"][0]
    first_venue_name = local_map.get("venue_names", {}).get(first_venue, first_venue)
    from loop.genesis_opening import publish_opening
    opening_seq, narration = publish_opening(engine, frame=frame, pitch=pitch,
        provided=spec.get("opening"), legacy_summary=world_summary)
    new_boundaries["opening"] = opening_seq

    # Update attempts
    new_attempts = dict(prev_attempts)
    new_attempts[step] = step_attempt

    # Project
    engine.world = _project(engine.registry, store.iter_events())

    # Build fresh result
    n_lore = len(new_state.get("threads_summary", {}).get("threads", []))
    n_factions_actual = len(new_state.get("factions_summary", {}).get("factions", []))
    n_npcs_actual = len(new_state.get("npcs_summary", {}).get("npcs", []))

    summary = {
        "world_name": frame["world_name"],
        "tone": frame["tone"],
        "central_conflict": frame["central_conflict"],
        "n_regions": frame["n_regions"],
        "n_factions": n_factions_actual,
        "n_npcs": n_npcs_actual,
        "n_lore": n_lore,
        "narration_excerpt": narration[:120] if narration else "",
        "opening": narration or "",
        "protagonist_name": protagonist_authored["name"],
        "protagonist_origin": protagonist_authored["origin"],
        "protagonist_goal": protagonist_authored["goal"],
        "objective": protagonist_authored["objective"],
    }

    new_state["attempts"] = new_attempts
    new_state["pitch"] = pitch
    new_state["protagonist_authored"] = protagonist_authored

    return {
        "summary": summary,
        "_state": new_state,
        "_boundaries": new_boundaries,
    }


def _gen_threads_fallback(
    oracle: Oracle,
    local_map: dict,
    protagonist: str,
) -> tuple[list[dict], dict]:
    """Deterministic stub fallback: emit >= 3 valid skeletons without LLM."""
    venues = list(local_map["venues"])
    start_town = local_map["start_town"]
    skeletons: list[dict] = []
    summary_threads: list[dict] = []

    # 3 campaign threads (minimum)
    for i in range(3):
        thread_id = f"thread_{i}"
        complexity = "medium"
        threshold = 50
        stage_count = _STAGE_COUNT[complexity]
        sk = _stub_thread_skeleton(
            thread_id, complexity, start_town, threshold, venues, stage_count, i
        )
        skeletons.append(sk)
        summary_threads.append({
            "id": thread_id,
            "type": "阴谋",
            "complexity": complexity,
            "anchor": start_town,
            "about": sk["about"],
        })

    # 1 protagonist-bound thread
    sk = _stub_thread_skeleton(
        "pthread_0", "medium", protagonist, 50, venues, _STAGE_COUNT["medium"], 0
    )
    skeletons.append(sk)
    summary_threads.append({
        "id": "pthread_0",
        "type": "protagonist",
        "complexity": "medium",
        "anchor": protagonist,
        "about": sk["about"],
    })

    summary: dict = {"threads": summary_threads}
    return skeletons, summary
