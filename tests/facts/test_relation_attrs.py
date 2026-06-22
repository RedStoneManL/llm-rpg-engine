"""Task 1: Relation carries attrs (travel_cost round-trip)."""
import pytest
from facts.graph import FactGraph


def test_relation_attrs_stored_and_retrieved():
    g = FactGraph()
    g.add_entity("A", "Place")
    g.add_entity("B", "Place")
    g.add_relation("A", "adjacent_to", "B", day=1, turn=1, source_event="e", travel_cost=2)
    result = g.relation_attrs_at("A", "adjacent_to", 1)
    assert result == [("B", {"travel_cost": 2})]


def test_relation_attrs_empty_when_none_given():
    g = FactGraph()
    g.add_entity("X", "Place")
    g.add_entity("Y", "Place")
    g.add_relation("X", "adjacent_to", "Y", day=1, turn=1, source_event="e")
    result = g.relation_attrs_at("X", "adjacent_to", 1)
    assert result == [("Y", {})]


def test_relation_attrs_not_visible_before_start_day():
    g = FactGraph()
    g.add_entity("A", "Place")
    g.add_entity("B", "Place")
    g.add_relation("A", "adjacent_to", "B", day=5, turn=1, source_event="e", travel_cost=3)
    assert g.relation_attrs_at("A", "adjacent_to", 1) == []
    assert g.relation_attrs_at("A", "adjacent_to", 5) == [("B", {"travel_cost": 3})]
