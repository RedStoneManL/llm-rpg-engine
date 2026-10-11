import pytest

from app.engine import build_engine
from engine.store import EventStore
from kernel.events import kernel_event
from kernel.projection import project
from kernel.registry import Registry
from systems.interpretive_memory import (InterpretiveMemorySystem, records_for_actor,
                                         open_memory_read_session)


def _registry():
    return Registry().register(InterpretiveMemorySystem())


def _memory(actor, text, *, memory_id, turn=1):
    return kernel_event("character_interpretation_recorded", day=1, scene="inn",
                        summary="private character interpretation",
                        deltas={"actor": actor, "text": text, "memory_id": memory_id},
                        turn=turn)


def test_default_engine_does_not_admit_interpretive_memory(tmp_path):
    engine = build_engine(tmp_path / "campaign")
    try:
        assert engine.registry.owner_of_event("character_interpretation_recorded") is None
        with pytest.raises(ValueError):
            engine.store.append(_memory("hero", "private thought", memory_id="m1"))
    finally:
        engine.store.close()


def test_opt_in_memory_is_actor_scoped_and_not_a_fact(tmp_path):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", registry.event_types()) as store:
        store.append_many([_memory("hero", "hero only", memory_id="m1"),
                           _memory("npc", "npc only", memory_id="m2")])
        world = project(registry, store.iter_events())
        assert [m["text"] for m in records_for_actor(world, "hero")] == ["hero only"]
        assert records_for_actor(world, "npc")[0]["text"] == "npc only"
        assert "ontology" not in world["systems"]


def test_opt_in_memory_replays_after_reopen_and_retraction(tmp_path):
    registry = _registry()
    db, jsonl = tmp_path / "events.db", tmp_path / "events.jsonl"
    store = EventStore(db, jsonl, registry.event_types())
    store.append(_memory("hero", "withdrawn", memory_id="m1", turn=1))
    store.retract_from_turn(1)
    store.close()
    with EventStore(db, jsonl, registry.event_types()) as reopened:
        world = project(registry, reopened.iter_events())
        assert records_for_actor(world, "hero") == []
        assert list(reopened.iter_events(include_retracted=True))[0]["retracted"] is True


def test_host_handle_is_bound_to_exact_world_store_role_and_lifecycle(tmp_path):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", registry.event_types()) as store:
        store.append_many([_memory("hero", "hero only", memory_id="m1"),
                           _memory("npc", "npc only", memory_id="m2")])
        world = project(registry, store.iter_events())
        session = open_memory_read_session(world, store, "hero")
        handle = session.issue_handle()
        assert [r["text"] for r in session.read(handle, world, store)] == ["hero only"]
        with pytest.raises(PermissionError):
            session.read(object(), world, store)
        with pytest.raises(PermissionError):
            session.read(handle, project(registry, store.iter_events()), store)
        session.close()
        with pytest.raises(PermissionError):
            session.read(handle, world, store)
