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
import asyncio
import json
import hashlib
from typing import Any
from uuid import uuid4
from loop.resources import prepare_resources, validate_resources
from loop.narration_guard import validate_narration
from context.capabilities import (discovery_grant_errors, player_turn_knowledge_errors,
                                  player_turn_knowledge_event_errors,
                                  player_turn_fact_errors, player_turn_fact_event_errors,
                                  player_turn_entity_errors, player_turn_entity_event_errors,
                                  player_turn_world_errors, player_turn_world_event_errors,
                                  validate_protagonist_move_events, validate_protagonist_moves)
from context.native_authoring import (player_statement_receipt_errors,
                                      player_statement_receipt_event_errors)

from engine.store import ActionAlreadyCommitted, EventBatch, RevisionConflict

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
    # A transient user-interface hint from this turn's recap maintenance.  It
    # is persisted in result metadata for exact response-loss restoration but
    # intentionally is not an event or projected world state.
    recap_status: str = "not_due"


_RECAP_STATUSES = frozenset({"not_due", "saved", "pending"})


def _legacy_recap_status(events: list[dict]) -> str:
    """Safely derive a compatible status for pre-status result sidecars."""
    return "saved" if any(event.get("type") == "scene_summarized" for event in events) else "not_due"


class TurnRejected(ValueError):
    """The proposed action has no coherent, validated result to publish."""


def _read_preflight_metadata(metadata_raw):
    """Classify a committed sidecar without leaking malformed stored content."""
    if metadata_raw is None:
        return None, 'missing'
    try:
        metadata = json.loads(metadata_raw)
    except (TypeError, ValueError):
        return None, 'corrupt'
    if not isinstance(metadata, dict) or type(metadata.get('version')) is not int:
        return None, 'corrupt'
    if metadata['version'] != 1:
        return None, 'unsupported_version'
    return metadata, 'available'


def _memory_request_config(memory_role, memory_generator):
    """Return the stable memory portion of an explicit action request.

    Action-id retries only support the host-owned, fixed offline generator.  We
    fingerprint its non-plaintext configuration, never an arbitrary object,
    provider credential, or mutable session state.
    """
    if memory_generator is None:
        return {'enabled': False, 'role': memory_role}
    from systems.interpretive_memory import OfflineInterpretationGenerator
    if not isinstance(memory_generator, OfflineInterpretationGenerator):
        raise TurnRejected('action request requires a fixed offline memory generator')
    return {'enabled': True, 'role': memory_role,
            'generator': memory_generator.action_request_config()}


def _turn_request_fingerprint(scene, player_input, expected_revision, memory_role, *,
                              prev_scene, required_sections, max_repairs, memory_generator,
                              trusted_discovery_keys=frozenset(),
                              trusted_player_statement_receipts=(), legacy=False,
                              include_player_statement_receipts=True,
                              enforce_player_knowledge_authority: bool = False,
                              enforce_player_world_authority: bool = False,
                              enforce_player_fact_authority: bool = False,
                              enforce_player_entity_authority: bool = False):
    try:
        normalized_sections = sorted(set(required_sections))
    except TypeError as exc:
        raise TurnRejected('required_sections must be stable strings') from exc
    if not all(isinstance(section, str) for section in normalized_sections):
        raise TurnRejected('required_sections must be stable strings')
    if type(max_repairs) is not int or max_repairs < 0:
        raise TurnRejected('max_repairs must be a non-negative integer')
    envelope = {
        'version': 2,
        'scene': copy.deepcopy(scene),
        'player_input': player_input,
        'expected_revision': expected_revision,
        'prev_scene': copy.deepcopy(prev_scene),
        'required_sections': normalized_sections,
        'max_repairs': max_repairs,
        # A private interpretation is part of the action result.  It must
        # therefore be stable across a response-loss retry, without including
        # provider/session/credential implementation details.
        'memory': _memory_request_config(memory_role, memory_generator),
    }
    if enforce_player_knowledge_authority:
        # This is an authorization mode, not prose.  It must stay visible even
        # in the old compatibility envelope so an old, less-constrained
        # sidecar cannot be restored through the ordinary player boundary.
        envelope['ordinary_player_knowledge_authority'] = True
    if enforce_player_world_authority:
        # This authorization boundary must participate in action-id recovery:
        # a response issued before local-observation checks is not equivalent.
        envelope['ordinary_player_world_authority'] = True
    if enforce_player_fact_authority:
        # Canonical fact authority is independent of knowledge and local-world
        # observation authority; bind it to action-id recovery as well.
        envelope['ordinary_player_fact_authority'] = True
    if enforce_player_entity_authority:
        envelope['ordinary_player_entity_authority'] = True
    if not legacy:
        envelope['trusted_discovery_keys'] = sorted(trusted_discovery_keys)
        if include_player_statement_receipts:
            envelope['trusted_player_statement_receipts'] = copy.deepcopy(
                list(trusted_player_statement_receipts))
    try:
        encoded = json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TurnRejected('action request must be stable JSON') from exc
    return hashlib.sha256(encoded.encode()).hexdigest()


def _restore_completed_turn(registry, prior, metadata, fingerprint, expected_revision,
                            legacy_fingerprints=(), restricted_legacy_fingerprints=(),
                            legacy_validator=None):
    if not isinstance(metadata, dict) or metadata.get('kind') != 'turn_result':
        raise ActionAlreadyCommitted(prior['receipt'], None, metadata_status='corrupt')
    result = metadata.get('result')
    if not isinstance(result, dict):
        raise ActionAlreadyCommitted(prior['receipt'], None, metadata_status='corrupt')
    commit_data = result.get('commit')
    events = result.get('events')
    if (not isinstance(commit_data, dict)
            or not isinstance(commit_data.get('narration'), str)
            or not isinstance(commit_data.get('sections'), dict)
            or type(result.get('repair_attempts')) is not int
            or result['repair_attempts'] < 0
            or not isinstance(result.get('dropped_sections'), list)
            or not all(isinstance(section, str) for section in result['dropped_sections'])
            or not isinstance(events, list)
            or not all(isinstance(event, dict)
                       and isinstance(event.get('id'), str) and event['id']
                       and isinstance(event.get('type'), str) and event['type']
                       and type(event.get('seq')) is int and event['seq'] > 0
                       and type(event.get('day')) is int
                       and isinstance(event.get('scene'), str)
                       and isinstance(event.get('summary'), str)
                       and type(event.get('turn')) is int
                       for event in events)):
        raise ActionAlreadyCommitted(prior['receipt'], None, metadata_status='corrupt')
    receipt_events = {event['id']: event for event in prior['events']}
    if (len(receipt_events) != len(prior['events'])
            or any(receipt_events.get(event['id']) != event for event in events)):
        raise ActionAlreadyCommitted(prior['receipt'], None, metadata_status='corrupt')
    if (not isinstance(metadata.get('request_fingerprint'), str)
            or 'base_revision' not in metadata
            or (metadata['base_revision'] is not None
                and type(metadata['base_revision']) is not int)):
        raise ActionAlreadyCommitted(prior['receipt'], None, metadata_status='corrupt')
    accepted_fingerprints = {fingerprint, *legacy_fingerprints}
    if (metadata['request_fingerprint'] not in accepted_fingerprints
            or metadata['base_revision'] != expected_revision):
        raise RevisionConflict('action_id already belongs to a different request')
    if (metadata['request_fingerprint'] in set(restricted_legacy_fingerprints)
            and legacy_validator is not None):
        # A legacy request envelope omitted an authorization bit.  It is never
        # a licence to repeat or reinterpret the old action: the caller must
        # prove its already-published payload obeys the current boundary before
        # this function returns the stored result.
        legacy_validator(events, metadata)
    if 'recap_status' not in result:
        # Older v1 sidecars predate this presentation-only field.  Preserve
        # their recovery compatibility without guessing a failed recap.
        recap_status = _legacy_recap_status(events)
    else:
        recap_status = result['recap_status']
        # Check the type before set membership: JSON can carry containers,
        # which are unhashable and must fail closed rather than escape as a
        # TypeError from recovery.
        if not isinstance(recap_status, str) or recap_status not in _RECAP_STATUSES:
            raise ActionAlreadyCommitted(prior['receipt'], None, metadata_status='corrupt')
    from kernel.turncommit import TurnCommit
    world = project(registry, prior['history'])
    world['_revision'] = prior['revision']
    receipt = {**prior['receipt'], 'duplicate': True,
               'mirror_pending': prior['mirror_pending']}
    return TurnResult(narration=commit_data['narration'], world=world,
                      commit=TurnCommit(commit_data['narration'], copy.deepcopy(commit_data['sections'])),
                      events=copy.deepcopy(events), repair_attempts=result['repair_attempts'],
                      dropped_sections=list(result['dropped_sections']), receipt=receipt,
                      recap_status=recap_status)


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
    enforce_player_travel: bool = False,
    enforce_player_knowledge_authority: bool = False,
    enforce_player_world_authority: bool = False,
    enforce_player_fact_authority: bool = False,
    enforce_player_entity_authority: bool = False,
    trusted_discovery_keys=frozenset(),
    trusted_player_statement_receipts=(),
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
    travel_narration_baseline: str | None = None

    def validate_candidate():
        """Validate the current proposal and retain travel prose obligations.

        A bad protagonist move makes the original narration conditionally
        unsafe.  Repairs are section-granular, so deleting the move must not
        silently erase the requirement to replace that narration on a later
        pass.
        """
        nonlocal travel_narration_baseline
        candidate_errors = validate_commit(
            registry, commit, world, required_sections=required_sections,
        )
        candidate_errors.extend(player_statement_receipt_errors(
            world, scene, commit, receipts=trusted_player_statement_receipts,
        ))
        if enforce_player_knowledge_authority:
            candidate_errors.extend(player_turn_knowledge_errors(commit, scene.get("protagonist")))
        if enforce_player_world_authority:
            candidate_errors.extend(player_turn_world_errors(
                commit, world, scene, trusted_keys=trusted_discovery_keys,
            ))
        if enforce_player_fact_authority:
            candidate_errors.extend(player_turn_fact_errors(commit, world, scene))
        if enforce_player_entity_authority:
            candidate_errors.extend(player_turn_entity_errors(commit, world, scene))
        if enforce_player_travel:
            candidate_errors.extend(discovery_grant_errors(commit, scene.get("protagonist"), trusted_discovery_keys))
            travel_errors = validate_protagonist_moves(
                commit, world, scene,
                narration_rewrite_from=travel_narration_baseline,
                trusted_keys=trusted_discovery_keys,
            )
            if (travel_narration_baseline is None
                    and any(error.code == "scene_capability_narration"
                            for error in travel_errors)):
                travel_narration_baseline = commit.narration
            candidate_errors.extend(travel_errors)
        candidate_errors.extend(validate_resources(
            commit, scene.get('_resolved_values', {}), scene.get('_resolved_clock'), world,
        ))
        candidate_errors.extend(validate_narration(commit, world))
        return candidate_errors

    augment_unresolved_refs(commit, world, scene=_aug_scene, day=_aug_day)
    errors = validate_candidate()

    def reject_player_statement_authority_errors(candidate_errors):
        if any(error.code in {
            "missing_host_receipt", "invalid_host_receipt",
            "duplicate_host_receipt", "existing_player_statement",
            "untrusted_npc_knowledge",
        } for error in candidate_errors):
            # This is an authority error, not a malformed optional section.
            # Do not let a section-only repair erase the durable claim while
            # retaining narration that says an NPC heard private player words.
            raise TurnRejected("player-to-NPC knowledge lacks a valid host communication receipt")

    def reject_player_world_authority_errors(candidate_errors):
        if any(error.code == "player_world_observation_boundary" for error in candidate_errors):
            # Dropping only ``world`` could leave narration asserting a remote
            # or hidden observation.  Reject before a partial write instead.
            raise TurnRejected("player world observation lies outside the scene capability")

    def reject_player_fact_authority_errors(candidate_errors):
        if any(error.code == "player_fact_authority_boundary" for error in candidate_errors):
            # A facts-only repair could retain prose that promoted an unseen
            # canonical truth.  Reject rather than partially publishing it.
            raise TurnRejected("player fact lies outside the local public authority boundary")

    def reject_player_entity_authority_errors(candidate_errors):
        if any(error.code == "player_entity_authority_boundary" for error in candidate_errors):
            raise TurnRejected("player entity change lies outside the local public authority boundary")

    reject_player_statement_authority_errors(errors)
    reject_player_world_authority_errors(errors)
    reject_player_fact_authority_errors(errors)
    reject_player_entity_authority_errors(errors)

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
                merged_sections.update({k:v for k,v in repaired.items() if k != 'narration'})
                commit = _TC(
                    narration=repaired.get('narration', commit.narration),
                    sections=merged_sections,
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
        errors = validate_candidate()
        reject_player_statement_authority_errors(errors)
        reject_player_world_authority_errors(errors)
        reject_player_fact_authority_errors(errors)
        reject_player_entity_authority_errors(errors)
        attempts += 1

    # --------------------------------------------------------------------------
    # Step 3: Drop still-failing sections (fallback)
    # --------------------------------------------------------------------------
    dropped_sections: list[str] = []
    if errors:
        if enforce_player_travel and any(error.code in {
            "outside_scene_capability", "scene_capability_narration",
        } for error in errors):
            # A partial fallback would risk committing prose that says the
            # protagonist arrived somewhere the durable world rejects.  This
            # particular policy is therefore all-or-nothing after repairs.
            raise TurnRejected("player travel could not be reconciled with the scene capability")
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
    turn_num = _next_turn(store)

    # `apply_turn` is public and some callers use it without `produce_turn`.
    # Do the ownership check before asking *any* system to materialize events:
    # skipping an unknown section would make a model-declared effect silently
    # disappear, while checking during iteration could produce a partial local
    # proposal before the error is reported.  Normal run_turn callers already
    # receive the same diagnosis from validate_commit; this is the durable
    # boundary for direct callers too.
    unowned_sections = sorted(
        section for section in commit.sections
        if registry.owner_of_section(section) is None
    )
    if unowned_sections:
        raise TurnRejected(
            'No registered owner for commit section(s): ' + ', '.join(unowned_sections)
        )

    events: list[dict] = []
    for section, decl in commit.sections.items():
        owner = registry.owner_of_section(section)
        # The preflight above establishes this invariant; keep the assertion
        # explicit so a future Registry change cannot reintroduce silent loss.
        assert owner is not None
        section_events = owner.to_events(section, decl,
                                         turn=turn_num, day=day, scene=scene)
        events.extend(section_events)
        log.debug("apply_turn: section=%s events=%d", section, len(section_events))

    store.append_many(events, preflight=lambda history: project(registry, history))
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
    memory_role=None,
    memory_generator=None,
    trusted_memory_role=None,
    enforce_player_travel: bool = False,
    enforce_player_knowledge_authority: bool = False,
    enforce_player_world_authority: bool = False,
    enforce_player_fact_authority: bool = False,
    enforce_player_entity_authority: bool = False,
    trusted_discovery_keys=frozenset(),
    trusted_player_statement_receipts=(),
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
            enforce_player_travel=enforce_player_travel,
            enforce_player_knowledge_authority=enforce_player_knowledge_authority,
            enforce_player_world_authority=enforce_player_world_authority,
            enforce_player_fact_authority=enforce_player_fact_authority,
            enforce_player_entity_authority=enforce_player_entity_authority,
            trusted_discovery_keys=trusted_discovery_keys,
            trusted_player_statement_receipts=trusted_player_statement_receipts,
        )
        if dropped_sections:
            raise TurnRejected('Action still contains invalid sections: ' + ', '.join(dropped_sections))

        scene_id = scene.get("id") or scene.get("location") or "scene"
        day = advanced_day(world, commit)   # clock delta -> this turn stamps at post-advance day

        new_world = apply_turn(registry, store, commit, day=day, scene=scene_id)
        # The visible story is part of the action, never an optional digest side
        # effect. A failing reflection hook cannot lose a successfully shown turn.
        canonical_binding = None
        if commit.narration and registry.owner_of_event('narration_recorded') is not None:
            store.append(kernel_event('narration_recorded', day=day, scene=scene_id,
                summary='narration recorded', deltas={'scene': scene_id, 'text': commit.narration},
                turn=turn_num_before))
            canonical_narration = store.events[-1]
            if memory_role is not None:
                from systems.interpretive_memory import HostNarrationBinding
                canonical_binding = HostNarrationBinding(store, canonical_narration,
                    canonical_narration['id'], store.turn, memory_role)
            new_world = project(registry, store.iter_events())
        if memory_generator is not None:
            from systems.interpretive_memory import HostNarrationBinding, stage_offline_interpretation
            try:
                if canonical_binding is not None:
                    with store.savepoint():
                        graph = world.get('systems', {}).get('ontology')
                        stage_offline_interpretation(store, canonical_binding, registry, memory_generator,
                            protagonist=trusted_memory_role,
                            role_exists=bool(graph and graph.get_entity(trusted_memory_role)))
            except asyncio.CancelledError:
                raise
            except PermissionError:
                raise
            except Exception:
                log.warning("offline interpretation failed; keeping foreground turn (private-error)")
            else:
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
        recap_status = "not_due"
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
            recap_status = getattr(appended_events, 'recap_status',
                                   _legacy_recap_status(appended_events))
        except asyncio.CancelledError:
            # A cancellation is control flow, not a recoverable recap status.
            # Let the enclosing batch abort instead of publishing a turn with
            # an invented presentation result.
            raise
        except Exception:
            # Do not surface the exception body or retry the provider here.
            # ``pending`` is meaningful only when digest_fleet itself reached
            # a recap attempt and returned that explicit status.  An earlier
            # reflection/backstage failure says nothing about recap work, so
            # keep the neutral ``not_due`` presentation state.
            log.error("run_turn: digest_fleet failed (non-fatal, backstage)")

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
                lore_events = _backstage_call(store, run_lore, registry, store, new_world)
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
            recap_status=recap_status,
        )


def _backstage_call(store, function, *args, **kwargs):
    with store.savepoint():
        return function(*args, **kwargs)


def run_turn(registry, store, world, scene, player_input, *, strategy, provider,
             embedder=None, max_repairs=3, required_sections=frozenset(),
             cascade_provider=None, catchup_provider=None, prev_scene=None,
             action_id=None, expected_revision=None, memory_role=None, memory_generator=None,
             host_envelope=None, enforce_player_travel: bool = False,
             enforce_player_knowledge_authority: bool = False,
             enforce_player_world_authority: bool = False,
             enforce_player_fact_authority: bool = False,
             enforce_player_entity_authority: bool = False,
             trusted_discovery_keys=frozenset(),
             trusted_player_statement_receipts=()) -> TurnResult:
    """Prepare one complete action without a lock, then atomically publish it.

    Foreground effects, narration and backstage consequences share one turn.
    A stale proposal or failed write changes neither the database nor the
    narrator's committed conversation. Callers may supply an action identity.
    """
    trusted_player_statement_receipts = tuple(trusted_player_statement_receipts)
    version = expected_revision if expected_revision is not None else world.get('_revision')
    fingerprint = (_turn_request_fingerprint(
        scene, player_input, version, memory_role, prev_scene=prev_scene,
        required_sections=required_sections, max_repairs=max_repairs,
        memory_generator=memory_generator, trusted_discovery_keys=trusted_discovery_keys,
        trusted_player_statement_receipts=trusted_player_statement_receipts,
        enforce_player_knowledge_authority=enforce_player_knowledge_authority,
        enforce_player_world_authority=enforce_player_world_authority,
        enforce_player_fact_authority=enforce_player_fact_authority,
        enforce_player_entity_authority=enforce_player_entity_authority)
        if action_id else None)
    if action_id:
        prior = store.read_committed_action(action_id)
        if prior is not None:
            if prior['retracted']:
                raise RevisionConflict('action was retracted; submit a new action_id')
            metadata, status = _read_preflight_metadata(prior['metadata_raw'])
            if status != 'available':
                raise ActionAlreadyCommitted(prior['receipt'], None, metadata_status=status)
            receiptless = (_turn_request_fingerprint(
                           scene, player_input, version, memory_role,
                           prev_scene=prev_scene, required_sections=required_sections,
                           max_repairs=max_repairs, memory_generator=memory_generator,
                           trusted_discovery_keys=trusted_discovery_keys,
                           trusted_player_statement_receipts=(),
                      include_player_statement_receipts=False,
                      enforce_player_knowledge_authority=enforce_player_knowledge_authority,
                      enforce_player_world_authority=enforce_player_world_authority,
                      enforce_player_fact_authority=enforce_player_fact_authority,
                      enforce_player_entity_authority=enforce_player_entity_authority)
                           if not trusted_player_statement_receipts else None)
            legacy = (_turn_request_fingerprint(scene, player_input, version, memory_role,
                      prev_scene=prev_scene, required_sections=required_sections,
                      max_repairs=max_repairs, memory_generator=memory_generator,
                      trusted_discovery_keys=trusted_discovery_keys,
                      trusted_player_statement_receipts=trusted_player_statement_receipts,
                      legacy=True,
                      enforce_player_knowledge_authority=enforce_player_knowledge_authority,
                      enforce_player_world_authority=enforce_player_world_authority,
                      enforce_player_fact_authority=enforce_player_fact_authority,
                      enforce_player_entity_authority=enforce_player_entity_authority)
                      if not trusted_discovery_keys and not trusted_player_statement_receipts else None)
            # Sidecars written before ordinary-player knowledge authority had
            # no mode marker.  Offer a deliberately narrow, read-only bridge
            # only for an otherwise unprivileged envelope; any host discovery
            # or communication grant remains a current-mode-only contract.
            old_ordinary_mode = None
            if (enforce_player_knowledge_authority and not enforce_player_world_authority
                    and not enforce_player_fact_authority
                    and not enforce_player_entity_authority
                    and not trusted_discovery_keys
                    and not trusted_player_statement_receipts):
                old_ordinary_mode = _turn_request_fingerprint(
                    scene, player_input, version, memory_role,
                    prev_scene=prev_scene, required_sections=required_sections,
                    max_repairs=max_repairs, memory_generator=memory_generator,
                    enforce_player_knowledge_authority=False,
                    enforce_player_world_authority=False,
                    enforce_player_fact_authority=False,
                    enforce_player_entity_authority=False,
                )

            def validate_old_ordinary_mode(events, stored_metadata):
                # New sidecars explicitly say which mode produced them.  Only
                # the genuinely pre-mode shape can use this bridge; a current
                # false-mode request must remain a cross-mode conflict.
                if "authority_modes" in stored_metadata:
                    raise RevisionConflict('action_id already belongs to a different request')
                errors = player_turn_knowledge_event_errors(
                    events, scene.get("protagonist"),
                    allow_player_statement=False,
                )
                # We cannot recreate a host-issued speech receipt from an old
                # no-mode sidecar.  Require the existing canonical validator
                # to find no protected player-statement row at all.
                errors.extend(player_statement_receipt_event_errors(
                    world, scene, events,
                ))
                if errors:
                    raise RevisionConflict('action_id cannot be safely recovered under current authority')

            return _restore_completed_turn(
                registry, prior, metadata, fingerprint, version,
                tuple(value for value in (receiptless, legacy, old_ordinary_mode)
                      if value is not None),
                restricted_legacy_fingerprints=(old_ordinary_mode,) if old_ordinary_mode else (),
                legacy_validator=(validate_old_ordinary_mode if old_ordinary_mode else None),
            )
    trusted_memory_role = scene.get('protagonist')
    travel_world = world
    travel_scene = copy.deepcopy(scene)
    # Filled only after the validated final commit is available.  The publish
    # validator must distinguish a merely *offered* host receipt from a
    # receipt that the accepted commit actually authorizes to materialize.
    authorized_player_statement_receipt_ids: tuple[str, ...] = ()
    batch = EventBatch(store, preflight=lambda history: project(registry, history))
    if enforce_player_travel:
        def validate_travel_payload(events):
            errors = validate_protagonist_move_events(events, travel_world, travel_scene,
                                                       trusted_keys=trusted_discovery_keys)
            if errors:
                raise TurnRejected("final publish violates the player scene capability")
        batch.add_publish_validator(validate_travel_payload)
    if enforce_player_knowledge_authority:
        def validate_player_knowledge_payload(events):
            errors = player_turn_knowledge_event_errors(events, travel_scene.get("protagonist"))
            if errors:
                raise TurnRejected("final publish violates the player knowledge authority boundary")
        batch.add_publish_validator(validate_player_knowledge_payload)
    if enforce_player_world_authority:
        def validate_player_world_payload(events):
            errors = player_turn_world_event_errors(
                events, travel_world, travel_scene, trusted_keys=trusted_discovery_keys,
            )
            if errors:
                raise TurnRejected("final publish violates the player world observation boundary")
        batch.add_publish_validator(validate_player_world_payload)
    if enforce_player_fact_authority:
        def validate_player_fact_payload(events):
            errors = player_turn_fact_event_errors(events, travel_world, travel_scene)
            if errors:
                raise TurnRejected("final publish violates the player fact authority boundary")
        batch.add_publish_validator(validate_player_fact_payload)
    if enforce_player_entity_authority:
        def validate_player_entity_payload(events):
            errors = player_turn_entity_event_errors(events, travel_world, travel_scene)
            if errors:
                raise TurnRejected("final publish violates the player entity authority boundary")
        batch.add_publish_validator(validate_player_entity_payload)
    def validate_player_statement_payload(events):
        errors = player_statement_receipt_event_errors(
            travel_world, travel_scene, events,
            receipts=trusted_player_statement_receipts,
            authorized_receipt_ids=authorized_player_statement_receipt_ids,
        )
        if errors:
            raise TurnRejected("final publish violates the player-statement receipt boundary")
    batch.add_publish_validator(validate_player_statement_payload)
    if version is not None and version != batch.revision:
        raise RevisionConflict('refresh the world before preparing another action')
    cached_revision = getattr(strategy, '_committed_revision', None)
    if cached_revision is not None and cached_revision != batch.revision and hasattr(strategy, 'reset'):
        strategy.reset()
    old_state = copy.deepcopy(getattr(strategy, '__dict__', {}))
    try:
        if registry.owner_of_event('resources_resolved') is not None:
            resolution, expected, prompt = prepare_resources(world, scene, player_input, provider, batch.turn)
            if resolution:
                batch.append(resolution)
                world = project(registry, batch.iter_events())
                scene = {**scene, '_resolved_values':expected, '_resolution_prompt':prompt,
                         '_resolved_clock':resolution['deltas'].get('wait_until')}
        from loop.variation import prepare_variation, variation_fragment
        variation = prepare_variation(registry, world, scene, batch.turn)
        if variation:
            batch.append(variation)
            scene = {**scene, '_variation_prompt': variation_fragment(variation)}
        # Normal CLI/GUI authors may reuse only the same redacted conditional
        # arrival and recipient-recall projection used by native hand-off.
        # Project the active EventStore snapshot immediately to bounded text:
        # raw rows never enter a strategy scene, provider tool closure, GUI
        # host envelope, fingerprint, or result sidecar.  This also cannot
        # mint a player-statement receipt from natural-language input.
        from context.assembler import safe_authoring_handoff
        scene = {
            **scene,
            '_safe_authoring_handoff': safe_authoring_handoff(
                world, scene,
                trusted_events=tuple(copy.deepcopy(event) for event in batch.iter_events(include_retracted=True)),
                player_statement_receipts=trusted_player_statement_receipts,
            ),
        }
        result = _run_turn_staged(registry, batch, world, scene, player_input,
            strategy=strategy, provider=provider, embedder=embedder,
            max_repairs=max_repairs, required_sections=required_sections,
            cascade_provider=cascade_provider, catchup_provider=catchup_provider,
            prev_scene=prev_scene, memory_role=memory_role, memory_generator=memory_generator,
            trusted_memory_role=trusted_memory_role, enforce_player_travel=enforce_player_travel,
            enforce_player_knowledge_authority=enforce_player_knowledge_authority,
            enforce_player_world_authority=enforce_player_world_authority,
            enforce_player_fact_authority=enforce_player_fact_authority,
            enforce_player_entity_authority=enforce_player_entity_authority,
            trusted_discovery_keys=trusted_discovery_keys,
            trusted_player_statement_receipts=trusted_player_statement_receipts)
        authorized_player_statement_receipt_ids = tuple(
            item['receipt_id']
            for item in result.commit.sections.get('knowledge', [])
            if (isinstance(item, dict) and item.get('op', 'told') == 'heard_player'
                and isinstance(item.get('receipt_id'), str))
        )
        graph = result.world.get('systems', {}).get('ontology')
        for (subject, predicate), value in scene.get('_resolved_values', {}).items():
            if graph.value_at(subject, predicate, result.world['meta'].get('day') or 1) != value:
                raise TurnRejected('a backstage effect conflicts with the resolved resource outcome')
        result_metadata = None
        if action_id:
            if host_envelope is not None:
                try:
                    json.dumps(host_envelope, sort_keys=True, ensure_ascii=False, allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise TurnRejected('host action envelope must be stable JSON') from exc
            result_metadata = {
                'version': 1,
                'kind': 'turn_result',
                'request_fingerprint': fingerprint,
                'base_revision': version,
                # Persist the explicit mode so future changes can distinguish
                # this current false-mode request from a truly old sidecar
                # that predated the authorization feature.
                'authority_modes': {
                    'ordinary_player_knowledge_authority': bool(enforce_player_knowledge_authority),
                    'ordinary_player_world_authority': bool(enforce_player_world_authority),
                    'ordinary_player_fact_authority': bool(enforce_player_fact_authority),
                    'ordinary_player_entity_authority': bool(enforce_player_entity_authority),
                },
                'host_envelope': copy.deepcopy(host_envelope),
                'result': {
                    'commit': {
                        'narration': result.commit.narration,
                        'sections': result.commit.sections,
                    },
                    'repair_attempts': result.repair_attempts,
                    'dropped_sections': result.dropped_sections,
                    'recap_status': result.recap_status,
                    # result.events is the caller-visible turn result.  Persist
                    # it rather than deriving it from the receipt range: later
                    # background work can share that range but was never part of
                    # the original response.
                    'events': result.events,
                },
            }
        receipt = batch.publish(action_id=action_id or str(uuid4()), result_metadata=result_metadata)
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
