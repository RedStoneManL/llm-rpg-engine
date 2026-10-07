"""Offline ownership/provenance checks at the real turn and event-store boundary.

Historical events deliberately use the permissive legacy event shape. Only new
proposals are required to identify the current holder; that is not an ownership
permission, theft, or protagonist-only policy.
"""

import copy
from pathlib import Path

import pytest

from app.engine import build_engine, rewind
from app.play import _build_scene
from engine import settings
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from kernel.validation import validate_commit
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy
from loop.turn import TurnRejected, apply_turn, run_turn


def _event(kind, *, day=1, turn=0, **deltas):
    return kernel_event(kind, day=day, scene="inn", turn=turn,
                        summary="item integrity fixture", deltas=deltas)


def _transfer(source="A", destination="B", item="umbrella"):
    return {"op": "transfer", "item": item, "from": source, "to": destination}


def _proposal(items, *, days=1, narration="你把雨伞交给了等候的人。"):
    return {
        "narration": narration,
        "items": items,
        "clock": [{"advance": bool(days), "days": days, "bands": 0,
                   "reason": "约定的交接时间"}],
    }


@pytest.fixture
def item_game(tmp_path, monkeypatch):
    # No ambient model/embedder configuration or ordinary backstage hook can
    # consume a fake response or make this integration test contact a service.
    monkeypatch.setattr("app.engine.get_embedder", lambda: None)
    for name in ("digest_fleet", "run_director", "run_cascade", "run_catchup",
                 "run_lore", "run_density", "_run_demote_on_leave"):
        monkeypatch.setattr("loop.turn." + name, lambda *args, **kwargs: [])
    settings.set_conversation_mode("multiturn")
    game = build_engine(tmp_path / "item-integrity", provider=FakeLLMProvider())
    game.store.append_many([
        _event("entity_created", id="A", etype="Person", tier="tracked"),
        _event("entity_created", id="B", etype="Person", tier="tracked"),
        _event("entity_created", id="C", etype="Person", tier="tracked"),
        _event("entity_created", id="inn", etype="Place", tier="tracked",
               attrs={"level": 3, "kind": "venue", "seed": "旅店"}),
        _event("object_created", id="box"),
        _event("object_created", id="umbrella"),
        *[_event("relation_added", src=person, rel="located_in", dst="inn")
          for person in ("A", "B", "C")],
        # A legacy event without a source is a supported fixture/replay format.
        _event("item_transferred", item="umbrella", to="A"),
    ])
    game.world = project(game.registry, game.store.iter_events())
    game.world["_revision"] = game.store.revision
    assert _build_scene(game)["protagonist"] == "A"
    yield game
    game.store.close()


def _run(game, responses, *, strategy=None, **kwargs):
    strategy = strategy if strategy is not None else AuthorStrategy()
    provider = FakeLLMProvider(json_responses=copy.deepcopy(responses))
    result = run_turn(game.registry, game.store, game.world, _build_scene(game),
                      "交接物品", strategy=strategy, provider=provider, **kwargs)
    game.world = result.world
    return result, provider


def _holder(world, expected, *, item="umbrella", day=None):
    graph = world["systems"]["ontology"]
    day = world["meta"]["day"] if day is None else day
    assert graph.neighbors(item, "held_by", day) == [expected]
    return graph


def _single_current(world, expected, *, item="umbrella"):
    graph = _holder(world, expected, item=item)
    current = [relation for relation in graph.relations
               if relation.src == item and relation.rel == "held_by"
               and relation.is_current()]
    assert len(current) == 1
    assert current[0].dst == expected


def _snapshot(game, strategy):
    return {
        "events": copy.deepcopy(list(game.store.iter_events(include_retracted=True))),
        "revision": game.store.revision,
        "mirror": Path(game.store.jsonl_path).read_bytes(),
        "conversation": copy.deepcopy(strategy.__dict__),
        "graph": copy.deepcopy(game.world["systems"]["ontology"].__dict__),
        "meta": copy.deepcopy(game.world["meta"]),
    }


def _assert_unchanged(game, strategy, before):
    assert list(game.store.iter_events(include_retracted=True)) == before["events"]
    assert game.store.revision == before["revision"]
    assert Path(game.store.jsonl_path).read_bytes() == before["mirror"]
    assert strategy.__dict__ == before["conversation"]
    assert game.world["systems"]["ontology"].__dict__ == before["graph"]
    assert game.world["meta"] == before["meta"]


def _established_conversation(game):
    strategy = AuthorStrategy()
    _run(game, [{"narration": "你站在旅店门口，等候约定的人。"}], strategy=strategy)
    assert len(strategy._thread) == 3
    return strategy


@pytest.mark.parametrize("destination", ["B", "inn"])
def test_transfer_records_source_and_preserves_previous_day_holder(item_game, destination):
    result, provider = _run(item_game, [_proposal([_transfer(destination=destination)])])
    assert len(provider.calls) == 1
    assert result.repair_attempts == 0
    assert result.dropped_sections == []
    assert result.world["meta"]["day"] == 2
    _single_current(result.world, destination)
    _holder(result.world, "A", day=1)
    transfers = [event for event in result.events if event["type"] == "item_transferred"]
    assert len(transfers) == 1
    assert transfers[0]["deltas"] == _transfer(destination=destination)
    assert {event["turn"] for event in result.events} == {1}
    assert len([event for event in result.events if event["type"] == "narration_recorded"]) == 1


@pytest.mark.parametrize("source", ["B", None, "missing"])
def test_held_item_requires_matching_explicit_source_without_any_partial_commit(item_game, source):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    transfer = _transfer(source=source)
    if source == "missing":
        transfer.pop("from")
    with pytest.raises(TurnRejected):
        _run(item_game, [_proposal([transfer])], strategy=strategy, max_repairs=0)
    _assert_unchanged(item_game, strategy, before)
    _single_current(item_game.world, "A")


@pytest.mark.parametrize(("bad_update", "field"), [
    ({"item": "A"}, "item"),
    ({"item": "inn"}, "item"),
    ({"item": "absent_object"}, "item"),
    ({"from": "box"}, "from"),
    ({"from": "absent_person"}, "from"),
    ({"from": ["A"]}, "from"),
    ({"from": {"id": "A"}}, "from"),
    ({"to": "box"}, "to"),
    ({"to": "absent_holder"}, "to"),
    ({"to": ["B"]}, "to"),
])
def test_invalid_transfer_references_and_entity_types_are_repairable(item_game, bad_update, field):
    bad = {**_transfer(), **bad_update}
    errors = validate_commit(item_game.registry, TurnCommit("交接", {"items": [bad]}),
                             item_game.world)
    assert any(error.section == "items" and error.field.endswith("." + field)
               for error in errors)
    assert not any(error.code == "validator_error" for error in errors)
    result, provider = _run(item_game, [_proposal([bad]), {"items": [_transfer()]}])
    assert len(provider.calls) == 2
    assert result.repair_attempts == 1
    assert result.dropped_sections == []
    _single_current(result.world, "B")
    _holder(result.world, "A", day=1)
    assert [event["deltas"] for event in result.events
            if event["type"] == "item_transferred"] == [_transfer()]


@pytest.mark.parametrize("include_null", [False, True])
@pytest.mark.parametrize("destination", ["B", "inn"])
def test_unheld_object_first_placement_allows_absent_or_null_source(item_game, include_null, destination):
    placement = {"op": "transfer", "item": "box", "to": destination}
    if include_null:
        placement["from"] = None
    result, _ = _run(item_game, [_proposal([placement])])
    _single_current(result.world, destination, item="box")
    assert result.world["systems"]["ontology"].neighbors("box", "held_by", 1) == []
    _single_current(result.world, "A")


def test_unheld_object_cannot_claim_a_fictitious_prior_holder(item_game):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    with pytest.raises(TurnRejected):
        _run(item_game, [_proposal([_transfer(item="box")])], strategy=strategy, max_repairs=0)
    _assert_unchanged(item_game, strategy, before)


def test_new_object_can_be_created_placed_and_transferred_in_one_batch(item_game):
    items = [
        {"op": "create", "id": "lantern", "material": "brass"},
        {"op": "transfer", "item": "lantern", "to": "A"},
        _transfer(item="lantern"),
    ]
    result, _ = _run(item_game, [_proposal(items)])
    graph = result.world["systems"]["ontology"]
    assert graph.get_entity("lantern").etype == "Object"
    assert graph.get_entity("lantern").attrs["material"] == "brass"
    assert graph.neighbors("lantern", "held_by", 1) == []
    _single_current(result.world, "B", item="lantern")
    _single_current(result.world, "A")


def test_place_can_be_the_explicit_source_of_a_later_transfer(item_game):
    _run(item_game, [_proposal([_transfer(destination="inn")])])
    result, _ = _run(item_game, [_proposal([_transfer(source="inn", destination="C")])])
    _single_current(result.world, "C")
    _holder(result.world, "A", day=1)
    _holder(result.world, "inn", day=2)


def test_valid_same_batch_chain_uses_intermediate_holder_and_is_read_only_during_validation(item_game):
    chain = [_transfer(), _transfer(source="B", destination="C")]
    graph = item_game.world["systems"]["ontology"]
    before = copy.deepcopy(graph.__dict__)
    owner = item_game.registry.owner_of_section("items")
    assert owner.validate("items", chain, item_game.world) == []
    assert graph.__dict__ == before
    result, _ = _run(item_game, [_proposal(chain)])
    _single_current(result.world, "C")
    graph = _holder(result.world, "A", day=1)
    assert [(relation.dst, relation.event_time_start, relation.event_time_end)
            for relation in graph.relations
            if relation.src == "umbrella" and relation.rel == "held_by"] == [
                ("A", 1, 2), ("B", 2, 2), ("C", 2, None)]
    assert [event["deltas"] for event in result.events
            if event["type"] == "item_transferred"] == chain
    rewind(item_game, result.events[0]["turn"])
    _single_current(item_game.world, "A")
    _holder(item_game.world, "A", day=2)


def test_invalid_second_source_rejects_entire_chain_and_conversation(item_game):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    chain = [_transfer(), _transfer(source="A", destination="C")]
    errors = item_game.registry.owner_of_section("items").validate("items", chain, item_game.world)
    assert any(error.field == "[1].from" for error in errors)
    with pytest.raises(TurnRejected):
        _run(item_game, [_proposal(chain)], strategy=strategy, max_repairs=0)
    _assert_unchanged(item_game, strategy, before)
    _single_current(item_game.world, "A")


def test_source_is_provenance_not_protagonist_permission(item_game):
    item_game.store.append(_event("item_transferred", item="umbrella", to="B"))
    item_game.world = project(item_game.registry, item_game.store.iter_events())
    assert _build_scene(item_game)["protagonist"] == "A"
    result, _ = _run(item_game, [_proposal([_transfer(source="B", destination="C")])])
    _single_current(result.world, "C")
    _holder(result.world, "B", day=1)


def test_generic_held_by_relation_cannot_bypass_items_route(item_game):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    proposal = {"narration": "你把雨伞递给对方。", "relations": [
        {"src": "umbrella", "rel": "held_by", "dst": "B"}]}
    with pytest.raises(TurnRejected):
        _run(item_game, [proposal], strategy=strategy, max_repairs=0)
    _assert_unchanged(item_game, strategy, before)


@pytest.mark.parametrize(("kind", "deltas"), [
    ("item_transferred", _transfer(source="A", destination="C")),
    ("item_transferred", {"item": "umbrella", "to": "C"}),
    ("item_transferred", _transfer(source=None, destination="C")),
    ("item_transferred", _transfer(source="B", destination="box")),
    ("item_transferred", {"item": "A", "to": "C"}),
    ("relation_added", {"src": "umbrella", "rel": "held_by", "dst": "C"}),
])
@pytest.mark.parametrize("report_events", [False, True])
def test_backstage_direct_events_cannot_bypass_transfer_integrity(item_game, monkeypatch, kind, deltas, report_events):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    injected = []

    def inject_invalid(registry, store, world, *args, **kwargs):
        marker = _event("fact_asserted", day=2, subject="A", predicate="bad_hook_marker", value=True)
        # Foreground already moved A -> B. Sources, types, and the dedicated
        # event route all need checking against that staged current state.
        invalid = _event(kind, day=2, **deltas)
        injected.extend([marker["id"], invalid["id"]])
        store.append(marker)
        store.append(invalid)
        return [marker, invalid] if report_events else []

    monkeypatch.setattr("loop.turn.run_density", inject_invalid)
    try:
        result, _ = _run(item_game, [_proposal([_transfer()])], strategy=strategy)
    except TurnRejected:
        # Rejecting the whole action is safe; savepoint-based implementations
        # can instead discard this hook and preserve the valid foreground.
        _assert_unchanged(item_game, strategy, before)
        _single_current(item_game.world, "A")
    else:
        _single_current(result.world, "B")
        _holder(result.world, "A", day=1)
        assert result.world["systems"]["ontology"].value_at("A", "bad_hook_marker", 2) is None
        assert [event["deltas"] for event in result.events
                if event["type"] == "item_transferred"] == [_transfer()]
        assert any(event["type"] == "narration_recorded" for event in result.events)
    assert len(injected) == 2  # Prove the hook actually exercised the staged path.
    assert not set(injected) & {event["id"] for event in item_game.store.iter_events(include_retracted=True)}


@pytest.mark.parametrize("report_events", [False, True])
def test_valid_backstage_transfer_uses_foreground_updated_holder(item_game, monkeypatch, report_events):
    def transfer_onward(registry, store, world, *args, **kwargs):
        event = _event("item_transferred", day=2, **_transfer(source="B", destination="C"))
        store.append(event)
        return [event] if report_events else []

    monkeypatch.setattr("loop.turn.run_density", transfer_onward)
    result, _ = _run(item_game, [_proposal([_transfer()])])
    _single_current(result.world, "C")
    _holder(result.world, "A", day=1)
    assert [event["deltas"] for event in result.events if event["type"] == "item_transferred"] == [
        _transfer(), _transfer(source="B", destination="C")]


def test_transfer_history_survives_reopen_and_rewind_restores_previous_owner(item_game):
    first, _ = _run(item_game, [_proposal([_transfer()])])
    second, _ = _run(item_game, [_proposal([_transfer(source="B", destination="C")])])
    campaign_dir = Path(item_game.store.db_path).parent
    reopened = build_engine(campaign_dir, provider=FakeLLMProvider())
    try:
        assert list(reopened.store.iter_events()) == list(item_game.store.iter_events())
        assert reopened.store.revision == item_game.store.revision
        _single_current(reopened.world, "C")
        _holder(reopened.world, "A", day=1)
        _holder(reopened.world, "B", day=2)
        second_ids = {event["id"] for event in second.events}
        before_rewind_revision = reopened.store.revision
        result = rewind(reopened, second.events[0]["turn"])
        assert result["retracted"] == len(second.events)
        assert reopened.store.revision > before_rewind_revision
        _single_current(reopened.world, "B")
        _holder(reopened.world, "A", day=1)
        _holder(reopened.world, "B", day=3)
        assert not second_ids & {event["id"] for event in reopened.store.iter_events()}
        assert {event["id"] for event in first.events} <= {
            event["id"] for event in reopened.store.iter_events()}
        assert all(event["retracted"] for event in reopened.store.iter_events(include_retracted=True)
                   if event["id"] in second_ids)
    finally:
        reopened.store.close()
    reopened_again = build_engine(campaign_dir, provider=FakeLLMProvider())
    try:
        _single_current(reopened_again.world, "B")
        _holder(reopened_again.world, "A", day=1)
        _holder(reopened_again.world, "B", day=3)
    finally:
        reopened_again.store.close()


def test_write_failure_after_transfer_insert_rolls_back_events_history_and_conversation(item_game, monkeypatch):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    original_insert = item_game.store._insert
    inserted = []

    def fail_second(event):
        inserted.append(event["type"])
        if len(inserted) == 2:
            raise OSError("injected item transfer write failure")
        return original_insert(event)

    monkeypatch.setattr(item_game.store, "_insert", fail_second)
    with pytest.raises(OSError, match="injected item transfer write failure"):
        _run(item_game, [_proposal([_transfer()])], strategy=strategy)
    assert len(inserted) == 2
    assert inserted[0] == "item_transferred"
    _assert_unchanged(item_game, strategy, before)
    _single_current(item_game.world, "A")
    fresh = project(item_game.registry, item_game.store.iter_events())
    _single_current(fresh, "A")
    _holder(fresh, "A", day=2)


@pytest.mark.parametrize(("kind", "deltas", "holder"), [
    ("item_transferred", {"item": "umbrella", "to": "B"}, "B"),
    ("item_transferred", {"item": "umbrella", "from": "C", "to": "B"}, "B"),
    ("relation_added", {"src": "umbrella", "rel": "held_by", "dst": "B"}, "B"),
    ("relation_added", {"src": "umbrella", "rel": "held_by", "dst": "box"}, "box"),
])
def test_legacy_replay_is_unchanged_and_old_events_do_not_block_new_turns(item_game, kind, deltas, holder):
    legacy = _event(kind, day=2, turn=1, **deltas)
    item_game.store.append(legacy)
    item_game.world = project(item_game.registry, item_game.store.iter_events())
    _single_current(item_game.world, holder)
    _holder(item_game.world, "A", day=1)
    reopened = build_engine(Path(item_game.store.db_path).parent, provider=FakeLLMProvider())
    try:
        _single_current(reopened.world, holder)
        _holder(reopened.world, "A", day=1)
        result, _ = _run(reopened, [{"narration": "你望向窗外，雨还没有停。"}])
        _single_current(result.world, holder)
        _holder(result.world, "A", day=1)
        saved = next(event for event in reopened.store.iter_events() if event["id"] == legacy["id"])
        assert saved["deltas"] == deltas
    finally:
        reopened.store.close()


@pytest.mark.parametrize("entity_id", ["umbrella", "A"])
@pytest.mark.parametrize("repair", [False, True])
def test_faction_declaration_cannot_retype_an_item_or_current_holder(item_game, entity_id, repair):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    proposal = {"narration": "你查看墙上的行会告示。", "factions": [
        {"op": "faction", "id": entity_id, "ranks": ["member"]}]}
    original = copy.deepcopy(proposal)
    errors = validate_commit(item_game.registry, TurnCommit.from_dict(proposal), item_game.world)
    assert any(error.section == "factions" and error.field == "[0].id"
               and error.code in {"item_type_change", "holder_type"} for error in errors)
    assert proposal == original
    _assert_unchanged(item_game, strategy, before)
    if repair:
        result, provider = _run(item_game, [proposal, {"factions": []}], strategy=strategy)
        assert result.repair_attempts == 1
        assert len(provider.calls) == 2
        assert not any(event["type"] == "faction_created" for event in result.events)
        assert len(strategy._thread) == 5
    else:
        with pytest.raises(TurnRejected):
            _run(item_game, [proposal], strategy=strategy, max_repairs=0)
        _assert_unchanged(item_game, strategy, before)
    graph = item_game.world["systems"]["ontology"]
    assert graph.get_entity("umbrella").etype == "Object"
    assert graph.get_entity("A").etype == "Person"
    _single_current(item_game.world, "A")


@pytest.mark.parametrize("entity_id", ["umbrella", "A"])
@pytest.mark.parametrize("report_events", [False, True])
def test_backstage_faction_creation_cannot_retype_items_or_holders(item_game, monkeypatch, entity_id, report_events):
    strategy = _established_conversation(item_game)
    before = _snapshot(item_game, strategy)
    injected = []

    def retype_as_faction(registry, store, world, *args, **kwargs):
        marker = _event("fact_asserted", day=2, subject="A", predicate="type_hook_marker", value=True)
        retype = _event("faction_created", day=2, id=entity_id, ranks=["member"])
        injected.extend([marker["id"], retype["id"]])
        store.append(marker)
        store.append(retype)
        return [marker, retype] if report_events else []

    monkeypatch.setattr("loop.turn.run_density", retype_as_faction)
    try:
        result, _ = _run(item_game, [_proposal([])], strategy=strategy)
    except TurnRejected:
        _assert_unchanged(item_game, strategy, before)
    else:
        assert result.world["systems"]["ontology"].value_at("A", "type_hook_marker", 2) is None
        assert not any(event["type"] == "faction_created" for event in result.events)
        assert any(event["type"] == "narration_recorded" for event in result.events)
    assert len(injected) == 2
    assert not set(injected) & {event["id"] for event in item_game.store.iter_events(include_retracted=True)}
    graph = item_game.world["systems"]["ontology"]
    assert graph.get_entity("umbrella").etype == "Object"
    assert graph.get_entity("A").etype == "Person"
    _single_current(item_game.world, "A")


@pytest.mark.parametrize("sections", [
    {"items": [_transfer(source="B")]},
    {"items": [{"op": "transfer", "item": "umbrella", "to": "B"}]},
    {"items": [_transfer(destination="box")]},
    {"relations": [{"src": "umbrella", "rel": "held_by", "dst": "B"}]},
])
def test_direct_apply_turn_cannot_bypass_item_preflight(item_game, sections):
    strategy = AuthorStrategy()
    before = _snapshot(item_game, strategy)
    commit = TurnCommit("交接物品", {
        "facts": [{"subject": "A", "predicate": "uncommitted_marker", "value": True}],
        **sections,
    })
    with pytest.raises(TurnRejected):
        apply_turn(item_game.registry, item_game.store, commit, day=2, scene="inn")
    _assert_unchanged(item_game, strategy, before)
    _single_current(project(item_game.registry, item_game.store.iter_events()), "A")


@pytest.mark.parametrize("new_item", [False, True])
def test_cross_section_real_types_allow_new_person_and_item_transfer_chains_without_preview_mutation(item_game, new_item):
    strategy = AuthorStrategy()
    before = _snapshot(item_game, strategy)
    entities = [{"id": "D", "etype": "Person"}]
    if new_item:
        entities.append({"id": "parcel", "etype": "Object"})
        chain = [{"op": "transfer", "item": "parcel", "to": "D"},
                 _transfer(item="parcel", source="D")]
    else:
        chain = [_transfer(destination="D"), _transfer(source="D")]
    proposal = {"entities": entities, **_proposal(chain)}
    original = copy.deepcopy(proposal)
    assert validate_commit(item_game.registry, TurnCommit.from_dict(proposal), item_game.world) == []
    assert proposal == original
    _assert_unchanged(item_game, strategy, before)
    assert item_game.world["systems"]["ontology"].get_entity("D") is None
    result, provider = _run(item_game, [proposal], strategy=strategy)
    assert len(provider.calls) == 1
    assert result.world["systems"]["ontology"].get_entity("D").etype == "Person"
    _single_current(result.world, "B", item="parcel" if new_item else "umbrella")
    _holder(result.world, "A", day=1)
    if new_item:
        assert result.world["systems"]["ontology"].get_entity("parcel").etype == "Object"
        assert result.world["systems"]["ontology"].neighbors("parcel", "held_by", 1) == []


@pytest.mark.parametrize(("etype", "bad_transfer", "field"), [
    ("Object", _transfer(destination="D"), "to"),
    ("Faction", _transfer(destination="D"), "to"),
    ("Person", {"op": "transfer", "item": "D", "to": "B"}, "item"),
])
def test_cross_section_new_wrong_types_are_repairable_and_preview_never_mutates_world(item_game, etype, bad_transfer, field):
    strategy = AuthorStrategy()
    before = _snapshot(item_game, strategy)
    proposal = {"entities": [{"id": "D", "etype": etype}], **_proposal([bad_transfer])}
    original = copy.deepcopy(proposal)
    errors = validate_commit(item_game.registry, TurnCommit.from_dict(proposal), item_game.world)
    assert any(error.section == "items" and error.field.endswith("." + field)
               and error.code in {"item_type", "holder_type"} for error in errors)
    assert proposal == original
    _assert_unchanged(item_game, strategy, before)
    assert item_game.world["systems"]["ontology"].get_entity("D") is None
    result, provider = _run(item_game, [proposal, {"items": [_transfer()]}], strategy=strategy)
    assert len(provider.calls) == 2
    assert result.repair_attempts == 1
    assert result.dropped_sections == []
    assert result.world["systems"]["ontology"].get_entity("D").etype == etype
    _single_current(result.world, "B")
    _holder(result.world, "A", day=1)
    assert result.world["systems"]["ontology"].neighbors("D", "held_by", 2) == []
    assert [event["deltas"] for event in result.events
            if event["type"] == "item_transferred"] == [_transfer()]


def test_later_entity_section_is_staged_before_items_without_repair(item_game):
    proposal = _proposal([_transfer(destination="new_holder")])
    proposal["entities"] = [{"id": "new_holder", "etype": "Person"}]
    before = copy.deepcopy(item_game.world["systems"]["ontology"].__dict__)
    assert validate_commit(item_game.registry, TurnCommit.from_dict(proposal), item_game.world) == []
    assert item_game.world["systems"]["ontology"].__dict__ == before
    result, _ = _run(item_game, [proposal])
    assert result.repair_attempts == 0
    _single_current(result.world, "new_holder")
    types = [event['type'] for event in result.events]
    assert types.index('entity_created') < types.index('item_transferred')


def test_later_wrong_type_holder_is_repairable_after_canonical_ordering(item_game):
    proposal = _proposal([_transfer(destination="new_box")])
    proposal["entities"] = [{"id": "new_box", "etype": "Object"}]
    errors = validate_commit(item_game.registry, TurnCommit.from_dict(proposal), item_game.world)
    assert any(error.section == 'items' and error.code == 'holder_type' for error in errors)
    result, _ = _run(item_game, [proposal, {"items": [_transfer()]}])
    assert result.repair_attempts == 1
    _single_current(result.world, 'B')
    assert result.world['systems']['ontology'].get_entity('new_box').etype == 'Object'


def test_section_normalization_never_reorders_rows_inside_items(item_game):
    proposal = _proposal([
        {"op": "transfer", "item": "late_lantern", "to": "A"},
        {"op": "create", "id": "late_lantern"},
    ])
    strategy = AuthorStrategy()
    before = _snapshot(item_game, strategy)
    with pytest.raises(TurnRejected):
        _run(item_game, [proposal], strategy=strategy, max_repairs=0)
    _assert_unchanged(item_game, strategy, before)
