from facts.entity import Entity

def test_entity_defaults_to_mentioned_tier():
    e = Entity(id="艾拉", etype="Person")
    assert e.tier == "mentioned" and e.attrs == {}

def test_entity_carries_type_tier_attrs():
    e = Entity(id="王都", etype="Place", tier="tracked", attrs={"level": 2})
    assert e.etype == "Place" and e.tier == "tracked" and e.attrs["level"] == 2
