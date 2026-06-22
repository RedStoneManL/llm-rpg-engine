"""tests.loop.test_endgame — Task 1: pure-logic tests for loop/endgame.py.

Tests:
  - world_rescue_chance: monotonic, base at stage 0, higher near end, clamped
  - roll_world_rescue: deterministic with pinned Oracle seeds
  - rescue_summary / catastrophe_summary: contain expected text
  - build_catastrophe_events: returns quest_catastrophe + world_change anchored
    at region_scope(anchor); omits world_change when emit_world_change=False
"""
from __future__ import annotations

import pytest

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.lore import LoreSystem
from engine.oracle import Oracle
from loop.density import region_scope
from loop.endgame import (
    RESCUE_BASE,
    RESCUE_RANGE,
    RESCUE_GRACE_STAGES,
    FINALE_RESCUE_CHANCE,
    world_rescue_chance,
    roll_world_rescue,
    rescue_summary,
    catastrophe_summary,
    build_catastrophe_events,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _place_ev(pid, level, kind, seed_text, parent=None, density=None):
    d = {"id": pid, "level": level, "kind": kind, "seed": seed_text, "tier": "tracked"}
    if parent:
        d["parent"] = parent
    if density is not None:
        d["density"] = density
    return kernel_event("place_created", day=1, scene="s1", summary=pid, deltas=d, turn=0)


def _build_world():
    """Build a minimal world: L1 region -> L2 town -> L3 venue."""
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(LoreSystem())
    evs = [
        _place_ev("region1", 1, "region", "北境", density=0.5),
        _place_ev("town1",   2, "settlement", "边城", parent="region1"),
        _place_ev("venue1",  3, "venue",      "集市", parent="town1"),
    ]
    w = project(r, evs)
    return w


def _complex_line():
    return {
        "id": "dark_complex",
        "complexity": "complex",
        "about": "商队失踪",
        "secret": "商队首领卷款逃走",
        "anchor": "town1",
        "stages": [{"hint": "h1"}, {"hint": "h2"}, {"hint": "h3"},
                   {"hint": "h4"}, {"hint": "h5"}],
    }


# ---------------------------------------------------------------------------
# world_rescue_chance
# ---------------------------------------------------------------------------

class TestWorldRescueChance:
    def test_equals_rescue_base_at_stage_0(self):
        # stage_idx=0, n_stages=5 → RESCUE_BASE + 0 = 10
        assert world_rescue_chance(0, 5) == RESCUE_BASE

    def test_equals_rescue_base_at_stage_0_any_n(self):
        for n in [2, 3, 5, 10]:
            assert world_rescue_chance(0, n) == RESCUE_BASE, f"failed for n={n}"

    def test_higher_near_last_stage(self):
        n = 5
        chance_early = world_rescue_chance(0, n)
        chance_late = world_rescue_chance(n - 1, n)
        assert chance_late > chance_early

    def test_monotonic_non_decreasing(self):
        n = 5
        chances = [world_rescue_chance(i, n) for i in range(n)]
        for i in range(len(chances) - 1):
            assert chances[i] <= chances[i + 1], (
                f"Not monotonic at idx {i}: {chances[i]} > {chances[i+1]}"
            )

    def test_monotonic_n2(self):
        """Edge case: n_stages=2 (stage 0 and 1)."""
        assert world_rescue_chance(0, 2) <= world_rescue_chance(1, 2)

    def test_clamped_max_100(self):
        # Extreme: stage_idx way beyond n_stages
        assert world_rescue_chance(1000, 5) <= 100

    def test_clamped_min_0(self):
        # Extreme: negative stage_idx (should not happen in practice, but guard)
        assert world_rescue_chance(-10, 5) >= 0

    def test_formula_at_last_stage(self):
        # stage_idx = n-1, n=5 → RESCUE_BASE + round((n-1)/(n-1) * RESCUE_RANGE) = 50
        n = 5
        expected = RESCUE_BASE + round((n - 1) / (n - 1) * RESCUE_RANGE)
        assert world_rescue_chance(n - 1, n) == expected

    def test_n_stages_1_no_division_by_zero(self):
        # n_stages=1: max(1, n-1)=max(1,0)=1; stage_idx=0 → RESCUE_BASE
        assert world_rescue_chance(0, 1) == RESCUE_BASE


# ---------------------------------------------------------------------------
# roll_world_rescue
# ---------------------------------------------------------------------------

class TestRollWorldRescue:
    def test_deterministic_rescue_true(self):
        # Oracle(1).d100() = 18; chance at stage_idx=2, n_stages=5 = 30
        # 18 <= 30 → True
        o = Oracle(1)
        result = roll_world_rescue(o, stage_idx=2, n_stages=5)
        assert result is True

    def test_deterministic_rescue_false(self):
        # Oracle(3).d100() = 31; chance at stage_idx=2, n_stages=5 = 30
        # 31 > 30 → False
        o = Oracle(3)
        result = roll_world_rescue(o, stage_idx=2, n_stages=5)
        assert result is False

    def test_same_seed_same_result(self):
        """Same seed, same stage → same bool (determinism)."""
        o1 = Oracle(42)
        o2 = Oracle(42)
        r1 = roll_world_rescue(o1, stage_idx=3, n_stages=5)
        r2 = roll_world_rescue(o2, stage_idx=3, n_stages=5)
        assert r1 == r2

    def test_result_is_bool(self):
        o = Oracle(7)
        assert isinstance(roll_world_rescue(o, 1, 5), bool)


# ---------------------------------------------------------------------------
# rescue_summary / catastrophe_summary
# ---------------------------------------------------------------------------

class TestSummaries:
    def test_rescue_summary_contains_about(self):
        line = {"about": "商队失踪", "secret": "卷款逃走"}
        s = rescue_summary(line)
        assert "商队失踪" in s

    def test_rescue_summary_contains_世界自行了结(self):
        line = {"about": "暗线测试", "secret": "隐情"}
        s = rescue_summary(line)
        assert "世界" in s or "了结" in s

    def test_catastrophe_summary_contains_about(self):
        line = {"about": "商队失踪", "secret": "卷款逃走"}
        s = catastrophe_summary(line, "region1")
        assert "商队失踪" in s

    def test_catastrophe_summary_contains_secret(self):
        line = {"about": "商队失踪", "secret": "卷款逃走"}
        s = catastrophe_summary(line, "region1")
        assert "卷款逃走" in s

    def test_catastrophe_summary_contains_region(self):
        line = {"about": "商队失踪", "secret": "卷款逃走"}
        s = catastrophe_summary(line, "region1")
        assert "region1" in s

    def test_rescue_summary_missing_about_no_crash(self):
        """Empty about → no crash, just returns string."""
        line = {}
        s = rescue_summary(line)
        assert isinstance(s, str)

    def test_catastrophe_summary_missing_fields_no_crash(self):
        line = {}
        s = catastrophe_summary(line, "r1")
        assert isinstance(s, str)


# ---------------------------------------------------------------------------
# build_catastrophe_events
# ---------------------------------------------------------------------------

class TestBuildCatastropheEvents:
    def test_returns_two_events_by_default(self):
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(line, w, day=5, scene="s1", turn=10)
        assert len(evs) == 2

    def test_first_event_is_quest_catastrophe(self):
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(line, w, day=5, scene="s1", turn=10)
        assert evs[0]["type"] == "quest_catastrophe"

    def test_second_event_is_world_change(self):
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(line, w, day=5, scene="s1", turn=10)
        assert evs[1]["type"] == "world_change"

    def test_quest_catastrophe_id_matches_line(self):
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(line, w, day=5, scene="s1", turn=10)
        assert evs[0]["deltas"]["id"] == "dark_complex"

    def test_world_change_place_equals_region_scope(self):
        """world_change.deltas['place'] must equal region_scope(anchor, day)."""
        w = _build_world()
        line = _complex_line()
        day = 5
        expected_region = region_scope(w, line["anchor"], day)
        evs = build_catastrophe_events(line, w, day=day, scene="s1", turn=10)
        wc = evs[1]
        assert wc["deltas"]["place"] == expected_region

    def test_world_change_place_is_region1(self):
        """Concretely: anchor=town1 → region_scope → 'region1'."""
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(line, w, day=1, scene="s1", turn=1)
        assert evs[1]["deltas"]["place"] == "region1"

    def test_world_change_has_level(self):
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(line, w, day=1, scene="s1", turn=1)
        assert "level" in evs[1]["deltas"]
        assert isinstance(evs[1]["deltas"]["level"], int)

    def test_quest_catastrophe_anchor_is_region(self):
        """quest_catastrophe.deltas['anchor'] should be the region (region_scope result)."""
        w = _build_world()
        line = _complex_line()
        expected_region = region_scope(w, "town1", 1)
        evs = build_catastrophe_events(line, w, day=1, scene="s1", turn=1)
        assert evs[0]["deltas"]["anchor"] == expected_region

    def test_emit_world_change_false_returns_one_event(self):
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(
            line, w, day=1, scene="s1", turn=1, emit_world_change=False
        )
        assert len(evs) == 1
        assert evs[0]["type"] == "quest_catastrophe"

    def test_events_have_required_kernel_fields(self):
        w = _build_world()
        line = _complex_line()
        evs = build_catastrophe_events(line, w, day=3, scene="s2", turn=7)
        for ev in evs:
            assert "id" in ev
            assert ev["day"] == 3
            assert ev["scene"] == "s2"
            assert ev["turn"] == 7

    def test_no_random_time_dependency(self):
        """Same inputs → same outputs (no random/time calls)."""
        w = _build_world()
        line = _complex_line()
        evs1 = build_catastrophe_events(line, w, day=1, scene="s1", turn=1)
        evs2 = build_catastrophe_events(line, w, day=1, scene="s1", turn=1)
        # types and key deltas should match even if UUIDs differ
        assert evs1[0]["type"] == evs2[0]["type"]
        assert evs1[0]["deltas"] == evs2[0]["deltas"]
        assert evs1[1]["type"] == evs2[1]["type"]
        assert evs1[1]["deltas"] == evs2[1]["deltas"]

    def test_anchor_falls_back_to_itself_when_no_l1(self):
        """If anchor has no L1 ancestor, region_scope returns anchor itself."""
        r = Registry()
        r.register(OntologySystem())
        r.register(PlaceSystem())
        r.register(LoreSystem())
        # Only L2 place, no L1 parent
        evs_setup = [
            _place_ev("orphan_town", 2, "settlement", "孤镇"),
        ]
        w = project(r, evs_setup)
        line = {**_complex_line(), "anchor": "orphan_town"}
        evs = build_catastrophe_events(line, w, day=1, scene="s1", turn=1)
        # region_scope returns orphan_town itself when no L1 ancestor
        assert evs[1]["deltas"]["place"] == "orphan_town"
