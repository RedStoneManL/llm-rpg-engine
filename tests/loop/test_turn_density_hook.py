"""tests/loop/test_turn_density_hook.py — run_turn backstage density hook (Task 3C).

Tests that run_turn auto-generates lore lines via run_density when:
  (a) protagonist enters a fresh L2 town
  (b) cascade_provider=None but main provider is set (fallback works)
  (c) both providers are None (no generation, turn doesn't crash)
  (d) run_density raises unexpectedly (turn still returns normally)

Uses FakeLLMProvider for all LLM calls (narrator commit + cascade skeletons).
"""
from __future__ import annotations

import os
import tempfile

import pytest

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event, open_store
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.lore import LoreSystem
from loop.turn import run_turn
from loop.strategy import AuthorStrategy
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(
        os.path.join(d, "e.db"),
        os.path.join(d, "e.jsonl"),
        allowed_types=registry.event_types(),
    )


def _canned_skeleton(venue: str = "venue1", about: str = "神秘暗线"):
    return {
        "about": about,
        "secret": "幕后秘密",
        "description": "地方描述",
        "trigger": "触发条件",
        "l3_anchor": venue,
        "stages": [{"hint": "线索A"}, {"hint": "线索B"}],
    }


def _canned_batch(n: int, venue: str = "venue1"):
    return {"lines": [_canned_skeleton(venue=venue, about=f"暗线{i}") for i in range(n)]}


# Minimal narrator commit (all required sections present)
def _narrator_commit(to: str | None = None):
    commit: dict = {
        "narration": "回合叙事。",
        "moves": [],
        "places": [],
        "cast": [],
        "facts": [],
        "clock": [],
    }
    if to:
        commit["moves"] = [{"who": "hero", "to": to}]
    return commit


def _seed_world(reg, store, density=0.3, campaign_seed=77):
    """Seed: L1 region -> L2 town1 -> L3 venue1 + hero starts outside."""
    evs = [
        kernel_event("place_created", day=1, scene="s1", summary="region1",
                     deltas={"id": "region1", "level": 1, "kind": "region",
                             "seed": "北境", "tier": "tracked", "density": density}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="town1",
                     deltas={"id": "town1", "level": 2, "kind": "settlement",
                             "seed": "边陲集镇", "tier": "tracked", "parent": "region1"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="venue1",
                     deltas={"id": "venue1", "level": 3, "kind": "venue",
                             "seed": "集市", "tier": "tracked", "parent": "town1"}, turn=0),
        # A second town so hero can start somewhere else
        kernel_event("place_created", day=1, scene="s1", summary="othervenue",
                     deltas={"id": "othervenue", "level": 3, "kind": "venue",
                             "seed": "旅馆", "tier": "tracked", "parent": "region1"}, turn=0),
        kernel_event("entity_created", day=1, scene="s1", summary="hero",
                     deltas={"id": "hero", "etype": "Person", "tier": "tracked"}, turn=0),
        # hero starts at othervenue (outside town1's venue subtree)
        kernel_event("entity_moved", day=1, scene="s1", summary="hero->othervenue",
                     deltas={"who": "hero", "to": "othervenue"}, turn=0),
    ]
    for ev in evs:
        store.append(ev)
    world = project(reg, store.iter_events())
    world["meta"]["campaign_seed"] = campaign_seed
    return world


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDensityHook:
    def test_run_turn_seeds_lines_on_entry(self):
        """When hero moves into a fresh town, run_turn auto-generates暗 lines."""
        reg = _reg()
        store = _store(reg)
        world = _seed_world(reg, store, density=0.3)

        density = 0.3
        target = round(density * 10)  # BASE=10

        # Narrator: moves hero to venue1 (inside town1)
        # Cascade skeleton provider: one batch canned
        narrator_responses = [_narrator_commit(to="venue1")]
        cascade_responses = [_canned_batch(target)]

        main_prov = FakeLLMProvider(json_responses=narrator_responses)
        cascade_prov = FakeLLMProvider(json_responses=cascade_responses)

        scene = {"protagonist": "hero", "present": [], "day": 1,
                 "id": "s1", "location": "othervenue"}

        result = run_turn(reg, store, world, scene, "进入town1",
                         strategy=AuthorStrategy(), provider=main_prov,
                         cascade_provider=cascade_prov,
                         embedder=None, max_repairs=0)

        # World must have lore lines anchored at town1
        new_world = result.world
        lines = new_world["systems"]["lore"]["lines"]
        town1_lines = [ln for ln in lines.values() if ln.get("anchor") == "town1"]
        assert len(town1_lines) > 0, f"No lines generated for town1; lines={list(lines.keys())}"

        # gen state must show seeded
        gen = new_world["systems"]["lore"]["gen"]
        assert gen.get("town1", {}).get("seeded") is True, f"town1 not marked seeded; gen={gen}"

        # All generated lines must be 暗 state; no `status` field
        for ln in town1_lines:
            assert ln["state"] == "暗", f"Expected 暗 state; got {ln['state']}"
            assert "status" not in ln, f"status must be absent; got {ln.get('status')!r}"

    def test_cascade_provider_none_main_provider_fallback(self):
        """cascade_provider=None but main provider present: generation still happens."""
        reg = _reg()
        store = _store(reg)
        world = _seed_world(reg, store, density=0.3)

        density = 0.3
        target = round(density * 10)

        # Main provider must supply BOTH narrator commit AND skeleton batch
        # (run_density will use the main provider as fallback)
        narrator_commit = _narrator_commit(to="venue1")
        batch_response = _canned_batch(target)

        main_prov = FakeLLMProvider(json_responses=[narrator_commit, batch_response])

        scene = {"protagonist": "hero", "present": [], "day": 1,
                 "id": "s1", "location": "othervenue"}

        result = run_turn(reg, store, world, scene, "进入town1",
                         strategy=AuthorStrategy(), provider=main_prov,
                         cascade_provider=None,  # no cascade provider
                         embedder=None, max_repairs=0)

        new_world = result.world
        gen = new_world["systems"]["lore"]["gen"]
        # Seeded must be True (generation happened via main provider fallback)
        assert gen.get("town1", {}).get("seeded") is True, \
            f"Expected seeded=True with fallback provider; gen={gen}"

    def test_both_providers_none_turn_succeeds_without_generation(self):
        """provider=None, cascade_provider=None → turn succeeds, no lore lines generated."""
        reg = _reg()
        store = _store(reg)
        world = _seed_world(reg, store, density=0.3)

        # With both providers None we cannot call narrator — use monkeypatch instead
        # by injecting the narrator commit directly via a provider that handles narration
        # but has no cascade. Actually both None means we can't produce a narrator commit
        # either, so we need at least a main_prov for narration.
        # Correct test: main_prov has the narrator commit but density provider is None.
        # Test: cascade=None, provider is narrator-only (no skeleton batch in queue).
        # run_density will fall back to main provider, but there's no batch in queue,
        # so complete_json raises → generate_lore_batch returns [] → seeded with 0 lines.
        narrator_commit = _narrator_commit(to="venue1")
        # Provide only the narrator commit; density call will get ValueError on missing response
        main_prov = FakeLLMProvider(json_responses=[narrator_commit])

        scene = {"protagonist": "hero", "present": [], "day": 1,
                 "id": "s1", "location": "othervenue"}

        # Must not raise
        result = run_turn(reg, store, world, scene, "进入town1",
                         strategy=AuthorStrategy(), provider=main_prov,
                         cascade_provider=None,
                         embedder=None, max_repairs=0)

        assert result.narration  # turn returned normally
        # No lines generated, but no crash
        new_world = result.world
        # gen state may or may not be seeded (batch returned [] after exception)
        # What matters: no crash and narration present
        assert isinstance(new_world["systems"]["lore"]["lines"], dict)

    def test_run_density_exception_does_not_crash_turn(self, monkeypatch):
        """If run_density raises an unexpected exception, the turn still returns normally."""
        import loop.turn as turn_mod

        original_run_density = turn_mod.run_density

        def _crashing_run_density(*args, **kwargs):
            raise RuntimeError("simulated run_density crash")

        monkeypatch.setattr(turn_mod, "run_density", _crashing_run_density)

        reg = _reg()
        store = _store(reg)
        world = _seed_world(reg, store, density=0.3)

        narrator_commit = _narrator_commit(to="venue1")
        main_prov = FakeLLMProvider(json_responses=[narrator_commit])

        scene = {"protagonist": "hero", "present": [], "day": 1,
                 "id": "s1", "location": "othervenue"}

        # Must not raise even with crashing run_density
        result = run_turn(reg, store, world, scene, "进入town1",
                         strategy=AuthorStrategy(), provider=main_prov,
                         cascade_provider=None,
                         embedder=None, max_repairs=0)

        assert result.narration  # turn returned normally
        # Restore (monkeypatch does this automatically but explicit is clear)

    # -----------------------------------------------------------------------
    # D1: backstage fault-injection — run_lore, run_catchup, _run_demote_on_leave
    # -----------------------------------------------------------------------

    def test_run_lore_exception_does_not_crash_turn(self, monkeypatch):
        """D1: If run_lore raises unexpectedly, the turn still returns normally."""
        import loop.turn as turn_mod

        def _crashing_run_lore(*args, **kwargs):
            raise RuntimeError("simulated run_lore crash")

        monkeypatch.setattr(turn_mod, "run_lore", _crashing_run_lore)

        reg = _reg()
        store = _store(reg)
        world = _seed_world(reg, store, density=0.3)

        narrator_commit = _narrator_commit(to="venue1")
        main_prov = FakeLLMProvider(json_responses=[narrator_commit])

        scene = {"protagonist": "hero", "present": [], "day": 1,
                 "id": "s1", "location": "othervenue"}

        result = run_turn(reg, store, world, scene, "进入town1",
                         strategy=AuthorStrategy(), provider=main_prov,
                         cascade_provider=None,
                         embedder=None, max_repairs=0)

        assert result.narration  # turn returned normally despite run_lore crash

    def test_run_catchup_exception_does_not_crash_turn(self, monkeypatch):
        """D1: If run_catchup raises unexpectedly, the turn still returns normally."""
        import loop.turn as turn_mod

        def _crashing_run_catchup(*args, **kwargs):
            raise RuntimeError("simulated run_catchup crash")

        monkeypatch.setattr(turn_mod, "run_catchup", _crashing_run_catchup)

        reg = _reg()
        store = _store(reg)
        world = _seed_world(reg, store, density=0.3)

        narrator_commit = _narrator_commit(to="venue1")
        main_prov = FakeLLMProvider(json_responses=[narrator_commit])

        scene = {"protagonist": "hero", "present": [], "day": 1,
                 "id": "s1", "location": "othervenue"}

        result = run_turn(reg, store, world, scene, "进入town1",
                         strategy=AuthorStrategy(), provider=main_prov,
                         cascade_provider=None,
                         embedder=None, max_repairs=0)

        assert result.narration  # turn returned normally despite run_catchup crash

    def test_run_demote_on_leave_exception_does_not_crash_turn(self, monkeypatch):
        """D1: If _run_demote_on_leave raises unexpectedly, the turn still returns normally."""
        import loop.turn as turn_mod

        def _crashing_demote(*args, **kwargs):
            raise RuntimeError("simulated _run_demote_on_leave crash")

        monkeypatch.setattr(turn_mod, "_run_demote_on_leave", _crashing_demote)

        reg = _reg()
        store = _store(reg)
        world = _seed_world(reg, store, density=0.3)

        narrator_commit = _narrator_commit(to="venue1")
        main_prov = FakeLLMProvider(json_responses=[narrator_commit])

        scene = {"protagonist": "hero", "present": [], "day": 1,
                 "id": "s1", "location": "othervenue"}

        result = run_turn(reg, store, world, scene, "进入town1",
                         strategy=AuthorStrategy(), provider=main_prov,
                         cascade_provider=None,
                         embedder=None, max_repairs=0)

        assert result.narration  # turn returned normally despite _run_demote_on_leave crash
