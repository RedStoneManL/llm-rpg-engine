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
import copy
from typing import Any
from uuid import uuid4
from loop.resources import prepare_resources, registered_balances, validate_resources
from loop.narration_guard import validate_narration
from systems.time import normalize_clock, validate_resolved_time
from loop.strategy import AuthorOutputError

from engine.store import EventBatch, RevisionConflict

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
    receipt: dict = field(default_factory=dict)


class TurnRejected(ValueError):
    """The proposed action has no coherent, validated result to publish."""


def _whole_turn_repair(player_input: str) -> str:
    """An absent/unusable proposal is not a partially authored, committed turn."""
    return (
        "上一整回合的输出不可用，没有任何部分提交，也不能当作已发生的剧情。"
        "根据当前已提交世界状态和下面最后一条实际玩家行动，重新生成完整 TurnCommit。"
        "只输出一个合法 JSON 对象，不要散文前缀或代码围栏。"
        "narration 必须为非空叙事字符串；moves、places、cast、facts、clock 必须齐全。"
        "同时输出本次行动实际需要的所有可选段，尤其物品交接的 items、信息流动的 knowledge 等；"
        "不要仅补缺失段，不保留无效输出的正文或假定其变更已经发生。\n"
        f"[player] {player_input}"
    )


def _item_preflight(registry, prior_ids, authorized_return_creations=None,
                    authorized_player_inputs=None):
    """Keep legacy replay permissive, but check every new staged item event."""
    def check_new(projected, event):
        if event['id'] not in prior_ids and event['type'] == 'player_input_recorded':
            approved = (authorized_player_inputs or {}).get(event['id'])
            if approved is None or any(event.get(key) != value for key, value in approved.items()):
                raise TurnRejected('Player input sources require exact host provenance')
        if event['id'] not in prior_ids and event['type'] == 'item_return_promised':
            approved = (authorized_return_creations or {}).get(event['id'])
            if approved is None or any(event.get(key) != value for key, value in approved.items()):
                raise TurnRejected('A return commitment requires a verified current-player intent')
        if event['id'] not in prior_ids and registry.owner_of_event('item_transferred') is not None:
            from kernel.item_integrity import item_event_error
            error = item_event_error(projected, event)
            if error:
                raise TurnRejected('Invalid item transition: ' + error[2])
    return lambda history: project(registry, history, before_apply=check_new)


def _next_turn(store) -> int:
    """Compute turn number = max existing event turn + 1, or 1 if none."""
    if hasattr(store, 'next_turn'):
        return store.next_turn()
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

    The narrator's `clock` section gives a delta or absolute target. We normalize
    it against the current (day, band) from world.meta and return the new day; the new
    band is folded separately by TimeSystem.apply on the clock_advanced event.
    Absent/none clock => no advance (back-compat with callers that omit it).

    Public — also used by app.play in compare mode so the 甲 commit stamps at
    the correct post-advance day rather than the pre-turn scene day.
    """
    meta = world.get("meta", {})
    cur_day = meta.get("day") or 1
    cur_band = meta.get("band") or 0
    decl = normalize_clock(commit.sections.get("clock") or [], world)
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
    output_error = None
    try:
        with get_tracer().span("produce"):
            commit = strategy.produce(
                registry, world, scene, player_input,
                provider=provider, embedder=embedder,
            )
    except AuthorOutputError as exc:
        commit, output_error = None, exc

    # --------------------------------------------------------------------------
    # Step 2: Validate + modular repair loop
    #
    # An unusable Author response needs a WHOLE proposal, not missing-section
    # repair of fallback prose. Whole and modular retries share max_repairs.
    # When a usable proposal fails validation, re-emit ONLY failing sections (not narration,
    # not passing sections).  This is far cheaper than a full re-author (the
    # usable narration is preserved for latency, not guaranteed semantically
    # consistent with every repaired effect; full semantic reconciliation is a
    # separate concern). Regeneration on every repair was measured at ~59s/repair.
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
    def validate(proposal):
        augment_unresolved_refs(proposal, world, scene=_aug_scene, day=_aug_day)
        result = validate_commit(registry, proposal, world, required_sections=required_sections)
        result.extend(validate_resources(proposal, scene.get('_resolved_values', {})))
        result.extend(validate_resolved_time(proposal, scene.get('_resolved_clock'), world))
        result.extend(validate_narration(proposal, world))
        return result

    errors = validate(commit) if output_error is None else []
    while (output_error is not None or errors) and attempts < max_repairs:
        failing = {e.section for e in errors}
        log.debug("produce_turn: repair attempt=%d errors=%d failing=%s output_error=%s",
                  attempts + 1, len(errors), sorted(failing),
                  output_error.code if output_error is not None else None)
        with get_tracer().span("repair", attempt=attempts + 1):
            try:
                if output_error is not None:
                    commit = strategy.produce(
                        registry, world, scene, player_input,
                        provider=provider, embedder=embedder,
                        repair=_whole_turn_repair(player_input),
                    )
                else:
                    try:
                        repaired = strategy.repair_sections(failing, errors, provider=provider)
                        # Preserve passing sections only for a usable whole proposal.
                        from kernel.turncommit import TurnCommit as _TC
                        merged_sections = dict(commit.sections)
                        merged_sections.update({k:v for k,v in repaired.items() if k != 'narration'})
                        commit = _TC(
                            narration=repaired.get('narration', commit.narration),
                            sections=merged_sections,
                        )
                    except NotImplementedError:
                        # Third-party strategies retain the legacy re-author path.
                        commit = strategy.produce(
                            registry, world, scene, player_input,
                            provider=provider, embedder=embedder,
                            repair=build_repair_request(errors),
                        )
                output_error = None
            except AuthorOutputError as exc:
                commit, output_error = None, exc
        attempts += 1
        errors = validate(commit) if output_error is None else []

    if output_error is not None:
        raise TurnRejected(
            f"Author output unusable after {attempts} repairs: {output_error.code}"
        ) from None

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
    # Persistent conversation is advanced only by run_turn after durable commit.
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
    if hasattr(store, 'snapshot'):
        source_revision, prior_events = store.snapshot()
        turn_num = max((event.get('turn') or 0 for event in prior_events
                        if not event.get('retracted')), default=0) + 1
    else:
        source_revision = None
        prior_events = list(store.iter_events(include_retracted=True))
        turn_num = _next_turn(store)

    # Absolute endpoints are resolved against the PRE-turn clock, never the
    # post-advance `day` argument. Keep the event format and replay unchanged.
    sections = commit.sections
    clock_decl = sections.get("clock") or []
    if (isinstance(clock_decl, list) and any(
            isinstance(item, dict) and "target" in item for item in clock_decl)):
        prior_world = project(registry, (event for event in prior_events
                                         if not event.get("retracted")))
        normalized = normalize_clock(clock_decl, prior_world)
        sections = {**sections, "clock": normalized}
        from kernel.turncommit import TurnCommit
        day = advanced_day(prior_world, TurnCommit(commit.narration, sections))

    events: list[dict] = []
    from kernel.item_integrity import creation_first_sections
    for section, decl in creation_first_sections(sections):
        owner = registry.owner_of_section(section)
        if owner is None:
            log.warning("apply_turn: no owner for section=%r (skipped)", section)
            continue
        section_events = owner.to_events(section, decl,
                                         turn=turn_num, day=day, scene=scene)
        events.extend(section_events)
        log.debug("apply_turn: section=%s events=%d", section, len(section_events))

    prior_ids = {event['id'] for event in prior_events}
    store.append_many(events, expected_revision=source_revision,
                      preflight=_item_preflight(registry, prior_ids))
    new_world = project(registry, store.iter_events())

    log.debug("apply_turn: turn=%d events_appended=%d", turn_num, len(events))
    return new_world


def _run_turn_staged(
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
        if dropped_sections:
            raise TurnRejected('Action still contains invalid sections: ' + ', '.join(dropped_sections))

        scene_id = scene.get("id") or scene.get("location") or "scene"
        day = advanced_day(world, commit)   # clock delta -> this turn stamps at post-advance day

        new_world = apply_turn(registry, store, commit, day=day, scene=scene_id)
        # The visible story is part of the action, never an optional digest side
        # effect. A failing reflection hook cannot lose a successfully shown turn.
        if commit.narration and registry.owner_of_event('narration_recorded') is not None:
            store.append(kernel_event('narration_recorded', day=day, scene=scene_id,
                summary='narration recorded', deltas={'scene': scene_id, 'text': commit.narration},
                turn=turn_num_before))
            new_world = project(registry, store.iter_events())

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
                appended_events = _backstage_call(store, digest_fleet,
                    registry, store, events, new_world,
                    provider=provider,
                    narration_text=commit.narration,
                    record_narration=False,
                    scene=scene_id,
                    recap_provider=cascade_provider or provider,
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
                dir_events = _backstage_call(store, run_director, registry, store, new_world)
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
                cas_events = _backstage_call(store, run_cascade, registry, store, new_world,
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
                cat_events = _backstage_call(store, run_catchup, registry, store, new_world,
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
                lore_events = _backstage_call(store, run_lore, registry, store, new_world,
                                             protagonist=protagonist)
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
                _backstage_call(store, _run_demote_on_leave,
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
                    dens_events = _backstage_call(store, run_density,
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
            events=[ev for ev in store.iter_events() if ev.get('turn') == turn_num_before],
            repair_attempts=attempts,
            dropped_sections=dropped_sections,
        )


def _backstage_call(store, function, *args, **kwargs):
    with store.savepoint():
        return function(*args, **kwargs)


def run_turn(registry, store, world, scene, player_input, *, strategy, provider,
             embedder=None, max_repairs=3, required_sections=frozenset(),
             cascade_provider=None, catchup_provider=None, prev_scene=None,
             action_id=None, expected_revision=None, return_commitment=None) -> TurnResult:
    """Prepare one complete action without a lock, then atomically publish it.

    Foreground effects, narration and backstage consequences share one turn.
    A stale proposal or failed write changes neither the database nor the
    narrator's committed conversation. Callers may supply an action identity.
    """
    # Bind the observer before resource-intent or any other model call. Custom
    # strategies retain their own contracts; shipped narrators share this gate.
    from loop.strategy import AuthorStrategy, HybridStrategy, _bound_actor_id
    if isinstance(strategy, (AuthorStrategy, HybridStrategy)) or return_commitment is not None:
        _bound_actor_id(world, scene)
    from systems.player_sources import capture_player_input, player_input_event
    captured_input = (capture_player_input(world, scene, player_input)
        if registry.owner_of_event('player_input_recorded') is not None else None)
    batch = EventBatch(store)
    prior_ids = {event['id'] for event in batch.iter_events(include_retracted=True)}
    authorized_return_creations = {}
    authorized_player_inputs = {}
    batch.preflight = _item_preflight(registry, prior_ids, authorized_return_creations,
                                      authorized_player_inputs)
    version = expected_revision if expected_revision is not None else world.get('_revision')
    if version is not None and version != batch.revision:
        raise RevisionConflict('refresh the world before preparing another action')
    cached_revision = getattr(strategy, '_committed_revision', None)
    if cached_revision is not None and cached_revision != batch.revision and hasattr(strategy, 'reset'):
        strategy.reset()
    old_state = copy.deepcopy(getattr(strategy, '__dict__', {}))
    try:
        if return_commitment is not None:
            if registry.owner_of_event('item_return_promised') is None:
                raise TurnRejected('Return commitments are not enabled in this registry')
            if not isinstance(return_commitment, dict) or set(return_commitment) != {'item', 'recipient', 'due', 'evidence'}:
                raise TurnRejected('Invalid authorized return commitment shape')
            evidence = return_commitment.get('evidence')
            actions = evidence.get('player_actions') if isinstance(evidence, dict) else None
            if (not isinstance(actions, list) or not actions
                    or any(not isinstance(action, str) or not action.strip() for action in actions)
                    or '\n'.join(actions) != player_input):
                raise TurnRejected('Return commitment evidence must match the actual player input')
            data = {**copy.deepcopy(return_commitment), 'id': 'return_' + uuid4().hex,
                    'debtor': scene['protagonist']}
            promised = kernel_event('item_return_promised',
                day=world.get('meta', {}).get('day') or 1,
                scene=scene.get('id') or scene.get('location') or 'scene',
                turn=batch.turn, summary='item return commitment recorded', deltas=data)
            # A reaffirmation keeps the original open obligation and its evidence.
            # Validate even a duplicate candidate before taking the no-create path;
            # otherwise direct callers could bypass quote/type/deadline checks.
            from systems.return_commitments import _creation_record
            candidate = _creation_record(world, promised)
            records = world.get('systems', {}).get('return_commitments', {}).get('records', {})
            already_open = any(
                record.get('status') == 'open' and all(
                    record.get(field) == candidate[field]
                    for field in ('debtor', 'item', 'recipient', 'due'))
                for record in records.values())
            if not already_open:
                authorized_return_creations[promised['id']] = copy.deepcopy(promised)
                batch.append(promised)
                world = project(registry, batch.iter_events())
        # Lock all registered owners, not just the acting protagonist. These
        # private values stay in the host's validation context, never the prompt.
        expected_balances = registered_balances(world)
        if registry.owner_of_event('resources_resolved') is not None:
            resolution, expected, prompt = prepare_resources(world, scene, player_input, provider, batch.turn)
            if resolution:
                batch.append(resolution)
                world = project(registry, batch.iter_events())
                expected_balances.update(expected)
                scene = {**scene, '_resolution_prompt':prompt,
                         '_resolved_clock':resolution['deltas'].get('wait_until')}
        scene = {**scene, '_resolved_values': expected_balances}
        from loop.variation import prepare_variation, variation_fragment
        variation = prepare_variation(registry, world, scene, batch.turn)
        if variation:
            batch.append(variation)
            scene = {**scene, '_variation_prompt': variation_fragment(variation)}
        world = {**world, '_action_turn': batch.turn}
        result = _run_turn_staged(registry, batch, world, scene, player_input,
            strategy=strategy, provider=provider, embedder=embedder,
            max_repairs=max_repairs, required_sections=required_sections,
            cascade_provider=cascade_provider, catchup_provider=catchup_provider,
            prev_scene=prev_scene)
        # Hook return lists are advisory; check the actual staged history even
        # when a hook appended an event but failed to report it to the caller.
        result.world = project(registry, batch.iter_events())
        for event_id, approved in authorized_return_creations.items():
            matches = [event for event in batch.events if event.get('id') == event_id]
            if (len(matches) != 1 or matches[0].get('retracted')
                    or any(matches[0].get(key) != value for key, value in approved.items())):
                raise TurnRejected('Authorized return creation was removed or changed')
            record = result.world.get('systems', {}).get('return_commitments', {}).get('records', {}).get(
                approved['deltas']['id'])
            if record is None or any(record.get(key) != value for key, value in approved['deltas'].items()):
                raise TurnRejected('Authorized return commitment is missing or changed')
        from systems.return_commitments import _return_proven
        records = result.world.get('systems', {}).get('return_commitments', {}).get('records', {})
        for event in batch.events:
            if event.get('type') == 'item_return_fulfilled' and not event.get('retracted'):
                record = records.get(event.get('deltas', {}).get('id'))
                if record is None or not _return_proven(result.world, record,
                        result.world.get('meta', {}).get('day') or 1):
                    raise TurnRejected('Return completion conflicts with final physical possession')
                final_clock = {'day': result.world.get('meta', {}).get('day') or 1,
                               'band': result.world.get('meta', {}).get('band') or 0}
                if record.get('fulfilled_at') != final_clock:
                    raise TurnRejected('Return completion conflicts with final action clock')
        if registry.owner_of_event('item_return_fulfilled') is not None:
            from systems.return_commitments import completion_events
            completions = completion_events(result.world,
                day=result.world.get('meta', {}).get('day') or 1,
                scene=scene.get('id') or scene.get('location') or 'scene', turn=batch.turn)
            if completions:
                batch.append_many(completions)
                result.world = project(registry, batch.iter_events())
                result.events = [event for event in batch.iter_events() if event.get('turn') == batch.turn]
        graph = result.world.get('systems', {}).get('ontology')
        for (subject, predicate), value in scene.get('_resolved_values', {}).items():
            if graph.value_at(subject, predicate, result.world['meta'].get('day') or 1) != value:
                raise TurnRejected('a backstage effect conflicts with the resolved resource outcome')
        # This is the only creation path. Backstage hooks and model sections
        # cannot supply source records, even if they forge plausible evidence.
        if any(event.get('type') == 'player_input_recorded' for event in batch.events):
            raise TurnRejected('Player input sources require exact host provenance')
        if captured_input is not None:
            recorded = player_input_event(captured_input, result.world, result.commit,
                                          batch.events, turn=batch.turn)
            authorized_player_inputs[recorded['id']] = copy.deepcopy(recorded)
            batch.append(recorded)
            result.world = project(registry, batch.iter_events())
        result.events = [event for event in batch.iter_events() if event.get('turn') == batch.turn]
        receipt = batch.publish(action_id=action_id or str(uuid4()))
    except BaseException:
        if hasattr(strategy, '__dict__'):
            strategy.__dict__.clear()
            strategy.__dict__.update(old_state)
        raise
    result.receipt = receipt
    result.world['_revision'] = receipt['revision']
    strategy._committed_revision = receipt['revision']
    try:
        commit_thread = getattr(strategy, 'commit_to_thread', None)
        if commit_thread:
            commit_thread(result.narration)
    except Exception:
        # Persistence already succeeded. Rebuild this derivative cache next turn
        # instead of telling the caller that the committed action failed.
        log.exception('Conversation cache update failed after commit')
        if hasattr(strategy, 'reset'):
            strategy.reset()
    return result


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
