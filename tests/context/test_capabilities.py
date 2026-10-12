"""Scene capability contract: one POV topology for prompt, UI and publication."""
from __future__ import annotations

import pytest

from app.engine import build_engine
from app.gui import _seed_demo_campaign
from app.play import _build_scene
from context.capabilities import scene_capabilities, validate_protagonist_moves
from engine.store import EventBatch
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.turn import REQUIRED_SECTIONS, TurnRejected, run_turn


def _commit(*, destination: str, places=None, links=None, narration="测试移动") -> TurnCommit:
    return TurnCommit(narration, {
        "moves": [{"who": "protagonist", "to": destination}],
        "places": places or [], "links": links or [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试"}],
    })


def _demo_engine(tmp_path):
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None)
    _seed_demo_campaign(engine)
    return engine


def test_scene_capability_is_pov_filtered_and_matches_map_contract(tmp_path):
    engine = _demo_engine(tmp_path)
    capability = scene_capabilities(engine.world, _build_scene(engine))

    assert capability.location == "stonebridge_inn"
    assert capability.allowed_move_ids == frozenset({"stonebridge_market"})
    assert {node["id"] for node in capability.map_dto()["nodes"]} == {
        "stonebridge_inn", "stonebridge_market",
    }
    assert capability.map_dto()["edges"] == [{
        "from": "stonebridge_inn", "to": "stonebridge_market", "cost": "1",
    }]
    rendered = capability.prompt_fragment()
    assert "stonebridge_market" in rendered
    assert "north_bank_old_tower" not in rendered
    engine.store.close()


def test_protagonist_move_outside_pov_capability_is_repairable_but_npc_is_not(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    hidden = _commit(destination="north_bank_old_tower")
    errors = validate_protagonist_moves(hidden, engine.world, scene)
    assert [(error.section, error.field, error.code) for error in errors] == [
        ("moves", "[0].to", "outside_scene_capability"),
        ("narration", "", "scene_capability_narration"),
    ]

    npc_move = TurnCommit("NPC 行动", {"moves": [{"who": "tower_warden", "to": "stonebridge_market"}]})
    assert validate_protagonist_moves(npc_move, engine.world, scene) == []
    engine.store.close()


def test_new_discovery_requires_same_turn_direct_route_and_replays_atomically(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    undisclosed = _commit(destination="river_gate", places=[{
        "id": "river_gate", "level": 2, "kind": "venue", "seed": "河门",
    }])
    assert validate_protagonist_moves(undisclosed, engine.world, scene)

    discovered = _commit(destination="river_gate", places=[{
        "id": "river_gate", "level": 2, "kind": "venue", "seed": "河门",
    }], links=[{"a": "stonebridge_inn", "b": "river_gate", "travel_cost": 1}])
    assert validate_protagonist_moves(discovered, engine.world, scene) == []

    class DiscoveryStrategy:
        def produce(self, *args, **kwargs):
            return discovered

        def repair_sections(self, *args, **kwargs):
            return {}

        def commit_to_thread(self, narration):
            return None

    result = run_turn(engine.registry, engine.store, engine.world, scene, "发现河门",
                      strategy=DiscoveryStrategy(), provider=engine.provider,
                      required_sections=REQUIRED_SECTIONS, enforce_player_travel=True)
    engine.world = result.world
    assert engine.world["systems"]["ontology"].neighbors("protagonist", "located_in", 1) == ["river_gate"]
    engine.store.close()


def test_run_turn_repairs_invalid_player_teleport_before_publish(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    invalid = _commit(destination="north_bank_old_tower")

    class RepairingStrategy:
        def __init__(self):
            self.repairs = []

        def produce(self, *args, **kwargs):
            return invalid

        def repair_sections(self, failing_sections, errors, **kwargs):
            self.repairs.append((failing_sections, errors))
            return {"moves": [], "narration": "封印阻住了脚步，你留在客栈。"}

        def commit_to_thread(self, narration):
            return None

    strategy = RepairingStrategy()
    result = run_turn(engine.registry, engine.store, engine.world, scene, "穿过封印",
                      strategy=strategy, provider=engine.provider,
                      required_sections=REQUIRED_SECTIONS, enforce_player_travel=True)
    engine.world = result.world
    assert result.commit.sections["moves"] == []
    assert result.narration == "封印阻住了脚步，你留在客栈。"
    assert strategy.repairs and "moves" in strategy.repairs[0][0]
    assert any(error.code == "outside_scene_capability" for error in strategy.repairs[0][1])
    assert engine.world["systems"]["ontology"].neighbors("protagonist", "located_in", 1) == ["stonebridge_inn"]
    engine.store.close()


def test_run_turn_rejects_if_travel_repair_only_deletes_move(tmp_path):
    """A repair cannot retain arrival prose after deleting an illegal move."""
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    initial = _commit(
        destination="north_bank_old_tower",
        narration="你已经抵达北岸旧塔，封印在身后合拢。",
    )
    before_revision = engine.store.revision
    before_event_ids = [event["id"] for event in engine.store.iter_events(include_retracted=True)]
    repair_calls = []

    class MoveOnlyRepairStrategy:
        def produce(self, *args, **kwargs):
            return initial

        def repair_sections(self, failing_sections, errors, **kwargs):
            repair_calls.append((failing_sections, errors))
            return {"moves": []}

        def commit_to_thread(self, narration):
            return None

    strategy = MoveOnlyRepairStrategy()
    with pytest.raises(TurnRejected, match="scene capability"):
        run_turn(engine.registry, engine.store, engine.world, scene, "闯入旧塔",
                 strategy=strategy, provider=engine.provider,
                 max_repairs=1, required_sections=REQUIRED_SECTIONS,
                 enforce_player_travel=True)
    assert repair_calls and "narration" in repair_calls[0][0]
    assert engine.store.revision == before_revision
    assert [event["id"] for event in engine.store.iter_events(include_retracted=True)] == before_event_ids
    engine.store.close()


def test_final_publish_validator_rejects_post_produce_hidden_move_without_writes(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    before_revision = engine.store.revision
    batch = EventBatch(engine.store, preflight=lambda history: project(engine.registry, history))

    def travel_validator(events):
        from context.capabilities import validate_protagonist_move_events
        if validate_protagonist_move_events(events, engine.world, scene):
            raise TurnRejected("final publish violates the player scene capability")

    batch.add_publish_validator(travel_validator)
    batch.append(kernel_event("entity_moved", day=1, scene="s1", turn=batch.turn,
                              summary="bad hidden move", deltas={
                                  "who": "protagonist", "to": "north_bank_old_tower",
                              }))
    with pytest.raises(TurnRejected, match="scene capability"):
        batch.publish(action_id="capability-final-validator")
    assert engine.store.revision == before_revision
    assert engine.store.get_action_receipt("capability-final-validator") is None
    engine.store.close()


def test_final_validator_ignores_retracted_discovery_route_and_writes_nothing(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    before_revision = engine.store.revision
    batch = EventBatch(engine.store, preflight=lambda history: project(engine.registry, history))

    def travel_validator(events):
        from context.capabilities import validate_protagonist_move_events
        if validate_protagonist_move_events(events, engine.world, scene):
            raise TurnRejected("final publish violates the player scene capability")

    batch.add_publish_validator(travel_validator)
    batch.append(kernel_event("place_created", day=1, scene="s1", turn=batch.turn,
                              summary="new river gate", deltas={"id": "river_gate"}))
    retracted_link = kernel_event("place_linked", day=1, scene="s1", turn=batch.turn,
                                  summary="withdrawn route", deltas={
                                      "a": "stonebridge_inn", "b": "river_gate",
                                  })
    retracted_link["retracted"] = True
    batch.append(retracted_link)
    batch.append(kernel_event("entity_moved", day=1, scene="s1", turn=batch.turn,
                              summary="bad route move", deltas={
                                  "who": "protagonist", "to": "river_gate",
                              }))
    with pytest.raises(TurnRejected, match="scene capability"):
        batch.publish(action_id="capability-retracted-route")
    assert engine.store.revision == before_revision
    assert engine.store.get_action_receipt("capability-retracted-route") is None
    assert "river_gate" not in engine.world["systems"]["ontology"].entities
    engine.store.close()


def test_missing_start_location_fails_closed_for_proposal_and_final_payload(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = {**_build_scene(engine), "location": None}
    proposal_errors = validate_protagonist_moves(
        _commit(destination="north_bank_old_tower"), engine.world, scene,
    )
    assert {(error.section, error.code) for error in proposal_errors} == {
        ("moves", "missing_scene_location"),
        ("narration", "scene_capability_narration"),
    }

    before_revision = engine.store.revision
    batch = EventBatch(engine.store, preflight=lambda history: project(engine.registry, history))

    def travel_validator(events):
        from context.capabilities import validate_protagonist_move_events
        if validate_protagonist_move_events(events, engine.world, scene):
            raise TurnRejected("final publish violates the player scene capability")

    batch.add_publish_validator(travel_validator)
    batch.append(kernel_event("entity_moved", day=1, scene="s1", turn=batch.turn,
                              summary="move without start", deltas={
                                  "who": "protagonist", "to": "north_bank_old_tower",
                              }))
    with pytest.raises(TurnRejected, match="scene capability"):
        batch.publish(action_id="capability-no-start")
    assert engine.store.revision == before_revision
    assert engine.store.get_action_receipt("capability-no-start") is None
    engine.store.close()

