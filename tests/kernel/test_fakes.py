from kernel.registry import Registry
from tests.kernel.fakes import FakeNoteSystem


def test_fake_note_system_roundtrips_through_registry():
    s = FakeNoteSystem()
    r = Registry().register(s)
    assert r.owner_of_event("note_added") is s
    assert r.owner_of_section("notes") is s
    state = s.empty_state()
    world = {"systems": {s.name: state}}
    evs = s.to_events("notes", [{"text": "门开了"}], turn=1, day=1, scene="s1")
    assert evs[0]["type"] == "note_added"
    s.apply(world, evs[0])
    assert state["notes"] == ["门开了"]
