"""tests/systems/test_lore_gen_state.py — LoreSystem gen sub-state (Task 3A).

Tests that lore_seeded and density_refreshed events correctly fold into
world["systems"]["lore"]["gen"] and that re-applying is idempotent.
"""
from __future__ import annotations

import os
import tempfile

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event, open_store
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.lore import LoreSystem


# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_lore_seeded_sets_seeded_and_last_refresh_day():
    """lore_seeded event sets gen[town].seeded=True and initialises last_refresh_day."""
    r = _reg()
    store = _store(r)
    ev = kernel_event(
        "lore_seeded", day=5, scene="s1",
        summary="seeded town1",
        deltas={"town": "town1"},
        turn=1,
    )
    store.append(ev)
    world = project(r, store.iter_events())
    gen = world["systems"]["lore"]["gen"]
    assert "town1" in gen
    assert gen["town1"]["seeded"] is True
    assert gen["town1"]["last_refresh_day"] == 5


def test_density_refreshed_updates_last_refresh_day():
    """density_refreshed event updates gen[town].last_refresh_day."""
    r = _reg()
    store = _store(r)
    # First seed the town
    store.append(kernel_event(
        "lore_seeded", day=1, scene="s1",
        summary="seeded town1",
        deltas={"town": "town1"},
        turn=1,
    ))
    # Then refresh 3 days later
    store.append(kernel_event(
        "density_refreshed", day=4, scene="s1",
        summary="refreshed town1",
        deltas={"town": "town1"},
        turn=2,
    ))
    world = project(r, store.iter_events())
    gen = world["systems"]["lore"]["gen"]
    assert gen["town1"]["seeded"] is True
    assert gen["town1"]["last_refresh_day"] == 4


def test_density_refreshed_without_prior_seed():
    """density_refreshed on an unseeded town just sets last_refresh_day (no seeded key)."""
    r = _reg()
    store = _store(r)
    store.append(kernel_event(
        "density_refreshed", day=7, scene="s1",
        summary="refreshed town2",
        deltas={"town": "town2"},
        turn=1,
    ))
    world = project(r, store.iter_events())
    gen = world["systems"]["lore"]["gen"]
    assert gen["town2"]["last_refresh_day"] == 7
    # seeded key was never set
    assert "seeded" not in gen["town2"]


def test_lore_seeded_is_idempotent():
    """Replaying lore_seeded twice produces the same result (replay-safe)."""
    r = _reg()
    store = _store(r)
    ev = kernel_event(
        "lore_seeded", day=3, scene="s1",
        summary="seeded town1",
        deltas={"town": "town1"},
        turn=1,
    )
    store.append(ev)
    # project once
    world1 = project(r, store.iter_events())
    # replay the same event (simulate rewind by re-projecting from scratch;
    # append a duplicate and project — both events produce the same final state)
    store.append(kernel_event(
        "lore_seeded", day=3, scene="s1",
        summary="seeded town1 (duplicate)",
        deltas={"town": "town1"},
        turn=2,
    ))
    world2 = project(r, store.iter_events())
    gen1 = world1["systems"]["lore"]["gen"]["town1"]
    gen2 = world2["systems"]["lore"]["gen"]["town1"]
    # seeded is True in both; last_refresh_day unchanged
    assert gen2["seeded"] is True
    assert gen2["last_refresh_day"] == gen1["last_refresh_day"] == 3


def test_multiple_towns_independent():
    """gen state tracks each town independently."""
    r = _reg()
    store = _store(r)
    store.append(kernel_event(
        "lore_seeded", day=1, scene="s1",
        summary="seeded town_a",
        deltas={"town": "town_a"},
        turn=1,
    ))
    store.append(kernel_event(
        "lore_seeded", day=2, scene="s1",
        summary="seeded town_b",
        deltas={"town": "town_b"},
        turn=2,
    ))
    store.append(kernel_event(
        "density_refreshed", day=5, scene="s1",
        summary="refreshed town_a",
        deltas={"town": "town_a"},
        turn=3,
    ))
    world = project(r, store.iter_events())
    gen = world["systems"]["lore"]["gen"]
    assert gen["town_a"]["seeded"] is True
    assert gen["town_a"]["last_refresh_day"] == 5
    assert gen["town_b"]["seeded"] is True
    assert gen["town_b"]["last_refresh_day"] == 2
