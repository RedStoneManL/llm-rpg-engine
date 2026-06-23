"""#R7 Phase 2 — co-located walk-ons (mentioned Persons in the scene but not in
`present`) get a terse "也在场" continuity line, so the model remembers them
without the full-card bloat reserved for tracked NPCs. Off-scene entities stay
hidden (anti-bloat / filtering)."""
from facts.graph import FactGraph
from systems.character import CharacterSystem


def _rel(g, who, place):
    g.add_relation(who, "located_in", place, day=1, turn=0, source_event="t")


def test_inject_present_cards_plus_terse_ambient_walkons():
    g = FactGraph()
    g.add_entity("protagonist", "Person", tier="tracked")
    g.add_entity("venue_0", "Place")
    g.add_entity("elsewhere", "Place")
    # tracked present NPC -> full card
    g.add_entity("npc_boss", "Person", tier="tracked")
    g.assert_fact("npc_boss", "sketch", "独臂老板", day=1, turn=0, source_event="t")
    g.assert_fact("npc_boss", "goal", "守店", day=1, turn=0, source_event="t")
    _rel(g, "npc_boss", "venue_0")
    # mentioned walk-on, co-located, with 真名 -> terse ambient
    g.add_entity("npc_auto_1_1", "Person", tier="mentioned")
    g.assert_fact("npc_auto_1_1", "真名", "卡恩", day=1, turn=0, source_event="t")
    _rel(g, "npc_auto_1_1", "venue_0")
    # mentioned walk-on elsewhere -> hidden
    g.add_entity("npc_auto_1_2", "Person", tier="mentioned")
    g.assert_fact("npc_auto_1_2", "真名", "别处的人", day=1, turn=0, source_event="t")
    _rel(g, "npc_auto_1_2", "elsewhere")

    world = {"systems": {"ontology": g}}
    scene = {"protagonist": "protagonist", "present": ["npc_boss"],
             "location": "venue_0", "day": 1}
    frag = CharacterSystem().inject(scene, world)
    assert frag is not None
    text = frag.text
    assert "独臂老板" in text          # tracked present -> full card
    assert "卡恩" in text              # co-located walk-on -> terse line
    assert "也在场" in text
    assert "别处的人" not in text      # off-scene -> hidden (anti-bloat)


def test_inject_ambient_only_when_no_present_cards():
    g = FactGraph()
    g.add_entity("protagonist", "Person", tier="tracked")
    g.add_entity("venue_0", "Place")
    g.add_entity("npc_auto_x", "Person", tier="mentioned")
    g.assert_fact("npc_auto_x", "真名", "卡恩", day=1, turn=0, source_event="t")
    _rel(g, "npc_auto_x", "venue_0")
    world = {"systems": {"ontology": g}}
    scene = {"protagonist": "protagonist", "present": [],
             "location": "venue_0", "day": 1}
    frag = CharacterSystem().inject(scene, world)
    assert frag is not None and "卡恩" in frag.text


def test_inject_none_when_nothing_present_or_ambient():
    g = FactGraph()
    g.add_entity("protagonist", "Person", tier="tracked")
    g.add_entity("venue_0", "Place")
    world = {"systems": {"ontology": g}}
    scene = {"protagonist": "protagonist", "present": [], "location": "venue_0", "day": 1}
    assert CharacterSystem().inject(scene, world) is None
