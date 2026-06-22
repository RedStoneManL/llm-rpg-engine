"""Tests for loop.fleet: backstage digest_fleet — importance scoring + reflection write-back."""
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
    tmp_dir = tempfile.mkdtemp()
    db = os.path.join(tmp_dir, "events.db")
    jsonl = os.path.join(tmp_dir, "events.jsonl")
    return open_store(db, jsonl, allowed_types=registry.event_types())


def _make_character_created_event(pid, day=1):
    return kernel_event(
        "character_created", day=day, scene="sc01",
        summary=f"{pid} 登场",
        deltas={"id": pid, "sketch": "A brave soul", "goal": "Survive", "tier": "tracked"},
    )


# ---------------------------------------------------------------------------
# Test 1: low importance events — no reflection triggered
# ---------------------------------------------------------------------------

def test_digest_fleet_low_importance_no_reflection():
    """When accumulated importance is below threshold, no arc events are appended."""
    from loop.fleet import digest_fleet

    registry = _make_registry()

    # Create a world with an entity
    store = _open_temp_store(registry)
    try:
        create_ev = _make_character_created_event("aria")
        store.append(create_ev)
        world = project(registry, store.iter_events())

        # New events with low importance score (provider returns "2")
        new_events = [
            kernel_event("action", day=1, scene="sc01",
                         summary="aria walks around",
                         actors=["aria"]),
        ]

        # Provider returns low importance score
        provider = FakeLLMProvider(responses=["2"])

        arc_events = digest_fleet(
            registry, store, new_events, world,
            provider=provider, importance_provider=provider, threshold=30,
        )

        assert arc_events == [], f"Expected no arc events, got {arc_events}"
        # Store should not have gained any character_evolved events
        all_events = list(store.iter_events())
        evolved = [e for e in all_events if e["type"] == "character_evolved"]
        assert evolved == []
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Test 2: high importance events — reflection triggers and arc event appended
# ---------------------------------------------------------------------------

def test_digest_fleet_high_importance_triggers_reflection():
    """When accumulated importance >= threshold, reflect() is called and arc event appended."""
    from loop.fleet import digest_fleet

    registry = _make_registry()
    store = _open_temp_store(registry)
    try:
        # Seed world with a character
        create_ev = _make_character_created_event("ryn", day=1)
        store.append(create_ev)
        world = project(registry, store.iter_events())

        # New events: multiple high-importance events for "ryn"
        # Use relationship_change type which has heuristic score 7, so with LLM=10
        # and just one event, total = 10 >= threshold=8 → should trigger
        new_events = [
            kernel_event("relationship_change", day=2, scene="sc01",
                         summary="ryn betrays the guild",
                         actors=["ryn"],
                         deltas={"subject": "ryn", "predicate": "loyalty", "value": "broken"}),
        ]

        # importance.score calls provider.complete → LLM returns "10"
        # reflection.reflect calls provider.complete → returns JSON arc
        arc_json = '{"predicate": "arc", "value": "ryn经历了信仰的崩塌，从忠诚者蜕变为叛徒"}'

        provider = FakeLLMProvider(responses=["10", arc_json])

        arc_events = digest_fleet(
            registry, store, new_events, world,
            provider=provider, importance_provider=provider, threshold=8,
        )

        assert len(arc_events) >= 1, f"Expected at least one arc event, got {arc_events}"

        # The arc event must be a character_evolved event
        arc_ev = arc_events[0]
        assert arc_ev["type"] == "character_evolved"
        assert arc_ev["deltas"]["predicate"] == "arc"
        assert "ryn" in arc_ev["deltas"]["value"] or "叛徒" in arc_ev["deltas"]["value"] or arc_ev["deltas"]["value"]

        # The arc event should be appended to the store
        all_events = list(store.iter_events())
        evolved = [e for e in all_events if e["type"] == "character_evolved"]
        assert len(evolved) >= 1
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Test 3: arc event projected into world (value_at returns the arc)
# ---------------------------------------------------------------------------

def test_digest_fleet_arc_visible_in_reprojected_world():
    """After digest_fleet, re-projecting the store shows the arc fact on the subject."""
    from loop.fleet import digest_fleet

    registry = _make_registry()
    store = _open_temp_store(registry)
    try:
        create_ev = _make_character_created_event("kira", day=1)
        store.append(create_ev)
        world = project(registry, store.iter_events())

        new_events = [
            kernel_event("relationship_change", day=3, scene="sc01",
                         summary="kira sacrifices herself",
                         actors=["kira"],
                         deltas={"subject": "kira", "predicate": "fate", "value": "martyrdom"}),
        ]

        arc_json = '{"predicate": "arc", "value": "kira完成了自我牺牲的英雄弧"}'
        provider = FakeLLMProvider(responses=["9", arc_json])

        arc_events = digest_fleet(
            registry, store, new_events, world,
            provider=provider, importance_provider=provider, threshold=5,
        )

        assert len(arc_events) >= 1

        # Re-project from store to see the arc
        new_world = project(registry, store.iter_events())
        g = new_world["systems"]["ontology"]
        arc_value = g.value_at("kira", "arc", day=3)
        assert arc_value is not None, "arc fact should be in world after digest_fleet"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Test 4: accumulation per subject (two subjects, only one crosses threshold)
# ---------------------------------------------------------------------------

def test_digest_fleet_accumulates_per_subject():
    """Importance accumulates per primary subject; only subjects crossing threshold reflect."""
    from loop.fleet import digest_fleet

    registry = _make_registry()
    store = _open_temp_store(registry)
    try:
        # Create two characters
        store.append(_make_character_created_event("alpha", day=1))
        store.append(_make_character_created_event("beta", day=1))
        world = project(registry, store.iter_events())

        # alpha gets a high-importance event, beta gets a low one
        new_events = [
            kernel_event("relationship_change", day=2, scene="sc01",
                         summary="alpha's arc event",
                         actors=["alpha"],
                         deltas={"subject": "alpha"}),
            kernel_event("action", day=2, scene="sc01",
                         summary="beta walks",
                         actors=["beta"]),
        ]

        # importance responses: first for alpha (high=10), then for beta (low=2)
        # reflection response for alpha's arc
        arc_json = '{"predicate": "arc", "value": "alpha崛起"}'
        provider = FakeLLMProvider(responses=["10", "2", arc_json])

        arc_events = digest_fleet(
            registry, store, new_events, world,
            provider=provider, importance_provider=provider, threshold=8,
        )

        # Only alpha should have triggered reflection
        arc_subjects = [e["deltas"]["id"] for e in arc_events]
        assert "alpha" in arc_subjects
        assert "beta" not in arc_subjects
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Test 5: empty events list returns empty arc list
# ---------------------------------------------------------------------------

def test_digest_fleet_empty_events():
    """digest_fleet with no new events returns an empty list."""
    from loop.fleet import digest_fleet

    registry = _make_registry()
    store = _open_temp_store(registry)
    try:
        world = project(registry, store.iter_events())
        provider = FakeLLMProvider(responses=[])
        arc_events = digest_fleet(
            registry, store, [], world,
            provider=provider, importance_provider=provider, threshold=30,
        )
        assert arc_events == []
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Task 8: P2 — narration recording, scene summarization, backstop
# ---------------------------------------------------------------------------

import systems.narrative as nmod
from systems.lore import LoreSystem
from systems.narrative import NarrativeSystem


def _reg_full():
    """Registry with ontology+place+character+lore+narrative for fleet P2 tests."""
    from systems.ontology import OntologySystem
    return (Registry()
            .register(OntologySystem())
            .register(PlaceSystem())
            .register(CharacterSystem())
            .register(LoreSystem())
            .register(NarrativeSystem()))


def _store(reg):
    """Open a temp store for reg (alias for _open_temp_store)."""
    return _open_temp_store(reg)


def test_digest_records_narration():
    from loop.fleet import digest_fleet
    reg = _reg_full()
    store = _store(reg)
    try:
        # Use character_evolved (registered by CharacterSystem) as the turn event
        evs = [kernel_event("character_evolved", day=1, scene="s1", summary="walk",
                            actors=["hero"],
                            deltas={"id": "hero", "predicate": "state",
                                    "value": "alert", "op": "evolve"}, turn=1)]
        for e in evs:
            store.append(e)
        digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                     provider=FakeLLMProvider(), narration_text="你走进村庄。", scene="s1")
        w = project(reg, store.iter_events())
        raw = w["systems"]["narrative"]["scenes"][-1]["raw"]
        assert "你走进村庄。" in raw
    finally:
        store.close()


def test_digest_summarizes_only_when_scene_ages_out():
    from loop.fleet import digest_fleet
    reg = _reg_full()
    store = _store(reg)
    try:
        # pre-seed 2 scenes of narration (within window → no summary yet)
        for i, sc in enumerate(["s1", "s2"], start=1):
            store.append(kernel_event("narration_recorded", day=1, scene=sc,
                         summary="n", deltas={"scene": sc, "text": f"原文{sc}"}, turn=i))
        world = project(reg, store.iter_events())
        cheap = FakeLLMProvider(json_responses=[{"summary": "s1 的摘要"}])
        # this turn's narration starts s3 → s1 ages out → ONE summarize call
        evs = [kernel_event("character_evolved", day=1, scene="s3", summary="x",
                            actors=["hero"],
                            deltas={"id": "hero", "predicate": "state",
                                    "value": "tired", "op": "evolve"}, turn=3)]
        for e in evs:
            store.append(e)
        digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                     provider=FakeLLMProvider(), narration_text="原文s3", scene="s3",
                     recap_provider=cheap)
        w = project(reg, store.iter_events())
        s1b = next(b for b in w["systems"]["narrative"]["scenes"] if b["scene"] == "s1")
        assert s1b["summary"] == "s1 的摘要"
        assert len(cheap.calls) == 1                    # gated: exactly one cheap call
    finally:
        store.close()


def test_digest_no_summarize_within_window():
    from loop.fleet import digest_fleet
    reg = _reg_full()
    store = _store(reg)
    try:
        cheap = FakeLLMProvider(json_responses=[{"summary": "X"}])
        evs = [kernel_event("character_evolved", day=1, scene="s1", summary="x",
                            actors=["hero"],
                            deltas={"id": "hero", "predicate": "state",
                                    "value": "calm", "op": "evolve"}, turn=1)]
        for e in evs:
            store.append(e)
        digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                     provider=FakeLLMProvider(), narration_text="原文", scene="s1",
                     recap_provider=cheap)
        assert len(cheap.calls) == 0                    # nothing aged out → no LLM cost
    finally:
        store.close()


def test_backstop_flags_dormant_when_no_active_thread():
    from loop.fleet import digest_fleet
    reg = _reg_full()
    store = _store(reg)
    try:
        # substantive player event (character_evolved heuristic_floor >=2 with deltas),
        # empty lore lines → backstop flags one 暗 line via quest_created
        evs = [kernel_event("character_evolved", day=1, scene="s1", summary="断桥崩塌",
                            actors=["hero"],
                            deltas={"id": "hero", "predicate": "state", "value": "shaken",
                                    "op": "evolve"}, turn=1)]
        for e in evs:
            store.append(e)
        digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                     provider=FakeLLMProvider(), narration_text="桥塌了。", scene="s1")
        lines = project(reg, store.iter_events())["systems"]["lore"]["lines"]
        assert any(ln.get("state") == "暗" for ln in lines.values())
    finally:
        store.close()


def test_backstop_silent_when_active_thread_exists():
    from loop.fleet import digest_fleet
    reg = _reg_full()
    store = _store(reg)
    try:
        # Seed a 明 line via quest_opened → backstop should stay silent
        store.append(kernel_event("quest_opened", day=1, scene="s1", summary="o",
                                  deltas={"id": "th_x", "summary": "现有活跃线",
                                          "state": "明"}, turn=1))
        evs = [kernel_event("character_evolved", day=1, scene="s1", summary="大事",
                            actors=["hero"],
                            deltas={"id": "hero", "predicate": "state", "value": "bold",
                                    "op": "evolve"}, turn=2)]
        for e in evs:
            store.append(e)
        digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                     provider=FakeLLMProvider(), narration_text="x", scene="s1")
        # no new quest_created events (backstop stayed silent because 明 line exists)
        qc = [e for e in store.iter_events() if e["type"] == "quest_created"]
        assert len(qc) == 0                              # backstop did not fire
    finally:
        store.close()


def test_summarize_scene_repair_loop_uses_repaired_result():
    """First malformed response + conforming second response → summarize_scene
    uses the repaired summary, named field appears in repair message."""
    from loop.fleet import summarize_scene
    from llm.provider import FakeLLMProvider

    # First response missing "summary"; second conforming
    bad_resp  = {"note": "oops"}          # missing "summary"
    good_resp = {"summary": "这是摘要"}
    fake = FakeLLMProvider(json_responses=[bad_resp, good_resp])
    result = summarize_scene(fake, "sc_repair", ["原文"])
    assert result is not None
    assert result["deltas"]["summary"] == "这是摘要"
    # Two calls: initial + 1 repair
    assert len(fake.calls) == 2
    # Repair message named the missing field
    repair_msg = fake.calls[1][1]  # 2nd call's user turn
    assert '"summary"' in repair_msg


def test_recompress_repair_loop_uses_repaired_result():
    """Full digest_fleet recompress path: bad first response + good second → repaired super_summary used."""
    from loop.fleet import digest_fleet
    from llm.provider import FakeLLMProvider
    from kernel.registry import Registry
    from kernel.events import open_store, kernel_event
    from kernel.projection import project
    from systems.ontology import OntologySystem
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    from systems.lore import LoreSystem
    from systems.narrative import NarrativeSystem
    import tempfile, os

    reg = (Registry()
           .register(OntologySystem())
           .register(PlaceSystem())
           .register(CharacterSystem())
           .register(LoreSystem())
           .register(NarrativeSystem()))

    tmp_dir = tempfile.mkdtemp()
    store = open_store(
        os.path.join(tmp_dir, "e.db"), os.path.join(tmp_dir, "e.jsonl"),
        allowed_types=reg.event_types(),
    )
    try:
        # Pre-seed enough scenes to force a recompress.
        import systems.narrative as nmod
        FANOUT = nmod.RECAP_SUMMARY_FANOUT
        # Seed FANOUT scenes already narrated+summarized (all in the summarized bucket pool).
        for i in range(FANOUT):
            sc = f"sc{i}"
            store.append(kernel_event("narration_recorded", day=1, scene=sc, summary="n",
                                      deltas={"scene": sc, "text": f"原文{i}"}, turn=i * 3 + 1))
            store.append(kernel_event("scene_summarized", day=1, scene=sc, summary="s",
                                      deltas={"scene": sc, "summary": f"摘要{i}"}, turn=i * 3 + 2))

        # Add two extra narration-only scenes after the summarized block.
        # When digest_fleet later records narration for new_sc, the scene window grows to
        # FANOUT+3, cutoff = FANOUT+1, so sc{FANOUT} (idx=FANOUT) ages out and gets
        # summarized → total summarized = FANOUT+1 > FANOUT → recompress triggers.
        sc_extra1 = f"sc{FANOUT}"
        sc_extra2 = f"sc{FANOUT + 1}"
        store.append(kernel_event("narration_recorded", day=1, scene=sc_extra1, summary="n",
                                  deltas={"scene": sc_extra1, "text": "原文extra1"},
                                  turn=FANOUT * 3 + 1))
        store.append(kernel_event("narration_recorded", day=1, scene=sc_extra2, summary="n",
                                  deltas={"scene": sc_extra2, "text": "原文extra2"},
                                  turn=FANOUT * 3 + 2))

        # The turn event (in a brand-new scene)
        new_sc = f"sc{FANOUT + 2}"
        evs = [kernel_event("character_evolved", day=1, scene=new_sc, summary="x",
                            actors=["hero"],
                            deltas={"id": "hero", "predicate": "state",
                                    "value": "tired", "op": "evolve"},
                            turn=FANOUT * 3 + 3)]
        for e in evs:
            store.append(e)

        # recap_provider response queue:
        #   call 1 = summarize the aged scene (conforming)
        #   call 2 = recompress initial attempt (malformed)
        #   call 3 = recompress repair (conforming)
        bad_rc  = {"note": "wrong"}
        good_rc = {"summary": "总概要修复后"}
        summarize_resp = {"summary": "场景摘要"}
        recap = FakeLLMProvider(json_responses=[summarize_resp, bad_rc, good_rc])

        digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                     provider=FakeLLMProvider(), narration_text="原文new", scene=new_sc,
                     recap_provider=recap)

        all_ev = list(store.iter_events())
        rc_ev = next((e for e in all_ev if e["type"] == "recap_recompressed"), None)
        assert rc_ev is not None, "recap_recompressed event should be appended"
        assert rc_ev["deltas"]["super_summary"] == "总概要修复后"
        # 3 calls: summarize (1) + recompress initial (1) + recompress repair (1)
        assert len(recap.calls) == 3
    finally:
        store.close()
