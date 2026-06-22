from facts.fact import Fact, Relation

def test_fact_is_current_when_no_end():
    f = Fact(subject="艾拉", predicate="trust", value="中", event_time_start=1, ingest_turn=1, source_event="e1")
    assert f.is_current() is True
    f.event_time_end = 5
    assert f.is_current() is False

def test_fact_valid_at_respects_bitemporal_window():
    f = Fact(subject="桥", predicate="status", value="断", event_time_start=5, ingest_turn=9, source_event="e2")
    assert f.valid_at(4) is False and f.valid_at(5) is True and f.valid_at(99) is True
    f.event_time_end = 10
    assert f.valid_at(9) is True and f.valid_at(10) is False

def test_relation_is_bitemporal_like_fact():
    r = Relation(src="剑", rel="held_by", dst="艾拉", event_time_start=2, ingest_turn=2, source_event="e3")
    assert r.is_current() and r.valid_at(2) and not r.valid_at(1)
