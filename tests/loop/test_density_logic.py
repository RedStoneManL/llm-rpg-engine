"""tests.loop.test_density_logic — Task 1: density resolution, caps, complexity roll.

Build worlds via the real pipeline: append place_created kernel_events + project().
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
from systems.lore import LoreSystem
from loop.lore import create_lore_line
from engine.oracle import Oracle, scene_seed


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(LoreSystem())
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(
        os.path.join(d, "e.db"),
        os.path.join(d, "e.jsonl"),
        allowed_types=registry.event_types(),
    )


def _place_ev(pid, level, kind, seed, parent=None, density=None):
    d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
    if parent:
        d["parent"] = parent
    if density is not None:
        d["density"] = density
    return kernel_event("place_created", day=1, scene="s1", summary=pid, deltas=d, turn=0)


def _sk(lid, complexity, anchor, l3_anchor="v1"):
    return {
        "id": lid,
        "complexity": complexity,
        "about": f"about-{lid}",
        "secret": "secret",
        "anchor": anchor,
        "description": f"desc-{lid}",
        "trigger": "trigger",
        "l3_anchor": l3_anchor,
        "stages": [{"hint": "h1"}, {"hint": "h2"}],
        "threshold": 0,  # never advances automatically
    }


# ---------------------------------------------------------------------------
# Build world with L1 region → L2 town → L3 venue
# ---------------------------------------------------------------------------

def _world_with_l1(density=0.5):
    """L1 region (with density) → L2 town → L3 venue."""
    r = _reg()
    store = _store(r)
    evs = [
        _place_ev("region1", 1, "region", "北境", density=density),
        _place_ev("town1", 2, "settlement", "边城", parent="region1"),
        _place_ev("venue1", 3, "venue", "集市", parent="town1"),
    ]
    for ev in evs:
        store.append(ev)
    world = project(r, store.iter_events())
    world["meta"]["day"] = 1
    world["meta"]["campaign_seed"] = 12345
    return r, store, world


def _world_no_l1():
    """Only L2 town → L3 venue (no L1 region)."""
    r = _reg()
    store = _store(r)
    evs = [
        _place_ev("town_only", 2, "settlement", "孤城"),
        _place_ev("venue_only", 3, "venue", "街道", parent="town_only"),
    ]
    for ev in evs:
        store.append(ev)
    world = project(r, store.iter_events())
    world["meta"]["day"] = 1
    world["meta"]["campaign_seed"] = 12345
    return r, store, world


# ===========================================================================
# 1. resolve_density
# ===========================================================================

class TestResolveDensity:
    def test_l1_density_propagates_to_town(self):
        from loop.density import resolve_density
        _, _, world = _world_with_l1(density=0.5)
        result = resolve_density(world, "town1", day=1)
        assert result == 0.5

    def test_no_l1_returns_default(self):
        from loop.density import resolve_density, DENSITY_DEFAULT
        _, _, world = _world_no_l1()
        result = resolve_density(world, "town_only", day=1)
        assert result == DENSITY_DEFAULT

    def test_l1_without_density_returns_default(self):
        """L1 exists but has no density attr → default."""
        from loop.density import resolve_density, DENSITY_DEFAULT
        r = _reg()
        store = _store(r)
        evs = [
            _place_ev("region_no_d", 1, "region", "空区"),  # no density
            _place_ev("town2", 2, "settlement", "城", parent="region_no_d"),
        ]
        for ev in evs:
            store.append(ev)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1
        result = resolve_density(world, "town2", day=1)
        assert result == DENSITY_DEFAULT


# ===========================================================================
# 2. region_scope
# ===========================================================================

class TestRegionScope:
    def test_returns_l1_id_when_present(self):
        from loop.density import region_scope
        _, _, world = _world_with_l1()
        result = region_scope(world, "town1", day=1)
        assert result == "region1"

    def test_returns_town_id_when_no_l1(self):
        from loop.density import region_scope
        _, _, world = _world_no_l1()
        result = region_scope(world, "town_only", day=1)
        assert result == "town_only"


# ===========================================================================
# 3. count_tier
# ===========================================================================

def _world_two_towns():
    """Two towns in one region for cap/count tests."""
    r = _reg()
    store = _store(r)
    evs = [
        _place_ev("reg_a", 1, "region", "大区"),
        _place_ev("town_a", 2, "settlement", "甲城", parent="reg_a"),
        _place_ev("venue_a", 3, "venue", "集市甲", parent="town_a"),
        _place_ev("town_b", 2, "settlement", "乙城", parent="reg_a"),
        _place_ev("venue_b", 3, "venue", "集市乙", parent="town_b"),
    ]
    for ev in evs:
        store.append(ev)
    world = project(r, store.iter_events())
    world["meta"]["day"] = 1
    world["meta"]["campaign_seed"] = 99999
    return r, store, world


class TestCountTier:
    def test_simple_per_town(self):
        from loop.density import count_tier
        r, store, world = _world_two_towns()
        # 2 simple lines in town_a, 1 in town_b
        for lid in ("s1", "s2"):
            create_lore_line(store, _sk(lid, "simple", "town_a", "venue_a"),
                             day=1, scene="s1", turn=0)
        create_lore_line(store, _sk("s3", "simple", "town_b", "venue_b"),
                         day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1
        assert count_tier(world, "town_a", "simple") == 2
        assert count_tier(world, "town_b", "simple") == 1

    def test_medium_per_town(self):
        from loop.density import count_tier
        r, store, world = _world_two_towns()
        create_lore_line(store, _sk("m1", "medium", "town_a", "venue_a"),
                         day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1
        assert count_tier(world, "town_a", "medium") == 1
        assert count_tier(world, "town_b", "medium") == 0

    def test_complex_per_region(self):
        from loop.density import count_tier
        r, store, world = _world_two_towns()
        # 1 complex in town_a, 1 complex in town_b → region total = 2
        create_lore_line(store, _sk("c1", "complex", "town_a", "venue_a"),
                         day=1, scene="s1", turn=0)
        create_lore_line(store, _sk("c2", "complex", "town_b", "venue_b"),
                         day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1
        # scope_id = reg_a (the region)
        assert count_tier(world, "reg_a", "complex") == 2

    def test_resolved_lines_excluded(self):
        """了结/expired lines must NOT count."""
        from loop.density import count_tier
        r, store, world = _world_two_towns()
        create_lore_line(store, _sk("s_done", "simple", "town_a", "venue_a"),
                         day=1, scene="s1", turn=0)
        # Manually mark it as resolved via quest_expired
        from kernel.events import kernel_event as ke
        store.append(ke("quest_expired", day=2, scene="s1",
                        summary="expired", deltas={"id": "s_done"}, turn=1))
        world = project(r, store.iter_events())
        world["meta"]["day"] = 2
        assert count_tier(world, "town_a", "simple") == 0

    def test_明_state_lines_count(self):
        """Lines in state 明 (surfaced) still count toward cap."""
        from loop.density import count_tier
        r, store, world = _world_two_towns()
        create_lore_line(store, _sk("s_ming", "simple", "town_a", "venue_a"),
                         day=1, scene="s1", turn=0)
        from kernel.events import kernel_event as ke
        store.append(ke("quest_surfaced", day=2, scene="s1",
                        summary="surfaced", deltas={"id": "s_ming", "summary": "x"},
                        turn=1))
        world = project(r, store.iter_events())
        world["meta"]["day"] = 2
        assert count_tier(world, "town_a", "simple") == 1


# ===========================================================================
# 4. roll_complexity
# ===========================================================================

class TestRollComplexity:
    def _empty_world_with_towns(self):
        return _world_two_towns()

    def test_deterministic_sequence_with_fixed_seed(self):
        """Same seed → same roll sequence."""
        from loop.density import roll_complexity, region_scope
        r, store, world = self._empty_world_with_towns()
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1
        world["meta"]["campaign_seed"] = 12345

        oracle1 = Oracle(scene_seed(12345, "density:town_a:0"))
        oracle2 = Oracle(scene_seed(12345, "density:town_a:0"))

        result1 = roll_complexity(oracle1, world, "town_a", "reg_a")
        result2 = roll_complexity(oracle2, world, "town_a", "reg_a")
        assert result1 == result2

    def test_tier_ranges(self):
        """Roll ranges map to correct tiers (no caps hit in empty world)."""
        from loop.density import roll_complexity

        r, store, world = self._empty_world_with_towns()
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1

        # Patch oracle to force specific roll values
        class MockOracle:
            def __init__(self, val):
                self._val = val
            def d100(self):
                return self._val

        assert roll_complexity(MockOracle(1), world, "town_a", "reg_a") == "simple"
        assert roll_complexity(MockOracle(70), world, "town_a", "reg_a") == "simple"
        assert roll_complexity(MockOracle(71), world, "town_a", "reg_a") == "medium"
        assert roll_complexity(MockOracle(95), world, "town_a", "reg_a") == "medium"
        assert roll_complexity(MockOracle(96), world, "town_a", "reg_a") == "complex"
        assert roll_complexity(MockOracle(100), world, "town_a", "reg_a") == "complex"

    def test_complex_cap_downgrades_to_medium(self):
        """Region with 2 complex → complex roll downgrades to medium."""
        from loop.density import roll_complexity, CAP_COMPLEX
        r, store, world = _world_two_towns()
        # Fill region with CAP_COMPLEX complex lines
        for i in range(CAP_COMPLEX):
            create_lore_line(store, _sk(f"cx{i}", "complex", "town_a", "venue_a"),
                             day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1

        class AlwaysComplex:
            def d100(self): return 100  # 96-100 = complex

        result = roll_complexity(AlwaysComplex(), world, "town_a", "reg_a")
        assert result == "medium"

    def test_medium_cap_downgrades_to_simple(self):
        """Town with 8 medium → medium roll (or downgraded-complex) becomes simple."""
        from loop.density import roll_complexity, CAP_MEDIUM
        r, store, world = _world_two_towns()
        for i in range(CAP_MEDIUM):
            create_lore_line(store, _sk(f"med{i}", "medium", "town_a", "venue_a"),
                             day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1

        class AlwaysMedium:
            def d100(self): return 80  # 71-95 = medium

        result = roll_complexity(AlwaysMedium(), world, "town_a", "reg_a")
        assert result == "simple"

    def test_simple_cap_returns_none(self):
        """Town with 15 simple → simple (or double-downgraded) returns None."""
        from loop.density import roll_complexity, CAP_SIMPLE
        r, store, world = _world_two_towns()
        for i in range(CAP_SIMPLE):
            create_lore_line(store, _sk(f"sm{i}", "simple", "town_a", "venue_a"),
                             day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1

        class AlwaysSimple:
            def d100(self): return 50  # 1-70 = simple

        result = roll_complexity(AlwaysSimple(), world, "town_a", "reg_a")
        assert result is None

    def test_all_caps_hit_returns_none(self):
        """If all caps hit (complex→medium→simple→None), returns None."""
        from loop.density import roll_complexity, CAP_COMPLEX, CAP_MEDIUM, CAP_SIMPLE
        r, store, world = _world_two_towns()
        # Fill all caps in town_a / reg_a
        for i in range(CAP_COMPLEX):
            create_lore_line(store, _sk(f"cx{i}", "complex", "town_a", "venue_a"),
                             day=1, scene="s1", turn=0)
        for i in range(CAP_MEDIUM):
            create_lore_line(store, _sk(f"med{i}", "medium", "town_a", "venue_a"),
                             day=1, scene="s1", turn=0)
        for i in range(CAP_SIMPLE):
            create_lore_line(store, _sk(f"sm{i}", "simple", "town_a", "venue_a"),
                             day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        world["meta"]["day"] = 1

        class AlwaysComplex:
            def d100(self): return 100

        result = roll_complexity(AlwaysComplex(), world, "town_a", "reg_a")
        assert result is None
