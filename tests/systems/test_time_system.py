"""Tests for TimeSystem (Phase D Task 5)."""
from __future__ import annotations

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import Fragment
from systems.ontology import OntologySystem
from systems.character import CharacterSystem
from systems.time import TimeSystem


def _reg():
    return (Registry().register(OntologySystem())
            .register(CharacterSystem()).register(TimeSystem()))


def test_timesystem_registers_and_owns_event():
    reg = _reg()
    ts = TimeSystem()
    assert "time_advanced" in ts.event_types()
    assert "clock_advanced" in ts.event_types()
    assert ts.commit_sections() == {"clock"}


def test_time_advanced_scoped_bumps_last_update_only():
    reg = _reg()
    world = project(reg, [
        kernel_event("character_created", day=1, scene="s", summary="登场",
                     deltas={"id": "npc", "tier": "tracked",
                             "sketch": "守桥人", "goal": "守桥"}, turn=1),
        kernel_event("time_advanced", day=5, scene="s", summary="时间流逝",
                     deltas={"id": "npc", "to_day": 5, "reason": "catchup-noop"},
                     turn=2),
    ])
    g = world["systems"]["ontology"]
    assert g.get_entity("npc").attrs.get("last_update") == 5
    assert g.value_at("npc", "mood", 5) is None


def test_time_advanced_unscoped_does_not_crash():
    reg = _reg()
    world = project(reg, [
        kernel_event("time_advanced", day=3, scene="s", summary="三天后",
                     deltas={"to_day": 3, "reason": "elapse"}, turn=1),
    ])
    assert world["meta"]["day"] == 3


def test_clock_validate_accepts_well_formed_advance():
    ts = TimeSystem()
    decl = [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}]
    assert ts.validate("clock", decl, {}) == []


def test_clock_validate_accepts_well_formed_non_advance():
    ts = TimeSystem()
    decl = [{"advance": False, "days": 0, "bands": 0, "reason": "紧接上一刻"}]
    assert ts.validate("clock", decl, {}) == []


def test_clock_validate_rejects_wrong_element_count():
    ts = TimeSystem()
    errs = ts.validate("clock", [], {})
    assert any(e.code == "bad_count" for e in errs)
    errs2 = ts.validate("clock", [{"advance": False, "days": 0, "bands": 0, "reason": "a"},
                                   {"advance": False, "days": 0, "bands": 0, "reason": "b"}], {})
    assert any(e.code == "bad_count" for e in errs2)


def test_clock_validate_requires_reason():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": True, "days": 1, "bands": 0, "reason": "  "}], {})
    assert any(e.field == "[0].reason" for e in errs)


def test_clock_validate_requires_bool_advance():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"days": 0, "bands": 0, "reason": "x"}], {})
    assert any(e.field == "[0].advance" for e in errs)


def test_clock_validate_rejects_bool_as_int():
    """bool is a subclass of int in Python (isinstance(True, int) == True);
    the validator must reject bool values for days/bands fields."""
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": True, "days": True, "bands": 0, "reason": "x"}], {})
    assert any(e.field == "[0].days" for e in errs)


def test_clock_validate_rejects_negative_amounts():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": True, "days": -1, "bands": 0, "reason": "x"}], {})
    assert any(e.field == "[0].days" for e in errs)


def test_clock_validate_advance_true_needs_nonzero_amount():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": True, "days": 0, "bands": 0, "reason": "x"}], {})
    assert any(e.code == "bad_advance" for e in errs)


def test_clock_validate_advance_false_needs_zero_amount():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": False, "days": 1, "bands": 0, "reason": "x"}], {})
    assert any(e.code == "bad_advance" for e in errs)


def test_clock_to_events_emits_clock_advanced():
    ts = TimeSystem()
    decl = [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}]
    evs = ts.to_events("clock", decl, turn=1, day=3, scene="s1")
    assert len(evs) == 1
    ev = evs[0]
    assert ev["type"] == "clock_advanced"
    assert ev["day"] == 3
    assert ev["deltas"] == {"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}


def test_clock_apply_folds_band_only():
    ts = TimeSystem()
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    ev = ts.to_events("clock", [{"advance": True, "days": 5, "bands": 2, "reason": "x"}],
                      turn=1, day=6, scene="s")[0]
    ts.apply(world, ev)
    # band only depends on dbands: 晨(0)+2 -> 下午(2). days do not move band.
    assert world["meta"]["band"] == 2


def test_clock_apply_band_wraps():
    ts = TimeSystem()
    world = {"meta": {"day": 1, "band": 3}, "systems": {}}
    ev = ts.to_events("clock", [{"advance": True, "days": 0, "bands": 1, "reason": "x"}],
                      turn=1, day=2, scene="s")[0]
    ts.apply(world, ev)
    assert world["meta"]["band"] == 0   # 夜晚(3)+1 -> 晨(0)


def test_clock_inject_shows_current_clock():
    ts = TimeSystem()
    world = {"meta": {"day": 4, "band": 1}, "systems": {}}
    frag = ts.inject({}, world)
    assert isinstance(frag, Fragment)
    assert frag.layer == "scene"
    assert "第 4 天" in frag.text
    assert "中午" in frag.text
    assert "clock" in frag.affordance
