import pytest
from engine.schema import make_event, validate_event, EVENT_TYPES


def test_make_event_fills_defaults_and_id():
    ev = make_event("action", day=1, scene="s001", actors=["雷德"], summary="出场")
    assert ev["id"].startswith("ev_") and len(ev["id"]) > 3
    assert ev["type"] == "action" and ev["day"] == 1
    assert ev["deltas"] == {} and ev["thread_refs"] == [] and ev["retracted"] is False


def test_make_event_preserves_explicit_id_and_deltas():
    ev = make_event("relationship_change", day=2, scene="s002", actors=["艾拉"],
                    summary="信任提升", deltas={"艾拉.trust": "高→极高"}, id="ev_fixed01")
    assert ev["id"] == "ev_fixed01"
    assert ev["deltas"]["艾拉.trust"] == "高→极高"


def test_validate_rejects_unknown_type():
    with pytest.raises(ValueError):
        make_event("teleport", day=1, scene="s1", actors=[], summary="x")


def test_validate_rejects_missing_summary():
    with pytest.raises(ValueError):
        validate_event({"id": "ev_1", "type": "action", "day": 1, "scene": "s1",
                        "actors": [], "summary": ""})


def test_day_zero_is_valid():
    ev = make_event("world_fact", day=0, scene="s000", actors=[], summary="穿越")
    assert ev["day"] == 0


def test_known_types_present():
    for t in ("relationship_change", "thread_open", "villain_knowledge_gain", "landmark"):
        assert t in EVENT_TYPES


def test_make_event_accepts_turn():
    ev = make_event("action", 1, "s1", ["雷德"], "出场", turn=7)
    assert ev["turn"] == 7

def test_make_event_turn_defaults_none():
    assert make_event("action", 1, "s1", ["雷德"], "出场")["turn"] is None
