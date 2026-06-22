"""Tests for CascadeSystem (Phase C1)."""
from __future__ import annotations

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.cascade import CascadeSystem


def _reg():
    return (Registry().register(OntologySystem())
            .register(PlaceSystem()).register(CascadeSystem()))


def test_cascade_owns_event_types():
    cs = CascadeSystem()
    assert cs.name == "cascade"
    assert cs.event_types() == {"place_evolved", "populace_shifted", "world_change"}
    # P1: CascadeSystem now owns the LLM-authored `world` commit section.
    assert cs.commit_sections() == {"world"}


def test_world_section_routed_to_cascade():
    reg = _reg()
    owner = reg.owner_of_section("world")
    assert owner is not None and owner.name == "cascade"


def test_cascade_requires_ontology_and_registers():
    reg = _reg()
    assert "cascade" in {s.name for s in reg.systems}
    assert reg.owner_of_event("place_evolved").name == "cascade"
    assert reg.owner_of_event("world_change").name == "cascade"


def test_empty_state_shape():
    assert CascadeSystem().empty_state() == {
        "queue": [], "changes": [], "consumed_through_turn": 0,
    }


# ---------------------------------------------------------------------------
# Task 2: CascadeSystem.apply
# ---------------------------------------------------------------------------

def _place(pid, parent=None, day=1):
    d = {"id": pid, "level": 3, "kind": "venue", "seed": "x", "tier": "tracked"}
    if parent:
        d["parent"] = parent
    return kernel_event("place_created", day=day, scene="s1",
                        summary=f"{pid} 创建", deltas=d, turn=1)


def test_place_evolved_asserts_state_fact():
    reg = _reg()
    world = project(reg, [
        _place("market"),
        kernel_event("place_evolved", day=2, scene="s1", summary="market 演化",
                     deltas={"id": "market", "state": "戒严", "note": "卫兵封锁"}, turn=2),
    ])
    g = world["systems"]["ontology"]
    assert g.value_at("market", "state", day=2) == "戒严"
    assert g.get_entity("market").attrs.get("last_cascade_turn") == 2


def test_populace_shifted_asserts_mood_fact():
    reg = _reg()
    world = project(reg, [
        _place("market"),
        kernel_event("populace_shifted", day=2, scene="s1", summary="民心",
                     deltas={"id": "market", "mood": "惶恐"}, turn=2),
    ])
    assert world["systems"]["ontology"].value_at("market", "populace", day=2) == "惶恐"


def test_world_change_records_audit_and_fact():
    reg = _reg()
    world = project(reg, [
        _place("capital"),
        kernel_event("world_change", day=2, scene="s1", summary="王都陷落",
                     deltas={"place": "capital", "level": 1, "valence": "disaster"}, turn=2),
    ])
    slice_ = world["systems"]["cascade"]
    assert len(slice_["changes"]) == 1
    assert slice_["changes"][0]["place"] == "capital" and slice_["changes"][0]["level"] == 1
    assert world["systems"]["ontology"].value_at("capital", "world_change", day=2) == "王都陷落"


def test_apply_defensive_on_missing_id():
    reg = _reg()
    # missing id / dangling id must NOT crash projection (invariant 11)
    world = project(reg, [
        kernel_event("place_evolved", day=1, scene="s1", summary="bad",
                     deltas={"state": "x"}, turn=1),          # no id
        kernel_event("populace_shifted", day=1, scene="s1", summary="bad",
                     deltas={"id": "ghost", "mood": "y"}, turn=1),  # dangling id
    ])
    assert world is not None  # did not raise


# ---------------------------------------------------------------------------
# Task 10: CascadeSystem.apply — deferred queue + consume watermark (C2)
# ---------------------------------------------------------------------------

def test_world_change_deferred_marker_enqueues():
    reg = _reg()
    world = project(reg, [
        _place("capital"),
        kernel_event("world_change", day=2, scene="s1", summary="远区波及",
                     deltas={"place": "capital", "deferred": True, "level": 2,
                             "reason": "remote", "depth": 2}, turn=3),
    ])
    q = world["systems"]["cascade"]["queue"]
    assert any(e["region"] == "capital" and e["consumed"] is False
               and e["enqueue_turn"] == 3 for e in q)


def test_world_change_consume_watermark_sets_through_turn():
    reg = _reg()
    world = project(reg, [
        _place("capital"),
        kernel_event("world_change", day=2, scene="s1", summary="drain bookkeeping",
                     deltas={"place": "capital", "deferred_consume_through": 5}, turn=6),
    ])
    assert world["systems"]["cascade"]["consumed_through_turn"] == 5


# ---------------------------------------------------------------------------
# Phase D Task 1: last_update stamp in CascadeSystem.apply
# ---------------------------------------------------------------------------

def test_place_evolved_stamps_last_update():
    reg = _reg()
    world = project(reg, [
        _place("market"),
        kernel_event("place_evolved", day=2, scene="s1", summary="market 演化",
                     deltas={"id": "market", "state": "戒严", "note": "卫兵封锁"}, turn=2),
    ])
    g = world["systems"]["ontology"]
    assert g.get_entity("market").attrs.get("last_update") == 2


# ---------------------------------------------------------------------------
# P1 Task 2: CascadeSystem.validate for the `world` section
# ---------------------------------------------------------------------------

def _world_world(reg, *places):
    """Project a world with the given Place ids, return the world dict."""
    return project(reg, [_place(p) for p in places])


def test_validate_world_accepts_good_item():
    reg = _reg()
    world = _world_world(reg, "capital", "harbor")
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "harbor"], "level": 1, "summary": "王都陷落"}]
    assert cs.validate("world", decl, world) == []


def test_validate_world_flags_dangling_area():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "ghost_town"], "level": 1, "summary": "战火蔓延"}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "dangling_ref" and "ghost_town" in e.hint for e in errs)


def test_validate_world_flags_bad_level():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": ["capital"], "level": 9, "summary": "x"}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "bad_enum" and e.field == "[0].level" for e in errs)


def test_validate_world_flags_missing_summary():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": ["capital"], "level": 1, "summary": ""}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "missing" and e.field == "[0].summary" for e in errs)


def test_validate_world_flags_empty_areas():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": [], "level": 1, "summary": "x"}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "missing" and e.field == "[0].areas" for e in errs)


def test_validate_world_resolves_same_commit_place():
    """A place created THIS commit is stubbed into the graph by the strict gate,
    so an area referencing it must validate (mirror of place/move cross-section)."""
    reg = _reg()
    world = _world_world(reg, "capital")
    g = world["systems"]["ontology"]
    g.add_entity("new_region", "_pending")     # simulate the gate's stub
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "new_region"], "level": 2, "summary": "扩散"}]
    assert cs.validate("world", decl, world) == []


def test_validate_world_ignores_other_sections():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    assert cs.validate("knowledge", [{"op": "told"}], world) == []


# ---------------------------------------------------------------------------
# P1 Task 3: CascadeSystem.to_events — one world_change per area
# ---------------------------------------------------------------------------

def test_to_events_world_emits_one_per_area():
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "harbor", "farms"], "level": 1, "summary": "王都陷落"}]
    evs = cs.to_events("world", decl, turn=5, day=3, scene="s1")
    assert len(evs) == 3
    assert all(e["type"] == "world_change" for e in evs)
    places = {e["deltas"]["place"] for e in evs}
    assert places == {"capital", "harbor", "farms"}
    for e in evs:
        assert e["deltas"]["level"] == 1
        assert e["deltas"]["summary"] == "王都陷落"
        assert e["summary"] == "王都陷落"
        assert e["turn"] == 5 and e["day"] == 3


def test_to_events_world_multiple_items_flattened():
    cs = CascadeSystem()
    decl = [
        {"areas": ["a"], "level": 1, "summary": "地震"},
        {"areas": ["b", "c"], "level": 2, "summary": "瘟疫"},
    ]
    evs = cs.to_events("world", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 3
    by_place = {e["deltas"]["place"]: e["deltas"] for e in evs}
    assert by_place["a"]["summary"] == "地震" and by_place["a"]["level"] == 1
    assert by_place["b"]["summary"] == "瘟疫" and by_place["c"]["level"] == 2


def test_to_events_non_world_section_empty():
    cs = CascadeSystem()
    assert cs.to_events("knowledge", [{"op": "told"}], turn=1, day=1, scene="s1") == []


def test_to_events_world_roundtrips_through_apply():
    """Emitted world_change events project cleanly via the existing apply branch:
    each area gets an audit entry + a world_change fact."""
    reg = _reg()
    base = [_place("capital"), _place("harbor")]
    cs = CascadeSystem()
    evs = cs.to_events("world", [{"areas": ["capital", "harbor"], "level": 1,
                                  "summary": "陷落"}], turn=2, day=1, scene="s1")
    world = project(reg, base + evs)
    g = world["systems"]["ontology"]
    assert g.value_at("capital", "world_change", day=1) == "陷落"
    assert g.value_at("harbor", "world_change", day=1) == "陷落"
    changes = world["systems"]["cascade"]["changes"]
    assert {c["place"] for c in changes} == {"capital", "harbor"}
