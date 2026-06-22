"""Tests for loop.turn: run_turn pipeline + validate/repair loop + drop-fallback.

S4b Task 1: also tests produce_turn + apply_turn split.
"""
from __future__ import annotations

import tempfile
import os
import pytest

from kernel.registry import Registry
from kernel.projection import project, empty_world
from kernel.events import open_store, kernel_event
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.director import DirectorSystem


def _make_registry():
    registry = Registry()
    registry.register(OntologySystem())
    registry.register(PlaceSystem())
    registry.register(CharacterSystem())
    return registry


def _make_scene(day=1, scene_id="sc01"):
    return {"protagonist": "hero", "present": [], "day": day, "location": "town",
            "id": scene_id}


def _open_temp_store(registry):
    """Return an EventStore backed by temp files (caller must close)."""
    tmp_dir = tempfile.mkdtemp()
    db = os.path.join(tmp_dir, "events.db")
    jsonl = os.path.join(tmp_dir, "events.jsonl")
    return open_store(db, jsonl, allowed_types=registry.event_types())


# ---------------------------------------------------------------------------
# Task 2a: valid canned commit → events appended, world reflects them
# ---------------------------------------------------------------------------

def test_run_turn_valid_commit_appends_events():
    """A valid canned commit produces events and updates the world."""
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    canned = {
        "narration": "The hero emerges from darkness.",
        "entities": [
            {"id": "hero", "etype": "Person", "tier": "tracked"},
        ],
        "places": [
            {"id": "town", "level": 2, "kind": "settlement"},
        ],
    }
    provider = FakeLLMProvider(json_responses=[canned])
    strategy = AuthorStrategy()

    store = _open_temp_store(registry)
    try:
        result = run_turn(
            registry, store, world, scene, "I look around",
            strategy=strategy, provider=provider,
        )
    finally:
        store.close()

    assert result.narration == "The hero emerges from darkness."
    assert result.repair_attempts == 0
    assert len(result.events) > 0
    # hero entity created in world
    g = result.world.get("systems", {}).get("ontology")
    assert g is not None
    hero = g.get_entity("hero")
    assert hero is not None
    assert hero.etype == "Person"
    # town place created
    town = g.get_entity("town")
    assert town is not None


def test_run_turn_narration_returned():
    """run_turn returns TurnResult with narration from commit."""
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    canned = {"narration": "Moonlight falls on the cobblestones.", "entities": []}
    provider = FakeLLMProvider(json_responses=[canned])

    store = _open_temp_store(registry)
    try:
        result = run_turn(
            registry, store, world, scene, "look",
            strategy=AuthorStrategy(), provider=provider,
        )
    finally:
        store.close()

    assert result.narration == "Moonlight falls on the cobblestones."
    assert result.repair_attempts == 0


# ---------------------------------------------------------------------------
# Task 2b: invalid first, repaired on second attempt → repair_attempts >= 1
# ---------------------------------------------------------------------------

def test_run_turn_repair_loop_fixes_invalid_commit():
    """An invalid commit triggers MODULAR repair; only the failing section is re-emitted.

    Fix #8 behaviour (modular repair):
    - First pass: narration + entities (valid) + facts (dangling ref → invalid).
    - Repair call: returns ONLY the corrected `facts` section (no narration, no entities).
    - Result: narration == first-pass narration (NOT regenerated); facts fixed;
      entities from first pass preserved; ghost_entity appears via entities.
    """
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    # First response: valid narration + valid entities + invalid facts (dangling ref to
    # a DIFFERENT entity not in entities → cross-commit pending check won't save it)
    invalid_canned = {
        "narration": "First attempt narration.",
        "entities": [{"id": "ghost_entity", "etype": "Person", "tier": "tracked"}],
        "facts": [{"subject": "unknown_npc", "predicate": "mood", "value": "angry"}],  # dangling
    }
    # Repair response: ONLY the fixed facts section (modular — no full rewrite)
    repair_canned = {
        "facts": [],  # remove the dangling facts reference
    }
    provider = FakeLLMProvider(json_responses=[invalid_canned, repair_canned])

    store = _open_temp_store(registry)
    try:
        result = run_turn(
            registry, store, world, scene, "I act",
            strategy=AuthorStrategy(), provider=provider,
        )
    finally:
        store.close()

    # Should have done exactly 1 repair attempt
    assert result.repair_attempts >= 1
    # Narration unchanged from first pass (modular repair doesn't regenerate it)
    assert result.narration == "First attempt narration."
    # Final world should have the entity from the first-pass entities
    g = result.world.get("systems", {}).get("ontology")
    assert g is not None
    assert g.get_entity("ghost_entity") is not None


# ---------------------------------------------------------------------------
# Task 2c: commit stays invalid all attempts → section dropped, valid applied
# ---------------------------------------------------------------------------

def test_run_turn_drop_fallback_still_invalid_sections():
    """If a section stays invalid after max_repairs, it's dropped; valid sections apply."""
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn, TurnResult

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    # Every response has: valid entity + facts that dangle (ghost_entity never declared)
    stubborn_invalid = {
        "narration": "Stubborn turn.",
        "entities": [{"id": "real_hero", "etype": "Person", "tier": "tracked"}],
        "facts": [{"subject": "ghost_entity", "predicate": "mood", "value": "angry"}],
    }
    # Repeat the same invalid response for all attempts (max_repairs=3 → 4 calls total)
    provider = FakeLLMProvider(json_responses=[stubborn_invalid] * 4)

    store = _open_temp_store(registry)
    try:
        result = run_turn(
            registry, store, world, scene, "I act",
            strategy=AuthorStrategy(), provider=provider,
            max_repairs=3,
        )
    finally:
        store.close()

    # The 'facts' section should be in dropped_sections
    assert "facts" in result.dropped_sections
    # The 'entities' section had no errors (real_hero is valid), so real_hero exists
    g = result.world.get("systems", {}).get("ontology")
    assert g is not None
    assert g.get_entity("real_hero") is not None
    # ghost_entity was never added (the facts section was dropped, and no entities for it)
    assert g.get_entity("ghost_entity") is None


# ---------------------------------------------------------------------------
# Task 3: two-turn end-to-end sequence (world state accrues)
# ---------------------------------------------------------------------------

def test_two_turn_sequence():
    """Two successive run_turn calls accrue state in the shared event store.

    Turn 1: creates a place + creates characters (no cross-section moves, as
            validation checks destination against the current world before commit).
    Turn 2: moves protagonist to place (now exists) + evolves a character +
            asserts a fact.
    After turn 2: world has entities/facts from both turns; located_in is
                  correct (point-in-time via value_at / neighbors).
    """
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene(day=1, scene_id="sc01")

    # Turn 1: create place + characters (no moves — keep doesn't exist in world yet)
    turn1_canned = {
        "narration": "You reach the ancient keep.",
        "places": [
            {"id": "keep", "level": 2, "kind": "dungeon"},
        ],
        "cast": [
            {"op": "create", "id": "hero",
             "sketch": "Brave wanderer", "goal": "Find the artifact", "tier": "tracked"},
            {"op": "create", "id": "guard",
             "sketch": "Stern sentinel", "goal": "Protect the keep", "tier": "tracked"},
        ],
    }
    # Turn 2: move hero to keep (now exists) + evolve guard + assert fact
    turn2_canned = {
        "narration": "The guard eyes you warily as you step inside.",
        "moves": [
            {"who": "hero", "to": "keep"},
        ],
        "cast": [
            {"op": "evolve", "id": "guard", "predicate": "mood", "value": "suspicious"},
        ],
        "facts": [
            {"subject": "guard", "predicate": "on_duty", "value": "true"},
        ],
    }

    provider = FakeLLMProvider(json_responses=[turn1_canned, turn2_canned])
    strategy = AuthorStrategy()

    store = _open_temp_store(registry)
    try:
        # --- Turn 1 ---
        result1 = run_turn(
            registry, store, world, scene, "enter the keep",
            strategy=strategy, provider=provider,
        )
        assert result1.repair_attempts == 0
        world1 = result1.world

        # After turn 1: keep, hero, guard exist
        g1 = world1["systems"]["ontology"]
        assert g1.get_entity("keep") is not None
        assert g1.get_entity("hero") is not None
        assert g1.get_entity("guard") is not None

        # --- Turn 2 --- (world1 reflects turn-1 events; keep + characters now exist)
        scene2 = {**scene, "day": 2}
        result2 = run_turn(
            registry, store, world1, scene2, "talk to the guard",
            strategy=strategy, provider=provider,
        )
        assert result2.repair_attempts == 0
        world2 = result2.world

        # Hero moved to keep in turn 2
        g2 = world2["systems"]["ontology"]
        locations2 = g2.neighbors("hero", "located_in", day=2)
        assert "keep" in locations2

        # Guard evolved: mood=suspicious
        mood = g2.value_at("guard", "mood", day=2)
        assert mood == "suspicious"

        # guard.on_duty fact asserted via OntologySystem
        on_duty = g2.value_at("guard", "on_duty", day=2)
        assert on_duty == "true"

        # World state covers both turns' events
        assert len(result2.events) > 0
        assert result2.narration == "The guard eyes you warily as you step inside."
    finally:
        store.close()


# ---------------------------------------------------------------------------
# S4b Task 1: produce_turn + apply_turn split
# ---------------------------------------------------------------------------

def test_produce_turn_returns_commit_without_writing_store():
    """produce_turn produces (commit, attempts, dropped) but does NOT write to store."""
    from loop.strategy import AuthorStrategy
    from loop.turn import produce_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    canned = {
        "narration": "The hero emerges silently.",
        "entities": [{"id": "hero", "etype": "Person", "tier": "tracked"}],
    }
    provider = FakeLLMProvider(json_responses=[canned])
    strategy = AuthorStrategy()

    store = _open_temp_store(registry)
    try:
        commit, attempts, dropped = produce_turn(
            registry, world, scene, "I look around",
            strategy=strategy, provider=provider,
        )
        # Store must be empty — no events written
        events_in_store = list(store.iter_events())
        assert events_in_store == [], (
            f"produce_turn must not write to store; found {events_in_store}"
        )
    finally:
        store.close()

    # Returned commit has the narration
    assert commit.narration == "The hero emerges silently."
    assert attempts == 0
    assert dropped == []


def test_apply_turn_appends_events_and_returns_world():
    """apply_turn appends events from commit to store and returns the new world."""
    from loop.strategy import AuthorStrategy
    from loop.turn import produce_turn, apply_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene(day=2)

    canned = {
        "narration": "A wolf howls.",
        "entities": [{"id": "wolf", "etype": "Person", "tier": "tracked"}],
    }
    provider = FakeLLMProvider(json_responses=[canned])
    strategy = AuthorStrategy()

    store = _open_temp_store(registry)
    try:
        commit, _, _ = produce_turn(
            registry, world, scene, "listen",
            strategy=strategy, provider=provider,
        )
        # Store is empty before apply
        assert list(store.iter_events()) == []

        new_world = apply_turn(registry, store, commit, day=2, scene="sc01")

        # Now events are written
        events_in_store = list(store.iter_events())
        assert len(events_in_store) > 0

        # World updated
        g = new_world.get("systems", {}).get("ontology")
        assert g is not None
        assert g.get_entity("wolf") is not None
    finally:
        store.close()


def test_run_turn_still_works_after_split():
    """run_turn delegates to produce_turn + apply_turn and is backward-compatible."""
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    canned = {
        "narration": "Backward compat test.",
        "entities": [{"id": "compat_hero", "etype": "Person", "tier": "tracked"}],
    }
    provider = FakeLLMProvider(json_responses=[canned])
    strategy = AuthorStrategy()

    store = _open_temp_store(registry)
    try:
        result = run_turn(
            registry, store, world, scene, "I act",
            strategy=strategy, provider=provider,
        )
        assert result.narration == "Backward compat test."
        g = result.world.get("systems", {}).get("ontology")
        assert g.get_entity("compat_hero") is not None
    finally:
        store.close()


def test_produce_turn_repair_loop_no_store_writes():
    """produce_turn with modular repair loop never writes to store.

    Fix #8 behaviour (modular repair):
    - First pass: narration + entities (valid) + facts (dangling ref → invalid).
    - Repair: ONLY the corrected `facts` section returned (partial dict).
    - narration stays == first-pass narration (NOT the repair response's narration).
    - Still zero store writes throughout.
    """
    from loop.strategy import AuthorStrategy
    from loop.turn import produce_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    # First pass: valid narration + valid entities + invalid facts (dangling ref to
    # an entity NOT in entities or world → dangling_ref validation error on facts)
    invalid_canned = {
        "narration": "First pass narration.",
        "entities": [{"id": "ghost_x", "etype": "Person", "tier": "tracked"}],
        "facts": [{"subject": "missing_npc", "predicate": "mood", "value": "angry"}],  # dangling
    }
    # Repair: ONLY fixed facts (modular — no narration key, no entities key)
    repair_canned = {
        "facts": [],  # remove dangling ref
    }
    provider = FakeLLMProvider(json_responses=[invalid_canned, repair_canned])

    store = _open_temp_store(registry)
    try:
        commit, attempts, dropped = produce_turn(
            registry, world, scene, "I act",
            strategy=AuthorStrategy(), provider=provider,
        )
        assert list(store.iter_events()) == []
    finally:
        store.close()

    assert attempts == 1
    # narration unchanged from first pass (modular repair doesn't regenerate it)
    assert commit.narration == "First pass narration."


# ---------------------------------------------------------------------------
# Task 6 (Phase B1): run_director wired into run_turn post-apply
# ---------------------------------------------------------------------------

def _reg_with_director():
    from systems.ontology import OntologySystem
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    reg = Registry()
    reg.register(OntologySystem()); reg.register(PlaceSystem())
    reg.register(CharacterSystem()); reg.register(DirectorSystem())
    return reg


def test_run_turn_invokes_director_and_next_turn_sees_directive(monkeypatch):
    """A forced-fire director appends director_fired post-apply; the NEXT turn's
    assembled context contains the 导演 directive. Offline + deterministic."""
    import loop.director as dirmod
    from context.assembler import assemble_context
    from loop.turn import run_turn

    reg = _reg_with_director()
    store = _open_temp_store(reg)
    # seed so the campaign has a known campaign_seed in meta
    store.append(kernel_event("campaign_seeded", day=1, scene="genesis",
                              summary="seed", deltas={"campaign_seed": 1}, turn=0))

    # Force the director to fire deterministically by stubbing director_check.
    def _always_fire(scenes_since, tension, oracle, *, tables):
        et = tables["event_types"][0]; tw = tables["twists"][0]
        return {"triggered": True, "type": "front_stage", "magnitude": "big",
                "valence": None, "seed": {"event_type": et, "twist": tw},
                "prob": 0.6, "roll": 0.1}
    monkeypatch.setattr(dirmod, "director_check", _always_fire)

    world = project(reg, store.iter_events())
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "sc1", "location": "town"}

    # A FakeLLMProvider returning a minimal valid commit (narration + empty reasons).
    provider = FakeLLMProvider(json_responses=[{
        "narration": "一切如常。", "moves": [], "places": [], "cast": [], "facts": [],
    }])
    from loop.strategy import AuthorStrategy
    result = run_turn(reg, store, world, scene, "环顾四周",
                      strategy=AuthorStrategy(), provider=provider,
                      embedder=None, max_repairs=1)

    # director_fired now in the store, attributed to a turn AFTER this one
    all_events = list(store.iter_events())
    assert any(e["type"] == "director_fired" for e in all_events)

    # NEXT turn's context (re-projected world) shows the directive
    world2 = project(reg, store.iter_events())
    ctx = assemble_context(reg, world2, scene)
    assert "导演·暗骰" in ctx


# ---------------------------------------------------------------------------
# Task 7 (Phase C1): run_cascade wired into run_turn post-apply (after director)
# ---------------------------------------------------------------------------

def _reg_with_cascade():
    """Registry with all systems needed for cascade integration."""
    from systems.cascade import CascadeSystem
    reg = Registry()
    reg.register(OntologySystem())
    reg.register(PlaceSystem())
    reg.register(CharacterSystem())
    reg.register(DirectorSystem())
    reg.register(CascadeSystem())
    return reg


def test_run_turn_invokes_cascade_on_triggering_turn(monkeypatch):
    """A turn that moves the protagonist into a place with children triggers the
    vertical cascade post-apply; a place_evolved event lands in the store."""
    import loop.cascade as cmod
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    # Build registry with cascade; seed capital ⊃ market tree + protagonist
    reg = _reg_with_cascade()
    store = _open_temp_store(reg)

    # Seed: capital place (parent), market place (child), and protagonist character
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="capital 创建",
        deltas={"id": "capital", "level": 1, "kind": "settlement", "seed": "x", "tier": "tracked"},
        turn=0,
    ))
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="market 创建",
        deltas={"id": "market", "level": 2, "kind": "venue", "seed": "y",
                "tier": "tracked", "parent": "capital"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_created", day=1, scene="genesis", summary="hero 创建",
        deltas={"id": "hero", "etype": "Person", "tier": "tracked"},
        turn=0,
    ))

    world = project(reg, store.iter_events())
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "capital", "location": "capital"}

    # Stub _node_verdict so cascade is deterministic and provider-independent.
    # P1 fix: capital is now the first frontier item (root evolve); it must
    # return evolve:true so market (its child) is enqueued for the next round.
    monkeypatch.setattr(
        cmod, "_node_verdict",
        lambda place_id, ctx, provider: (
            {"id": place_id, "evolve": True, "state": "动荡"}
            if place_id in ("capital", "market")
            else {"evolve": False}
        ),
    )

    # Narrator commit moves hero into capital AND declares a world event → triggers cascade
    provider = FakeLLMProvider(json_responses=[{
        "narration": "你步入王都。",
        "moves": [{"who": "hero", "to": "capital"}],
        "places": [], "cast": [], "facts": [],
        "world": [{"areas": ["capital"], "level": 1, "summary": "王都骤变"}],
    }])
    result = run_turn(reg, store, world, scene, "进入王都",
                      strategy=AuthorStrategy(), provider=provider,
                      embedder=None, max_repairs=1)

    all_events = list(store.iter_events())
    assert any(
        e["type"] == "place_evolved" and e["deltas"]["id"] == "market"
        for e in all_events
    ), f"Expected place_evolved for market; got types: {[e['type'] for e in all_events]}"


# ---------------------------------------------------------------------------
# Fix 2: cascade_provider kwarg threaded through run_turn → run_cascade
# ---------------------------------------------------------------------------

def test_run_turn_passes_cascade_provider_to_cascade(monkeypatch):
    """run_turn(..., cascade_provider=<sentinel>) causes cascade node calls to go
    to the sentinel provider rather than the main narrator provider.

    Strategy: stub cmod._node_verdict to record which provider instance was passed.
    Main provider = FakeLLMProvider for narration; cascade_provider = a distinct
    FakeLLMProvider. After the turn, assert cascade_provider received the call,
    not the main provider.
    """
    import loop.cascade as cmod
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    reg = _reg_with_cascade()
    store = _open_temp_store(reg)

    # Same capital ⊃ market tree as the existing cascade test
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="capital 创建",
        deltas={"id": "capital", "level": 1, "kind": "settlement", "seed": "x", "tier": "tracked"},
        turn=0,
    ))
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="market 创建",
        deltas={"id": "market", "level": 2, "kind": "venue", "seed": "y",
                "tier": "tracked", "parent": "capital"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_created", day=1, scene="genesis", summary="hero 创建",
        deltas={"id": "hero", "etype": "Person", "tier": "tracked"},
        turn=0,
    ))

    world = project(reg, store.iter_events())
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "capital", "location": "capital"}

    # Two distinct fake providers: narrator (main) and cascade sentinel
    main_provider = FakeLLMProvider(json_responses=[{
        "narration": "你步入王都。",
        "moves": [{"who": "hero", "to": "capital"}],
        "places": [], "cast": [], "facts": [],
        "world": [{"areas": ["capital"], "level": 1, "summary": "王都骤变"}],
    }])
    cascade_sentinel = FakeLLMProvider()

    # Track which provider instance _node_verdict receives
    received_providers: list = []

    def _tracking_node_verdict(place_id, ctx, provider):
        received_providers.append(provider)
        return {"id": place_id, "evolve": True, "state": "动荡"}

    monkeypatch.setattr(cmod, "_node_verdict", _tracking_node_verdict)

    run_turn(reg, store, world, scene, "进入王都",
             strategy=AuthorStrategy(), provider=main_provider,
             cascade_provider=cascade_sentinel,
             embedder=None, max_repairs=1)

    # At least one cascade call happened (market has a child triggering BFS)
    assert len(received_providers) >= 1, "No cascade node calls recorded"
    # All cascade calls must have gone to the sentinel, NOT the main provider
    assert all(p is cascade_sentinel for p in received_providers), (
        f"Expected cascade_sentinel; got providers: {received_providers}"
    )
    # Main provider was used only for narration, not for cascade
    assert not any(p is main_provider for p in received_providers)


# ---------------------------------------------------------------------------
# Phase D Task 7: run_turn accepts catchup_provider kwarg
# ---------------------------------------------------------------------------

def test_run_turn_accepts_catchup_provider():
    """run_turn accepts catchup_provider kwarg and does not raise."""
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn
    from systems.cascade import CascadeSystem
    from systems.time import TimeSystem

    registry = Registry()
    registry.register(OntologySystem())
    registry.register(PlaceSystem())
    registry.register(CharacterSystem())
    registry.register(CascadeSystem())
    registry.register(TimeSystem())

    from kernel.projection import empty_world
    world = empty_world(registry)
    scene = _make_scene()

    canned = {"narration": "时光流逝。", "entities": [], "places": [], "cast": [], "facts": []}
    provider = FakeLLMProvider(json_responses=[canned])
    catchup_provider = FakeLLMProvider(json_responses=[])
    strategy = AuthorStrategy()

    store = _open_temp_store(registry)
    try:
        result = run_turn(
            registry, store, world, scene, "I wait",
            strategy=strategy, provider=provider,
            catchup_provider=catchup_provider,
        )
    finally:
        store.close()
    assert result.narration == "时光流逝。"


# ---------------------------------------------------------------------------
# P1: narrator `world` section drives the cascade end-to-end
# ---------------------------------------------------------------------------

def test_run_turn_world_section_triggers_vertical_cascade(monkeypatch):
    """The narrator declares world:[{areas:[capital],...}] → one world_change per
    area → run_cascade descends capital's child (market) → place_evolved lands."""
    import loop.cascade as cmod
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    reg = _reg_with_cascade()
    store = _open_temp_store(reg)
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="capital 创建",
        deltas={"id": "capital", "level": 1, "kind": "settlement", "seed": "x", "tier": "tracked"},
        turn=0,
    ))
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="market 创建",
        deltas={"id": "market", "level": 2, "kind": "venue", "seed": "y",
                "tier": "tracked", "parent": "capital"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_created", day=1, scene="genesis", summary="hero 创建",
        deltas={"id": "hero", "etype": "Person", "tier": "tracked"},
        turn=0,
    ))
    world = project(reg, store.iter_events())
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "capital", "location": "capital"}

    # P1 fix: capital is now the first frontier item (root evolve); it must
    # return evolve:true so market (its child) is enqueued for the next round.
    monkeypatch.setattr(
        cmod, "_node_verdict",
        lambda place_id, ctx, provider: (
            {"id": place_id, "evolve": True, "state": "动荡"}
            if place_id in ("capital", "market") else {"evolve": False}
        ),
    )

    # Narrator declares a world event over `capital` (NOT a move) → triggers cascade
    provider = FakeLLMProvider(json_responses=[{
        "narration": "王都骤变。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "world": [{"areas": ["capital"], "level": 1, "summary": "王都骤变"}],
    }])
    run_turn(reg, store, world, scene, "环顾四周",
             strategy=AuthorStrategy(), provider=provider,
             embedder=None, max_repairs=1)

    all_events = list(store.iter_events())
    assert any(e["type"] == "place_evolved" and e["deltas"]["id"] == "market"
               for e in all_events), \
        f"Expected place_evolved for market; got {[e['type'] for e in all_events]}"


# ---------------------------------------------------------------------------
# Task 10 (P2): run_turn records narration into the recap slice
# ---------------------------------------------------------------------------

def _reg_with_narrative():
    """Registry with lore + narrative for P2 recap wiring test."""
    from systems.cascade import CascadeSystem
    from systems.lore import LoreSystem
    from systems.narrative import NarrativeSystem
    reg = Registry()
    reg.register(OntologySystem())
    reg.register(PlaceSystem())
    reg.register(CharacterSystem())
    reg.register(DirectorSystem())
    reg.register(CascadeSystem())
    reg.register(LoreSystem())
    reg.register(NarrativeSystem())
    return reg


def test_run_turn_records_narration_into_recap():
    """After run_turn, result.world["systems"]["narrative"] contains this turn's narration."""
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn

    reg = _reg_with_narrative()
    store = _open_temp_store(reg)
    try:
        world = empty_world(reg)
        scene = _make_scene()
        provider = FakeLLMProvider(json_responses=[{
            "narration": "你踏入静谧的村庄。",
            "moves": [], "places": [], "cast": [], "facts": [],
        }])
        result = run_turn(reg, store, world, scene, "四处看看",
                          strategy=AuthorStrategy(), provider=provider,
                          cascade_provider=provider)
        buckets = result.world["systems"]["narrative"]["scenes"]
        assert any("你踏入静谧的村庄。" in t
                   for b in buckets for t in b["raw"])
    finally:
        store.close()
