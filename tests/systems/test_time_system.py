"""Tests for TimeSystem (Phase D Task 5)."""
from __future__ import annotations

from copy import deepcopy

import pytest

from kernel.clock import advance
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import Fragment, ValidationError
from kernel.turncommit import TurnCommit
from systems.ontology import OntologySystem
from systems.character import CharacterSystem
from systems.time import TimeSystem, normalize_clock, validate_resolved_time


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


def _target_decl(day, band, *, advance=True):
    return [{"advance": advance, "target": {"day": day, "band": band}, "reason": "等到指定时刻"}]


@pytest.mark.parametrize("start,target,delta", [
    ((1, 0), (1, 3), (0, 3)),
    ((4, 1), (4, 3), (0, 2)),
    ((4, 3), (5, 0), (0, 1)),
    ((4, 2), (5, 1), (0, 3)),
    ((4, 2), (5, 2), (1, 0)),
    ((4, 3), (9, 1), (4, 2)),
])
def test_clock_absolute_target_normalizes_to_legacy_delta(start, target, delta):
    world = {"meta": {"day": start[0], "band": start[1]}}
    decl = _target_decl(*target)
    assert TimeSystem().validate("clock", decl, world) == []
    result = normalize_clock(decl, world)
    assert result == [{"advance": True, "days": delta[0], "bands": delta[1], "reason": "等到指定时刻"}]
    assert advance(*start, result[0]["days"], result[0]["bands"]) == target


def test_clock_absolute_target_equal_allows_no_advance():
    world = {"meta": {"day": 4, "band": 2}}
    decl = _target_decl(4, 2, advance=False)
    assert TimeSystem().validate("clock", decl, world) == []
    assert normalize_clock(decl, world) == [
        {"advance": False, "days": 0, "bands": 0, "reason": "等到指定时刻"}]
    assert any(error.code == "bad_advance" for error in
               TimeSystem().validate("clock", _target_decl(4, 2), world))


def test_clock_absolute_target_future_requires_advance():
    world = {"meta": {"day": 4, "band": 2}}
    errors = TimeSystem().validate("clock", _target_decl(4, 3, advance=False), world)
    assert any(error.code == "bad_advance" for error in errors)


@pytest.mark.parametrize("target", [(3, 3), (4, 1)])
def test_clock_absolute_target_rejects_past(target):
    world = {"meta": {"day": 4, "band": 2}}
    errors = TimeSystem().validate("clock", _target_decl(*target), world)
    assert any(error.code == "past_target" for error in errors)
    with pytest.raises(ValueError):
        normalize_clock(_target_decl(*target), world)


@pytest.mark.parametrize("extra", [{"days": 0}, {"bands": 0}, {"days": 0, "bands": 0},
                                  {"days": None}, {"bands": 1}])
def test_clock_absolute_target_rejects_any_delta_key_presence(extra):
    decl = _target_decl(2, 0)
    decl[0].update(extra)
    assert any(error.code == "conflicting_mode" for error in TimeSystem().validate("clock", decl, {}))
    with pytest.raises(ValueError):
        normalize_clock(decl, {})


@pytest.mark.parametrize("target", [None, True, 1, 1.0, "tomorrow", [], {},
    {"day": 2}, {"band": 0}, {"day": True, "band": 0}, {"day": 0, "band": 0},
    {"day": -1, "band": 0}, {"day": 2.0, "band": 0}, {"day": "2", "band": 0},
    {"day": None, "band": 0}, {"day": [], "band": 0}, {"day": 2, "band": True},
    {"day": 2, "band": -1}, {"day": 2, "band": 4}, {"day": 2, "band": 1.0},
    {"day": 2, "band": "1"}, {"day": 2, "band": None}, {"day": 2, "band": {}}])
def test_clock_absolute_target_bad_shapes_are_repairable(target):
    decl = [{"advance": True, "target": target, "reason": "等待"}]
    errors = TimeSystem().validate("clock", decl, {})
    assert errors and all(isinstance(error, ValidationError) for error in errors)
    with pytest.raises(ValueError):
        normalize_clock(decl, {})


@pytest.mark.parametrize("decl", [False, True, 1, 0, 1.0, "clock", "", {}, {"advance": False},
                                 [None], [True], [0], ["clock"], [[]]])
def test_clock_bad_section_shapes_are_repairable(decl):
    errors = TimeSystem().validate("clock", decl, {})
    assert errors and all(isinstance(error, ValidationError) for error in errors)
    with pytest.raises(ValueError):
        normalize_clock(decl, {})


@pytest.mark.parametrize("missing", ["advance", "reason"])
def test_clock_absolute_target_still_requires_advance_and_reason(missing):
    decl = _target_decl(2, 1)
    del decl[0][missing]
    assert any(error.field == f"[0].{missing}" for error in TimeSystem().validate("clock", decl, {}))


@pytest.mark.parametrize("value", [None, 1, "true", []])
def test_clock_absolute_target_rejects_non_bool_advance(value):
    decl = _target_decl(2, 1, advance=value)
    assert any(error.field == "[0].advance" for error in TimeSystem().validate("clock", decl, {}))


def test_clock_normalization_and_validation_are_pure_and_do_not_cache_world():
    decl = _target_decl(7, 1)
    decl[0]["extra"] = {"notes": ["retain"]}
    old_decl = deepcopy(decl)
    world = {"meta": {"day": 4, "band": 3}}
    old_world = deepcopy(world)
    system = TimeSystem()
    assert system.validate("clock", decl, world) == []
    assert system.validate("clock", decl, {"meta": {"day": 8, "band": 0}})
    result = normalize_clock(decl, world)
    assert (result[0]["days"], result[0]["bands"]) == (2, 2)
    result[0]["extra"]["notes"].append("result only")
    assert decl == old_decl
    assert world == old_world
    assert vars(system) == {}


@pytest.mark.parametrize("decl", [
    [{"advance": True, "days": 2, "bands": 9, "reason": "旅途"}],
    [{"advance": False, "reason": "接着交谈"}],
])
def test_clock_normalization_preserves_legacy_declarations(decl):
    result = normalize_clock(decl, {})
    assert result == decl
    assert result is not decl
    assert result[0] is not decl[0]


@pytest.mark.parametrize("decl", [None, []])
def test_clock_normalization_preserves_optional_absence(decl):
    assert normalize_clock(decl, {}) == []
    assert any(error.code == "bad_count" for error in TimeSystem().validate("clock", decl, {}))


def test_clock_to_events_rejects_unnormalized_target():
    with pytest.raises(ValueError, match="normalize_clock"):
        TimeSystem().to_events("clock", _target_decl(2, 1), turn=1, day=2, scene="town")


def test_clock_normalized_target_emits_same_legacy_event():
    world = {"meta": {"day": 4, "band": 3}}
    normalized = normalize_clock(_target_decl(7, 1), world)
    events = TimeSystem().to_events("clock", normalized, turn=3, day=7, scene="town")
    legacy = [{"advance": True, "days": 2, "bands": 2, "reason": "等到指定时刻"}]
    legacy_event = TimeSystem().to_events("clock", legacy, turn=3, day=7, scene="town")[0]
    assert {key: value for key, value in events[0].items() if key != "id"} == {
        key: value for key, value in legacy_event.items() if key != "id"}
    assert "target" not in events[0]["deltas"]
    replayed = project(_reg(), [
        kernel_event("clock_advanced", day=4, scene="town", summary="到夜晚",
                     deltas={"advance": True, "days": 3, "bands": 3, "reason": "旅途"}, turn=2),
        *events,
    ])
    assert (replayed["meta"]["day"], replayed["meta"]["band"]) == (7, 1)


@pytest.mark.parametrize("decl", [
    _target_decl(5, 1),
    [{"advance": True, "days": 0, "bands": 2, "reason": "等到中午"}],
])
def test_resolved_time_accepts_matching_absolute_and_legacy_clock(decl):
    commit = TurnCommit(sections={"clock": decl})
    before = deepcopy(commit)
    world = {"meta": {"day": 4, "band": 3}}
    assert validate_resolved_time(commit, {"day": 5, "band": 1}, world) == []
    assert commit == before


@pytest.mark.parametrize("decl", [
    None, [], {}, [None], ["invalid"],
    _target_decl(5, 2), _target_decl(4, 2),
    [{"advance": True, "days": "2", "bands": 0, "reason": "错误"}],
    [{"advance": False, "days": 0, "bands": 2, "reason": "错误"}],
    [{"advance": True, "target": {"day": 5, "band": 1}, "bands": 0, "reason": "错误"}],
])
def test_resolved_time_rejects_bad_or_mismatched_clock_without_crashing(decl):
    errors = validate_resolved_time(TurnCommit(sections={"clock": decl}),
                                   {"day": 5, "band": 1}, {"meta": {"day": 4, "band": 3}})
    assert errors and all(error.code == "resolved_time" for error in errors)


@pytest.mark.parametrize("target", [False, 3, "later", {}, [], {"day": 5, "band": True}])
def test_resolved_time_bad_target_is_repairable(target):
    errors = validate_resolved_time(TurnCommit(sections={"clock": _target_decl(5, 1)}), target, {})
    assert errors and all(error.code == "resolved_time" for error in errors)


@pytest.mark.parametrize("commit", [None, {}, TurnCommit(sections=None), TurnCommit(sections=[])])
def test_resolved_time_bad_commit_is_repairable(commit):
    errors = validate_resolved_time(commit, {"day": 5, "band": 1}, {})
    assert errors and all(error.code == "resolved_time" for error in errors)


def test_resolved_time_absent_constraint_does_not_validate_clock():
    assert validate_resolved_time(None, None, None) == []


def test_resolved_time_accepts_current_endpoint_without_advance():
    decl = _target_decl(4, 3, advance=False)
    assert validate_resolved_time(TurnCommit(sections={"clock": decl}),
                                  {"day": 4, "band": 3}, {"meta": {"day": 4, "band": 3}}) == []


def test_clock_inject_explains_absolute_target_and_duration_modes():
    affordance = TimeSystem().inject({}, {"meta": {"day": 4, "band": 1}}).affordance
    assert '"target"' in affordance
    assert "绝对" in affordance and "互斥" in affordance and "时长" in affordance
