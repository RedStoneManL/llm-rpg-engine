import pytest

from engine.schema import validate_event
from kernel.events import kernel_event, open_store


def test_validate_event_accepts_custom_types():
    ev = kernel_event("place_created", day=1, scene="s1", summary="王都·酒馆")
    # default frozenset rejects the new type
    with pytest.raises(ValueError):
        validate_event(ev)
    # but an explicit allow-set accepts it
    validate_event(ev, allowed_types={"place_created"})


def test_open_store_appends_registry_typed_events(tmp_path):
    store = open_store(tmp_path / "events.db", tmp_path / "events.jsonl",
                       allowed_types={"place_created", "note_added"})
    seq = store.append(kernel_event("place_created", day=1, scene="s1", summary="王都"))
    assert seq == 1
    got = list(store.iter_events())
    assert got[0]["type"] == "place_created" and got[0]["summary"] == "王都"
    store.close()


def test_open_store_still_rejects_unknown_type(tmp_path):
    store = open_store(tmp_path / "e.db", tmp_path / "e.jsonl", allowed_types={"place_created"})
    with pytest.raises(ValueError):
        store.append(kernel_event("not_registered", day=1, scene="s1", summary="x"))
    store.close()
