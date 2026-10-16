from app.player_character import player_character_facts
from facts.graph import FactGraph


def test_player_character_facts_bounds_public_field_names_and_values():
    graph = FactGraph()
    graph.add_entity("hero", "Person", tier="tracked")
    long_name = "状态" * 400
    long_value = "公开状态" * 300
    graph.assert_fact(
        "hero", long_name, long_value, day=1, turn=1,
        source_event="public-long", secrecy="public",
    )

    facts = player_character_facts({"systems": {"ontology": graph}}, "hero")

    assert facts == [{
        "name": long_name[:499] + "…",
        "value": long_value[:499] + "…",
    }]
    assert all(len(item[field]) == 500 for item in facts for field in ("name", "value"))


def test_player_character_facts_bounds_serialized_structure_too():
    graph = FactGraph()
    graph.add_entity("hero", "Person", tier="tracked")
    graph.assert_fact(
        "hero", "装备", {"公开": "线索" * 400}, day=1, turn=1,
        source_event="public-structure", secrecy="public",
    )

    facts = player_character_facts({"systems": {"ontology": graph}}, "hero")

    assert facts[0]["name"] == "装备"
    assert len(facts[0]["value"]) == 500
    assert facts[0]["value"].endswith("…")
