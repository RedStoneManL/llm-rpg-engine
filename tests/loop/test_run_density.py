"""tests/loop/test_run_density.py — run_density orchestrator (Task 3B).

All tests are offline (FakeLLMProvider). The world is hand-built using the
real projection pipeline so FactGraph, lore state, etc. are accurate.
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
from loop.density import run_density, BASE, REFRESH_INTERVAL_DAYS
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


# Canned skeleton for FakeLLMProvider — always passes validation
def _canned_batch_response(n: int, town_id: str = "town1", venue: str = "venue1"):
    """Return a canned {'lines': [...]} dict with n distinct skeletons."""
    return {
        "lines": [
            {
                "about": f"神秘事件{i}发生在{town_id}",
                "secret": f"背后秘密{i}",
                "description": f"描述{i}",
                "trigger": f"触发条件{i}",
                "l3_anchor": venue,
                "stages": [{"hint": f"线索{i}a"}, {"hint": f"线索{i}b"}],
            }
            for i in range(n)
        ]
    }


def _seed_world(density=0.3, campaign_seed=42):
    """Build: L1 region (density) -> L2 town1 -> L3 venue1, with hero at venue1."""
    r = _reg()
    store = _store(r)
    evs = [
        kernel_event("place_created", day=1, scene="s1", summary="r1",
                     deltas={"id": "region1", "level": 1, "kind": "region",
                             "seed": "北境", "tier": "tracked", "density": density}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="town1",
                     deltas={"id": "town1", "level": 2, "kind": "settlement",
                             "seed": "边陲集镇", "tier": "tracked", "parent": "region1"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="venue1",
                     deltas={"id": "venue1", "level": 3, "kind": "venue",
                             "seed": "集市", "tier": "tracked", "parent": "town1"}, turn=0),
        kernel_event("entity_created", day=1, scene="s1", summary="hero",
                     deltas={"id": "hero", "etype": "Person", "tier": "tracked"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="hero->venue1",
                     deltas={"who": "hero", "to": "venue1"}, turn=0),
    ]
    for ev in evs:
        store.append(ev)
    world = project(r, store.iter_events())
    world["meta"]["campaign_seed"] = campaign_seed
    return r, store, world


# ---------------------------------------------------------------------------
# 1. Seeding: first call generates lines + lore_seeded, marks seeded
# ---------------------------------------------------------------------------

class TestSeeding:
    def test_first_call_generates_lines_and_marks_seeded(self):
        """Unseeded town: generates ~round(density*BASE) lines + lore_seeded event."""
        density = 0.3
        target = round(density * BASE)  # 3
        r, store, world = _seed_world(density=density)

        # Provide enough canned responses for generate_lore_batch (it calls once)
        provider = FakeLLMProvider(json_responses=[_canned_batch_response(target)])

        events = run_density(r, store, world, "hero",
                             provider=provider, day=1, scene="s1", turn=1)

        types = [e["type"] for e in events]
        lore_created = [e for e in events if e["type"] == "lore_created"]
        assert "lore_seeded" in types, f"lore_seeded missing; got {types}"
        assert len(lore_created) > 0, "Expected at least 1 lore_created event"

        # World must reflect seeded state
        new_world = project(r, store.iter_events())
        gen = new_world["systems"]["lore"]["gen"]
        assert gen.get("town1", {}).get("seeded") is True

    def test_second_call_on_seeded_town_returns_empty(self):
        """After seeding, a second call (no interval passed) returns []."""
        density = 0.3
        target = round(density * BASE)
        r, store, world = _seed_world(density=density)

        provider = FakeLLMProvider(json_responses=[
            _canned_batch_response(target),
            _canned_batch_response(target),  # extra just in case
        ])

        # First call: seed
        run_density(r, store, world, "hero",
                    provider=provider, day=1, scene="s1", turn=1)

        # Re-project to pick up seeded state
        world2 = project(r, store.iter_events())
        world2["meta"]["campaign_seed"] = 42

        # Second call: same day → no refresh interval passed
        events2 = run_density(r, store, world2, "hero",
                              provider=provider, day=1, scene="s1", turn=2)
        assert events2 == [], f"Expected [] on second call; got {events2}"

    def test_provider_none_unseeded_returns_empty_and_not_marked(self):
        """provider=None on unseeded town → [] and town NOT marked seeded."""
        r, store, world = _seed_world()

        events = run_density(r, store, world, "hero",
                             provider=None, day=1, scene="s1", turn=1)
        assert events == []

        # World must NOT have the seeded marker (so it tries again when provider exists)
        new_world = project(r, store.iter_events())
        gen = new_world["systems"]["lore"]["gen"]
        assert not gen.get("town1", {}).get("seeded")

    def test_protagonist_none_returns_empty(self):
        """protagonist=None → []."""
        r, store, world = _seed_world()
        provider = FakeLLMProvider(json_responses=[_canned_batch_response(3)])
        events = run_density(r, store, world, None,
                             provider=provider, day=1, scene="s1", turn=1)
        assert events == []

    def test_protagonist_outside_any_town_returns_empty(self):
        """Protagonist located_in an L3 with no L2 ancestor → []."""
        r = _reg()
        store = _store(r)
        # L3 venue with NO parent (no L2 ancestor)
        for ev in [
            kernel_event("place_created", day=1, scene="s1", summary="orphan",
                         deltas={"id": "orphan_v", "level": 3, "kind": "venue",
                                 "seed": "x", "tier": "tracked"}, turn=0),
            kernel_event("entity_created", day=1, scene="s1", summary="hero",
                         deltas={"id": "hero", "etype": "Person", "tier": "tracked"}, turn=0),
            kernel_event("entity_moved", day=1, scene="s1", summary="hero->orphan_v",
                         deltas={"who": "hero", "to": "orphan_v"}, turn=0),
        ]:
            store.append(ev)
        world = project(r, store.iter_events())
        world["meta"]["campaign_seed"] = 42

        provider = FakeLLMProvider(json_responses=[_canned_batch_response(3)])
        events = run_density(r, store, world, "hero",
                             provider=provider, day=1, scene="s1", turn=1)
        assert events == []

    def test_seeded_even_if_zero_skeletons(self):
        """If provider returns 0 skeletons, still marks seeded."""
        r, store, world = _seed_world(density=0.3)
        # Provider returns empty batch
        provider = FakeLLMProvider(json_responses=[{"lines": []}])

        events = run_density(r, store, world, "hero",
                             provider=provider, day=1, scene="s1", turn=1)

        types = [e["type"] for e in events]
        assert "lore_seeded" in types

        new_world = project(r, store.iter_events())
        assert new_world["systems"]["lore"]["gen"]["town1"]["seeded"] is True


# ---------------------------------------------------------------------------
# 2. Refresh: after REFRESH_INTERVAL_DAYS, density_refreshed is emitted
# ---------------------------------------------------------------------------

class TestRefresh:
    def _seeded_world(self, density=0.3, seed_day=1):
        """Seed town1 and return world + store."""
        r, store, world = _seed_world(density=density)
        provider = FakeLLMProvider(json_responses=[{"lines": []}])
        run_density(r, store, world, "hero",
                    provider=provider, day=seed_day, scene="s1", turn=1)
        world = project(r, store.iter_events())
        world["meta"]["campaign_seed"] = 42
        return r, store, world

    def test_no_refresh_before_interval(self):
        """Day advance less than REFRESH_INTERVAL_DAYS → [] (no refresh check)."""
        r, store, world = self._seeded_world(seed_day=1)

        provider = FakeLLMProvider(json_responses=[_canned_batch_response(1)])
        day2 = 1 + REFRESH_INTERVAL_DAYS - 1  # just under interval
        events = run_density(r, store, world, "hero",
                             provider=provider, day=day2, scene="s1", turn=2)
        assert events == [], f"Expected no refresh; got {events}"

    def test_refresh_emits_density_refreshed_at_interval(self):
        """Day advance >= REFRESH_INTERVAL_DAYS → density_refreshed emitted."""
        density = 0.3
        r, store, world = self._seeded_world(density=density, seed_day=1)

        # Use density 0.99 so the d100 roll almost certainly spawns a line
        # (but we only care that density_refreshed is present regardless)
        provider = FakeLLMProvider(json_responses=[_canned_batch_response(1)])
        day_refresh = 1 + REFRESH_INTERVAL_DAYS
        events = run_density(r, store, world, "hero",
                             provider=provider, day=day_refresh, scene="s1", turn=3)

        types = [e["type"] for e in events]
        assert "density_refreshed" in types, f"Expected density_refreshed; got {types}"

        # Last_refresh_day must be updated
        new_world = project(r, store.iter_events())
        gen = new_world["systems"]["lore"]["gen"]["town1"]
        assert gen["last_refresh_day"] == day_refresh

    def test_refresh_provider_none_returns_empty(self):
        """provider=None at refresh interval → no events (refresh deferred)."""
        r, store, world = self._seeded_world(seed_day=1)
        day_refresh = 1 + REFRESH_INTERVAL_DAYS
        events = run_density(r, store, world, "hero",
                             provider=None, day=day_refresh, scene="s1", turn=3)
        assert events == []

    def test_deterministic_seed_decides_spawn(self):
        """With a deterministic campaign_seed, the spawn decision is reproducible.

        We pick a density such that the specific seed+day either always spawns
        or never spawns — then check consistency across two calls from identical state.
        """
        # Build two identical stores and compare spawn decisions
        density = 0.3
        r1, store1, world1 = _seed_world(density=density, campaign_seed=999)
        r2, store2, world2 = _seed_world(density=density, campaign_seed=999)

        # Seed both (no lines)
        for r, store, world in [(r1, store1, world1), (r2, store2, world2)]:
            prov = FakeLLMProvider(json_responses=[{"lines": []}])
            run_density(r, store, world, "hero", provider=prov, day=1, scene="s1", turn=1)

        world1 = project(r1, store1.iter_events())
        world2 = project(r2, store2.iter_events())
        world1["meta"]["campaign_seed"] = 999
        world2["meta"]["campaign_seed"] = 999

        day_refresh = 1 + REFRESH_INTERVAL_DAYS
        prov1 = FakeLLMProvider(json_responses=[_canned_batch_response(1)])
        prov2 = FakeLLMProvider(json_responses=[_canned_batch_response(1)])

        events1 = run_density(r1, store1, world1, "hero",
                              provider=prov1, day=day_refresh, scene="s1", turn=2)
        events2 = run_density(r2, store2, world2, "hero",
                              provider=prov2, day=day_refresh, scene="s1", turn=2)

        # Both should produce the same event types (spawn or not, deterministic)
        types1 = [e["type"] for e in events1]
        types2 = [e["type"] for e in events2]
        assert types1 == types2, f"Non-deterministic spawn decision: {types1} vs {types2}"
