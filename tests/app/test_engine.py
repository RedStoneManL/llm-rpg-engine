"""Tests for app.engine: build_engine + new_game."""
from __future__ import annotations

import pytest
from pathlib import Path
from llm.provider import FakeLLMProvider
from app.engine import build_engine, new_game


def test_build_engine_returns_required_attrs(tmp_path):
    """build_engine returns an object with registry, store, provider, embedder, world."""
    from app.engine import build_engine

    engine = build_engine(tmp_path, provider=FakeLLMProvider())

    assert hasattr(engine, "registry")
    assert hasattr(engine, "store")
    assert hasattr(engine, "provider")
    assert hasattr(engine, "embedder")
    assert hasattr(engine, "world")


def test_build_engine_registry_has_all_6_systems(tmp_path):
    """Registry must have all 6 systems with ontology first (no requires error)."""
    from app.engine import build_engine

    engine = build_engine(tmp_path, provider=FakeLLMProvider())

    system_names = [s.name for s in engine.registry.systems]
    assert "ontology" in system_names
    assert "place" in system_names
    assert "character" in system_names
    assert "object" in system_names
    assert "faction" in system_names
    assert "knowledge" in system_names
    # ontology must be first (others require it)
    assert system_names[0] == "ontology"


def test_build_engine_store_created(tmp_path):
    """store is created at campaign_dir/events.db."""
    from app.engine import build_engine

    engine = build_engine(tmp_path, provider=FakeLLMProvider())

    assert (tmp_path / "events.db").exists()


def test_build_engine_provider_is_injected(tmp_path):
    """Injected provider is stored on the engine."""
    from app.engine import build_engine

    fake = FakeLLMProvider()
    engine = build_engine(tmp_path, provider=fake)

    assert engine.provider is fake


def test_build_engine_world_is_dict(tmp_path):
    """World is a projected dict with meta and systems keys."""
    from app.engine import build_engine

    engine = build_engine(tmp_path, provider=FakeLLMProvider())

    assert isinstance(engine.world, dict)
    assert "meta" in engine.world
    assert "systems" in engine.world


def test_reopen_ignores_legacy_unowned_event_without_advancing_current_scene(tmp_path):
    """An old plugin event may remain stored but cannot skew recovered meta state."""
    from engine.store import EventStore
    from kernel.events import kernel_event

    db_path = tmp_path / "events.db"
    jsonl_path = tmp_path / "events.jsonl"
    # This simulates a campaign written before a plugin was removed.  The old
    # store has no current allow-list, while reopening through build_engine does.
    legacy = EventStore(db_path, jsonl_path, {"entity_created", "removed_plugin_event"})
    legacy.append_many([
        kernel_event("entity_created", day=1, scene="owned_scene", summary="主角", deltas={
            "id": "protagonist", "etype": "Person", "tier": "tracked",
        }),
        kernel_event("removed_plugin_event", day=9, scene="orphan_scene", summary="遗留插件事件"),
    ])
    legacy.close()
    db_before = db_path.read_bytes()
    jsonl_before = jsonl_path.read_bytes()

    engine = build_engine(tmp_path, provider=FakeLLMProvider(), embedder=None)
    try:
        assert engine.world["meta"]["day"] == 1
        assert engine.world["meta"]["scene"] == "owned_scene"
        assert engine.world["meta"]["timeline"] == [
            {"day": 1, "scene": "owned_scene", "summary": "主角"},
        ]
    finally:
        engine.store.close()
    assert db_path.read_bytes() == db_before
    assert jsonl_path.read_bytes() == jsonl_before


def test_reopen_ignores_owned_place_event_that_projection_skips_without_meta_drift(tmp_path):
    """A legacy malformed owned event stays durable but cannot move recovered meta.

    ``PlaceSystem.apply`` has always documented missing move endpoints as a
    compatibility skip.  The kernel must give that skip the same all-or-nothing
    meta treatment as an event with no installed owner: otherwise a later bad
    row changes the reopened day/scene while leaving the graph at the prior
    location.
    """
    from kernel.events import kernel_event

    campaign = tmp_path / "legacy-place-skip"
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        engine.store.append_many([
            kernel_event("entity_created", day=1, scene="owned_scene", summary="主角", deltas={
                "id": "protagonist", "etype": "Person", "tier": "tracked",
            }),
            kernel_event("place_created", day=1, scene="owned_scene", summary="客栈", deltas={
                "id": "inn", "level": 1, "kind": "venue", "tier": "tracked",
            }),
            kernel_event("entity_moved", day=1, scene="owned_scene", summary="到客栈", deltas={
                "who": "protagonist", "to": "inn",
            }),
        ])
        # This is accepted by the legacy direct store path and intentionally
        # remains readable for compatibility.  PlaceSystem skips it because
        # the durable move is incomplete.
        engine.store.append(kernel_event(
            "entity_moved", day=9, scene="bad_scene", summary="损坏移动",
            deltas={"who": "protagonist"},
        ))
    finally:
        engine.store.close()

    db_path = campaign / "events.db"
    jsonl_path = campaign / "events.jsonl"
    db_before = db_path.read_bytes()
    jsonl_before = jsonl_path.read_bytes()

    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        graph = reopened.world["systems"]["ontology"]
        assert reopened.world["meta"]["day"] == 1
        assert reopened.world["meta"]["scene"] == "owned_scene"
        assert reopened.world["meta"]["timeline"] == [
            {"day": 1, "scene": "owned_scene", "summary": "主角"},
            {"day": 1, "scene": "owned_scene", "summary": "客栈"},
            {"day": 1, "scene": "owned_scene", "summary": "到客栈"},
        ]
        assert graph.neighbors("protagonist", "located_in", day=9) == ["inn"]
    finally:
        reopened.store.close()
    assert db_path.read_bytes() == db_before
    assert jsonl_path.read_bytes() == jsonl_before


def test_reopen_ignores_owned_ontology_event_that_projection_skips_without_meta_drift(tmp_path):
    """A malformed fact row cannot move meta when its owner skips it."""
    from kernel.events import kernel_event

    campaign = tmp_path / "legacy-ontology-skip"
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        engine.store.append(kernel_event(
            "entity_created", day=1, scene="owned_scene", summary="主角", deltas={
                "id": "protagonist", "etype": "Person", "tier": "tracked",
            },
        ))
        # Legacy/direct EventStore callers can retain a registered but
        # incomplete fact event.  OntologySystem has always skipped it.
        engine.store.append(kernel_event(
            "fact_asserted", day=9, scene="bad_scene", summary="损坏事实",
            deltas={"predicate": "mood", "value": "坏"},
        ))
    finally:
        engine.store.close()

    db_path = campaign / "events.db"
    jsonl_path = campaign / "events.jsonl"
    db_before = db_path.read_bytes()
    jsonl_before = jsonl_path.read_bytes()

    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        graph = reopened.world["systems"]["ontology"]
        assert reopened.world["meta"] == {
            "day": 1,
            "scene": "owned_scene",
            "timeline": [{"day": 1, "scene": "owned_scene", "summary": "主角"}],
        }
        assert graph.value_at("protagonist", "mood", day=9) is None
    finally:
        reopened.store.close()
    assert db_path.read_bytes() == db_before
    assert jsonl_path.read_bytes() == jsonl_before


def test_reopen_ignores_remaining_owned_compatibility_skips_without_meta_drift(tmp_path):
    """Pure compatibility skips across the remaining owners are whole-row ignores.

    This deliberately uses the normal direct EventStore path that can carry
    legacy rows.  It does not exercise lifecycle no-ops (such as a duplicate
    interpretation) or Cascade audit/queue behavior, which are durable events
    with intentional semantics rather than malformed-row compatibility skips.
    """
    from kernel.events import kernel_event

    campaign = tmp_path / "legacy-remaining-owner-skips"
    engine = build_engine(
        campaign, provider=FakeLLMProvider(), embedder=None,
        enable_interpretive_memory=True,
    )
    try:
        base = kernel_event("entity_created", day=1, scene="owned_scene", summary="主角", deltas={
            "id": "protagonist", "etype": "Person", "tier": "tracked",
        })
        malformed = [
            # CharacterSystem: each event lacks the field that makes it a graph mutation.
            kernel_event("character_created", day=2, scene="bad_character", summary="bad", deltas={"sketch": "x", "goal": "y"}),
            kernel_event("character_evolved", day=3, scene="bad_character", summary="bad", deltas={"predicate": "mood", "value": "bad"}),
            kernel_event("relationship_changed", day=4, scene="bad_character", summary="bad", deltas={"toward": "npc", "value": "bad"}),
            # ObjectSystem.
            kernel_event("object_created", day=5, scene="bad_object", summary="bad", deltas={}),
            kernel_event("item_transferred", day=6, scene="bad_object", summary="bad", deltas={"to": "protagonist"}),
            # KnowledgeSystem.
            kernel_event("knowledge_set", day=7, scene="bad_knowledge", summary="bad", deltas={"fact_key": "rumor", "value": "bad"}),
            kernel_event("knowledge_broadcast", day=8, scene="bad_knowledge", summary="bad", deltas={"audience": {"place": "nowhere"}, "value": "bad"}),
            # FactionSystem.
            kernel_event("faction_created", day=9, scene="bad_faction", summary="bad", deltas={}),
            kernel_event("member_changed", day=10, scene="bad_faction", summary="bad", deltas={"faction": "none"}),
            # LoreSystem creation/bookkeeping entries with no durable key.
            kernel_event("lore_created", day=11, scene="bad_lore", summary="bad", deltas={}),
            kernel_event("quest_created", day=12, scene="bad_lore", summary="bad", deltas={}),
            kernel_event("quest_opened", day=13, scene="bad_lore", summary="bad", deltas={}),
            kernel_event("lore_seeded", day=14, scene="bad_lore", summary="bad", deltas={}),
            kernel_event("density_refreshed", day=15, scene="bad_lore", summary="bad", deltas={}),
            # Opt-in InterpretiveMemorySystem: no private body is put in diagnostics.
            kernel_event("character_interpretation_recorded", day=16, scene="bad_memory", summary="bad", deltas={"actor": "", "text": ""}),
        ]
        engine.store.append_many([base, *malformed])
    finally:
        engine.store.close()

    db_path = campaign / "events.db"
    jsonl_path = campaign / "events.jsonl"
    db_before = db_path.read_bytes()
    jsonl_before = jsonl_path.read_bytes()

    reopened = build_engine(
        campaign, provider=FakeLLMProvider(), embedder=None,
        enable_interpretive_memory=True,
    )
    try:
        assert reopened.world["meta"] == {
            "day": 1,
            "scene": "owned_scene",
            "timeline": [{"day": 1, "scene": "owned_scene", "summary": "主角"}],
        }
        assert reopened.world["systems"]["interpretive_memory"]["by_actor"] == {}
    finally:
        reopened.store.close()
    assert db_path.read_bytes() == db_before
    assert jsonl_path.read_bytes() == jsonl_before


def test_reproject_engine_world_keeps_the_snapshot_revision_when_peer_writes_after_read(tmp_path, monkeypatch):
    """A refresh must not label an old event snapshot as a newer peer revision."""
    from app.engine import reproject_engine_world
    from kernel.events import kernel_event

    campaign = tmp_path / "campaign"
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    peer = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    original_snapshot = engine.store.snapshot
    seen = {}

    def snapshot_then_peer_publish():
        revision, history = original_snapshot()
        seen["snapshot_revision"] = revision
        peer.store.append_many([kernel_event(
            "narration_recorded", day=1, scene="s1", turn=1,
            summary="peer published after snapshot", deltas={"text": "PEER_SNAPSHOT_RACE"},
        )])
        return revision, history

    monkeypatch.setattr(engine.store, "snapshot", snapshot_then_peer_publish)
    try:
        refreshed_revision = reproject_engine_world(engine)
        assert refreshed_revision == seen["snapshot_revision"]
        assert engine.world["_revision"] == seen["snapshot_revision"]
        assert engine.store.revision == seen["snapshot_revision"] + 1

        monkeypatch.setattr(engine.store, "snapshot", original_snapshot)
        assert reproject_engine_world(engine) == peer.store.revision
        assert engine.world["_revision"] == peer.store.revision
    finally:
        peer.store.close()
        engine.store.close()


def test_refresh_if_advanced_keeps_an_after_snapshot_peer_publish_for_next_read(tmp_path, monkeypatch):
    """The passive refresh comparison uses its own consistent snapshot too."""
    from app.engine import projected_revision, refresh_engine_world_if_advanced
    from kernel.events import kernel_event

    campaign = tmp_path / "campaign"
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    peer = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    original_snapshot = engine.store.snapshot

    def snapshot_then_peer_publish():
        revision, history = original_snapshot()
        peer.store.append_many([kernel_event(
            "narration_recorded", day=1, scene="s1", turn=1,
            summary="peer after passive snapshot", deltas={"text": "PEER_PASSIVE_SNAPSHOT_RACE"},
        )])
        return revision, history

    monkeypatch.setattr(engine.store, "snapshot", snapshot_then_peer_publish)
    try:
        assert not refresh_engine_world_if_advanced(engine)
        assert projected_revision(engine) == 0
        assert peer.store.revision == 1

        monkeypatch.setattr(engine.store, "snapshot", original_snapshot)
        assert refresh_engine_world_if_advanced(engine)
        assert projected_revision(engine) == peer.store.revision
    finally:
        peer.store.close()
        engine.store.close()


def test_new_game_creates_protagonist_and_place(tmp_path):
    """new_game seeds a bootstrapped world: protagonist + start town (town_0) + NPCs.

    Strengthened for Task 10: the bootstrap pipeline replaces the old three-event
    placeholder genesis.  The world must have:
      - 'protagonist' (tracked Person)
      - 'town_0' (L2 settlement) — NOT the old 'starting_location' placeholder
      - >= 2 NPC Persons
      - >= 1 lore line
      - NO 'starting_location' entity
    """
    from app.engine import build_engine, new_game

    fake = FakeLLMProvider()
    engine = build_engine(tmp_path, provider=fake)
    new_game(engine)

    g = engine.world["systems"]["ontology"]

    # 'protagonist' must exist as a tracked Person
    assert "protagonist" in g.entities, "protagonist not found after new_game"
    assert g.entities["protagonist"].etype == "Person"
    assert g.entities["protagonist"].tier == "tracked"

    # Old placeholder must be gone; bootstrap uses 'town_0'
    assert "starting_location" not in g.entities, (
        "'starting_location' placeholder present — bootstrap did not replace old genesis"
    )
    assert "town_0" in g.entities, "town_0 not found — bootstrap start town missing"
    assert g.entities["town_0"].etype == "Place"

    # At least 2 NPC Persons (bootstrap always creates >= 2)
    npc_persons = [
        eid for eid, e in g.entities.items()
        if e.etype == "Person" and eid != "protagonist"
    ]
    assert len(npc_persons) >= 2, (
        f"Expected >= 2 NPC Persons after bootstrap, got {npc_persons}"
    )

    # At least 1 lore line (bootstrap gen_threads always produces >= 3)
    lore = engine.world.get("systems", {}).get("lore", {})
    lines = lore.get("lines", {})
    assert len(lines) >= 1, f"Expected >= 1 lore line after bootstrap, got {len(lines)}"


def test_new_game_protagonist_located_in_place(tmp_path):
    """new_game places protagonist in a real venue (venue_*), not 'starting_location'.

    Strengthened for Task 10: bootstrap places the protagonist in the first L3
    venue (venue_0, venue_1, …) rather than the old 'starting_location' sentinel.
    """
    from app.engine import build_engine, new_game

    fake = FakeLLMProvider()
    engine = build_engine(tmp_path, provider=fake)
    new_game(engine)

    g = engine.world["systems"]["ontology"]
    # protagonist is still the canonical tracked Person id
    assert "protagonist" in g.entities, "protagonist not found"
    protagonist_id = "protagonist"

    # Must have a located_in relation
    day = engine.world["meta"]["day"] or 1
    locations = g.neighbors(protagonist_id, "located_in", day)
    assert len(locations) >= 1, f"Protagonist has no located_in relation"

    # Must be a venue (venue_*), NOT the old starting_location placeholder
    location = locations[0]
    assert location.startswith("venue_"), (
        f"Protagonist is in '{location}', expected a venue_* from bootstrap local map"
    )


def test_new_game_events_persisted(tmp_path):
    """new_game appends the full bootstrap event set to the store.

    Strengthened for Task 10: bootstrap generates many more events than the old
    3-event placeholder genesis (campaign_seeded + place_created + character_created
    + entity_moved).  We assert >= 10 events to catch any accidental no-op.
    Also assert campaign_seeded is present and lore_created events exist.
    """
    from app.engine import build_engine, new_game

    fake = FakeLLMProvider()
    engine = build_engine(tmp_path, provider=fake)
    new_game(engine)

    events = list(engine.store.iter_events())
    assert len(events) >= 10, (
        f"Expected >= 10 bootstrap genesis events, got {len(events)}"
    )

    types = {e["type"] for e in events}
    assert "campaign_seeded" in types, "campaign_seeded event missing"
    assert "lore_created" in types, "lore_created event missing — threads not bootstrapped"


def test_build_engine_registers_director(tmp_path):
    from llm.provider import FakeLLMProvider
    eng = build_engine(tmp_path / "campA", provider=FakeLLMProvider(), embedder=None)
    names = {s.name for s in eng.registry.systems}
    assert "director" in names
    # the store must accept director event types (strict allow-set)
    assert {"campaign_seeded", "oracle_roll", "director_fired"} <= eng.registry.event_types()


def test_new_game_seeds_campaign_seed_into_meta(tmp_path):
    from llm.provider import FakeLLMProvider
    eng = build_engine(tmp_path / "campB", provider=FakeLLMProvider(), embedder=None)
    new_game(eng)
    seed = eng.world["meta"].get("campaign_seed")
    assert isinstance(seed, int) and seed > 0


def test_build_engine_registers_lore_and_narrative(tmp_path):
    """build_engine must register LoreSystem + NarrativeSystem (T3)."""
    from llm.provider import FakeLLMProvider
    engine = build_engine(tmp_path / "p2_reg", provider=FakeLLMProvider())
    assert engine.registry.owner_of_section("quests").name == "lore"
    assert engine.registry.owner_of_event("quest_opened").name == "lore"
    assert engine.registry.owner_of_event("narration_recorded").name == "narrative"
    assert "lore" in engine.world["systems"]
    assert "narrative" in engine.world["systems"]


def test_campaign_seed_is_deterministic_per_campaign_name(tmp_path):
    from llm.provider import FakeLLMProvider
    e1 = build_engine(tmp_path / "same", provider=FakeLLMProvider(), embedder=None)
    new_game(e1)
    e2 = build_engine(tmp_path / "x" / "same", provider=FakeLLMProvider(), embedder=None)
    new_game(e2)
    # seed derives from the campaign dir *name*, so same name → same seed (rewind-safe)
    assert e1.world["meta"]["campaign_seed"] == e2.world["meta"]["campaign_seed"]


# ---------------------------------------------------------------------------
# Task 3 (Phase C1): CascadeSystem registered in build_engine
# ---------------------------------------------------------------------------

def test_build_engine_registers_cascade(tmp_path):
    """CascadeSystem must be registered so cascade event types are accepted."""
    engine = build_engine(tmp_path, provider=FakeLLMProvider())
    assert engine.registry.owner_of_event("place_evolved").name == "cascade"
    assert "world_change" in engine.registry.event_types()
    assert "cascade" in engine.world["systems"]


# ---------------------------------------------------------------------------
# Fix 2: cascade_provider field on Engine + RPG_CASCADE_MODEL env wiring
# ---------------------------------------------------------------------------

def test_build_engine_cascade_provider_none_with_fake_provider(tmp_path):
    """FakeLLMProvider has no model/api_key → cascade_provider stays None."""
    engine = build_engine(tmp_path, provider=FakeLLMProvider())
    assert engine.cascade_provider is None


def test_build_engine_cascade_provider_none_when_no_env_var(tmp_path, monkeypatch):
    """With a real-looking provider but no RPG_CASCADE_MODEL, cascade_provider is None."""
    from llm.provider import ZhipuProvider
    monkeypatch.delenv("RPG_CASCADE_MODEL", raising=False)
    monkeypatch.delenv("GLM_CASCADE_MODEL", raising=False)
    real_prov = ZhipuProvider(model="glm-5.1", api_key="x")
    engine = build_engine(tmp_path, provider=real_prov)
    assert engine.cascade_provider is None


def test_build_engine_cascade_provider_built_when_env_model_set(tmp_path, monkeypatch):
    """RPG_CASCADE_MODEL + a real-looking provider → cascade_provider built with that model."""
    from llm.provider import ZhipuProvider
    monkeypatch.setenv("RPG_CASCADE_MODEL", "glm-4.7")
    real_prov = ZhipuProvider(model="glm-5.1", api_key="x")
    engine = build_engine(tmp_path, provider=real_prov)
    assert engine.cascade_provider is not None
    assert engine.cascade_provider.model == "glm-4.7"


def test_build_engine_cascade_provider_inherits_api_key(tmp_path, monkeypatch):
    """cascade_provider reuses the main provider's api_key and base_url."""
    from llm.provider import ZhipuProvider
    monkeypatch.setenv("RPG_CASCADE_MODEL", "glm-4.7")
    real_prov = ZhipuProvider(model="glm-5.1", api_key="secret-key",
                              base_url="https://custom.example.com/v4")
    engine = build_engine(tmp_path, provider=real_prov)
    assert engine.cascade_provider is not None
    assert engine.cascade_provider.api_key == "secret-key"
    assert "custom.example.com" in engine.cascade_provider.base_url


# ---------------------------------------------------------------------------
# Phase D Task 7: TimeSystem registered in build_engine
# ---------------------------------------------------------------------------

def test_build_engine_registers_time_system(tmp_path):
    from llm.provider import FakeLLMProvider
    from app.engine import build_engine
    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    assert eng.registry.owner_of_event("time_advanced") is not None


# ---------------------------------------------------------------------------
# Phase E Task 2: rewind + last_turn
# ---------------------------------------------------------------------------

def test_last_turn_returns_zero_on_empty_store(tmp_path):
    """last_turn returns 0 when no events are in the store."""
    from app.engine import build_engine, last_turn
    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    assert last_turn(eng) == 0


def test_last_turn_returns_max_turn_after_genesis(tmp_path):
    """last_turn returns the maximum turn number across all events."""
    from app.engine import build_engine, new_game, last_turn
    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    new_game(eng)
    # genesis appends events with turn=0; last_turn must return 0
    assert last_turn(eng) == 0


def test_last_turn_after_explicit_turns(tmp_path):
    """last_turn reflects the highest turn number ever appended."""
    from kernel.events import kernel_event
    from app.engine import build_engine, new_game, last_turn
    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    new_game(eng)
    # Append a fake player-turn event (turn=1) and another (turn=2)
    eng.store.append(kernel_event("time_advanced", day=2, scene="s",
                                   summary="turn1",
                                   deltas={"to_day": 2, "reason": "t"}, turn=1))
    eng.store.append(kernel_event("time_advanced", day=3, scene="s",
                                   summary="turn2",
                                   deltas={"to_day": 3, "reason": "t"}, turn=2))
    assert last_turn(eng) == 2


def test_rewind_retracts_events_at_or_after_turn(tmp_path):
    """rewind(engine, n) retracts all events with turn >= n and re-projects."""
    from kernel.events import kernel_event
    from app.engine import build_engine, new_game, rewind, last_turn
    from kernel.projection import project

    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    # new_game via bootstrap_world: many turn=0 events (campaign_seeded, place_created
    # for regions/town/venues/l2, character_created for protagonist+NPCs, etc.)
    new_game(eng)

    # Append a fake turn-1 event: evolve protagonist
    eng.store.append(kernel_event("character_evolved", day=2, scene="s",
                                   summary="turn1 evolve",
                                   deltas={"id": "protagonist", "predicate": "mood",
                                           "value": "happy", "op": "evolve"},
                                   turn=1))
    # Confirm the world has the evolved predicate
    from kernel.projection import project
    eng.world = project(eng.registry, eng.store.iter_events())
    g = eng.world["systems"]["ontology"]
    assert g.value_at("protagonist", "mood", 2) == "happy"

    # Rewind to turn=1 (retract all turn>=1 events)
    result = rewind(eng, 1)
    assert result["retracted"] >= 1
    assert result["turn"] == 1

    # World re-projected: the evolved fact must be gone
    g2 = eng.world["systems"]["ontology"]
    assert g2.value_at("protagonist", "mood", 2) is None

    # But genesis (turn=0) entities survive
    assert g2.get_entity("protagonist") is not None


def test_rewind_returns_correct_retracted_count(tmp_path):
    """rewind returns {"retracted": N, "turn": T} with N = number of events retracted."""
    from kernel.events import kernel_event
    from app.engine import build_engine, new_game, rewind

    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    new_game(eng)

    # Append 3 events at turn=1
    for i in range(3):
        eng.store.append(kernel_event("time_advanced", day=i + 2, scene="s",
                                       summary=f"t{i}",
                                       deltas={"to_day": i + 2, "reason": "r"}, turn=1))
    result = rewind(eng, 1)
    assert result["retracted"] == 3
    assert result["turn"] == 1


def test_rewind_to_genesis_keeps_turn0_events(tmp_path):
    """rewind(engine, 1) does NOT retract genesis events (turn=0)."""
    from kernel.events import kernel_event
    from app.engine import build_engine, new_game, rewind

    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    new_game(eng)
    genesis_count = sum(1 for _ in eng.store.iter_events())

    eng.store.append(kernel_event("time_advanced", day=2, scene="s",
                                   summary="player1",
                                   deltas={"to_day": 2, "reason": "t"}, turn=1))

    rewind(eng, 1)
    surviving = sum(1 for _ in eng.store.iter_events())
    assert surviving == genesis_count  # turn=0 events intact, turn=1 gone


def test_rewind_rejects_nonpositive_turns_before_touching_genesis(tmp_path):
    """The engine API itself must protect callers beyond the CLI parser."""
    import pytest
    from app.engine import build_engine, new_game, rewind

    eng = build_engine(tmp_path, provider=FakeLLMProvider())
    new_game(eng)
    before = (eng.store.revision, list(eng.store.iter_events(include_retracted=True)))
    try:
        for turn in (0, -1, False):
            with pytest.raises(ValueError, match="positive player turn"):
                rewind(eng, turn)
        assert (eng.store.revision, list(eng.store.iter_events(include_retracted=True))) == before
    finally:
        eng.store.close()


# ---------------------------------------------------------------------------
# Task 9 — new_game(spec=) + resolve_genesis_spec
# ---------------------------------------------------------------------------

def test_new_game_forwards_spec(tmp_path):
    from app.engine import build_engine, new_game
    engine = build_engine(tmp_path / "c", provider=FakeLLMProvider())
    result = new_game(engine, "", spec={"protagonist": {"name": "凛"}})
    assert result["summary"]["protagonist_name"] == "凛"


def test_resolve_genesis_spec_file_then_session(tmp_path):
    import json
    from app.engine import resolve_genesis_spec
    bp = tmp_path / "g.json"
    bp.write_text(json.dumps({"world_premise": {"genre": "日式西幻"}}), encoding="utf-8")
    # session-zero fills the still-missing protagonist name
    spec = resolve_genesis_spec(
        provider=None, blueprint_path=bp,
        inputs=iter(["凛"]), out=lambda *_: None, interactive=True)
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"


def test_resolve_genesis_spec_pitch_seeds_premise(tmp_path):
    # pitch seeds world_premise.genre -> session-zero only asks for protagonist.
    from app.engine import resolve_genesis_spec
    spec = resolve_genesis_spec(
        provider=None, pitch="武侠",
        inputs=iter(["叶凡"]), out=lambda *_: None, interactive=True)
    assert spec["world_premise"]["genre"] == "武侠"
    assert spec["protagonist"]["name"] == "叶凡"


def test_resolve_genesis_spec_bad_import_path_raises(tmp_path):
    # A missing import file raises a clean BlueprintError, not a raw traceback.
    from app.engine import resolve_genesis_spec
    from loop.genesis_blueprint import BlueprintError
    with pytest.raises(BlueprintError):
        resolve_genesis_spec(provider=None, card_path=tmp_path / "nope.json")
