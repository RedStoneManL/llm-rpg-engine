from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.turncommit import TurnCommit


def test_base_system_defaults_are_inert():
    s = ContextSystem()
    assert s.event_types() == set()
    assert s.commit_sections() == set()
    assert s.empty_state() == {}
    assert s.validate("x", None, {}) == []
    assert s.to_events("x", None, turn=1, day=1, scene="s1") == []
    assert s.inject({}, {}) is None
    assert s.recall("q", {}) == []
    assert s.digest_extract("prose", {}) == {}


def test_dataclasses_carry_fields():
    e = ValidationError(section="cast", field="[0].who", code="missing", hint="needs who")
    assert e.section == "cast" and e.code == "missing"
    f = Fragment(system="notes", layer="scene", text="hi", affordance="can note")
    assert f.layer == "scene" and f.affordance == "can note"
    h = RecallHit(system="notes", score=0.9, text="t", ref={"id": 1})
    assert h.score == 0.9 and h.ref["id"] == 1


def test_turncommit_from_dict_splits_narration_and_sections():
    tc = TurnCommit.from_dict({"narration": "你推开门", "cast": [{"who": "Ela"}]})
    assert tc.narration == "你推开门"
    assert tc.sections == {"cast": [{"who": "Ela"}]}
    assert TurnCommit().sections == {}
