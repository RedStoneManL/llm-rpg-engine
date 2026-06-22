"""tests.loop.test_endgame_e2e — Task 3: end-to-end complex-line endgame.

Tests prove the full pipeline from world creation through run_lore orchestration
to final state.  No shortcuts: we assert concrete post-state events, projected
world state, and density cap behaviour.

Two paths:
  Path A (world-rescue): campaign_seed=3 causes a checkpoint rescue at stage 1
      → quest_world_resolved(by==world_rescue) emitted; line state==了结;
      density.count_tier(region, "complex") drops from 1 to 0 (cap released).

  Path B (catastrophe): campaign_seed=5 fails all checkpoint rescues; we inject
      pending_finale=True (simulating prior lifespan expiry) and day=30 so the
      finale oracle also fails → quest_catastrophe + world_change both emitted;
      world_change.place == region_scope(anchor) i.e. "region1"; line 了结 by
      catastrophe; cap released.

Additional:
  Determinism: same campaign_seed → same resolution events on a fresh store.
  region-bounded: world_change.place asserted to be region1 (not the whole world,
      not just the town).

Pinned seeds (verified via python3 -c assertions at bottom of this file):
  _SEED_PATH_A = 3  # roll_world_rescue at stage 1, n_stages=5, chance=20 → roll=13 SUCCESS
  _SEED_PATH_B = 5  # rescue FAILS stages 1/2/3; finale at day 30 → roll=74 > 60 FAIL
  _FINALE_DAY  = 30
"""
from __future__ import annotations

import os
import tempfile

import pytest

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import open_store, kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.lore import LoreSystem
from systems.cascade import CascadeSystem
from loop.lore import create_lore_line, run_lore
from loop.density import count_tier, region_scope
from engine.oracle import Oracle, scene_seed
from loop.endgame import (
    world_rescue_chance,
    RESCUE_GRACE_STAGES,
    FINALE_RESCUE_CHANCE,
)


# ---------------------------------------------------------------------------
# Pinned seeds
# ---------------------------------------------------------------------------

_SEED_PATH_A = 3    # rescue SUCCESS at stage 1 (n_stages=5, chance=20, roll=13)
_SEED_PATH_B = 5    # rescue FAIL at 1/2/3; finale at day=30 roll=74>60 → catastrophe
_FINALE_DAY  = 30   # now_day used for finale oracle

# ---------------------------------------------------------------------------
# World blueprint
# ---------------------------------------------------------------------------

# L1 region ⊃ L2 town ⊃ L3 venues (standard three-tier geography)
_REGION  = "region1"
_TOWN    = "town1"
_VENUE1  = "venue1"
_VENUE2  = "venue2"

# Complex 暗 line with 5 stages + threshold=100 so advance always fires
_COMPLEX_SK = {
    "id": "e2e_complex_1",
    "complexity": "complex",
    "about": "要塞机密泄露",
    "secret": "内奸是守将之子",
    "anchor": _TOWN,
    "description": "驻守要塞的军情悄然外流",
    "trigger": "玩家调查军情失踪案",
    "l3_anchor": _VENUE1,
    "stages": [
        {"hint": "小道消息开始流传"},
        {"hint": "要塞出现可疑人员"},
        {"hint": "军械失窃"},
        {"hint": "哨兵遭遇不明伏击"},
        {"hint": "机密全面外泄"},
    ],
    "threshold": 100,  # always advances so we can drive stages deterministically
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_registry(with_cascade: bool = True) -> Registry:
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(LoreSystem())
    if with_cascade:
        r.register(CascadeSystem())
    return r


def _build_store(registry: Registry):
    d = tempfile.mkdtemp()
    return open_store(
        os.path.join(d, "e.db"),
        os.path.join(d, "e.jsonl"),
        allowed_types=registry.event_types(),
    )


def _place_ev(pid: str, level: int, kind: str, seed_text: str, parent: str | None = None):
    d = {"id": pid, "level": level, "kind": kind, "seed": seed_text, "tier": "tracked"}
    if parent:
        d["parent"] = parent
    return kernel_event("place_created", day=1, scene="s1", summary=pid, deltas=d, turn=0)


def _seed_places(store) -> None:
    """Append place events: region1(L1) ⊃ town1(L2) ⊃ venue1(L3) + venue2(L3)."""
    for ev in [
        _place_ev(_REGION, 1, "region",     "北境荒原"),
        _place_ev(_TOWN,   2, "settlement", "边城要镇", parent=_REGION),
        _place_ev(_VENUE1, 3, "venue",      "要塞大营", parent=_TOWN),
        _place_ev(_VENUE2, 3, "venue",      "集市广场", parent=_TOWN),
    ]:
        store.append(ev)


def _make_world(campaign_seed: int, with_cascade: bool = True):
    """Build a registry+store+world with places + one complex 暗 line."""
    r = _build_registry(with_cascade=with_cascade)
    s = _build_store(r)
    _seed_places(s)
    create_lore_line(s, _COMPLEX_SK, day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w.setdefault("meta", {})["campaign_seed"] = campaign_seed
    w.setdefault("meta", {})["scene"] = "s1"
    return r, s, w


def _reproject(r, s, campaign_seed: int, day: int | None = None) -> dict:
    """Re-project from store events and patch meta back in."""
    w = project(r, s.iter_events())
    w.setdefault("meta", {})["campaign_seed"] = campaign_seed
    w.setdefault("meta", {})["scene"] = "s1"
    if day is not None:
        w["meta"]["day"] = day
    return w


# ---------------------------------------------------------------------------
# Path A: world-rescue at checkpoint
# ---------------------------------------------------------------------------

class TestPathAWorldRescue:
    """Path A: checkpoint rescue SUCCEEDS at stage 1 → line 了结 + cap released."""

    # Pinned: campaign_seed=3, lid="e2e_complex_1", stage_idx=1, n_stages=5
    #         chance = world_rescue_chance(1, 5) = 10+round(1/4*40) = 10+10 = 20
    #         Oracle(scene_seed(3, "rescue:e2e_complex_1", 1)).d100() = 13 ≤ 20 → SUCCESS

    def _drive_path_a(self):
        """Drive to stage 1 rescue; return (registry, store, world_before, world_after, appended)."""
        r, s, w = _make_world(campaign_seed=_SEED_PATH_A)

        # First call: stage -1 → 0 (no rescue at stage 0; RESCUE_GRACE_STAGES=1)
        run_lore(r, s, w)
        w = _reproject(r, s, _SEED_PATH_A)
        assert w["systems"]["lore"]["lines"]["e2e_complex_1"]["stage_idx"] == 0

        # Record count BEFORE the rescue resolves
        count_before = count_tier(w, _REGION, "complex")

        # Second call: stage 0 → 1 (rescue roll fires for complex line at stage ≥ 1)
        appended = run_lore(r, s, w)
        w_after = _reproject(r, s, _SEED_PATH_A)

        return r, s, w, w_after, appended, count_before

    def test_quest_world_resolved_emitted(self):
        _, _, _, _, appended, _ = self._drive_path_a()
        ev_types = [e["type"] for e in appended]
        assert "quest_world_resolved" in ev_types, \
            f"Expected quest_world_resolved in Path A; got {ev_types}"

    def test_resolved_event_by_world_rescue(self):
        _, _, _, _, appended, _ = self._drive_path_a()
        resolved = [e for e in appended if e["type"] == "quest_world_resolved"]
        assert len(resolved) == 1
        assert resolved[0]["deltas"]["id"] == "e2e_complex_1"
        assert resolved[0]["deltas"]["by"] == "world_rescue"

    def test_line_liujie_after_projection(self):
        _, _, _, w_after, _, _ = self._drive_path_a()
        ln = w_after["systems"]["lore"]["lines"]["e2e_complex_1"]
        assert ln["state"] == "了结", f"Line must be 了结 after rescue; got {ln['state']}"
        assert ln["resolved"]["by"] == "world_rescue"
        assert "status" not in ln, "status must be absent; state is the lifecycle field"

    def test_density_cap_released(self):
        """count_tier(region, complex) drops from 1 to 0 after rescue."""
        _, _, _, w_after, _, count_before = self._drive_path_a()
        count_after = count_tier(w_after, _REGION, "complex")
        assert count_before == 1, f"Expected 1 complex line before rescue; got {count_before}"
        assert count_after == 0, \
            f"Region complex cap must be released after 了结; got count_after={count_after}"
        assert count_after == count_before - 1

    def test_no_quest_surfaced_on_rescue_turn(self):
        """Rescue short-circuits world-push surface: no quest_surfaced emitted same trip."""
        _, _, _, _, appended, _ = self._drive_path_a()
        ev_types = [e["type"] for e in appended]
        assert "quest_surfaced" not in ev_types, \
            "quest_surfaced must NOT be emitted when checkpoint rescue fires"

    def test_line_not_reprocessed_on_next_call(self):
        """Already-了结 line must be skipped by run_lore on subsequent calls."""
        r, s, w, w_after, _, _ = self._drive_path_a()
        # Third call: line is 了结 → state guard skips it
        appended3 = run_lore(r, s, w_after)
        ev_types = [e["type"] for e in appended3]
        assert "quest_world_resolved" not in ev_types
        assert "lore_advanced" not in ev_types, \
            "了结 line must not receive further lore_advanced events"


# ---------------------------------------------------------------------------
# Path B: catastrophe
# ---------------------------------------------------------------------------

class TestPathBCatastrophe:
    """Path B: all rescues fail + finale fails → catastrophe + world_change at region."""

    # Pinned: campaign_seed=5
    #   stage 1: chance=20, roll=51 > 20 → FAIL
    #   stage 2: chance=30, roll=69 > 30 → FAIL
    #   stage 3: chance=40, roll=98 > 40 → FAIL
    #   stage 4: last stage → world-push surface (no rescue)
    # Then pending_finale=True injected + day=30:
    #   finale: chance=60, roll=74 > 60 → FAIL → quest_catastrophe + world_change

    def _inject_pending_finale(self, r, s) -> dict:
        """Project world + inject pending_finale=True + set day=FINALE_DAY."""
        w = _reproject(r, s, _SEED_PATH_B, day=_FINALE_DAY)
        ln = w["systems"]["lore"]["lines"]["e2e_complex_1"]
        ln["pending_finale"] = True
        return w

    def _setup_for_finale(self):
        """Build world, inject pending_finale; return (r, s, w_with_finale)."""
        r, s, w = _make_world(campaign_seed=_SEED_PATH_B)
        # Count before (line is active暗 at this point)
        count_before = count_tier(w, _REGION, "complex")
        # Inject pending_finale (simulating prior lifespan expiry + nobody engaged)
        w_fin = self._inject_pending_finale(r, s)
        return r, s, w_fin, count_before

    def test_quest_catastrophe_emitted(self):
        r, s, w_fin, _ = self._setup_for_finale()
        appended = run_lore(r, s, w_fin)
        ev_types = [e["type"] for e in appended]
        assert "quest_catastrophe" in ev_types, \
            f"Expected quest_catastrophe in Path B; got {ev_types}"

    def test_world_change_emitted(self):
        r, s, w_fin, _ = self._setup_for_finale()
        appended = run_lore(r, s, w_fin)
        ev_types = [e["type"] for e in appended]
        assert "world_change" in ev_types, \
            f"Expected world_change when cascade registered; got {ev_types}"

    def test_world_change_place_is_region_scope(self):
        """world_change.place must equal region_scope(anchor) = 'region1'."""
        r, s, w_fin, _ = self._setup_for_finale()
        appended = run_lore(r, s, w_fin)
        wc_evs = [e for e in appended if e["type"] == "world_change"]
        assert wc_evs, "No world_change event found"
        place = wc_evs[0]["deltas"]["place"]
        expected = region_scope(w_fin, _TOWN, _FINALE_DAY)
        assert expected == _REGION, f"region_scope returned {expected!r} but expected {_REGION!r}"
        assert place == expected, \
            f"world_change.place={place!r} must equal region_scope(anchor)={expected!r}"
        # Also assert it is NOT the whole world and NOT just the town
        assert place != _TOWN, "world_change.place must not be the town itself"

    def test_world_change_anchored_at_region_not_town(self):
        """region-bounded: place is the L1 region, not the L2 town or a global scope."""
        r, s, w_fin, _ = self._setup_for_finale()
        appended = run_lore(r, s, w_fin)
        wc = [e for e in appended if e["type"] == "world_change"]
        assert wc
        place = wc[0]["deltas"]["place"]
        assert place == _REGION,  f"Expected region1, got {place!r}"
        assert place != _TOWN,    "Must not be town1"
        assert place != "world",  "Must not be world-wide"

    def test_line_liujie_by_catastrophe_after_projection(self):
        r, s, w_fin, _ = self._setup_for_finale()
        run_lore(r, s, w_fin)
        # Re-project from store (catastrophe event is now persisted)
        w_after = _reproject(r, s, _SEED_PATH_B, day=_FINALE_DAY)
        ln = w_after["systems"]["lore"]["lines"]["e2e_complex_1"]
        assert ln["state"] == "了结", f"Line must be 了结 after catastrophe; got {ln['state']}"
        assert ln["resolved"]["by"] == "catastrophe"
        assert "status" not in ln, "status must be absent; state is the lifecycle field"

    def test_density_cap_released_after_catastrophe(self):
        """count_tier(region, complex) drops to 0 after catastrophe resolves the line."""
        r, s, w_fin, count_before = self._setup_for_finale()
        run_lore(r, s, w_fin)
        w_after = _reproject(r, s, _SEED_PATH_B, day=_FINALE_DAY)
        count_after = count_tier(w_after, _REGION, "complex")
        assert count_before == 1, f"Expected 1 complex before catastrophe; got {count_before}"
        assert count_after == 0, \
            f"Cap must be released after catastrophe; count_after={count_after}"
        assert count_after == count_before - 1

    def test_no_world_change_without_cascade(self):
        """Without CascadeSystem registered, world_change must NOT be emitted."""
        r_nc, s_nc, w_nc = _make_world(campaign_seed=_SEED_PATH_B, with_cascade=False)
        w_fin_nc = project(r_nc, s_nc.iter_events())
        w_fin_nc.setdefault("meta", {})["campaign_seed"] = _SEED_PATH_B
        w_fin_nc["meta"]["scene"] = "s1"
        w_fin_nc["meta"]["day"] = _FINALE_DAY
        w_fin_nc["systems"]["lore"]["lines"]["e2e_complex_1"]["pending_finale"] = True
        appended = run_lore(r_nc, s_nc, w_fin_nc)
        ev_types = [e["type"] for e in appended]
        assert "world_change" not in ev_types, \
            "world_change must not be emitted when CascadeSystem is absent"
        assert "quest_catastrophe" in ev_types, \
            "quest_catastrophe must still fire even without cascade"

    def test_catastrophe_line_not_reprocessed(self):
        """Once catastrophe'd, subsequent run_lore calls skip the line."""
        r, s, w_fin, _ = self._setup_for_finale()
        run_lore(r, s, w_fin)
        w_after = _reproject(r, s, _SEED_PATH_B, day=_FINALE_DAY)
        # Third call: state==了结 → state guard fires → no further events
        appended3 = run_lore(r, s, w_after)
        ev_types = [e["type"] for e in appended3]
        assert "quest_catastrophe" not in ev_types, "Already-了结 must not re-catastrophe"
        assert "quest_world_resolved" not in ev_types


# ---------------------------------------------------------------------------
# Determinism / rewind
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Same campaign_seed → same event types on a fresh rebuild (rewind-safe)."""

    def _run_path_a_once(self):
        """Full Path A run; return appended event types from the rescue call."""
        r, s, w = _make_world(campaign_seed=_SEED_PATH_A)
        run_lore(r, s, w)
        w = _reproject(r, s, _SEED_PATH_A)
        appended = run_lore(r, s, w)
        return [e["type"] for e in appended]

    def test_path_a_deterministic(self):
        types1 = self._run_path_a_once()
        types2 = self._run_path_a_once()
        assert types1 == types2, \
            f"Path A non-deterministic: first run {types1}, second run {types2}"

    def _run_path_b_once(self):
        """Path B run; return appended event types from finale call."""
        r, s, w = _make_world(campaign_seed=_SEED_PATH_B)
        w_fin = project(r, s.iter_events())
        w_fin.setdefault("meta", {})["campaign_seed"] = _SEED_PATH_B
        w_fin["meta"]["scene"] = "s1"
        w_fin["meta"]["day"] = _FINALE_DAY
        w_fin["systems"]["lore"]["lines"]["e2e_complex_1"]["pending_finale"] = True
        appended = run_lore(r, s, w_fin)
        return [e["type"] for e in appended]

    def test_path_b_deterministic(self):
        types1 = self._run_path_b_once()
        types2 = self._run_path_b_once()
        assert types1 == types2, \
            f"Path B non-deterministic: first run {types1}, second run {types2}"

    def test_path_a_and_b_produce_different_outcomes(self):
        """Sanity: paths A and B must resolve via different mechanisms."""
        types_a = self._run_path_a_once()
        types_b = self._run_path_b_once()
        assert "quest_world_resolved" in types_a
        assert "quest_catastrophe"    in types_b
        assert "quest_catastrophe"    not in types_a
        assert "quest_world_resolved" not in types_b


# ---------------------------------------------------------------------------
# Region-bounded
# ---------------------------------------------------------------------------

class TestRegionBounded:
    """Explicit assertion: catastrophe world_change anchors to region_scope(anchor)."""

    def test_region_scope_of_town1_is_region1(self):
        """region_scope(town1) == region1 in our test world."""
        r, s, w = _make_world(campaign_seed=_SEED_PATH_B)
        result = region_scope(w, _TOWN, 1)
        assert result == _REGION, f"region_scope(town1) must be region1; got {result!r}"

    def test_catastrophe_world_change_place_equals_region_scope(self):
        """world_change.place must exactly equal region_scope(anchor, day)."""
        r, s, w = _make_world(campaign_seed=_SEED_PATH_B)
        w_fin = project(r, s.iter_events())
        w_fin.setdefault("meta", {})["campaign_seed"] = _SEED_PATH_B
        w_fin["meta"]["scene"] = "s1"
        w_fin["meta"]["day"] = _FINALE_DAY
        w_fin["systems"]["lore"]["lines"]["e2e_complex_1"]["pending_finale"] = True

        appended = run_lore(r, s, w_fin)
        wc_evs = [e for e in appended if e["type"] == "world_change"]
        assert wc_evs, "world_change must be emitted in catastrophe path"

        actual_place = wc_evs[0]["deltas"]["place"]
        expected_place = region_scope(w_fin, _TOWN, _FINALE_DAY)

        assert actual_place == expected_place, (
            f"world_change.place={actual_place!r} != "
            f"region_scope(anchor={_TOWN!r}, day={_FINALE_DAY})={expected_place!r}"
        )
        assert actual_place == _REGION, \
            f"world_change must be region-scoped (region1), not {actual_place!r}"


# ---------------------------------------------------------------------------
# Pinned seed verification (self-documenting, run automatically)
# ---------------------------------------------------------------------------

def test_pinned_seeds_sanity_e2e():
    """Confirm pinned seeds produce the exact rolls documented at the top of this file."""
    lid = "e2e_complex_1"
    n_stages = 5

    # Path A: campaign_seed=3, stage 1 rescue SUCCESS
    chance_1 = world_rescue_chance(1, n_stages)
    assert chance_1 == 20, f"Expected world_rescue_chance(1,5)==20; got {chance_1}"
    roll_a = Oracle(scene_seed(_SEED_PATH_A, f"rescue:{lid}", 1)).d100()
    assert roll_a <= chance_1, \
        f"Seed {_SEED_PATH_A}: expected roll≤{chance_1} (rescue), got {roll_a}"
    assert roll_a == 13, f"Pinned roll mismatch: expected 13, got {roll_a}"

    # Path B: campaign_seed=5, stages 1+2+3 all FAIL
    for s_idx, exp_chance, exp_roll in [(1, 20, 51), (2, 30, 69), (3, 40, 98)]:
        ch = world_rescue_chance(s_idx, n_stages)
        assert ch == exp_chance, f"world_rescue_chance({s_idx},5) expected {exp_chance}, got {ch}"
        roll = Oracle(scene_seed(_SEED_PATH_B, f"rescue:{lid}", s_idx)).d100()
        assert roll == exp_roll, f"Pinned roll at stage {s_idx}: expected {exp_roll}, got {roll}"
        assert roll > ch, f"Stage {s_idx} must fail: roll={roll} vs chance={ch}"

    # Path B: finale FAILS at day 30
    fin_roll = Oracle(scene_seed(_SEED_PATH_B, f"finale:{lid}", _FINALE_DAY)).d100()
    assert fin_roll == 74, f"Pinned finale roll: expected 74, got {fin_roll}"
    assert fin_roll > FINALE_RESCUE_CHANCE, \
        f"Finale must fail: roll={fin_roll} vs chance={FINALE_RESCUE_CHANCE}"
