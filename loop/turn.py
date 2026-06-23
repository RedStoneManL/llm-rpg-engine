"""loop.turn — produce_turn + apply_turn + run_turn pipeline.

produce_turn(registry, world, scene, player_input, *, strategy, provider,
             embedder=None, max_repairs=3) -> (TurnCommit, attempts, dropped_sections):
  1. commit = strategy.produce(...)
  2. validate/repair loop (up to max_repairs)
  3. drop still-failing sections
  Returns (commit, repair_attempts, dropped_sections) — NO store write.

apply_turn(registry, store, commit, *, day, scene) -> world:
  1. to_events per section
  2. append to store
  3. project → return new world

run_turn(registry, store, world, scene, player_input, *, strategy, provider,
         embedder=None, max_repairs=3) -> TurnResult:
  Delegates to produce_turn + apply_turn (backward-compatible S4a API).

TurnResult: dataclass holding narration, world, commit, events,
            repair_attempts, dropped_sections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kernel.registry import Registry
from kernel.projection import project
from kernel.validation import validate_commit, build_repair_request
from loop.entity_resolve import augment_unresolved_refs
from kernel.observability import get_tracer
from kernel.events import kernel_event
from kernel import clock as _clock
from loop.fleet import digest_fleet
from loop.director import run_director
from loop.cascade import run_cascade
from loop.time import run_catchup
from loop.lore import run_lore, jit_resequence
from loop.lore_disclosure import _l2_ancestor
from loop.density import run_density
from engine.log import get_logger

log = get_logger("loop.turn")

# Sections the LLM must ALWAYS declare explicitly (an empty [] counts), so a
# forgotten section surfaces as a 'missing_section' error instead of being
# silently lost. The play layer passes this into run_turn/run_compare;
# produce_turn itself defaults to no requirement (keeps direct callers/tests free).
REQUIRED_SECTIONS = frozenset({"moves", "places", "cast", "facts", "clock"})

# Number of game-days idle before a 明 line is demoted (day-granular)
IDLE_DEMOTE_DAYS = 2


@dataclass
class TurnResult:
    """Result of a single run_turn call."""
    narration: str
    world: dict
    commit: Any          # TurnCommit
    events: list[dict]
    repair_attempts: int
    dropped_sections: list[str] = field(default_factory=list)


def _next_turn(store) -> int:
    """Compute turn number = max existing event turn + 1, or 1 if none."""
    max_turn = 0
    for ev in store.iter_events():
        t = ev.get("turn") or 0
        if t > max_turn:
            max_turn = t
    return max_turn + 1


def _protagonist_location(world: dict, protagonist: str | None) -> str | None:
    """Current place id the protagonist is located_in (or None)."""
    g = world.get("systems", {}).get("ontology")
    if g is None or not protagonist:
        return None
    day = world.get("meta", {}).get("day") or 1
    locs = g.neighbors(protagonist, "located_in", day)
    return locs[0] if locs else None


def advanced_day(world: dict, commit) -> int:
    """Post-advance day for this turn = current clock + the turn's clock delta.

    The narrator's `clock` section is a delta {advance, days, bands}. We fold it
    onto the current (day, band) from world.meta and return the new day; the new
    band is folded separately by TimeSystem.apply on the clock_advanced event.
    Absent/none clock => no advance (back-compat with callers that omit it).

    Public — also used by app.play in compare mode so the 甲 commit stamps at
    the correct post-advance day rather than the pre-turn scene day.
    """
    meta = world.get("meta", {})
    cur_day = meta.get("day") or 1
    cur_band = meta.get("band") or 0
    decl = commit.sections.get("clock") or []
    if (isinstance(decl, list) and decl and isinstance(decl[0], dict)
            and decl[0].get("advance")):
        ddays = int(decl[0].get("days", 0) or 0)
        dbands = int(decl[0].get("bands", 0) or 0)
    else:
        ddays = dbands = 0
    new_day, _new_band = _clock.advance(cur_day, cur_band, ddays, dbands)
    return new_day


def produce_turn(
    registry: Registry,
    world: dict,
    scene: dict,
    player_input: str,
    *,
    strategy,
    provider,
    embedder=None,
    max_repairs: int = 3,
    required_sections: frozenset = frozenset(),
) -> tuple:
    """Produce a validated TurnCommit without writing to the store.

    Args:
        registry:     Kernel registry.
        world:        Current projected world dict.
        scene:        Scene dict with keys protagonist/present/day/location/(id).
        player_input: Raw player action string.
        strategy:     TurnStrategy instance.
        provider:     LLMProvider.
        embedder:     Optional embedder for recall ranking.
        max_repairs:  Maximum repair attempts before dropping bad sections.

    Returns:
        (commit, repair_attempts, dropped_sections) — no store writes.
    """
    # --------------------------------------------------------------------------
    # Step 1: Produce initial commit
    # --------------------------------------------------------------------------
    with get_tracer().span("produce"):
        commit = strategy.produce(
            registry, world, scene, player_input,
            provider=provider, embedder=embedder,
        )
    log.debug("produce_turn: initial commit narration=%r sections=%s",
              commit.narration[:40], list(commit.sections))

    # --------------------------------------------------------------------------
    # Step 2: Validate + modular repair loop
    #
    # When validation fails, re-emit ONLY the failing sections (not narration,
    # not passing sections).  This is far cheaper than a full re-author (the
    # original narration prose is already valid and authoritative — regenerating
    # it on every repair turn was the dominant cost measured at ~59s/repair).
    #
    # Flow per repair attempt:
    #   a. Compute failing section names from the error list.
    #   b. Ask strategy.repair_sections() to continue the existing conversation
    #      and return ONLY a {section: decl} dict for those sections.
    #   c. Merge the repaired sections into the current commit via dict.update();
    #      narration + passing sections stay untouched.
    #   d. Re-validate the merged commit.
    #
    # Fallback: if the strategy hasn't implemented repair_sections (raises
    # NotImplementedError), we fall back to the legacy whole-commit re-author
    # so third-party strategies still work.
    # --------------------------------------------------------------------------
    attempts = 0
    # #R7 A': resolve/mint name-refs (new named NPCs/places) into ids BEFORE
    # validation, so a move to a brand-new "卡恩" creates+applies instead of dropping.
    _aug_scene = ((scene or {}).get("scene") or (scene or {}).get("id")
                  or (scene or {}).get("location") or "")
    _aug_day = (scene or {}).get("day", 0)
    augment_unresolved_refs(commit, world, scene=_aug_scene, day=_aug_day)
    errors = validate_commit(registry, commit, world, required_sections=required_sections)

    while errors and attempts < max_repairs:
        failing = {e.section for e in errors}
        log.debug("produce_turn: repair attempt=%d errors=%d failing=%s",
                  attempts + 1, len(errors), sorted(failing))
        with get_tracer().span("repair", attempt=attempts + 1):
            try:
                repaired = strategy.repair_sections(failing, errors, provider=provider)
                # Merge repaired sections into the existing commit; narration + passing
                # sections stay untouched.  Build a new TurnCommit (dataclass is frozen
                # by convention — reconstruct rather than mutate in place).
                from kernel.turncommit import TurnCommit as _TC
                merged_sections = dict(commit.sections)
                merged_sections.update(repaired)
                commit = _TC(
                    narration=commit.narration,
                    sections=merged_sections,
                    reasons=commit.reasons,
                )
            except NotImplementedError:
                # Legacy fallback: full re-author (for strategies that don't
                # implement repair_sections yet).
                repair_text = build_repair_request(errors)
                commit = strategy.produce(
                    registry, world, scene, player_input,
                    provider=provider, embedder=embedder,
                    repair=repair_text,
                )
        augment_unresolved_refs(commit, world, scene=_aug_scene, day=_aug_day)
        errors = validate_commit(registry, commit, world, required_sections=required_sections)
        attempts += 1

    # --------------------------------------------------------------------------
    # Step 3: Drop still-failing sections (fallback)
    # --------------------------------------------------------------------------
    dropped_sections: list[str] = []
    if errors:
        failing: set[str] = {e.section for e in errors}
        log.warning(
            "produce_turn: dropping %d still-invalid sections after %d repairs: %s",
            len(failing), attempts, sorted(failing),
        )
        dropped_sections = sorted(failing)
        clean_sections = {k: v for k, v in commit.sections.items()
                          if k not in failing}
        from kernel.turncommit import TurnCommit
        commit = TurnCommit(narration=commit.narration, sections=clean_sections)

    log.debug("produce_turn: done repair_attempts=%d dropped=%s", attempts, dropped_sections)
    return commit, attempts, dropped_sections


def apply_turn(
    registry: Registry,
    store,
    commit,
    *,
    day: int,
    scene: str,
) -> dict:
    """Apply a TurnCommit: explode to events, append to store, project world.

    Args:
        registry: Kernel registry.
        store:    EventStore (must be open).
        commit:   TurnCommit (already validated/repaired).
        day:      Current day number.
        scene:    Scene id string.

    Returns:
        New projected world dict.
    """
    turn_num = _next_turn(store)

    events: list[dict] = []
    for section, decl in commit.sections.items():
        owner = registry.owner_of_section(section)
        if owner is None:
            log.warning("apply_turn: no owner for section=%r (skipped)", section)
            continue
        section_events = owner.to_events(section, decl,
                                         turn=turn_num, day=day, scene=scene)
        events.extend(section_events)
        log.debug("apply_turn: section=%s events=%d", section, len(section_events))

    for ev in events:
        store.append(ev)
    new_world = project(registry, store.iter_events())

    log.debug("apply_turn: turn=%d events_appended=%d", turn_num, len(events))
    return new_world


def run_turn(
    registry: Registry,
    store,
    world: dict,
    scene: dict,
    player_input: str,
    *,
    strategy,
    provider,
    embedder=None,
    max_repairs: int = 3,
    required_sections: frozenset = frozenset(),
    cascade_provider=None,
    catchup_provider=None,
    prev_scene=None,
) -> TurnResult:
    """Run one complete turn: produce → validate/repair → drop → events → append → project.

    Delegates to produce_turn + apply_turn (backward-compatible S4a API).

    Args:
        registry:     Kernel registry (OntologySystem + others registered).
        store:        EventStore (open_store result, opened with registry.event_types()).
        world:        Current projected world dict.
        scene:        Scene dict with keys protagonist/present/day/location/(id).
        player_input: Raw player action string.
        strategy:     TurnStrategy instance (e.g. AuthorStrategy()).
        provider:     LLMProvider (FakeLLMProvider in tests, real in S5).
        embedder:     Optional embedder for recall ranking.
        max_repairs:  Maximum repair attempts before dropping bad sections.

    Returns:
        TurnResult with narration, updated world, commit, events,
        repair_attempts, and dropped_sections.
    """
    # Turn-level span: every LLM generation (produce/repair) and the digest fleet
    # nest under this in Langfuse. NoopTracer offline → zero overhead.
    turn_num_before = _next_turn(store)
    with get_tracer().span("turn", turn=turn_num_before,
                           player_input=(player_input or "")[:120]):
        protagonist = scene.get("protagonist")
        prev_loc = _protagonist_location(world, protagonist)
        prev_day = (world.get("meta", {}) or {}).get("day")

        commit, attempts, dropped_sections = produce_turn(
            registry, world, scene, player_input,
            strategy=strategy, provider=provider, embedder=embedder,
            max_repairs=max_repairs, required_sections=required_sections,
        )

        scene_id = scene.get("id") or scene.get("location") or "scene"
        day = advanced_day(world, commit)   # clock delta -> this turn stamps at post-advance day

        new_world = apply_turn(registry, store, commit, day=day, scene=scene_id)

        # Collect newly appended events (those with turn == turn_num_before)
        events: list[dict] = [
            ev for ev in store.iter_events()
            if ev.get("turn") == turn_num_before
        ]

        log.debug("run_turn: turn=%d events_appended=%d repair_attempts=%d dropped=%s",
                  turn_num_before, len(events), attempts, dropped_sections)

        # Backstage digest (design §12): cheap heuristic importance + threshold-gated
        # LLM reflection; appends arc events. Invisible to the main LLM, never fatal.
        # P2: also feeds narration_text + recap_provider for recap maintenance.
        try:
            with get_tracer().span("digest_fleet", turn=turn_num_before):
                appended_events = digest_fleet(
                    registry, store, events, new_world,
                    provider=provider,
                    narration_text=commit.narration,
                    scene=scene_id,
                    recap_provider=cascade_provider,
                )
            if appended_events:
                new_world = project(registry, store.iter_events())  # fold arc/narr facts into world
                log.debug("run_turn: digest appended %d event(s)", len(appended_events))
        except Exception:
            log.exception("run_turn: digest_fleet failed (non-fatal, backstage)")

        # 暗骰 director (design §16 / Phase B): a hidden seeded roll may append an
        # oracle_roll + director_fired directive that the NEXT turn's narrator weaves
        # in. Same shape as digest_fleet: post-apply, tracer span, never fatal.
        try:
            with get_tracer().span("director", turn=turn_num_before):
                dir_events = run_director(registry, store, new_world)
            if dir_events:
                new_world = project(registry, store.iter_events())
                log.debug("run_turn: director appended %d event(s)", len(dir_events))
        except Exception:
            log.exception("run_turn: run_director failed (non-fatal, backstage)")

        # §10 波状传播 (Phase C): a significant world-change ripples down nested
        # places. Same shape as digest_fleet/run_director: post-apply, tracer span,
        # never fatal, re-project on append.
        try:
            with get_tracer().span("cascade", turn=turn_num_before):
                cas_events = run_cascade(registry, store, new_world,
                                         scene=scene_id, provider=provider,
                                         cascade_provider=cascade_provider)
            if cas_events:
                new_world = project(registry, store.iter_events())
                log.debug("run_turn: cascade appended %d event(s)", len(cas_events))
        except Exception:
            log.exception("run_turn: run_cascade failed (non-fatal, backstage)")

        # Phase D catch-up: stale tracked entities entering scope get a cheap drift call.
        # Runs AFTER cascade (先链式下沉,再补在场/将进场 tracked). Non-fatal.
        # prev_scene is the scene from the PREVIOUS turn (passed in by play_loop).
        # If None (first turn or caller didn't thread it), default to empty dict so
        # prev_scope is empty — the staleness gate (last_update < now) prevents
        # spurious catch-up on turn 1 when entities were just created (last_update==now).
        _prev_scene = prev_scene if prev_scene is not None else {}
        try:
            with get_tracer().span("catchup", turn=turn_num_before):
                cat_events = run_catchup(registry, store, new_world,
                                         prev_scene=_prev_scene, new_scene=scene,
                                         provider=provider,
                                         catchup_provider=catchup_provider)
            if cat_events:
                new_world = project(registry, store.iter_events())
                log.debug("run_turn: catchup appended %d event(s)", len(cat_events))
        except Exception:
            log.exception("run_turn: run_catchup failed (non-fatal, backstage)")

        # Lore 暗骰 (L1): each active event-line independently rolls; a pass
        # advances a stage and drops a clue the NEXT turn's narrator can weave in.
        # Same shape as digest/director/cascade: post-apply, tracer span, non-fatal.
        try:
            with get_tracer().span("lore", turn=turn_num_before):
                lore_events = run_lore(registry, store, new_world)
            if lore_events:
                new_world = project(registry, store.iter_events())
                log.debug("run_turn: lore appended %d event(s)", len(lore_events))
        except Exception:
            log.exception("run_turn: run_lore failed (non-fatal, backstage)")

        # Demote-on-leave (T4): for each 明 line whose anchor town != the protagonist's
        # current L2 town, demote to 暗 with JIT-resequenced stages.
        # Guarded by registry ownership (quest_demoted) and non-fatal (like other hooks).
        try:
            if registry.owner_of_event("quest_demoted") is not None:
                _run_demote_on_leave(
                    registry, store, new_world, protagonist, provider,
                    turn_num=turn_num_before, day=day, scene=scene_id,
                )
                # Re-project after demote hook (safe: unchanged store → same world)
                new_world = project(registry, store.iter_events())
        except Exception:
            log.exception("run_turn: demote_on_leave failed (non-fatal, backstage)")

        # Density-based lore generation (L3): seed暗线 into the protagonist's current
        # L2 town on first entry; refresh on REFRESH_INTERVAL_DAYS cadence.
        # Guarded by registry ownership (lore_seeded) and non-fatal.
        # Provider preference: cascade_provider (cheap backstage model) first;
        # fall back to main provider so generation works even without RPG_CASCADE_MODEL.
        try:
            with get_tracer().span("density", turn=turn_num_before):
                if registry.owner_of_event("lore_seeded") is not None:
                    dens_events = run_density(
                        registry, store, new_world, protagonist,
                        provider=(cascade_provider or provider),
                        day=day, scene=scene_id, turn=turn_num_before,
                    )
                    if dens_events:
                        new_world = project(registry, store.iter_events())
                        log.debug("run_turn: density appended %d event(s)", len(dens_events))
        except Exception:
            log.exception("run_turn: run_density failed (non-fatal, backstage)")

        # Scene progression runs only when SceneSystem is registered (it owns
        # scene_advanced). Explicit opt-in: a clean no-op otherwise, instead of
        # appending a rejected event and swallowing the store's exception.
        if registry.owner_of_event("scene_advanced") is not None:
            new_loc = _protagonist_location(new_world, protagonist)
            new_day = (new_world.get("meta", {}) or {}).get("day")
            if (new_loc != prev_loc) or (new_day != prev_day):
                cur_no = (world.get("meta", {}) or {}).get("scene_no") or 1  # pre-turn world: stable counter
                new_no = cur_no + 1
                new_scene_id = f"s{new_no}"
                try:
                    store.append(kernel_event(
                        "scene_advanced", day=new_day or 1, scene=new_scene_id,
                        summary=f"场景推进→{new_scene_id}",
                        deltas={"scene_id": new_scene_id, "scene_no": new_no,
                                "location": new_loc, "day": new_day},
                        turn=turn_num_before,
                    ))
                    new_world = project(registry, store.iter_events())
                    log.debug("run_turn: scene advanced -> %s (loc %s->%s, day %s->%s)",
                              new_scene_id, prev_loc, new_loc, prev_day, new_day)
                except Exception:
                    log.exception("run_turn: scene_advanced failed (non-fatal)")

        return TurnResult(
            narration=commit.narration,
            world=new_world,
            commit=commit,
            events=events,
            repair_attempts=attempts,
            dropped_sections=dropped_sections,
        )


# ---------------------------------------------------------------------------
# demote-on-leave helper (T4)
# ---------------------------------------------------------------------------

def _run_demote_on_leave(registry, store, world: dict, protagonist: str | None,
                         provider, *, turn_num: int, day: int, scene: str) -> None:
    """Check all 明 lines: if anchor town != protagonist's current L2 town → demote.

    Emits quest_demoted{id, new_stages} for each qualifying line.
    Uses jit_resequence(line, world, provider) to get option-a continuation stages.
    Non-fatal: caller wraps in try/except.
    """
    if not protagonist:
        return

    # Resolve protagonist's current L2 town
    g = world.get("systems", {}).get("ontology")
    if g is None:
        return

    day_num = (world.get("meta", {}) or {}).get("day") or 1
    locs = g.neighbors(protagonist, "located_in", day_num)
    if not locs:
        return
    current_l3 = locs[0]
    current_l2 = _l2_ancestor(g, current_l3, day_num)
    # If we can't resolve a L2 ancestor, fall back to the L3 place itself
    # (e.g. when the protagonist IS at an L2 place directly)
    if current_l2 is None:
        e = g.get_entity(current_l3)
        if e and e.attrs.get("level") == 2:
            current_l2 = current_l3

    # If the protagonist's town is still unresolvable, skip demotion entirely
    # (e.g. protagonist at an L3 with no L2 ancestor in the graph, or unplaced)
    if current_l2 is None:
        return

    lines = (world.get("systems", {}).get("lore") or {}).get("lines", {})
    appended_count = 0
    for lid, ln in lines.items():
        if ln.get("state") != "明":
            continue
        anchor = ln.get("anchor")

        # Rule (a): protagonist has left the anchor town → demote.
        # Mass-demote guard: only fires when current_l2 is resolved (guaranteed
        # above — we returned early if current_l2 is None).
        # Same-turn surface guard: a line that world-push-surfaced to 明 THIS turn
        # gets one turn to reach the player before we consider demotion.
        just_surfaced = (ln.get("surfaced_turn") == turn_num)
        left_town = (anchor is not None and anchor != current_l2 and not just_surfaced)

        # Rule (b): 明 line idle >= IDLE_DEMOTE_DAYS game-days → demote regardless of location.
        last_adv_day = ln.get("last_advanced_day")
        idle_demote = (
            last_adv_day is not None
            and (day - last_adv_day) >= IDLE_DEMOTE_DAYS
        )

        if not left_town and not idle_demote:
            continue  # no demote condition met

        reason = []
        if left_town:
            reason.append(f"离开 anchor={anchor}")
        if idle_demote:
            reason.append(f"idle {day - last_adv_day} days >= {IDLE_DEMOTE_DAYS}")

        try:
            new_stages = jit_resequence(ln, world, provider)
        except Exception:
            # jit_resequence is already defensive, but double-guard
            idx = ln.get("stage_idx", -1)
            new_stages = list(ln.get("stages", [])[idx + 1:])
        ev = kernel_event(
            "quest_demoted", day=day, scene=scene,
            summary=f"明线降格:{lid} ({'; '.join(reason)})",
            deltas={"id": lid, "new_stages": new_stages},
            turn=turn_num,
        )
        store.append(ev)
        appended_count += 1
        log.debug("_run_demote_on_leave: %s demoted (%s, cur_l2=%s)",
                  lid, "; ".join(reason), current_l2)

    if appended_count:
        log.debug("_run_demote_on_leave: demoted %d line(s)", appended_count)
