from engine.store import EventStore
from kernel.events import kernel_event
from systems.interpretive_memory import public_event_history


def test_public_history_filters_active_and_retracted_private_rows_from_real_store(tmp_path):
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl",
                    allowed_types={"action", "character_interpretation_recorded"}) as store:
        normal = kernel_event("action", day=1, scene="inn", summary="PUBLIC_BODY", turn=1)
        private = kernel_event("character_interpretation_recorded", day=1, scene="inn",
                               summary="PRIVATE_SUMMARY", deltas={"text":"PRIVATE_BODY"}, turn=1)
        store.append_many([normal, private])
        store.retract_from_turn(1)
        shown = public_event_history(store.iter_events(include_retracted=True))
    assert [event["id"] for event in shown] == [normal["id"]]
    assert shown[0]["summary"] == "PUBLIC_BODY" and shown[0]["retracted"] is True
    assert "PRIVATE_BODY" not in str(shown) and private["id"] not in str(shown)


def test_public_history_rejects_explicit_private_reference():
    private = kernel_event("character_interpretation_recorded", day=1, scene="s", summary="p", turn=1, id="private-id")
    normal = kernel_event("action", day=1, scene="s", summary="n", turn=1, thread_refs=["private-id"])
    import pytest
    with pytest.raises(ValueError):
        public_event_history([normal, private])
