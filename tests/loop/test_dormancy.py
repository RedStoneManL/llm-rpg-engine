"""tests.loop.test_dormancy — ★6 dormancy gate for simple/medium 暗 lines.

Rule (per spec):
- simple/medium 暗 lines anchored to town A are DORMANT when the protagonist is
  NOT in town A → run_lore SKIPS the 暗骰 advance (no lore_advanced) but still
  runs lifespan-expiry (quest_expired).
- complex 暗 lines are NEVER dormant — they brew even when the player is away.
- protagonist in the anchor town → always advances (not dormant).
- protagonist off-graph (no location) → all simple/medium treated as dormant;
  expiry still runs.

Tests:
  1. simple 暗 anchored to town_a, protagonist in town_b → NOT advanced
  2. simple 暗 anchored to town_a, protagonist in town_a → IS advanced
  3. complex 暗 anchored to town_a, protagonist in town_b → IS advanced (region-scale)
  4. dormant simple line with elapsed lifespan → quest_expired still fires (expiry not gated)
  5. medium 暗 anchored to town_a, protagonist in town_b → NOT advanced
  6. protagonist off-graph (no located_in edge) → simple/medium dormant, not advanced
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
from systems.character import CharacterSystem
from systems.lore import LoreSystem
from loop.lore import create_lore_line, run_lore


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


def _place_ev(pid, level, parent=None):
    d = {"id": pid, "level": level, "kind": "settlement", "seed": pid, "tier": "tracked"}
    if parent:
        d["parent"] = parent
    return kernel_event("place_created", day=1, scene="s1", summary=pid, deltas=d, turn=0)


def _char_ev(cid):
    return kernel_event(
        "character_created", day=1, scene="s1", summary=cid,
        deltas={"id": cid, "tier": "tracked", "sketch": "a", "goal": "b"}, turn=0
    )


def _move_ev(cid, place):
    return kernel_event(
        "entity_moved", day=1, scene="s1", summary="move",
        deltas={"who": cid, "to": place}, turn=0
    )


def _skeleton(lid, complexity, anchor, threshold=100):
    """Build a minimal lore line skeleton with deterministic threshold."""
    return {
        "id": lid,
        "complexity": complexity,
        "about": f"test line {lid}",
        "secret": "hidden",
        "anchor": anchor,
        "description": f"desc {lid}",
        "trigger": "trigger",
        "l3_anchor": f"{anchor}_venue",
        "stages": [{"hint": "h0"}, {"hint": "h1"}, {"hint": "h2"}],
        "threshold": threshold,  # 100 → always advances; 0 → never advances
    }


def _base_world(store, reg, *, hero_location, campaign_seed=0):
    """
    Seed world:
      L1: region1
      L2: town_a (in region1), town_b (in region1)
      L3: town_a_venue (in town_a), town_b_venue (in town_b)
      Person: hero (tracked), located_in hero_location at day=1

    Returns projected world with campaign_seed injected.
    """
    evs = [
        _place_ev("region1", 1),
        _place_ev("town_a", 2, parent="region1"),
        _place_ev("town_b", 2, parent="region1"),
        _place_ev("town_a_venue", 3, parent="town_a"),
        _place_ev("town_b_venue", 3, parent="town_b"),
        _char_ev("hero"),
    ]
    for ev in evs:
        store.append(ev)

    if hero_location is not None:
        store.append(_move_ev("hero", hero_location))

    w = project(reg, store.iter_events())
    w.setdefault("meta", {})["campaign_seed"] = campaign_seed
    w.setdefault("meta", {})["scene"] = "s1"
    w.setdefault("meta", {})["day"] = 1
    return w


# ---------------------------------------------------------------------------
# Test 1: simple line anchored to town_a, protagonist in town_b → NOT advanced
# ---------------------------------------------------------------------------

def test_simple_dormant_when_protagonist_in_different_town():
    """Protagonist in town_b; simple line anchored to town_a → dormant → no lore_advanced."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_b")
    create_lore_line(s, _skeleton("dormant_simple", "simple", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" not in ev_types, (
        f"Dormant simple line should NOT advance when protagonist is in a different town; "
        f"got events: {ev_types}"
    )


def test_simple_dormant_stage_idx_unchanged():
    """Protagonist in town_b; simple line anchored to town_a — stage_idx stays at -1."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_b")
    create_lore_line(s, _skeleton("dormant_simple2", "simple", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    run_lore(r, s, w)
    w2 = project(r, s.iter_events())
    ln = w2["systems"]["lore"]["lines"]["dormant_simple2"]
    assert ln["stage_idx"] == -1, (
        f"Dormant line stage_idx should remain -1; got {ln['stage_idx']}"
    )


# ---------------------------------------------------------------------------
# Test 2: simple line anchored to town_a, protagonist in town_a → IS advanced
# ---------------------------------------------------------------------------

def test_simple_advances_when_protagonist_in_anchor_town():
    """Protagonist in town_a; simple line anchored to town_a → NOT dormant → lore_advanced."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_a")
    create_lore_line(s, _skeleton("active_simple", "simple", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" in ev_types, (
        f"Simple line should advance when protagonist is in the anchor town; "
        f"got events: {ev_types}"
    )


def test_simple_advances_when_protagonist_in_l3_venue_of_anchor():
    """Protagonist in town_a_venue (L3 inside town_a); simple line anchored to town_a
    → cur_town resolves to town_a → NOT dormant → lore_advanced."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_a_venue")
    create_lore_line(s, _skeleton("active_simple_l3", "simple", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" in ev_types, (
        f"Simple line should advance when protagonist is in an L3 venue of the anchor town; "
        f"got events: {ev_types}"
    )


# ---------------------------------------------------------------------------
# Test 3: complex line anchored to town_a, protagonist in town_b → STILL advances
# ---------------------------------------------------------------------------

def test_complex_advances_even_when_protagonist_in_different_town():
    """Complex 暗 line is region-scale — never dormant regardless of location."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_b")
    create_lore_line(s, _skeleton("region_complex", "complex", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" in ev_types, (
        f"Complex line should advance even when protagonist is in a different town; "
        f"got events: {ev_types}"
    )


# ---------------------------------------------------------------------------
# Test 4: dormant simple line with elapsed lifespan → quest_expired still fires
# ---------------------------------------------------------------------------

def test_dormant_simple_still_expires_on_elapsed_lifespan():
    """Dormant gate must NOT block expiry: quest_expired fires even when line is dormant."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_b", campaign_seed=0)

    # Create a simple line anchored to town_a with lifespan of 5 days
    skel = _skeleton("expiring_dormant", "simple", "town_a", threshold=100)
    create_lore_line(s, skel, day=1, scene="s1", turn=1, lifespan_days=5)

    # Project world at day=10 (well past lifespan)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 10  # day 10 - born_day 1 = 9 >= lifespan 5

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "quest_expired" in ev_types, (
        f"Dormant line with elapsed lifespan must still expire; got events: {ev_types}"
    )
    # But the advance should not have happened before expiry branch ran
    # (expiry fires and continues, skipping advance anyway in the existing flow)


# ---------------------------------------------------------------------------
# Test 5: medium line anchored to town_a, protagonist in town_b → NOT advanced
# ---------------------------------------------------------------------------

def test_medium_dormant_when_protagonist_in_different_town():
    """Medium 暗 lines follow the same dormancy rule as simple lines."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_b")
    create_lore_line(s, _skeleton("dormant_medium", "medium", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" not in ev_types, (
        f"Dormant medium line should NOT advance when protagonist is in a different town; "
        f"got events: {ev_types}"
    )


def test_medium_advances_when_protagonist_in_anchor_town():
    """Medium line active when protagonist is in its anchor town."""
    r = _reg()
    s = _store(r)
    w = _base_world(s, r, hero_location="town_a")
    create_lore_line(s, _skeleton("active_medium", "medium", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" in ev_types, (
        f"Medium line should advance when protagonist is in the anchor town; "
        f"got events: {ev_types}"
    )


# ---------------------------------------------------------------------------
# Test 6: protagonist off-graph (no located_in edge) → simple/medium dormant
# ---------------------------------------------------------------------------

def test_simple_dormant_when_protagonist_has_no_location():
    """When protagonist has no located_in edge, cur_town is None.
    Defensive rule: all simple/medium lines are dormant → no advance."""
    r = _reg()
    s = _store(r)
    # Don't add a move event → hero has no location
    w = _base_world(s, r, hero_location=None)
    create_lore_line(s, _skeleton("noloc_simple", "simple", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" not in ev_types, (
        f"Simple line should be dormant when protagonist has no location; "
        f"got events: {ev_types}"
    )


def test_complex_advances_when_protagonist_has_no_location():
    """Complex lines are never dormant — even off-graph protagonist doesn't freeze them."""
    r = _reg()
    s = _store(r)
    # Don't add a move event → hero has no location
    w = _base_world(s, r, hero_location=None)
    create_lore_line(s, _skeleton("noloc_complex", "complex", "town_a", threshold=100),
                     day=1, scene="s1", turn=1)
    w = project(r, s.iter_events())
    w["meta"]["campaign_seed"] = 0
    w["meta"]["scene"] = "s1"
    w["meta"]["day"] = 1

    appended = run_lore(r, s, w)
    ev_types = [e["type"] for e in appended]

    assert "lore_advanced" in ev_types, (
        f"Complex line should advance even when protagonist has no location; "
        f"got events: {ev_types}"
    )
