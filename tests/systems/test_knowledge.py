"""Tests for KnowledgeSystem — Tasks 1 and 2 (TDD)."""
from __future__ import annotations

import pytest
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import ValidationError, Fragment
from systems.ontology import OntologySystem
from systems.character import CharacterSystem
from systems.place import PlaceSystem
from systems.faction import FactionSystem
from systems.knowledge import KnowledgeSystem, knows, knowers_of
from facts.graph import FactGraph


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reg():
    return (
        Registry()
        .register(OntologySystem())
        .register(CharacterSystem())
        .register(PlaceSystem())
        .register(FactionSystem())
        .register(KnowledgeSystem())
    )


def _ev(typ, day=1, scene="s1", **deltas):
    return kernel_event(typ, day=day, scene=scene, summary=f"{typ}", deltas=deltas)


def _world():
    r = _reg()
    return project(r, [])


def _graph_with_persons(*ids):
    """Return a FactGraph with given Person entities pre-added."""
    g = FactGraph()
    for eid in ids:
        g.add_entity(eid, "Person")
    return g


# ---------------------------------------------------------------------------
# Task 1: apply — knowledge_set
# ---------------------------------------------------------------------------

def test_knowledge_set_stores_knows_fact():
    """A knowledge_set event makes the knower's knows:{fact_key} fact."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    ev = _ev("knowledge_set", day=1, knower="艾拉", fact_key="桥.status", value="毁坏")
    ks.apply(world, ev)

    assert knows(g, "艾拉", "桥.status", day=1) == "毁坏"


def test_knowledge_set_supersedes_old_belief():
    """Re-learning a different value supersedes (stale-belief via point-in-time)."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    ev1 = _ev("knowledge_set", day=1, knower="艾拉", fact_key="桥.status", value="完好")
    ev2 = _ev("knowledge_set", day=5, knower="艾拉", fact_key="桥.status", value="毁坏")
    ks.apply(world, ev1)
    ks.apply(world, ev2)

    # point-in-time: old belief at day 1 was "完好"
    assert knows(g, "艾拉", "桥.status", day=1) == "完好"
    # new belief at day 5 is "毁坏"
    assert knows(g, "艾拉", "桥.status", day=5) == "毁坏"


def test_knows_returns_none_when_ungranted():
    """knows() returns None when the knower has not been granted the fact."""
    g = _graph_with_persons("艾拉")
    assert knows(g, "艾拉", "桥.status", day=1) is None


def test_knowers_of_lists_current_knowers():
    """knowers_of() returns all entities that currently know the fact_key."""
    g = _graph_with_persons("艾拉", "王子")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    ev1 = _ev("knowledge_set", day=1, knower="艾拉", fact_key="桥.status", value="毁坏")
    ev2 = _ev("knowledge_set", day=1, knower="王子", fact_key="桥.status", value="毁坏")
    ks.apply(world, ev1)
    ks.apply(world, ev2)

    result = knowers_of(g, "桥.status", day=1)
    assert set(result) == {"艾拉", "王子"}


def test_knowers_of_excludes_expired_belief():
    """knowers_of excludes entities whose belief was superseded before the query day."""
    g = _graph_with_persons("艾拉", "王子")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    # 艾拉 learns on day 1, superseded on day 3 (new belief); query at day 2 she still knows
    ev1 = _ev("knowledge_set", day=1, knower="艾拉", fact_key="桥.status", value="完好")
    ev2 = _ev("knowledge_set", day=3, knower="艾拉", fact_key="桥.status", value="毁坏")
    # 王子 learns on day 1 and never updates
    ev3 = _ev("knowledge_set", day=1, knower="王子", fact_key="桥.status", value="完好")
    ks.apply(world, ev1)
    ks.apply(world, ev2)
    ks.apply(world, ev3)

    # At day 2: 艾拉 believes "完好", 王子 believes "完好" — both are knowers
    result_d2 = knowers_of(g, "桥.status", day=2)
    assert "艾拉" in result_d2
    assert "王子" in result_d2

    # At day 3: 艾拉 now believes "毁坏" (still a knower), 王子 believes "完好"
    result_d3 = knowers_of(g, "桥.status", day=3)
    assert "艾拉" in result_d3
    assert "王子" in result_d3


def test_knowers_of_empty_when_none_know():
    """knowers_of returns [] when no one has been told the fact."""
    g = _graph_with_persons("艾拉")
    result = knowers_of(g, "反派身份", day=1)
    assert result == []


# ---------------------------------------------------------------------------
# Task 1: validate — "knowledge" section
# ---------------------------------------------------------------------------

def test_validate_told_missing_knower_entity():
    """validate: told with knower entity not in graph → dangling_ref."""
    g = FactGraph()  # empty graph — no entities
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{"op": "told", "knower": "不存在人", "fact_key": "桥.status", "value": "毁坏"}]
    errs = ks.validate("knowledge", decl, world)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_told_valid_no_errors():
    """validate: told with valid knower → no errors."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{"op": "told", "knower": "艾拉", "fact_key": "桥.status", "value": "毁坏"}]
    errs = ks.validate("knowledge", decl, world)
    assert errs == []


def test_validate_wrong_section_no_errors():
    """validate: non-knowledge section → always empty."""
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": FactGraph(), "knowledge": {}}}
    errs = ks.validate("cast", [{"op": "told", "knower": "nobody"}], world)
    assert errs == []


def test_validate_endowment_missing_knower():
    """validate: endowment with knower entity not in graph → dangling_ref."""
    g = FactGraph()
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{
        "op": "endowment",
        "knower": "不存在人",
        "grants": [{"fact_key": "桥.status", "value": "完好"}],
    }]
    errs = ks.validate("knowledge", decl, world)
    assert any(e.code == "dangling_ref" for e in errs)


# ---------------------------------------------------------------------------
# Task 1: to_events — told and endowment
# ---------------------------------------------------------------------------

def test_to_events_told_one_event():
    """to_events: a told item → exactly one knowledge_set event."""
    ks = KnowledgeSystem()
    decl = [{"op": "told", "knower": "艾拉", "fact_key": "桥.status", "value": "毁坏", "via": "NPC"}]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "knowledge_set"
    assert evs[0]["deltas"]["knower"] == "艾拉"
    assert evs[0]["deltas"]["fact_key"] == "桥.status"
    assert evs[0]["deltas"]["value"] == "毁坏"


def test_to_events_endowment_n_events():
    """to_events: an endowment item with N grants → N knowledge_set events."""
    ks = KnowledgeSystem()
    decl = [{
        "op": "endowment",
        "knower": "艾拉",
        "grants": [
            {"fact_key": "桥.status", "value": "完好"},
            {"fact_key": "反派身份", "value": "影主"},
        ],
    }]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 2
    for ev in evs:
        assert ev["type"] == "knowledge_set"
        assert ev["deltas"]["knower"] == "艾拉"

    fact_keys = {ev["deltas"]["fact_key"] for ev in evs}
    assert fact_keys == {"桥.status", "反派身份"}


def test_to_events_unknown_section_empty():
    """to_events: non-knowledge section → []."""
    ks = KnowledgeSystem()
    decl = [{"op": "told", "knower": "艾拉", "fact_key": "x", "value": "y"}]
    evs = ks.to_events("cast", decl, turn=1, day=1, scene="s1")
    assert evs == []


# ---------------------------------------------------------------------------
# Task 2: knowledge_broadcast — faction audience
# ---------------------------------------------------------------------------

def _setup_faction_world():
    """Set up a world with faction 龙之会 and three members at different ranks.

    Returns (FactGraph, world) with:
      - 龙之会: ranks=["学徒","正式","资深","会长"]
      - 甲: rank=资深 (senior, index 2)
      - 乙: rank=正式 (mid, index 1)
      - 丙: rank=学徒 (junior, index 0)
    All members joined on day=1.
    """
    from systems.faction import FactionSystem

    g = FactGraph()
    g.add_entity("龙之会", "Faction", tier="tracked",
                 ranks=["学徒", "正式", "资深", "会长"], groups=[])
    g.add_entity("甲", "Person")
    g.add_entity("乙", "Person")
    g.add_entity("丙", "Person")
    world = {"systems": {"ontology": g, "knowledge": {}, "faction": {}}}

    fs = FactionSystem()
    fs.apply(world, _ev("member_changed", day=1, person="甲", faction="龙之会", rank="资深"))
    fs.apply(world, _ev("member_changed", day=1, person="乙", faction="龙之会", rank="正式"))
    fs.apply(world, _ev("member_changed", day=1, person="丙", faction="龙之会", rank="学徒"))
    return g, world


def test_broadcast_faction_all_members():
    """broadcast with faction audience (no min_rank) → all members know the fact."""
    g, world = _setup_faction_world()
    ks = KnowledgeSystem()

    ev = _ev("knowledge_broadcast", day=2,
             fact_key="密谋", value="反叛",
             audience={"faction": "龙之会"})
    ks.apply(world, ev)

    assert knows(g, "甲", "密谋", day=2) == "反叛"
    assert knows(g, "乙", "密谋", day=2) == "反叛"
    assert knows(g, "丙", "密谋", day=2) == "反叛"


def test_broadcast_faction_min_rank_filters():
    """broadcast with min_rank=资深 → only senior members (甲) know the fact."""
    g, world = _setup_faction_world()
    ks = KnowledgeSystem()

    ev = _ev("knowledge_broadcast", day=2,
             fact_key="秘密计划", value="夺权",
             audience={"faction": "龙之会", "min_rank": "资深"})
    ks.apply(world, ev)

    # 甲 is 资深 (index 2) → included
    assert knows(g, "甲", "秘密计划", day=2) == "夺权"
    # 乙 is 正式 (index 1) → excluded
    assert knows(g, "乙", "秘密计划", day=2) is None
    # 丙 is 学徒 (index 0) → excluded
    assert knows(g, "丙", "秘密计划", day=2) is None


def test_broadcast_place_audience():
    """broadcast with place audience → only occupants of the place learn the fact."""
    g = FactGraph()
    g.add_entity("王都", "Place")
    g.add_entity("野外", "Place")
    g.add_entity("甲", "Person")
    g.add_entity("乙", "Person")
    g.add_entity("丙", "Person")
    world = {"systems": {"ontology": g, "knowledge": {}}}

    # 甲 and 乙 are in 王都; 丙 is in 野外
    from systems.place import PlaceSystem
    ps = PlaceSystem()
    ps.apply(world, _ev("entity_moved", day=1, who="甲", to="王都"))
    ps.apply(world, _ev("entity_moved", day=1, who="乙", to="王都"))
    ps.apply(world, _ev("entity_moved", day=1, who="丙", to="野外"))

    ks = KnowledgeSystem()
    ev = _ev("knowledge_broadcast", day=2,
             fact_key="布告", value="戒严令",
             audience={"place": "王都"})
    ks.apply(world, ev)

    assert knows(g, "甲", "布告", day=2) == "戒严令"
    assert knows(g, "乙", "布告", day=2) == "戒严令"
    assert knows(g, "丙", "布告", day=2) is None


def test_broadcast_place_audience_day_matters():
    """place audience resolves occupants at event day, not current day."""
    g = FactGraph()
    g.add_entity("王都", "Place")
    g.add_entity("甲", "Person")
    g.add_entity("乙", "Person")
    world = {"systems": {"ontology": g, "knowledge": {}}}

    from systems.place import PlaceSystem
    ps = PlaceSystem()
    # 甲 is in 王都 on day 1; moves away on day 4
    ps.apply(world, _ev("entity_moved", day=1, who="甲", to="王都"))
    ps.apply(world, _ev("entity_moved", day=4, who="甲", to="elsewhere"))
    # 乙 is in 王都 from day 1
    ps.apply(world, _ev("entity_moved", day=1, who="乙", to="王都"))

    ks = KnowledgeSystem()
    # broadcast on day 2 → 甲 was still in 王都
    ev = _ev("knowledge_broadcast", day=2,
             fact_key="通知", value="集结令",
             audience={"place": "王都"})
    ks.apply(world, ev)

    assert knows(g, "甲", "通知", day=2) == "集结令"
    assert knows(g, "乙", "通知", day=2) == "集结令"


def test_to_events_broadcast_one_event():
    """to_events: a broadcast item → exactly one knowledge_broadcast event."""
    ks = KnowledgeSystem()
    decl = [{
        "op": "broadcast",
        "fact_key": "密谋",
        "value": "反叛",
        "audience": {"faction": "龙之会"},
    }]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "knowledge_broadcast"
    d = evs[0]["deltas"]
    assert d["fact_key"] == "密谋"
    assert d["value"] == "反叛"
    assert d["audience"] == {"faction": "龙之会"}


def test_to_events_broadcast_place():
    """to_events: broadcast with place audience → one knowledge_broadcast event."""
    ks = KnowledgeSystem()
    decl = [{
        "op": "broadcast",
        "fact_key": "布告",
        "value": "戒严令",
        "audience": {"place": "王都"},
    }]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "knowledge_broadcast"
    assert evs[0]["deltas"]["audience"]["place"] == "王都"


# ---------------------------------------------------------------------------
# Hardening: validate() missing-field checks (Task 4a)
# ---------------------------------------------------------------------------

def test_validate_told_missing_fact_key():
    """validate: told without fact_key → missing error at [0].fact_key."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{"op": "told", "knower": "艾拉", "value": "毁坏"}]  # no fact_key
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].fact_key", "missing") in codes


def test_validate_told_missing_value():
    """validate: told without value → missing error at [0].value."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{"op": "told", "knower": "艾拉", "fact_key": "桥.status"}]  # no value
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].value", "missing") in codes


def test_validate_told_missing_knower_field():
    """validate: told without knower field at all → missing error at [0].knower."""
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": FactGraph(), "knowledge": {}}}

    decl = [{"op": "told", "fact_key": "桥.status", "value": "完好"}]  # no knower
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].knower", "missing") in codes


def test_validate_told_all_fields_present_no_missing_errors():
    """validate: complete told decl with valid entity → no missing errors."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{"op": "told", "knower": "艾拉", "fact_key": "桥.status", "value": "毁坏"}]
    errs = ks.validate("knowledge", decl, world)
    missing = [e for e in errs if e.code == "missing"]
    assert missing == []


def test_validate_broadcast_missing_fact_key():
    """validate: broadcast without fact_key → missing error at [0].fact_key."""
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": FactGraph(), "knowledge": {}}}

    decl = [{"op": "broadcast", "value": "反叛", "audience": {"faction": "龙之会"}}]
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].fact_key", "missing") in codes


def test_validate_broadcast_missing_value():
    """validate: broadcast without value → missing error at [0].value."""
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": FactGraph(), "knowledge": {}}}

    decl = [{"op": "broadcast", "fact_key": "密谋", "audience": {"faction": "龙之会"}}]
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].value", "missing") in codes


def test_validate_broadcast_missing_audience():
    """validate: broadcast without audience → missing error at [0].audience."""
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": FactGraph(), "knowledge": {}}}

    decl = [{"op": "broadcast", "fact_key": "密谋", "value": "反叛"}]
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].audience", "missing") in codes


def test_validate_broadcast_all_fields_present_no_missing_errors():
    """validate: complete broadcast decl → no missing errors."""
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": FactGraph(), "knowledge": {}}}

    decl = [{"op": "broadcast", "fact_key": "密谋", "value": "反叛",
             "audience": {"faction": "龙之会"}}]
    errs = ks.validate("knowledge", decl, world)
    missing = [e for e in errs if e.code == "missing"]
    assert missing == []


def test_validate_endowment_missing_grant_fact_key():
    """validate: endowment grant without fact_key → missing error."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{
        "op": "endowment",
        "knower": "艾拉",
        "grants": [{"value": "毁坏"}],  # no fact_key
    }]
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].grants[0].fact_key", "missing") in codes


def test_validate_endowment_missing_grant_value():
    """validate: endowment grant without value → missing error."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{
        "op": "endowment",
        "knower": "艾拉",
        "grants": [{"fact_key": "桥.status"}],  # no value
    }]
    errs = ks.validate("knowledge", decl, world)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].grants[0].value", "missing") in codes


# ---------------------------------------------------------------------------
# Hardening: complete valid decl — to_events + apply work (Task 4b)
# ---------------------------------------------------------------------------

def test_valid_told_roundtrip():
    """Complete told decl: validate clean, to_events produces event, apply stores fact."""
    g = _graph_with_persons("艾拉")
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    decl = [{"op": "told", "knower": "艾拉", "fact_key": "桥.status", "value": "毁坏"}]
    errs = ks.validate("knowledge", decl, world)
    assert errs == []

    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    ks.apply(world, evs[0])
    assert knows(g, "艾拉", "桥.status", day=1) == "毁坏"


def test_valid_broadcast_roundtrip():
    """Complete broadcast decl: validate clean, to_events produces event, apply stores facts."""
    g, world = _setup_faction_world()
    ks = KnowledgeSystem()

    decl = [{
        "op": "broadcast",
        "fact_key": "密令",
        "value": "集结",
        "audience": {"faction": "龙之会"},
    }]
    errs = ks.validate("knowledge", decl, world)
    assert errs == []

    evs = ks.to_events("knowledge", decl, turn=1, day=2, scene="s1")
    assert len(evs) == 1
    ks.apply(world, evs[0])
    assert knows(g, "甲", "密令", day=2) == "集结"
    assert knows(g, "乙", "密令", day=2) == "集结"


# ---------------------------------------------------------------------------
# Hardening: defensive to_events / apply do NOT raise (Task 4c)
# ---------------------------------------------------------------------------

def test_to_events_told_missing_knower_no_raise():
    """to_events: told missing knower → skips item, returns []."""
    ks = KnowledgeSystem()
    decl = [{"op": "told", "fact_key": "桥.status", "value": "毁坏"}]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert evs == []  # item skipped, no KeyError


def test_to_events_told_missing_fact_key_no_raise():
    """to_events: told missing fact_key → skips item."""
    ks = KnowledgeSystem()
    decl = [{"op": "told", "knower": "艾拉", "value": "毁坏"}]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert evs == []


def test_to_events_told_missing_value_no_raise():
    """to_events: told missing value → skips item."""
    ks = KnowledgeSystem()
    decl = [{"op": "told", "knower": "艾拉", "fact_key": "桥.status"}]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert evs == []


def test_to_events_broadcast_missing_audience_no_raise():
    """to_events: broadcast missing audience → skips item."""
    ks = KnowledgeSystem()
    decl = [{"op": "broadcast", "fact_key": "密谋", "value": "反叛"}]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    assert evs == []


def test_to_events_endowment_grant_missing_fact_key_no_raise():
    """to_events: endowment grant missing fact_key → that grant skipped, no raise."""
    ks = KnowledgeSystem()
    decl = [{
        "op": "endowment",
        "knower": "艾拉",
        "grants": [
            {"value": "毁坏"},                        # missing fact_key — skipped
            {"fact_key": "反派身份", "value": "影主"},  # good
        ],
    }]
    evs = ks.to_events("knowledge", decl, turn=1, day=1, scene="s1")
    # Only the good grant generates an event
    assert len(evs) == 1
    assert evs[0]["deltas"]["fact_key"] == "反派身份"


def test_apply_knowledge_set_missing_knower_no_raise():
    """apply: knowledge_set event missing knower in deltas → skips, no raise."""
    g = FactGraph()
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    ev = _ev("knowledge_set", day=1, fact_key="桥.status", value="毁坏")  # no knower
    ks.apply(world, ev)  # must not raise
    # nothing written
    assert knowers_of(g, "桥.status", day=1) == []


def test_apply_knowledge_set_missing_fact_key_no_raise():
    """apply: knowledge_set event missing fact_key in deltas → skips, no raise."""
    g = FactGraph()
    ks = KnowledgeSystem()
    world = {"systems": {"ontology": g, "knowledge": {}}}

    ev = _ev("knowledge_set", day=1, knower="艾拉", value="毁坏")  # no fact_key
    ks.apply(world, ev)  # must not raise


def test_apply_knowledge_broadcast_missing_fact_key_no_raise():
    """apply: knowledge_broadcast event missing fact_key → skips, no raise."""
    g, world = _setup_faction_world()
    ks = KnowledgeSystem()

    ev = _ev("knowledge_broadcast", day=2, value="反叛",
             audience={"faction": "龙之会"})  # no fact_key
    ks.apply(world, ev)  # must not raise
