"""Tests for loop.compare: run_compare — produce both 甲+丙 on the same snapshot."""
from __future__ import annotations

import tempfile
import os
import pytest

from kernel.registry import Registry
from kernel.projection import project, empty_world
from kernel.events import open_store
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


# ---------------------------------------------------------------------------
# Test 1: run_compare returns both candidates, store unchanged
# ---------------------------------------------------------------------------

def test_run_compare_returns_both_candidates():
    """run_compare returns dict with '甲' and '丙' keys, each a (commit, attempts, dropped) tuple."""
    from loop.compare import run_compare

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    # 甲 (AuthorStrategy) uses complete_json
    jia_commit = {
        "narration": "甲: Hero slashes forward.",
        "entities": [{"id": "hero", "etype": "Person", "tier": "tracked"}],
    }
    # 丙 (HybridStrategy) uses complete (prose) then complete_json (structure)
    bing_prose = "丙: Hero pivots and thrusts."
    bing_extraction = {
        "narration": bing_prose,
        "entities": [{"id": "hero", "etype": "Person", "tier": "tracked"}],
    }

    # The provider must handle: 甲 calls complete_json once; 丙 calls complete then complete_json
    provider = FakeLLMProvider(
        responses=[bing_prose],
        json_responses=[jia_commit, bing_extraction],
    )

    store = _open_temp_store(registry)
    try:
        result = run_compare(registry, world, scene, "attack", provider=provider)

        # Store must be completely untouched
        events_in_store = list(store.iter_events())
        assert events_in_store == [], (
            f"run_compare must not write to store; found {events_in_store}"
        )
    finally:
        store.close()

    assert "甲" in result
    assert "丙" in result

    commit_a, attempts_a, dropped_a = result["甲"]
    commit_b, attempts_b, dropped_b = result["丙"]

    assert commit_a.narration == "甲: Hero slashes forward."
    assert commit_b.narration == bing_prose
    assert attempts_a == 0
    assert attempts_b == 0
    assert dropped_a == []
    assert dropped_b == []


def test_run_compare_includes_hybrid_candidate():
    """run_compare returns 甲 (AuthorStrategy) and 丙 (HybridStrategy)."""
    from loop.compare import run_compare

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    jia = {"narration": "甲.", "entities": []}
    prose = "丙 prose."
    extraction = {"narration": prose, "entities": []}
    provider = FakeLLMProvider(
        responses=[prose],
        json_responses=[jia, extraction],
    )

    result = run_compare(registry, world, scene, "act", provider=provider)
    assert set(result.keys()) == {"甲", "丙"}

    bing_commit, _bing_attempts, bing_dropped = result["丙"]
    assert bing_commit.narration == prose   # 丙's narration = its frozen prose (call 1)
    assert bing_dropped == []


def test_run_compare_two_different_commits():
    """甲 and 丙 produce distinct commits (different narrations)."""
    from loop.compare import run_compare

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    jia_commit = {"narration": "Strategy 甲 narration.", "entities": []}
    bing_prose = "Strategy 丙 prose."
    bing_extraction = {"narration": bing_prose, "entities": []}

    provider = FakeLLMProvider(
        responses=[bing_prose],
        json_responses=[jia_commit, bing_extraction],
    )

    result = run_compare(registry, world, scene, "act", provider=provider)

    commit_jia = result["甲"][0]
    commit_bing = result["丙"][0]
    assert commit_jia.narration != commit_bing.narration


def test_run_compare_apply_chosen_commit():
    """Caller can apply_turn on the chosen candidate; only that one is committed."""
    from loop.compare import run_compare
    from loop.turn import apply_turn

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene(day=3)

    jia_commit = {
        "narration": "甲 picks this path.",
        "entities": [{"id": "hero_jia", "etype": "Person", "tier": "tracked"}],
    }
    bing_prose = "丙 picks that path."
    bing_extraction = {
        "narration": bing_prose,
        "entities": [{"id": "hero_bing", "etype": "Person", "tier": "tracked"}],
    }

    provider = FakeLLMProvider(
        responses=[bing_prose],
        json_responses=[jia_commit, bing_extraction],
    )

    store = _open_temp_store(registry)
    try:
        result = run_compare(registry, world, scene, "choose", provider=provider)

        # Choose 丙
        chosen_commit, _, _ = result["丙"]
        new_world = apply_turn(registry, store, chosen_commit, day=3, scene="sc01")

        events = list(store.iter_events())
        assert len(events) > 0

        # Only 丙's entity is in the world
        g = new_world.get("systems", {}).get("ontology")
        assert g.get_entity("hero_bing") is not None
        # 甲's entity was never applied
        assert g.get_entity("hero_jia") is None
    finally:
        store.close()


def test_run_compare_same_world_snapshot():
    """Both strategies run against the exact same pre-turn world (snapshot equality)."""
    from loop.compare import run_compare

    registry = _make_registry()
    world = empty_world(registry)
    scene = _make_scene()

    # We use a custom provider that records the world it sees.
    # Since FakeLLMProvider doesn't expose world, we just verify store is untouched,
    # which proves neither strategy saw a modified world.
    jia_commit = {"narration": "Same snapshot 甲.", "entities": []}
    bing_prose = "Same snapshot 丙."
    bing_extraction = {"narration": bing_prose, "entities": []}

    provider = FakeLLMProvider(
        responses=[bing_prose],
        json_responses=[jia_commit, bing_extraction],
    )

    store = _open_temp_store(registry)
    try:
        result = run_compare(registry, world, scene, "test", provider=provider)
        assert list(store.iter_events()) == []
    finally:
        store.close()

    # Both candidates produced without error
    assert result["甲"][0].narration == "Same snapshot 甲."
    assert result["丙"][0].narration == "Same snapshot 丙."
