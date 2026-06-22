"""Tests for PlaceSystem — Tasks 2, 3, 4."""
from __future__ import annotations
import pytest
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import ValidationError, Fragment
from systems.ontology import OntologySystem
from systems.place import PlaceSystem, navigate
from facts.graph import FactGraph


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reg():
    return Registry().register(OntologySystem()).register(PlaceSystem())


def _ev(typ, day=1, scene="s1", **deltas):
    return kernel_event(typ, day=day, scene=scene, summary=f"{typ}", deltas=deltas)


# ---------------------------------------------------------------------------
# Task 2: apply — place_created
# ---------------------------------------------------------------------------

def test_place_created_entity_in_graph():
    r = _reg()
    evs = [_ev("place_created", id="王都", level=1, kind="settlement",
               seed="繁华都城", tier="tracked", detail="partial")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("王都")
    assert e is not None
    assert e.etype == "Place"
    assert e.attrs["level"] == 1
    assert e.attrs["kind"] == "settlement"
    assert e.attrs["seed"] == "繁华都城"
    assert e.attrs["detail"] == "partial"


def test_place_created_with_parent_adds_contained_by():
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="都城", tier="tracked", detail="partial"),
        _ev("place_created", id="集市", level=2, kind="venue",
            seed="热闹集市", tier="tracked", detail="partial", parent="王都"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert "王都" in g.neighbors("集市", "contained_by", day=1)


def test_place_created_without_parent_no_contained_by():
    r = _reg()
    evs = [_ev("place_created", id="王都", level=1, kind="settlement",
               seed="都城", tier="tracked", detail="partial")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.neighbors("王都", "contained_by", day=1) == []


# ---------------------------------------------------------------------------
# Task 2: apply — place_materialized
# ---------------------------------------------------------------------------

def test_place_materialized_sets_detail_full():
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="都城", tier="tracked", detail="partial"),
        _ev("place_materialized", day=2, id="王都"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.get_entity("王都").attrs["detail"] == "full"


# ---------------------------------------------------------------------------
# Task 2: apply — place_linked (symmetric adjacent_to with travel_cost)
# ---------------------------------------------------------------------------

def test_place_linked_adds_adjacent_to_both_directions():
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="都城", tier="tracked", detail="partial"),
        _ev("place_created", id="暗黑森林", level=1, kind="wilderness",
            seed="危险森林", tier="tracked", detail="partial"),
        _ev("place_linked", day=2, a="王都", b="暗黑森林", travel_cost=1),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    fwd = g.relation_attrs_at("王都", "adjacent_to", day=2)
    rev = g.relation_attrs_at("暗黑森林", "adjacent_to", day=2)
    assert ("暗黑森林", {"travel_cost": 1}) in fwd
    assert ("王都", {"travel_cost": 1}) in rev


def test_place_linked_multiple_neighbors_all_present():
    """A place can have multiple adjacent neighbors without supersession."""
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="都城", tier="tracked", detail="partial"),
        _ev("place_created", id="暗黑森林", level=1, kind="wilderness",
            seed="森林", tier="tracked", detail="partial"),
        _ev("place_created", id="边境城", level=1, kind="settlement",
            seed="边境", tier="tracked", detail="partial"),
        _ev("place_linked", day=1, a="王都", b="暗黑森林", travel_cost=1),
        _ev("place_linked", day=1, a="王都", b="边境城", travel_cost=5),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    neighbors = g.relation_attrs_at("王都", "adjacent_to", day=1)
    dsts = [dst for dst, _ in neighbors]
    assert "暗黑森林" in dsts
    assert "边境城" in dsts


# ---------------------------------------------------------------------------
# Task 2: apply — entity_moved
# ---------------------------------------------------------------------------

def test_entity_moved_adds_located_in():
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="都城", tier="tracked", detail="partial"),
        _ev("entity_moved", day=1, who="主角", to="王都"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.neighbors("主角", "located_in", day=1) == ["王都"]


def test_entity_moved_second_move_supersedes_first():
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="都城", tier="tracked", detail="partial"),
        _ev("place_created", id="边境城", level=1, kind="settlement",
            seed="边境", tier="tracked", detail="partial"),
        _ev("entity_moved", day=1, who="主角", to="王都"),
        _ev("entity_moved", day=5, who="主角", to="边境城"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.neighbors("主角", "located_in", day=1) == ["王都"]
    assert g.neighbors("主角", "located_in", day=5) == ["边境城"]


# ---------------------------------------------------------------------------
# Task 2: validate — "places" section
# ---------------------------------------------------------------------------

def _world_with_place(place_id="王都"):
    r = _reg()
    g = FactGraph()
    g.add_entity(place_id, "Place")
    return {"systems": {"ontology": g, "place": {}}}


def test_validate_places_missing_id():
    ps = PlaceSystem()
    w = _world_with_place()
    errs = ps.validate("places", [{"level": 1, "kind": "settlement"}], w)
    assert len(errs) >= 1
    assert any(e.code == "missing" for e in errs)


def test_validate_places_bad_level():
    ps = PlaceSystem()
    w = _world_with_place()
    errs = ps.validate("places", [{"id": "新地点", "level": 99, "kind": "settlement"}], w)
    assert any(e.code == "bad_enum" for e in errs)


def test_validate_places_bad_kind():
    ps = PlaceSystem()
    w = _world_with_place()
    errs = ps.validate("places", [{"id": "新地点", "level": 1, "kind": "unknown_kind"}], w)
    assert any(e.code == "bad_enum" for e in errs)


def test_validate_places_dangling_parent():
    ps = PlaceSystem()
    w = _world_with_place()
    errs = ps.validate("places", [{"id": "集市", "level": 2, "kind": "venue",
                                   "parent": "不存在的地方"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_places_no_errors_valid():
    ps = PlaceSystem()
    w = _world_with_place("王都")
    errs = ps.validate("places", [{"id": "新地", "level": 1, "kind": "settlement",
                                   "parent": "王都"}], w)
    assert errs == []


# ---------------------------------------------------------------------------
# Task 2: validate — "moves" section
# ---------------------------------------------------------------------------

def test_validate_moves_to_not_in_graph():
    ps = PlaceSystem()
    w = _world_with_place()
    errs = ps.validate("moves", [{"who": "主角", "to": "不存在"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_moves_valid():
    ps = PlaceSystem()
    w = _world_with_place("王都")
    w["systems"]["ontology"].add_entity("主角", "Person")  # who must exist now
    errs = ps.validate("moves", [{"who": "主角", "to": "王都"}], w)
    assert errs == []


def test_validate_commit_cross_section_move_to_new_place():
    """A commit that creates a place AND moves to it in one turn validates clean
    (cross-section refs resolve via created_ids pre-registration)."""
    from kernel.validation import validate_commit
    from kernel.turncommit import TurnCommit
    r = _reg()
    w = _world_with_place("王都")
    w["systems"]["ontology"].add_entity("主角", "Person")
    commit = TurnCommit(narration="走向新城", sections={
        "places": [{"id": "新城", "level": 1, "kind": "settlement", "seed": "一座新城"}],
        "moves": [{"who": "主角", "to": "新城"}],
    })
    errs = validate_commit(r, commit, w)
    assert errs == [], errs
    # stub must be cleaned up — 新城 only exists after apply, not after validation
    assert w["systems"]["ontology"].get_entity("新城") is None


def test_validate_commit_required_sections_need_content_or_reason():
    """A required section that's empty must carry a reason (force the model to
    confirm it didn't just forget); bare [] no longer satisfies."""
    from kernel.validation import validate_commit
    from kernel.turncommit import TurnCommit
    r = _reg()
    w = _world_with_place("王都")
    w["systems"]["ontology"].add_entity("主角", "Person")
    req = frozenset({"moves", "places"})
    # empty + no reason -> empty_no_reason for both
    errs = validate_commit(r, TurnCommit(narration="x", sections={}), w,
                           required_sections=req)
    assert {e.section for e in errs if e.code == "empty_no_reason"} == {"moves", "places"}
    # bare empty [] still NOT enough — must justify
    errs2 = validate_commit(r, TurnCommit(narration="x", sections={"moves": [], "places": []}),
                            w, required_sections=req)
    assert {e.section for e in errs2 if e.code == "empty_no_reason"} == {"moves", "places"}
    # content satisfies one; a reason satisfies the other
    c = TurnCommit(narration="x", sections={"moves": [{"who": "主角", "to": "王都"}]},
                   reasons={"places": "未发现新地点"})
    ok = validate_commit(r, c, w, required_sections=req)
    assert [e for e in ok if e.code == "empty_no_reason"] == []


# ---------------------------------------------------------------------------
# Task 2: to_events
# ---------------------------------------------------------------------------

def test_to_events_places_section():
    ps = PlaceSystem()
    decl = [{"id": "王都", "level": 1, "kind": "settlement",
             "seed": "都城", "tier": "tracked", "detail": "partial"}]
    evs = ps.to_events("places", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "place_created"
    assert evs[0]["deltas"]["id"] == "王都"


def test_to_events_moves_section():
    ps = PlaceSystem()
    decl = [{"who": "主角", "to": "王都"}]
    evs = ps.to_events("moves", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "entity_moved"
    assert evs[0]["deltas"]["who"] == "主角"


# ---------------------------------------------------------------------------
# Task 3: navigate() — Dijkstra
# ---------------------------------------------------------------------------

def _graph_with_map():
    """王都—(1)—暗黑森林—(3)—边境城; 王都—(5)—边境城"""
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="都城", tier="tracked", detail="partial"),
        _ev("place_created", id="暗黑森林", level=1, kind="wilderness",
            seed="森林", tier="tracked", detail="partial"),
        _ev("place_created", id="边境城", level=1, kind="settlement",
            seed="边境", tier="tracked", detail="partial"),
        _ev("place_linked", day=1, a="王都", b="暗黑森林", travel_cost=1),
        _ev("place_linked", day=1, a="暗黑森林", b="边境城", travel_cost=3),
        _ev("place_linked", day=1, a="王都", b="边境城", travel_cost=5),
    ]
    w = project(r, evs)
    return w["systems"]["ontology"]


def test_navigate_finds_least_cost_path():
    g = _graph_with_map()
    result = navigate(g, "王都", "边境城", day=1)
    assert result["path"] == ["王都", "暗黑森林", "边境城"]
    assert result["total_cost"] == 4


def test_navigate_same_node():
    g = _graph_with_map()
    result = navigate(g, "王都", "王都", day=1)
    assert result == {"path": ["王都"], "total_cost": 0}


def test_navigate_unreachable():
    g = _graph_with_map()
    g.add_entity("孤岛", "Place")
    result = navigate(g, "王都", "孤岛", day=1)
    assert result == {"path": [], "total_cost": None}


# ---------------------------------------------------------------------------
# Task 4: inject() — current location + exits
# ---------------------------------------------------------------------------

def _world_for_inject():
    r = _reg()
    evs = [
        _ev("place_created", id="王都", level=1, kind="settlement",
            seed="繁华都城", tier="tracked", detail="partial"),
        _ev("place_created", id="暗黑森林", level=1, kind="wilderness",
            seed="危险森林", tier="tracked", detail="partial"),
        _ev("place_created", id="边境城", level=1, kind="settlement",
            seed="边境重镇", tier="tracked", detail="partial"),
        _ev("place_linked", day=1, a="王都", b="暗黑森林", travel_cost=1),
        _ev("place_linked", day=1, a="王都", b="边境城", travel_cost=5),
        _ev("entity_moved", day=1, who="主角", to="王都"),
    ]
    return project(r, evs)


def test_inject_returns_fragment_with_location():
    ps = PlaceSystem()
    w = _world_for_inject()
    scene = {"protagonist": "主角", "day": 1}
    frag = ps.inject(scene, w)
    assert isinstance(frag, Fragment)
    assert frag.system == "place"
    assert frag.layer == "scene"
    assert "王都" in frag.text


def test_inject_lists_exits():
    ps = PlaceSystem()
    w = _world_for_inject()
    scene = {"protagonist": "主角", "day": 1}
    frag = ps.inject(scene, w)
    assert "暗黑森林" in frag.text
    assert "边境城" in frag.text
    # travel costs appear
    assert "1" in frag.text
    assert "5" in frag.text


def test_inject_no_location_returns_none():
    ps = PlaceSystem()
    r = _reg()
    w = project(r, [])
    scene = {"protagonist": "主角", "day": 1}
    result = ps.inject(scene, w)
    assert result is None


def test_inject_affordance_lists_move_targets():
    ps = PlaceSystem()
    w = _world_for_inject()
    scene = {"protagonist": "主角", "day": 1}
    frag = ps.inject(scene, w)
    assert frag.affordance != ""
    assert "暗黑森林" in frag.affordance or "边境城" in frag.affordance


# ---------------------------------------------------------------------------
# I2: to_events for "links" and "materialize" sections
# ---------------------------------------------------------------------------

def test_to_events_links_section():
    """to_events('links', [...]) → place_linked events (I2)."""
    ps = PlaceSystem()
    decl = [{"a": "王都", "b": "暗黑森林", "travel_cost": 2}]
    evs = ps.to_events("links", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "place_linked"
    assert evs[0]["deltas"]["a"] == "王都"
    assert evs[0]["deltas"]["b"] == "暗黑森林"


def test_to_events_links_section_default_cost():
    """to_events('links', [...]) without travel_cost still produces place_linked (I2)."""
    ps = PlaceSystem()
    decl = [{"a": "王都", "b": "边境城"}]
    evs = ps.to_events("links", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "place_linked"


def test_to_events_materialize_section():
    """to_events('materialize', [...]) → place_materialized events (I2)."""
    ps = PlaceSystem()
    decl = [{"id": "王都"}]
    evs = ps.to_events("materialize", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "place_materialized"
    assert evs[0]["deltas"]["id"] == "王都"


# I2: commit_sections includes "links" and "materialize"

def test_commit_sections_includes_links_and_materialize():
    """PlaceSystem must advertise 'links' and 'materialize' sections (I2)."""
    ps = PlaceSystem()
    secs = ps.commit_sections()
    assert "links" in secs
    assert "materialize" in secs


# I2: validate — "links" section dangling refs

def _world_with_two_places():
    g = FactGraph()
    g.add_entity("王都", "Place")
    g.add_entity("暗黑森林", "Place")
    return {"systems": {"ontology": g, "place": {}}}


def test_validate_links_dangling_a():
    """validate 'links' with non-existent a → dangling_ref (I2)."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("links", [{"a": "不存在", "b": "暗黑森林"}], w)
    assert any(e.code == "dangling_ref" and "a" in e.field for e in errs)


def test_validate_links_dangling_b():
    """validate 'links' with non-existent b → dangling_ref (I2)."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("links", [{"a": "王都", "b": "不存在"}], w)
    assert any(e.code == "dangling_ref" and "b" in e.field for e in errs)


def test_validate_links_valid():
    """validate 'links' with both existing → no errors (I2)."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("links", [{"a": "王都", "b": "暗黑森林"}], w)
    assert errs == []


def test_validate_materialize_dangling_id():
    """validate 'materialize' with non-existent id → dangling_ref (I2)."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("materialize", [{"id": "不存在"}], w)
    assert any(e.code == "dangling_ref" and "id" in e.field for e in errs)


def test_validate_materialize_valid():
    """validate 'materialize' with existing id → no errors (I2)."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("materialize", [{"id": "王都"}], w)
    assert errs == []


# ---------------------------------------------------------------------------
# M4: recall hook
# ---------------------------------------------------------------------------

def _world_with_places():
    """World with two places for recall tests."""
    r = _reg()
    evs = [
        _ev("place_created", day=1, id="王都", level=1, kind="settlement",
            seed="繁华的首都城市", tier="tracked", detail="partial"),
        _ev("place_created", day=1, id="暗黑森林", level=2, kind="wilderness",
            seed="危险的幽暗树林", tier="tracked", detail="partial"),
    ]
    return project(r, evs)


def test_place_recall_finds_by_seed_substring():
    """PlaceSystem.recall matches places whose seed contains the query."""
    w = _world_with_places()
    ps = PlaceSystem()
    hits = ps.recall("繁华", w)
    assert len(hits) >= 1
    assert any(h.ref.get("id") == "王都" for h in hits)
    assert not any(h.ref.get("id") == "暗黑森林" for h in hits)


def test_place_recall_finds_by_id_substring():
    """PlaceSystem.recall matches places whose id contains the query."""
    w = _world_with_places()
    ps = PlaceSystem()
    hits = ps.recall("森林", w)
    assert any(h.ref.get("id") == "暗黑森林" for h in hits)


def test_place_recall_returns_empty_when_no_match():
    """PlaceSystem.recall returns empty list when query matches nothing."""
    w = _world_with_places()
    ps = PlaceSystem()
    hits = ps.recall("绝对不存在的词语XYZ", w)
    assert hits == []


def test_place_recall_hits_have_correct_system():
    """RecallHit objects from PlaceSystem have system='place'."""
    w = _world_with_places()
    ps = PlaceSystem()
    hits = ps.recall("首都", w)
    assert all(h.system == "place" for h in hits)


# ---------------------------------------------------------------------------
# Hardening: validate() catches missing required fields (new tests)
# ---------------------------------------------------------------------------

# --- places: missing id already tested above; confirm field path and code ---

def test_validate_places_missing_id_field_path():
    """Missing id in places produces ValidationError with correct field path."""
    ps = PlaceSystem()
    w = _world_with_place()
    errs = ps.validate("places", [{"level": 1, "kind": "settlement"}], w)
    assert any(e.code == "missing" and e.field == "[0].id" for e in errs)


# --- links: missing a ---

def test_validate_links_missing_a():
    """links item without 'a' yields ValidationError code=missing field=[0].a."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("links", [{"b": "暗黑森林"}], w)
    assert any(e.code == "missing" and e.field == "[0].a" for e in errs)


def test_validate_links_missing_b():
    """links item without 'b' yields ValidationError code=missing field=[0].b."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("links", [{"a": "王都"}], w)
    assert any(e.code == "missing" and e.field == "[0].b" for e in errs)


def test_validate_links_missing_both():
    """links item with neither a nor b yields two missing errors."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("links", [{}], w)
    codes = [(e.field, e.code) for e in errs]
    assert ("[0].a", "missing") in codes
    assert ("[0].b", "missing") in codes


def test_validate_links_complete_valid_no_errors():
    """A complete links item with existing a+b passes validation clean."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("links", [{"a": "王都", "b": "暗黑森林", "travel_cost": 2}], w)
    assert errs == []


# --- materialize: missing id ---

def test_validate_materialize_missing_id():
    """materialize item without 'id' yields ValidationError code=missing field=[0].id."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("materialize", [{}], w)
    assert any(e.code == "missing" and e.field == "[0].id" for e in errs)


def test_validate_materialize_missing_id_second_item():
    """Missing id on item index 1 produces field=[1].id."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("materialize", [{"id": "王都"}, {}], w)
    assert any(e.code == "missing" and e.field == "[1].id" for e in errs)
    # first item is fine, no error on it
    assert not any(e.field == "[0].id" for e in errs)


def test_validate_materialize_complete_valid_no_errors():
    """A complete materialize item with existing id passes validation clean."""
    ps = PlaceSystem()
    w = _world_with_two_places()
    errs = ps.validate("materialize", [{"id": "王都"}], w)
    assert errs == []


# --- to_events + apply do not raise on malformed input ---

def test_apply_place_created_missing_id_no_raise():
    """apply(place_created) with no id in deltas skips without raising."""
    r = _reg()
    # Build a place_created event with no id in deltas
    ev = kernel_event("place_created", day=1, scene="s1",
                      summary="bad", deltas={"level": 1, "kind": "settlement"}, turn=1)
    w = project(r, [ev])  # must not raise
    g: FactGraph = w["systems"]["ontology"]
    # No place entity should have been added (id was missing)
    # The graph should contain no Place entities
    places = [e for e in g.entities.values() if e.etype == "Place"]
    assert places == []


def test_apply_place_materialized_missing_id_no_raise():
    """apply(place_materialized) with no id in deltas skips without raising."""
    r = _reg()
    ev_create = kernel_event("place_created", day=1, scene="s1",
                             summary="create", deltas={"id": "王都", "level": 1,
                             "kind": "settlement"}, turn=1)
    ev_mat = kernel_event("place_materialized", day=2, scene="s1",
                          summary="bad mat", deltas={}, turn=1)
    w = project(r, [ev_create, ev_mat])  # must not raise
    g: FactGraph = w["systems"]["ontology"]
    # 王都 detail should not have been changed to "full"
    e = g.get_entity("王都")
    assert e is not None
    assert e.attrs.get("detail") != "full"


def test_apply_place_linked_missing_ab_no_raise():
    """apply(place_linked) with no a/b in deltas skips without raising."""
    r = _reg()
    ev = kernel_event("place_linked", day=1, scene="s1",
                      summary="bad link", deltas={}, turn=1)
    w = project(r, [ev])  # must not raise


def test_to_events_links_missing_ab_no_raise():
    """to_events('links') with missing a and b produces an event but does not raise."""
    ps = PlaceSystem()
    # Both a and b absent — to_events should produce an event (it uses .get())
    evs = ps.to_events("links", [{}], turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "place_linked"


def test_to_events_materialize_missing_id_no_raise():
    """to_events('materialize') with missing id produces an event but does not raise."""
    ps = PlaceSystem()
    evs = ps.to_events("materialize", [{}], turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "place_materialized"


def test_to_events_places_missing_id_no_raise():
    """to_events('places') with missing id produces an event but does not raise."""
    ps = PlaceSystem()
    evs = ps.to_events("places", [{"level": 1, "kind": "settlement"}],
                       turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "place_created"


# ---------------------------------------------------------------------------
# Phase D Task 1: last_update stamping on place events
# ---------------------------------------------------------------------------

def test_place_created_stamps_last_update():
    reg = _reg()
    world = project(reg, [
        kernel_event("place_created", day=3, scene="s1", summary="创建",
                     deltas={"id": "town", "tier": "tracked"}, turn=1),
    ])
    assert world["systems"]["ontology"].get_entity("town").attrs.get("last_update") == 3


def test_entity_moved_stamps_destination_last_update():
    reg = _reg()
    world = project(reg, [
        kernel_event("place_created", day=1, scene="s1", summary="创建",
                     deltas={"id": "town", "tier": "tracked"}, turn=1),
        kernel_event("entity_moved", day=4, scene="s1", summary="移动",
                     deltas={"who": "protagonist", "to": "town"}, turn=2),
    ])
    assert world["systems"]["ontology"].get_entity("town").attrs.get("last_update") == 4


# ---------------------------------------------------------------------------
# Phase D Task 2: arrive_day opt-in in to_events moves
# ---------------------------------------------------------------------------

def test_moves_with_arrive_day_stamps_event_day():
    ps = PlaceSystem()
    evs = ps.to_events("moves",
                       [{"who": "protagonist", "to": "town", "arrive_day": 4}],
                       turn=2, day=1, scene="s1")
    assert evs[0]["type"] == "entity_moved"
    assert evs[0]["day"] == 4


def test_moves_without_arrive_day_keeps_scene_day():
    ps = PlaceSystem()
    evs = ps.to_events("moves", [{"who": "p", "to": "town"}],
                       turn=2, day=1, scene="s1")
    assert evs[0]["day"] == 1


def test_moves_arrive_day_never_goes_backward():
    ps = PlaceSystem()
    evs = ps.to_events("moves", [{"who": "p", "to": "town", "arrive_day": 1}],
                       turn=2, day=6, scene="s1")
    assert evs[0]["day"] == 6


# ---------------------------------------------------------------------------
# Density Task 1: place_created density attr
# ---------------------------------------------------------------------------

def test_place_created_with_density_stores_attr():
    """place_created with density=0.5 → entity.attrs['density'] == 0.5"""
    r = _reg()
    evs = [_ev("place_created", id="region1", level=1, kind="region",
               seed="北境", tier="tracked", density=0.5)]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("region1")
    assert e is not None
    assert e.attrs.get("density") == 0.5


def test_place_created_without_density_has_no_attr():
    """place_created without density → key absent from attrs (resolve_density uses default)."""
    r = _reg()
    evs = [_ev("place_created", id="region2", level=1, kind="region",
               seed="南境", tier="tracked")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("region2")
    assert e is not None
    assert "density" not in e.attrs


def test_place_created_density_zero_stored():
    """density=0.0 should be stored (falsy but valid)."""
    r = _reg()
    evs = [_ev("place_created", id="region3", level=1, kind="region",
               seed="荒区", tier="tracked", density=0.0)]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("region3")
    assert "density" in e.attrs
    assert e.attrs["density"] == 0.0


# ---------------------------------------------------------------------------
# A2 fix: moves.arrive_day type validation
# ---------------------------------------------------------------------------

def test_moves_arrive_day_non_int_string_returns_validation_error():
    """A2: arrive_day='下周' (non-int string) must fail validation with an error
    mentioning 'arrive_day' — not crash inside to_events."""
    r = _reg()
    ps = PlaceSystem()
    # No graph needed: the type error should be caught before dangling-ref checks.
    world_no_graph = {}
    item = {"who": "hero", "to": "town", "arrive_day": "下周"}
    errs = ps.validate("moves", [item], world_no_graph)
    assert errs, "Expected at least one ValidationError for non-int arrive_day"
    fields = [e.field for e in errs]
    assert any("arrive_day" in f for f in fields), \
        f"Expected an error mentioning 'arrive_day'; got fields={fields}"


def test_moves_arrive_day_int_passes_validation():
    """A2: arrive_day as a plain int must NOT produce a ValidationError."""
    ps = PlaceSystem()
    world_no_graph = {}
    item = {"who": "hero", "to": "town", "arrive_day": 5}
    errs = ps.validate("moves", [item], world_no_graph)
    # dangling_ref errors may appear (no graph) — but no arrive_day error
    arrive_errs = [e for e in errs if "arrive_day" in e.field]
    assert arrive_errs == [], f"Unexpected arrive_day errors: {arrive_errs}"


def test_moves_arrive_day_digit_string_passes_validation():
    """A2: arrive_day='7' (digit string, int()-coercible) must pass."""
    ps = PlaceSystem()
    world_no_graph = {}
    item = {"who": "hero", "to": "town", "arrive_day": "7"}
    errs = ps.validate("moves", [item], world_no_graph)
    arrive_errs = [e for e in errs if "arrive_day" in e.field]
    assert arrive_errs == [], f"Unexpected arrive_day errors for digit string: {arrive_errs}"


def test_moves_arrive_day_absent_passes_validation():
    """A2: omitting arrive_day entirely must not produce any error about it."""
    ps = PlaceSystem()
    world_no_graph = {}
    item = {"who": "hero", "to": "town"}
    errs = ps.validate("moves", [item], world_no_graph)
    arrive_errs = [e for e in errs if "arrive_day" in e.field]
    assert arrive_errs == [], f"Unexpected arrive_day errors when field absent: {arrive_errs}"
