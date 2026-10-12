"""Scene capability contract: one POV topology for prompt, UI and publication."""
from __future__ import annotations

import pytest

from app.engine import build_engine, rewind
from app.gui import _seed_demo_campaign
from app.play import _build_scene
from context.capabilities import (discovery_grant_errors, native_player_dto, place_discovery_key,
                                  route_discovery_key, scene_capabilities,
                                  validate_protagonist_moves)
from engine.store import EventBatch
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from kernel.validation import validate_commit
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


def test_existing_hidden_place_id_cannot_be_redeclared_as_new_discovery(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    disguised = _commit(destination="north_bank_old_tower", places=[{
        "id": "north_bank_old_tower", "level": 2, "kind": "venue", "seed": "伪装入口",
    }], links=[{"a": "stonebridge_inn", "b": "north_bank_old_tower", "travel_cost": 1}])
    assert any(error.code == "already_exists"
               for error in validate_commit(engine.registry, disguised, engine.world,
                                            required_sections=REQUIRED_SECTIONS))
    assert any(error.code == "outside_scene_capability"
               for error in validate_protagonist_moves(disguised, engine.world, scene))
    engine.store.close()


def test_sqlite_host_grant_discovers_undiscovered_route_and_moves_same_turn(tmp_path):
    engine = _demo_engine(tmp_path)
    engine.store.append_many([
        kernel_event("place_created", day=1, scene="s1", turn=0, summary="sealed gate", deltas={
            "id": "sealed_gate", "level": 2, "kind": "venue", "seed": "封门", "visibility": "undiscovered"}),
        kernel_event("place_linked", day=1, scene="s1", turn=0, summary="sealed route", deltas={
            "a": "stonebridge_inn", "b": "sealed_gate", "visibility": "undiscovered"}),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    scene = _build_scene(engine)
    grants = frozenset({place_discovery_key("sealed_gate"),
                        route_discovery_key("stonebridge_inn", "sealed_gate")})
    request_world = engine.world
    commit = TurnCommit("你循着线索抵达封门。", {"moves": [{"who": "protagonist", "to": "sealed_gate"}],
        "knowledge": [{"op": "discover_place", "knower": "protagonist", "place": "sealed_gate"},
                      {"op": "discover_route", "knower": "protagonist", "a": "stonebridge_inn", "b": "sealed_gate"}],
        "places": [], "links": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "test"}]})
    calls = []
    class Strategy:
        def produce(self, *args, **kwargs): calls.append("produce"); return commit
        def repair_sections(self, *args, **kwargs): return {}
        def commit_to_thread(self, narration): return None
    result = run_turn(engine.registry, engine.store, request_world, scene, "去封门", strategy=Strategy(),
                      provider=engine.provider, action_id="grant-move", required_sections=REQUIRED_SECTIONS,
                      enforce_player_travel=True, trusted_discovery_keys=grants)
    assert result.receipt["action_id"] == "grant-move" and result.receipt["revision"] > 1
    assert engine.store.get_action_receipt("grant-move") is not None
    dto = native_player_dto(result.world, {**scene, "location": "sealed_gate"})
    assert dto["location"] == "sealed_gate" and "sealed_gate" in {n["id"] for n in dto["map"]["nodes"]}
    assert "GUI_DEMO_PRIVATE_SENTINEL" not in repr(dto)
    retry = run_turn(engine.registry, engine.store, request_world, scene, "去封门", strategy=Strategy(),
                     provider=engine.provider, action_id="grant-move", required_sections=REQUIRED_SECTIONS,
                     enforce_player_travel=True, trusted_discovery_keys=grants)
    assert retry.receipt["duplicate"] is True and calls == ["produce"]
    with pytest.raises(Exception):
        run_turn(engine.registry, engine.store, request_world, scene, "去封门", strategy=Strategy(),
                 provider=engine.provider, action_id="grant-move", required_sections=REQUIRED_SECTIONS,
                 enforce_player_travel=True, trusted_discovery_keys=frozenset())
    assert calls == ["produce"]
    rewind(engine, result.events[0]["turn"])
    reverted = native_player_dto(engine.world, scene)
    assert "sealed_gate" not in {n["id"] for n in reverted["map"]["nodes"]}
    engine.store.close()
    reopened = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None)
    reopened_dto = native_player_dto(reopened.world, _build_scene(reopened))
    assert reopened_dto == reverted
    assert "GUI_DEMO_PRIVATE_SENTINEL" not in repr(reopened_dto)
    reopened.store.close()


def _install_undiscovered_route(engine, destination="sealed_gate"):
    """Add a canonical-only destination for real SQLite discovery-turn tests."""
    engine.store.append_many([
        kernel_event("place_created", day=1, scene="s1", turn=0, summary="sealed gate", deltas={
            "id": destination, "level": 2, "kind": "venue", "seed": "封门",
            "visibility": "undiscovered",
        }),
        kernel_event("place_linked", day=1, scene="s1", turn=0, summary="sealed route", deltas={
            "a": "stonebridge_inn", "b": destination, "visibility": "undiscovered",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())


def _host_discovery_turn(destination, knowledge):
    return TurnCommit("你沿着公开线索前往封门。", {
        "moves": [{"who": "protagonist", "to": destination}],
        "knowledge": knowledge,
        "places": [], "links": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "test"}],
    })


def test_sqlite_discovery_true_then_false_blocks_move_without_writes(tmp_path):
    """The final same-turn grant value, rather than any earlier True, controls travel."""
    engine = _demo_engine(tmp_path)
    _install_undiscovered_route(engine)
    scene = _build_scene(engine)
    place_key = place_discovery_key("sealed_gate")
    route_key = route_discovery_key("stonebridge_inn", "sealed_gate")
    commit = _host_discovery_turn("sealed_gate", [
        {"op": "discover_place", "knower": "protagonist", "place": "sealed_gate"},
        {"op": "discover_route", "knower": "protagonist", "a": "stonebridge_inn", "b": "sealed_gate"},
        {"op": "told", "knower": "protagonist", "fact_key": place_key, "value": False},
    ])
    before_revision = engine.store.revision
    before_events = [event["id"] for event in engine.store.iter_events(include_retracted=True)]

    class Strategy:
        def produce(self, *args, **kwargs):
            return commit

        def repair_sections(self, *args, **kwargs):
            return {}

        def commit_to_thread(self, narration):
            return None

    with pytest.raises(TurnRejected, match="scene capability"):
        run_turn(
            engine.registry, engine.store, engine.world, scene, "前往封门", strategy=Strategy(),
            provider=engine.provider, action_id="grant-true-then-false",
            required_sections=REQUIRED_SECTIONS, enforce_player_travel=True,
            trusted_discovery_keys=frozenset({place_key, route_key}),
        )
    assert engine.store.revision == before_revision
    assert [event["id"] for event in engine.store.iter_events(include_retracted=True)] == before_events
    assert engine.store.get_action_receipt("grant-true-then-false") is None
    engine.store.close()


def test_sqlite_discovery_false_then_true_allows_move_with_final_grant(tmp_path):
    """A later effective host discovery grant enables the same legal direct move."""
    engine = _demo_engine(tmp_path)
    _install_undiscovered_route(engine)
    scene = _build_scene(engine)
    place_key = place_discovery_key("sealed_gate")
    route_key = route_discovery_key("stonebridge_inn", "sealed_gate")
    commit = _host_discovery_turn("sealed_gate", [
        {"op": "told", "knower": "protagonist", "fact_key": place_key, "value": False},
        {"op": "told", "knower": "protagonist", "fact_key": route_key, "value": False},
        {"op": "discover_place", "knower": "protagonist", "place": "sealed_gate"},
        {"op": "discover_route", "knower": "protagonist", "a": "stonebridge_inn", "b": "sealed_gate"},
    ])

    class Strategy:
        def produce(self, *args, **kwargs):
            return commit

        def repair_sections(self, *args, **kwargs):
            return {}

        def commit_to_thread(self, narration):
            return None

    result = run_turn(
        engine.registry, engine.store, engine.world, scene, "前往封门", strategy=Strategy(),
        provider=engine.provider, action_id="grant-false-then-true",
        required_sections=REQUIRED_SECTIONS, enforce_player_travel=True,
        trusted_discovery_keys=frozenset({place_key, route_key}),
    )
    graph = result.world["systems"]["ontology"]
    day = result.world["meta"].get("day") or 1
    assert graph.value_at("protagonist", f"knows:{place_key}", day) is True
    assert graph.value_at("protagonist", f"knows:{route_key}", day) is True
    assert graph.neighbors("protagonist", "located_in", day) == ["sealed_gate"]
    assert engine.store.get_action_receipt("grant-false-then-true") is not None
    engine.store.close()


def test_proposal_rejects_discovery_broadcast_without_knower_for_protagonist_audience():
    """A broadcast has no knower field and must not skip the proposal guard."""
    commit = TurnCommit("广播发现权限", {
        "knowledge": [{
            "op": "broadcast",
            "fact_key": place_discovery_key("sealed_gate"),
            "value": True,
            "audience": {"kind": "members", "ids": ["protagonist"]},
        }],
    })

    errors = discovery_grant_errors(commit, "protagonist")

    assert [(error.section, error.field, error.code) for error in errors] == [
        ("knowledge", "[0]", "protected_discovery_write"),
    ]


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


def test_final_validator_rejects_forged_discovery_grant_without_writes(tmp_path):
    engine = _demo_engine(tmp_path)
    scene = _build_scene(engine)
    before_revision = engine.store.revision
    batch = EventBatch(engine.store, preflight=lambda history: project(engine.registry, history))

    def travel_validator(events):
        from context.capabilities import validate_protagonist_move_events
        if validate_protagonist_move_events(events, engine.world, scene):
            raise TurnRejected("final publish violates the player scene capability")

    batch.add_publish_validator(travel_validator)
    batch.append(kernel_event("knowledge_set", day=1, scene="s1", turn=batch.turn,
                              summary="forged", deltas={"knower": "protagonist",
                              "fact_key": "north_bank_old_tower.discovered", "value": True}))
    with pytest.raises(TurnRejected, match="scene capability"):
        batch.publish(action_id="forged-discovery")
    assert engine.store.revision == before_revision
    assert engine.store.get_action_receipt("forged-discovery") is None
    engine.store.close()


@pytest.mark.parametrize("event_type,deltas", [
    ("knowledge_broadcast", {"fact_key": "north_bank_old_tower.discovered", "value": True,
                               "audience": {"place": "stonebridge_inn"}}),
    ("fact_asserted", {"subject": "protagonist", "predicate": "knows:north_bank_old_tower.discovered",
                         "value": True}),
])
def test_final_validator_rejects_equivalent_discovery_writes(tmp_path, event_type, deltas):
    engine = _demo_engine(tmp_path); scene = _build_scene(engine); before = engine.store.revision
    batch = EventBatch(engine.store, preflight=lambda history: project(engine.registry, history))
    def validate(events):
        from context.capabilities import validate_protagonist_move_events
        if validate_protagonist_move_events(events, engine.world, scene):
            raise TurnRejected("final publish violates the player scene capability")
    batch.add_publish_validator(validate)
    batch.append(kernel_event(event_type, day=1, scene="s1", turn=batch.turn, summary="forged", deltas=deltas))
    with pytest.raises(TurnRejected, match="scene capability"):
        batch.publish(action_id=f"forged-{event_type}")
    assert engine.store.revision == before
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

