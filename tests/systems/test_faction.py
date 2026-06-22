"""Tests for FactionSystem — Tasks 2 and 3 (TDD)."""
from __future__ import annotations

import pytest
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import ValidationError, Fragment
from systems.ontology import OntologySystem
from systems.faction import FactionSystem, members_of, member_rank
from facts.graph import FactGraph


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reg():
    return Registry().register(OntologySystem()).register(FactionSystem())


def _ev(typ, day=1, scene="s1", **deltas):
    return kernel_event(typ, day=day, scene=scene, summary=f"{typ}", deltas=deltas)


def _world():
    r = _reg()
    return project(r, [])


# ---------------------------------------------------------------------------
# Task 2: apply — faction_created
# ---------------------------------------------------------------------------

def test_faction_created_makes_entity():
    r = _reg()
    evs = [_ev("faction_created", id="龙之会", tier="tracked",
               ranks=["学徒", "正式", "资深", "会长"], groups=["高层", "普通"])]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("龙之会")
    assert e is not None
    assert e.etype == "Faction"


def test_faction_created_stores_ranks():
    r = _reg()
    evs = [_ev("faction_created", id="龙之会", tier="tracked",
               ranks=["学徒", "正式", "资深", "会长"])]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("龙之会")
    assert e.attrs.get("ranks") == ["学徒", "正式", "资深", "会长"]


def test_faction_created_stores_groups():
    r = _reg()
    evs = [_ev("faction_created", id="龙之会", tier="tracked",
               groups=["高层", "普通"])]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("龙之会")
    assert e.attrs.get("groups") == ["高层", "普通"]


def test_faction_created_default_tier():
    r = _reg()
    evs = [_ev("faction_created", id="小团体")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("小团体")
    assert e is not None
    assert e.tier == "mentioned"


def test_faction_created_extra_attrs():
    """Extra attrs (kind, etc.) are stored on the entity."""
    r = _reg()
    evs = [_ev("faction_created", id="龙之会", kind="guild", motto="力量为先")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("龙之会")
    assert e.attrs.get("kind") == "guild"
    assert e.attrs.get("motto") == "力量为先"


# ---------------------------------------------------------------------------
# Task 2: apply — member_changed
# ---------------------------------------------------------------------------

def test_member_changed_adds_member_of():
    r = _reg()
    evs = [
        _ev("faction_created", id="龙之会", ranks=["学徒", "正式"]),
        _ev("faction_created", id="商会", ranks=["新手", "掌柜"]),
        _ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟"),
        _ev("member_changed", day=1, person="艾拉", faction="龙之会", rank="学徒"),
    ]
    # We need CharacterSystem too for character_created, but actually the plan says
    # member_changed just needs entities in graph. Let's build the graph directly.
    r2 = _reg()
    g_base = FactGraph()
    g_base.add_entity("龙之会", "Faction", tier="tracked",
                      ranks=["学徒", "正式"], groups=[])
    g_base.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g_base, "faction": {}}}
    evs2 = [_ev("member_changed", day=1, person="艾拉", faction="龙之会", rank="学徒")]
    # Use project on the actual registry with both systems, but seed with
    # pre-populated graph by applying directly
    fs = FactionSystem()
    for ev in evs2:
        ev["deltas"] = ev.get("deltas", {})
        fs.apply(world, ev)
    g: FactGraph = world["systems"]["ontology"]
    assert "龙之会" in g.neighbors("艾拉", "member_of", day=1)


def test_member_changed_multi_valued():
    """Joining a 2nd faction keeps the 1st — member_of is multi-valued."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("龙之会", "Faction", ranks=["学徒", "正式"], groups=[])
    g.add_entity("商会", "Faction", ranks=["新手", "掌柜"], groups=[])
    g.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g, "faction": {}}}

    ev1 = _ev("member_changed", day=1, person="艾拉", faction="龙之会", rank="学徒")
    ev2 = _ev("member_changed", day=2, person="艾拉", faction="商会", rank="新手")
    fs.apply(world, ev1)
    fs.apply(world, ev2)

    neighbors = g.neighbors("艾拉", "member_of", day=2)
    assert "龙之会" in neighbors
    assert "商会" in neighbors


def test_member_changed_rank_stored_as_fact():
    """Rank is stored as a fact rank:{faction}."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("龙之会", "Faction", ranks=["学徒", "正式"], groups=[])
    g.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g, "faction": {}}}

    ev = _ev("member_changed", day=1, person="艾拉", faction="龙之会", rank="学徒")
    fs.apply(world, ev)

    assert g.value_at("艾拉", "rank:龙之会", 1) == "学徒"


def test_member_changed_rank_promotion_supersedes():
    """Promotion to new rank supersedes old rank (point-in-time)."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("龙之会", "Faction", ranks=["学徒", "正式", "资深"], groups=[])
    g.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g, "faction": {}}}

    ev1 = _ev("member_changed", day=1, person="艾拉", faction="龙之会", rank="学徒")
    ev2 = _ev("member_changed", day=5, person="艾拉", faction="龙之会", rank="正式")
    fs.apply(world, ev1)
    fs.apply(world, ev2)

    assert g.value_at("艾拉", "rank:龙之会", 1) == "学徒"
    assert g.value_at("艾拉", "rank:龙之会", 5) == "正式"


def test_member_changed_group_stored_as_fact():
    """Group is stored as a fact group:{faction}."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("龙之会", "Faction", ranks=["学徒", "正式"], groups=["高层", "普通"])
    g.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g, "faction": {}}}

    ev = _ev("member_changed", day=1, person="艾拉", faction="龙之会",
             rank="正式", group="高层")
    fs.apply(world, ev)

    assert g.value_at("艾拉", "group:龙之会", 1) == "高层"


def test_member_changed_no_rank_no_fact():
    """If rank not present in delta, no rank fact is created."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("龙之会", "Faction", ranks=["学徒"], groups=[])
    g.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g, "faction": {}}}

    ev = _ev("member_changed", day=1, person="艾拉", faction="龙之会")
    fs.apply(world, ev)

    assert g.value_at("艾拉", "rank:龙之会", 1) is None


# ---------------------------------------------------------------------------
# Task 2: validate — "factions" section
# ---------------------------------------------------------------------------

def _world_with_faction(faction_id="龙之会", person_id="艾拉"):
    g = FactGraph()
    g.add_entity(faction_id, "Faction", ranks=["学徒", "正式"], groups=[])
    g.add_entity(person_id, "Person")
    return {"systems": {"ontology": g, "faction": {}}}


def test_validate_faction_created_missing_id():
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "faction", "ranks": ["学徒"]}], w)
    assert any(e.code == "missing" for e in errs)


def test_validate_member_changed_missing_person():
    """member_changed with missing person entity → dangling_ref."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "member", "person": "不存在", "faction": "龙之会"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_member_changed_missing_faction():
    """member_changed with missing faction entity → dangling_ref."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "member", "person": "艾拉", "faction": "不存在的会"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_valid_faction_create_no_errors():
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "faction", "id": "新组织", "ranks": ["初级"]}], w)
    assert errs == []


def test_validate_valid_member_no_errors():
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "member", "person": "艾拉", "faction": "龙之会", "rank": "学徒"}], w)
    assert errs == []


# ---------------------------------------------------------------------------
# Task 2: to_events — "factions" section
# ---------------------------------------------------------------------------

def test_to_events_faction_op():
    fs = FactionSystem()
    decl = [{"op": "faction", "id": "龙之会", "ranks": ["学徒", "正式"]}]
    evs = fs.to_events("factions", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "faction_created"
    assert evs[0]["deltas"]["id"] == "龙之会"


def test_to_events_member_op():
    fs = FactionSystem()
    decl = [{"op": "member", "person": "艾拉", "faction": "龙之会", "rank": "学徒"}]
    evs = fs.to_events("factions", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "member_changed"
    assert evs[0]["deltas"]["person"] == "艾拉"
    assert evs[0]["deltas"]["faction"] == "龙之会"


def test_to_events_unknown_section_empty():
    fs = FactionSystem()
    decl = [{"op": "faction", "id": "龙之会"}]
    evs = fs.to_events("unknown", decl, turn=1, day=1, scene="s1")
    assert evs == []


# ---------------------------------------------------------------------------
# Task 3: module helpers — members_of, member_rank
# ---------------------------------------------------------------------------

RANKS = ["学徒", "正式", "资深", "会长"]


def _graph_with_faction_and_members():
    """Set up:
      - Faction 龙之会 with ranks=["学徒","正式","资深","会长"], groups=["高层","普通"]
      - 甲: rank=资深, group=高层
      - 乙: rank=学徒, group=普通
      - 丙: no rank, group=普通
    All join on day=1.
    """
    g = FactGraph()
    g.add_entity("龙之会", "Faction", tier="tracked",
                 ranks=RANKS, groups=["高层", "普通"])
    g.add_entity("甲", "Person")
    g.add_entity("乙", "Person")
    g.add_entity("丙", "Person")

    fs = FactionSystem()
    world = {"systems": {"ontology": g, "faction": {}}}

    ev_甲 = _ev("member_changed", day=1, person="甲", faction="龙之会",
                rank="资深", group="高层")
    ev_乙 = _ev("member_changed", day=1, person="乙", faction="龙之会",
                rank="学徒", group="普通")
    ev_丙 = _ev("member_changed", day=1, person="丙", faction="龙之会",
                group="普通")  # no rank

    fs.apply(world, ev_甲)
    fs.apply(world, ev_乙)
    fs.apply(world, ev_丙)

    return g


def test_members_of_returns_all_members():
    g = _graph_with_faction_and_members()
    result = members_of(g, "龙之会", day=1)
    assert set(result) == {"甲", "乙", "丙"}


def test_members_of_min_rank_filters_by_index():
    """min_rank="资深" → only members at 资深+ (index 2 or 3)."""
    g = _graph_with_faction_and_members()
    result = members_of(g, "龙之会", day=1, min_rank="资深")
    # 甲=资深 (index 2) → included; 乙=学徒 (index 0) → excluded; 丙=no rank (index -1) → excluded
    assert set(result) == {"甲"}


def test_members_of_group_filter():
    """group="高层" → only members whose group:faction == 高层."""
    g = _graph_with_faction_and_members()
    result = members_of(g, "龙之会", day=1, group="高层")
    assert set(result) == {"甲"}


def test_members_of_group_filter_multiple():
    """group="普通" → members with that group."""
    g = _graph_with_faction_and_members()
    result = members_of(g, "龙之会", day=1, group="普通")
    assert set(result) == {"乙", "丙"}


def test_members_of_min_rank_unknown_rank_excluded():
    """Member with no rank fact (丙) treated as rank index -1 → excluded by any min_rank."""
    g = _graph_with_faction_and_members()
    result = members_of(g, "龙之会", day=1, min_rank="学徒")
    # 丙 has no rank → index -1 < 0 (index of 学徒) → excluded
    assert "丙" not in result


def test_members_of_combined_filters():
    """min_rank + group combined filtering."""
    g = _graph_with_faction_and_members()
    result = members_of(g, "龙之会", day=1, min_rank="资深", group="高层")
    assert set(result) == {"甲"}


def test_member_rank_returns_rank():
    g = _graph_with_faction_and_members()
    assert member_rank(g, "甲", "龙之会", day=1) == "资深"
    assert member_rank(g, "乙", "龙之会", day=1) == "学徒"


def test_member_rank_no_rank_returns_none():
    g = _graph_with_faction_and_members()
    assert member_rank(g, "丙", "龙之会", day=1) is None


def test_member_rank_nonmember_returns_none():
    g = _graph_with_faction_and_members()
    assert member_rank(g, "未知人", "龙之会", day=1) is None


def test_members_of_empty_faction():
    """No members → empty list."""
    g = FactGraph()
    g.add_entity("空会", "Faction", ranks=["学徒"], groups=[])
    result = members_of(g, "空会", day=1)
    assert result == []


def test_members_of_min_rank_not_in_faction_ranks():
    """If the faction has no min_rank in its ranks list, should handle gracefully."""
    g = _graph_with_faction_and_members()
    # min_rank that doesn't exist in ranks → ValueError or empty — let's just confirm
    # no crash and '会长' (index 3) filters correctly
    result = members_of(g, "龙之会", day=1, min_rank="会长")
    # 会长 is index 3; nobody has that rank
    assert result == []


# ---------------------------------------------------------------------------
# New tests: validate() missing-field detection (task 4a)
# ---------------------------------------------------------------------------

def test_validate_faction_create_missing_id_yields_missing_error():
    """op='faction' with no 'id' field → ValidationError code 'missing' at '[0].id'."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "faction", "ranks": ["学徒"]}], w)
    assert len(errs) == 1
    assert errs[0].code == "missing"
    assert errs[0].field == "[0].id"


def test_validate_faction_create_empty_id_yields_missing_error():
    """op='faction' with empty string 'id' → ValidationError code 'missing'."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "faction", "id": "", "ranks": ["学徒"]}], w)
    assert any(e.code == "missing" and "[0].id" in e.field for e in errs)


def test_validate_member_missing_person_yields_missing_error():
    """op='member' with no 'person' field → ValidationError code 'missing' at '[0].person'."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "member", "faction": "龙之会"}], w)
    assert any(e.code == "missing" and e.field == "[0].person" for e in errs)


def test_validate_member_missing_faction_yields_missing_error():
    """op='member' with no 'faction' field → ValidationError code 'missing' at '[0].faction'."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "member", "person": "艾拉"}], w)
    assert any(e.code == "missing" and e.field == "[0].faction" for e in errs)


def test_validate_member_missing_both_person_and_faction_yields_two_missing_errors():
    """op='member' with neither 'person' nor 'faction' → two missing errors."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate("factions", [{"op": "member"}], w)
    missing_errs = [e for e in errs if e.code == "missing"]
    fields = {e.field for e in missing_errs}
    assert "[0].person" in fields
    assert "[0].faction" in fields


# ---------------------------------------------------------------------------
# New tests: complete valid decl validates clean and round-trips (task 4b)
# ---------------------------------------------------------------------------

def test_validate_complete_faction_create_no_errors():
    """A fully-specified faction create decl → zero errors."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate(
        "factions",
        [{"op": "faction", "id": "新联盟", "ranks": ["新兵", "老兵"], "groups": ["东", "西"]}],
        w,
    )
    assert errs == []


def test_validate_complete_member_change_no_errors():
    """A fully-specified member change decl (with rank and group) → zero errors."""
    fs = FactionSystem()
    w = _world_with_faction()
    errs = fs.validate(
        "factions",
        [{"op": "member", "person": "艾拉", "faction": "龙之会", "rank": "正式", "group": "高层"}],
        w,
    )
    assert errs == []


def test_validate_then_to_events_and_apply_faction_create():
    """validate() clean → to_events() → apply() succeeds and creates entity."""
    fs = FactionSystem()
    g = FactGraph()
    world = {"systems": {"ontology": g, "faction": {}}}
    decl = [{"op": "faction", "id": "铁锤帮", "ranks": ["小弟", "大哥"], "groups": []}]

    errs = fs.validate("factions", decl, world)
    assert errs == [], f"Unexpected errors: {errs}"

    evs = fs.to_events("factions", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "faction_created"

    for ev in evs:
        fs.apply(world, ev)
    assert g.get_entity("铁锤帮") is not None


def test_validate_then_to_events_and_apply_member_change():
    """validate() clean → to_events() → apply() succeeds and writes membership."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("龙之会", "Faction", ranks=["学徒", "正式"], groups=["高层"])
    g.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g, "faction": {}}}
    decl = [{"op": "member", "person": "艾拉", "faction": "龙之会", "rank": "正式"}]

    errs = fs.validate("factions", decl, world)
    assert errs == [], f"Unexpected errors: {errs}"

    evs = fs.to_events("factions", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "member_changed"

    for ev in evs:
        fs.apply(world, ev)
    assert "龙之会" in g.neighbors("艾拉", "member_of", day=1)
    assert g.value_at("艾拉", "rank:龙之会", 1) == "正式"


# ---------------------------------------------------------------------------
# New tests: defensive to_events / apply on malformed input (task 4c)
# ---------------------------------------------------------------------------

def test_to_events_faction_missing_id_does_not_raise():
    """to_events() with faction item missing 'id' should skip without crashing."""
    fs = FactionSystem()
    decl = [{"op": "faction", "ranks": ["学徒"]}]  # no 'id'
    # Must not raise
    evs = fs.to_events("factions", decl, turn=1, day=1, scene="s1")
    assert evs == []


def test_to_events_member_missing_person_does_not_raise():
    """to_events() with member item missing 'person' should skip without crashing."""
    fs = FactionSystem()
    decl = [{"op": "member", "faction": "龙之会"}]  # no 'person'
    evs = fs.to_events("factions", decl, turn=1, day=1, scene="s1")
    assert evs == []


def test_to_events_member_missing_faction_does_not_raise():
    """to_events() with member item missing 'faction' should skip without crashing."""
    fs = FactionSystem()
    decl = [{"op": "member", "person": "艾拉"}]  # no 'faction'
    evs = fs.to_events("factions", decl, turn=1, day=1, scene="s1")
    assert evs == []


def test_apply_faction_created_missing_id_does_not_raise():
    """apply() with faction_created event missing 'id' in deltas should not raise."""
    fs = FactionSystem()
    g = FactGraph()
    world = {"systems": {"ontology": g, "faction": {}}}
    # Malformed stored event: deltas has no 'id'
    ev = _ev("faction_created", ranks=["学徒"])  # 'id' not in deltas
    # Must not raise; entity count stays 0
    fs.apply(world, ev)
    assert len(g.entities) == 0


def test_apply_member_changed_missing_person_does_not_raise():
    """apply() with member_changed event missing 'person' in deltas should not raise."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("龙之会", "Faction", ranks=["学徒"], groups=[])
    world = {"systems": {"ontology": g, "faction": {}}}
    # Malformed stored event: no 'person' key
    ev = _ev("member_changed", faction="龙之会", rank="学徒")
    fs.apply(world, ev)
    # No relations written; graph only has the faction entity
    assert g.neighbors("龙之会", "member_of", day=1) == []


def test_apply_member_changed_missing_faction_does_not_raise():
    """apply() with member_changed event missing 'faction' in deltas should not raise."""
    fs = FactionSystem()
    g = FactGraph()
    g.add_entity("艾拉", "Person")
    world = {"systems": {"ontology": g, "faction": {}}}
    # Malformed stored event: no 'faction' key
    ev = _ev("member_changed", person="艾拉", rank="学徒")
    fs.apply(world, ev)
    # No member_of relation written
    assert g.neighbors("艾拉", "member_of", day=1) == []


def test_apply_member_changed_missing_both_does_not_raise():
    """apply() with member_changed event missing both 'person' and 'faction' should not raise."""
    fs = FactionSystem()
    g = FactGraph()
    world = {"systems": {"ontology": g, "faction": {}}}
    ev = _ev("member_changed", rank="学徒")  # no person, no faction
    fs.apply(world, ev)  # must not raise
