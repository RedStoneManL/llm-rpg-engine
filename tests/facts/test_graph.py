import pytest
from facts.graph import FactGraph
from facts.entity import Entity

def _g():
    g = FactGraph()
    g.add_entity("艾拉", "Person", tier="tracked")
    return g

def test_add_get_entity_and_set_tier():
    g = _g()
    assert g.get_entity("艾拉").tier == "tracked"
    g.set_tier("艾拉", "retired")
    assert g.get_entity("艾拉").tier == "retired"
    assert g.get_entity("nope") is None

def test_assert_fact_supersedes_prior_current():
    g = _g()
    g.assert_fact("艾拉", "trust", "中", day=1, turn=1, source_event="e1")
    g.assert_fact("艾拉", "trust", "依赖", day=5, turn=2, source_event="e2")
    cur = g.current_facts("艾拉")
    assert len(cur) == 1 and cur[0].value == "依赖"
    # history preserved, point-in-time intact
    assert g.value_at("艾拉", "trust", 1) == "中"
    assert g.value_at("艾拉", "trust", 5) == "依赖"
    assert len(g.fact_history("艾拉", "trust")) == 2

def test_different_predicates_coexist():
    g = _g()
    g.assert_fact("艾拉", "trust", "中", day=1, turn=1, source_event="e1")
    g.assert_fact("艾拉", "mood", "警惕", day=1, turn=1, source_event="e1")
    assert {f.predicate for f in g.current_facts("艾拉")} == {"trust", "mood"}

def test_relations_bitemporal_and_neighbors():
    g = _g(); g.add_entity("王都", "Place", tier="tracked")
    g.add_relation("艾拉", "located_in", "王都", day=2, turn=1, source_event="e3")
    assert g.neighbors("艾拉", "located_in", day=2) == ["王都"]
    assert g.neighbors("艾拉", "located_in", day=1) == []
    # moving supersedes the prior location
    g.add_entity("边境城", "Place")
    g.add_relation("艾拉", "located_in", "边境城", day=9, turn=2, source_event="e4")
    assert g.neighbors("艾拉", "located_in", day=9) == ["边境城"]


# ---------------------------------------------------------------------------
# I1: monotonic-day invariant in assert_fact and add_relation
# ---------------------------------------------------------------------------

def test_assert_fact_non_monotonic_raises():
    """Asserting a fact at a day earlier than the prior start must raise ValueError (I1)."""
    g = _g()
    g.assert_fact("艾拉", "trust", "中", day=5, turn=1, source_event="e1")
    with pytest.raises(ValueError, match="non-monotonic"):
        g.assert_fact("艾拉", "trust", "低", day=3, turn=2, source_event="e2")


def test_add_relation_non_monotonic_raises():
    """add_relation at an earlier day than prior start must raise ValueError (I1)."""
    g = _g()
    g.add_entity("王都", "Place")
    g.add_relation("艾拉", "located_in", "王都", day=5, turn=1, source_event="e1")
    g.add_entity("边境城", "Place")
    with pytest.raises(ValueError, match="non-monotonic"):
        g.add_relation("艾拉", "located_in", "边境城", day=3, turn=2, source_event="e2")


def test_assert_fact_same_day_allowed():
    """Re-asserting at the SAME day (same start) is allowed — no inversion."""
    g = _g()
    g.assert_fact("艾拉", "trust", "中", day=5, turn=1, source_event="e1")
    # same day — should not raise
    g.assert_fact("艾拉", "trust", "高", day=5, turn=2, source_event="e2")
    assert g.value_at("艾拉", "trust", 5) == "高"


# ---------------------------------------------------------------------------
# M1: dedup multi-valued relations (supersede=False)
# ---------------------------------------------------------------------------

def test_relink_same_pair_no_duplicate():
    """Re-linking A-adjacent_to-B twice updates rather than duplicating (M1)."""
    g = FactGraph()
    g.add_entity("A", "Place")
    g.add_entity("B", "Place")
    g.add_relation("A", "adjacent_to", "B", day=1, turn=1,
                   source_event="e1", supersede=False, travel_cost=2)
    g.add_relation("A", "adjacent_to", "B", day=3, turn=2,
                   source_event="e2", supersede=False, travel_cost=5)
    # at day 3, must be exactly one B with the latest attrs
    result = g.neighbors("A", "adjacent_to", 3)
    assert result.count("B") == 1


def test_relink_same_pair_latest_attrs():
    """The surviving entry at the later day has the new attrs (M1)."""
    g = FactGraph()
    g.add_entity("A", "Place")
    g.add_entity("B", "Place")
    g.add_relation("A", "adjacent_to", "B", day=1, turn=1,
                   source_event="e1", supersede=False, travel_cost=2)
    g.add_relation("A", "adjacent_to", "B", day=3, turn=2,
                   source_event="e2", supersede=False, travel_cost=5)
    pairs = g.relation_attrs_at("A", "adjacent_to", 3)
    assert pairs == [("B", {"travel_cost": 5})]


def test_relink_different_dst_both_present():
    """Relinking to a DIFFERENT destination should keep both neighbors (M1 doesn't over-prune)."""
    g = FactGraph()
    g.add_entity("A", "Place")
    g.add_entity("B", "Place")
    g.add_entity("C", "Place")
    g.add_relation("A", "adjacent_to", "B", day=1, turn=1,
                   source_event="e1", supersede=False, travel_cost=1)
    g.add_relation("A", "adjacent_to", "C", day=2, turn=2,
                   source_event="e2", supersede=False, travel_cost=3)
    neighbors = g.neighbors("A", "adjacent_to", 2)
    assert "B" in neighbors
    assert "C" in neighbors
