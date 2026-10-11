import asyncio
import pytest

from app.engine import build_engine
from engine.store import EventStore
from engine.store import EventBatch
from kernel.events import kernel_event
from kernel.projection import project
from kernel.registry import Registry
from systems.interpretive_memory import (InterpretiveMemorySystem, records_for_actor,
                                         open_memory_read_session)
from systems.interpretive_memory import open_memory_write_session


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
        other_session = open_memory_read_session(world, store, "npc")
        with pytest.raises(PermissionError):
            other_session.read(handle, world, store)
        with EventStore(tmp_path / "other.db", tmp_path / "other.jsonl", registry.event_types()) as other_store:
            with pytest.raises(PermissionError):
                session.read(handle, world, other_store)
        session.close()
        with pytest.raises(PermissionError):
            session.read(handle, world, store)


def test_default_model_tool_registry_cannot_issue_or_read_memory_handles(tmp_path):
    engine = build_engine(tmp_path / "campaign")
    try:
        from app.play import _build_scene
        from llm.tools import build_tool_registry
        tools = build_tool_registry(engine.registry, engine.world, _build_scene(engine), dm=True)
        assert "character_interpretation_recorded" not in engine.registry.event_types()
        assert not any("memory" in name for name in tools._tools)
    finally:
        engine.store.close()


def test_host_write_session_requires_unchanged_current_batch_source(tmp_path):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl",
                    {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store)
        source = kernel_event("fact_asserted", day=1, scene="inn", summary="source",
            deltas={"subject":"hero", "predicate":"mood", "value":"calm"}, turn=1)
        batch.append(source)
        source = batch.events[0]
        writer = open_memory_write_session(batch, registry, "hero", source)
        writer.stage("host interpretation", "m1")
        assert [e["type"] for e in batch.events] == ["fact_asserted", "character_interpretation_recorded"]
        batch.publish(action_id="memory-write")
        assert len(list(store.iter_events())) == 2


def test_host_write_session_rejects_changed_or_cross_batch_source(tmp_path):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl",
                    {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store)
        source = kernel_event("fact_asserted", day=1, scene="inn", summary="source",
            deltas={"subject":"hero", "predicate":"mood", "value":"calm"}, turn=1)
        batch.append(source); source = batch.events[0]
        writer = open_memory_write_session(batch, registry, "hero", source)
        source["summary"] = "tampered"
        with pytest.raises(PermissionError): writer.stage("x", "m1")
        assert len(batch.events) == 1
        with pytest.raises(PermissionError):
            open_memory_write_session(EventBatch(store), registry, "hero", source)


def test_memory_publish_rechecks_source_and_keeps_store_unchanged(tmp_path):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
        batch.append(source); source = batch.events[0]
        open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
        source["summary"] = "tampered"
        with pytest.raises(PermissionError): batch.publish(action_id="bad")
        assert list(store.iter_events()) == [] and store.revision == 0


@pytest.mark.parametrize("exc", [RuntimeError, asyncio.CancelledError])
def test_memory_stage_savepoint_rollback_removes_validator(tmp_path, exc):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
        batch.append(source); source = batch.events[0]
        with pytest.raises(exc):
            with batch.savepoint():
                open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
                raise exc()
        assert len(batch.events) == 1 and batch.publish_validators == []
        batch.publish(action_id="ordinary")
        assert len(list(store.iter_events())) == 1


def test_preflight_mutation_cannot_bypass_memory_final_validation(tmp_path):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
        batch.append(source); source = batch.events[0]
        open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
        def mutate(history): source["summary"] = "changed-by-preflight"
        batch.preflight = mutate
        with pytest.raises(PermissionError): batch.publish(action_id="blocked")
        assert list(store.iter_events()) == [] and store.revision == 0


def test_original_preflight_error_is_not_masked_by_memory_validator(tmp_path):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
        batch.append(source); source = batch.events[0]
        open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
        batch.preflight = lambda history: (_ for _ in ()).throw(RuntimeError("original preflight"))
        with pytest.raises(RuntimeError, match="original preflight"): batch.publish(action_id="blocked")
        assert list(store.iter_events()) == [] and store.revision == 0


@pytest.mark.parametrize("target,path,value", [
    ("source", ("deltas", "value"), "changed"),
    ("record", ("deltas", "actor"), "npc"),
    ("record", ("deltas", "source_event_id"), "other"),
    ("record", ("type",), "fact_asserted"),
    ("record", ("id",), "changed-id"),
    ("record", ("turn",), 99),
])
def test_preflight_payload_mutation_is_rejected_atomically(tmp_path, target, path, value):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
        batch.append(source); source = batch.events[0]; open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
        def mutate(history):
            event = history[0 if target == "source" else 1]
            for key in path[:-1]:
                event = event[key]
            event[path[-1]] = value
        batch.preflight = mutate
        with pytest.raises(PermissionError): batch.publish(action_id="payload-mutation")
        assert list(store.iter_events(include_retracted=True)) == [] and store.revision == 0
        assert store._conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        assert not (tmp_path / "events.jsonl").exists()


@pytest.mark.parametrize("mutation", ["text", "actor", "source", "remove_source", "retract_source", "turn", "revision", "registry", "store"])
def test_memory_write_lifecycle_mutations_are_rejected(tmp_path, mutation):
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
        batch.append(source); source = batch.events[0]; writer = open_memory_write_session(batch, registry, "hero", source); writer.stage("private", "m1")
        record = batch.events[-1]
        if mutation == "text": record["deltas"]["text"] = "changed"
        elif mutation == "actor": record["deltas"]["actor"] = "npc"
        elif mutation == "source": record["deltas"]["source_event_id"] = "other"
        elif mutation == "remove_source": batch.events.remove(source)
        elif mutation == "retract_source": source["retracted"] = True
        elif mutation == "turn": batch.turn += 1
        elif mutation == "revision": batch.revision += 1
        elif mutation == "registry": writer.registry = Registry()
        elif mutation == "store": batch.store = object()
        with pytest.raises(PermissionError): batch.publish(action_id="blocked")
        assert list(store.iter_events()) == [] and store.revision == 0


@pytest.mark.parametrize("exc", [RuntimeError, asyncio.CancelledError])
def test_failed_savepoint_preserves_missing_retracted_for_existing_memory_writer(tmp_path, exc):
    """A pre-savepoint writer remains publishable when its source omits the flag."""
    registry = _registry()
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl",
                    {"fact_asserted", "character_interpretation_recorded"}) as store:
        batch = EventBatch(store)
        batch.append({"id": "manual-source", "type": "fact_asserted", "day": 1,
                      "scene": "s", "actors": [], "summary": "source",
                      "deltas": {"subject": "hero", "predicate": "mood", "value": "ok"},
                      "turn": 1})
        source = batch.events[0]
        open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
        validators_before = list(batch.publish_validators)
        with pytest.raises(exc):
            with batch.savepoint():
                batch.retract_from_turn(1)
                raise exc("failed backstage hook")
        assert "retracted" not in source
        assert batch.events[1]["retracted"] is False
        assert batch.retractions == [] and batch.publish_validators == validators_before
        batch.publish(action_id="missing-retracted-source")
        assert len(list(store.iter_events())) == 2


def test_failed_savepoint_restores_retracted_field_presence_and_value(tmp_path):
    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted"}) as store:
        batch = EventBatch(store)
        def source(event_id, retracted):
            event = {"id": event_id, "type": "fact_asserted", "day": 1, "scene": "s",
                     "actors": [], "summary": event_id, "deltas": {}, "turn": 1}
            if retracted is not None:
                event["retracted"] = retracted
            return event
        batch.append(source("missing", None))
        batch.append(source("false", False))
        batch.append(source("true", True))
        with pytest.raises(RuntimeError):
            with batch.savepoint():
                batch.retract_from_turn(1)
                raise RuntimeError("failed backstage hook")
        assert "retracted" not in batch.events[0]
        assert batch.events[1]["retracted"] is False
        assert batch.events[2]["retracted"] is True
        assert batch.retractions == [] and batch.publish_validators == []
