"""Offline return-promise tests at the real player, turn, and store boundaries.

The extractor is deliberately a fallible classifier: fake responses exercise its
schema/provenance checks, not semantic-model accuracy. No API keys or network are
needed. Fixtures contain typed people, one room, and a physically held umbrella.
"""

import copy
import json
from pathlib import Path

import pytest

from app.engine import build_engine, rewind
from app.play import _build_scene, play_loop
from context.assembler import assemble_context
from engine import settings
from kernel.events import kernel_event
from kernel.projection import project
from kernel.registry import Registry
from llm.provider import FakeLLMProvider
from llm.tools import build_tool_registry
from loop.return_intent import extract_return_intent
from loop.strategy import AuthorStrategy
from loop.turn import TurnRejected, run_turn
from systems.return_commitments import visible_records


ACTION = "我答应明天中午把雨伞还给阿林。"
AMBIGUOUS = "我答应明天把雨伞还给阿林。"
REPLY = "明天中午"


def _event(kind, *, day=1, turn=0, **deltas):
    return kernel_event(kind, day=day, scene="room", turn=turn,
                        summary="return integration fixture", deltas=deltas)


def _promise(action=ACTION, *, recipient="B", due=None, quotes=None):
    actions = action if isinstance(action, list) else [action]
    return {"item": "umbrella", "recipient": recipient,
            "due": due or {"day": 2, "band": 1},
            "evidence": {"player_actions": list(actions),
                         "quotes": list(quotes if quotes is not None else actions)}}


def _ready(*, quotes=None, expression="明天中午"):
    return {"status": "ready", "item": "umbrella", "recipient": "B",
            "due_expression": expression,
            "evidence_quotes": list(quotes if quotes is not None else [ACTION])}


def _proposal(*, items=None, promises=None, days=0, bands=0,
              narration="阿林听完，点了点头。"):
    # Promise order is intentionally first: evaluation must still follow items
    # and clock, independent of the narrator's JSON key ordering.
    result = {"narration": narration}
    if promises is not None:
        result["promises"] = promises
    result.update(moves=[], places=[], cast=[], facts=[],
                  clock=[{"advance": bool(days or bands), "days": days,
                          "bands": bands, "reason": "按本次行动经过的时间"}])
    if items is not None:
        result["items"] = items
    return result


def _transfer(source="A", target="B"):
    return {"op": "transfer", "item": "umbrella", "from": source, "to": target}


@pytest.fixture
def return_game(tmp_path, monkeypatch):
    monkeypatch.setattr("app.engine.get_embedder", lambda: None)
    for name in ("digest_fleet", "run_director", "run_cascade", "run_catchup",
                 "run_lore", "run_density", "_run_demote_on_leave"):
        monkeypatch.setattr("loop.turn." + name, lambda *args, **kwargs: [])

    def no_network(*args, **kwargs):
        pytest.fail("return integration tests must not contact a model service")

    monkeypatch.setattr("llm.provider._do_post", no_network)
    settings.set_conversation_mode("multiturn")
    game = build_engine(tmp_path / "return-integration", provider=FakeLLMProvider())
    game.store.append_many([
        _event("entity_created", id="A", etype="Person", tier="tracked"),
        _event("entity_created", id="B", etype="Person", tier="tracked"),
        _event("entity_created", id="room", etype="Place", tier="tracked",
               attrs={"level": 3, "kind": "venue", "seed": "旅店"}),
        _event("object_created", id="umbrella"),
        *[_event("relation_added", src=person, rel="located_in", dst="room")
          for person in ("A", "B")],
        _event("fact_asserted", subject="B", predicate="name", value="阿林", secrecy="public"),
        _event("fact_asserted", subject="umbrella", predicate="name", value="雨伞", secrecy="public"),
        _event("item_transferred", item="umbrella", to="A"),
    ])
    _refresh(game)
    assert _build_scene(game)["protagonist"] == "A"
    assert not game.world["systems"]["resources"].get("specs")
    yield game
    game.store.close()


def _refresh(game):
    game.world = project(game.registry, game.store.iter_events())
    game.world["_revision"] = game.store.revision


def _records(game):
    return game.world["systems"]["return_commitments"]["records"]


def _only(game):
    assert len(_records(game)) == 1
    return next(iter(_records(game).values()))


def _run(game, responses=None, *, action=ACTION, promise=None, strategy=None, **kwargs):
    provider = FakeLLMProvider(json_responses=copy.deepcopy(responses or [_proposal()]))
    result = run_turn(game.registry, game.store, game.world, _build_scene(game), action,
                      strategy=strategy or AuthorStrategy(), provider=provider,
                      return_commitment=promise, **kwargs)
    game.world = result.world
    return result, provider


def _create(game, *, strategy=None, **kwargs):
    return _run(game, promise=_promise(), strategy=strategy, **kwargs)


def _holder(game, holder):
    graph = game.world["systems"]["ontology"]
    assert graph.neighbors("umbrella", "held_by", game.world["meta"]["day"]) == [holder]
    current = [row for row in graph.relations
               if row.src == "umbrella" and row.rel == "held_by" and row.is_current()]
    assert len(current) == 1
    assert current[0].dst == holder


def _snapshot(game, strategy):
    return {"events": copy.deepcopy(list(game.store.iter_events(include_retracted=True))),
            "revision": game.store.revision,
            "mirror": Path(game.store.jsonl_path).read_bytes(),
            "strategy": copy.deepcopy(strategy.__dict__),
            "graph": copy.deepcopy(game.world["systems"]["ontology"].__dict__),
            "records": copy.deepcopy(_records(game)),
            "meta": copy.deepcopy(game.world["meta"])}


def _unchanged(game, strategy, before):
    assert _snapshot(game, strategy) == before


def test_host_creation_binds_actual_actor_and_reaches_narrator_before_commit(return_game):
    proposal = _promise()
    result, provider = _run(return_game, promise=proposal)
    record = _only(return_game)
    assert record["debtor"] == "A"
    assert record["item"] == "umbrella"
    assert record["recipient"] == "B"
    assert record["due"] == {"day": 2, "band": 1}
    assert record["evidence"] == proposal["evidence"]
    assert record["status"] == "open"
    assert record["created_at"] == {"day": 1, "band": 0}
    assert record["created_turn"] == 1
    assert isinstance(record["id"], str) and record["id"]
    assert len(provider.calls) == 1
    narrator_input = "\n".join(provider.calls[0])
    assert record["id"] in narrator_input and ACTION in narrator_input
    promised = [event for event in result.events if event["type"] == "item_return_promised"]
    assert len(promised) == 1
    assert result.events.index(promised[0]) < next(
        index for index, event in enumerate(result.events) if event["type"] == "narration_recorded")
    assert {event["turn"] for event in result.events} == {1}
    proposal["due"]["day"] = 99
    proposal["evidence"]["player_actions"][0] = "被篡改的输入"
    assert _only(return_game)["due"] == {"day": 2, "band": 1}
    assert _only(return_game)["evidence"]["player_actions"] == [ACTION]


@pytest.mark.parametrize("extra", [{"id": "model_chosen"}, {"debtor": "B"}])
def test_caller_cannot_supply_commitment_id_or_impersonate_actor(return_game, extra):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    with pytest.raises(TurnRejected):
        _run(return_game, promise={**_promise(), **extra}, strategy=strategy)
    _unchanged(return_game, strategy, before)


@pytest.mark.parametrize("mismatch", ["player_actions", "quotes"])
def test_wrong_source_actions_or_nonliteral_quotes_never_create_a_record(return_game, mismatch):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    proposal = _promise()
    proposal["evidence"][mismatch] = ["旁白声称玩家答应后天归还。"]
    with pytest.raises(ValueError):
        _run(return_game, promise=proposal, strategy=strategy)
    _unchanged(return_game, strategy, before)


@pytest.mark.parametrize("op", ["create", "promise", "amend", "cancel"])
def test_narrator_cannot_create_or_edit_commitments(return_game, op):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    forged = {"op": op, "id": "model_chosen", **_promise(), "debtor": "A"}
    with pytest.raises(TurnRejected):
        _run(return_game, [_proposal(promises=[forged])], strategy=strategy, max_repairs=0)
    _unchanged(return_game, strategy, before)


@pytest.mark.parametrize("reported", [False, True])
def test_backstage_cannot_create_a_promise_even_with_plausible_evidence(return_game, monkeypatch, reported):
    injected = []

    def forge(registry, store, world, *args, **kwargs):
        event = _event("item_return_promised", id="backstage_forged", debtor="A", **_promise())
        injected.append(event["id"])
        store.append(event)
        return [event] if reported else []

    monkeypatch.setattr("loop.turn.run_density", forge)
    result, _ = _run(return_game)
    assert len(injected) == 1
    assert not _records(return_game)
    assert not set(injected) & {event["id"] for event in result.events}
    assert any(event["type"] == "narration_recorded" for event in result.events)


@pytest.mark.parametrize("mutation", ["payload", "day", "turn", "retracted", "removed"])
def test_whitelisted_creation_event_envelope_and_presence_cannot_be_mutated(return_game, monkeypatch, mutation):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    touched = []

    def mutate(registry, store, world, *args, **kwargs):
        # Deliberately exercise direct staged-memory mutation. iter_events()
        # returns independent values and cannot serve as this attack path.
        event = next(event for event in store.events if event["type"] == "item_return_promised")
        touched.append(event["id"])
        if mutation == "payload":
            event["deltas"]["recipient"] = "room"
        elif mutation == "day":
            event["day"] = 2
        elif mutation == "turn":
            event["turn"] = 77
        elif mutation == "retracted":
            event["retracted"] = True
        else:
            store.events.remove(event)
        return []

    monkeypatch.setattr("loop.turn.run_density", mutate)
    with pytest.raises(TurnRejected):
        _create(return_game, strategy=strategy)
    assert touched
    _unchanged(return_game, strategy, before)


def test_mutating_old_iterator_event_changes_neither_returned_nor_durable_world(return_game, monkeypatch):
    _create(return_game)
    original = copy.deepcopy(_only(return_game))
    original_event = next(event for event in return_game.store.iter_events()
                          if event["type"] == "item_return_promised")
    touched = []

    def mutate_copy(registry, store, world, *args, **kwargs):
        event = next(event for event in store.iter_events() if event["type"] == "item_return_promised")
        event["deltas"]["due"]["day"] = 77
        touched.append(event)
        return []

    monkeypatch.setattr("loop.turn.run_density", mutate_copy)
    result, _ = _run(return_game)
    assert len(touched) == 1 and touched[0]["deltas"]["due"]["day"] == 77
    assert _only(return_game) == original
    assert result.world["systems"]["return_commitments"]["records"][original["id"]] == original
    assert next(event for event in return_game.store.iter_events()
                if event["type"] == "item_return_promised") == original_event
    replay = project(return_game.registry, return_game.store.iter_events())
    assert replay["systems"]["return_commitments"]["records"][original["id"]] == original


@pytest.mark.parametrize("extra", [{"recipient": "room"}, {"due": {"day": 9, "band": 3}},
                                   {"evidence": {"player_actions": ["fake"], "quotes": ["fake"]}}])
def test_fulfill_cannot_rewrite_immutable_fields(return_game, extra):
    strategy = AuthorStrategy()
    _create(return_game, strategy=strategy)
    before = _snapshot(return_game, strategy)
    with pytest.raises(TurnRejected):
        _run(return_game, [_proposal(promises=[{"op": "fulfill", "id": _only(return_game)["id"], **extra}])],
             strategy=strategy, max_repairs=0)
    _unchanged(return_game, strategy, before)


def test_false_fulfillment_is_rejected_atomically_after_repairs_exhausted(return_game):
    strategy = AuthorStrategy()
    _create(return_game, strategy=strategy)
    before = _snapshot(return_game, strategy)
    false = _proposal(promises=[{"op": "fulfill", "id": _only(return_game)["id"]}],
                      narration="阿林说事情已经办妥。")
    with pytest.raises(TurnRejected):
        _run(return_game, [false], strategy=strategy, max_repairs=1)
    _unchanged(return_game, strategy, before)
    _holder(return_game, "A")


def test_false_fulfillment_can_be_repaired_without_fabricating_a_transfer(return_game):
    _create(return_game)
    result, provider = _run(return_game, [
        _proposal(promises=[{"op": "fulfill", "id": _only(return_game)["id"]}]),
        {"promises": []},
    ])
    assert result.repair_attempts == 1 and len(provider.calls) == 2
    assert result.dropped_sections == []
    assert _only(return_game)["status"] == "open"
    _holder(return_game, "A")
    assert not any(event["type"] == "item_return_fulfilled" for event in result.events)


@pytest.mark.parametrize("recipient", ["B", "room"])
def test_creation_and_actual_return_autocomplete_in_one_atomic_action(return_game, recipient):
    result, _ = _run(return_game, [_proposal(items=[_transfer(target=recipient)])],
                     promise=_promise(recipient=recipient))
    assert _only(return_game)["status"] == "fulfilled"
    _holder(return_game, recipient)
    kinds = [event["type"] for event in result.events]
    assert kinds.count("item_return_promised") == kinds.count("item_return_fulfilled") == 1
    assert kinds.index("item_return_promised") < kinds.index("item_transferred") < kinds.index("item_return_fulfilled")
    assert {event["turn"] for event in result.events} == {1}


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("absolute", [False, True])
def test_later_return_closes_once_after_items_and_clock(return_game, explicit, absolute):
    _create(return_game)
    cid = _only(return_game)["id"]
    promises = [{"op": "fulfill", "id": cid}] if explicit else None
    proposal = _proposal(items=[_transfer()], promises=promises, days=1, bands=2)
    if absolute:
        proposal["clock"] = [{"advance": True, "target": {"day": 2, "band": 2},
                              "reason": "到约定的下午归还"}]
    result, provider = _run(return_game, [proposal], action="把雨伞交给阿林。")
    record = _only(return_game)
    assert result.repair_attempts == 0 and len(provider.calls) == 1
    assert record["status"] == "fulfilled"
    assert record["fulfilled_at"] == {"day": 2, "band": 2}
    assert record["due"] == {"day": 2, "band": 1}
    assert visible_records(return_game.world, _build_scene(return_game))[0]["overdue"] is False
    kinds = [event["type"] for event in result.events]
    assert kinds.count("item_return_fulfilled") == 1
    assert kinds.index("clock_advanced") < kinds.index("item_return_fulfilled")
    assert kinds.index("item_transferred") < kinds.index("item_return_fulfilled")
    _holder(return_game, "B")


def test_item_already_at_recipient_is_not_evidence_of_fulfilling_new_promise(return_game):
    return_game.store.append(_event("item_transferred", item="umbrella", to="B"))
    _refresh(return_game)
    _create(return_game)
    assert _only(return_game)["status"] == "open"
    _run(return_game, [_proposal(narration="你表示东西已经归还。")])
    assert _only(return_game)["status"] == "open"
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    with pytest.raises(TurnRejected):
        _run(return_game, [_proposal(promises=[{"op": "fulfill", "id": _only(return_game)["id"]}])],
             strategy=strategy, max_repairs=0)
    _unchanged(return_game, strategy, before)


@pytest.mark.parametrize("explicit", [False, True])
def test_new_same_holder_transfer_cannot_manufacture_return_evidence(return_game, explicit):
    return_game.store.append(_event("item_transferred", item="umbrella", to="B"))
    _refresh(return_game)
    strategy = AuthorStrategy()
    _create(return_game, strategy=strategy)
    before = _snapshot(return_game, strategy)
    promises = [{"op": "fulfill", "id": _only(return_game)["id"]}] if explicit else None
    with pytest.raises(TurnRejected):
        _run(return_game, [_proposal(items=[_transfer(source="B", target="B")], promises=promises)],
             strategy=strategy, max_repairs=0)
    _unchanged(return_game, strategy, before)
    assert _only(return_game)["status"] == "open"
    _holder(return_game, "B")


@pytest.mark.parametrize("explicit", [False, True])
def test_fulfillment_requires_recipient_to_hold_item_after_backstage_effects(return_game, monkeypatch, explicit):
    strategy = AuthorStrategy()
    _create(return_game, strategy=strategy)
    before = _snapshot(return_game, strategy)
    cid = _only(return_game)["id"]
    onward = []

    def transfer_onward(registry, store, world, *args, **kwargs):
        event = _event("item_transferred", **_transfer(source="B", target="room"))
        onward.append(event["id"])
        store.append(event)
        return [event]

    monkeypatch.setattr("loop.turn.run_density", transfer_onward)
    promises = [{"op": "fulfill", "id": cid}] if explicit else None
    proposal = _proposal(items=[_transfer()], promises=promises)
    if explicit:
        with pytest.raises(TurnRejected):
            _run(return_game, [proposal], strategy=strategy)
        _unchanged(return_game, strategy, before)
        _holder(return_game, "A")
        assert not set(onward) & {event["id"] for event in return_game.store.iter_events()}
    else:
        result, _ = _run(return_game, [proposal], strategy=strategy)
        _holder(return_game, "room")
        assert set(onward) <= {event["id"] for event in result.events}
        assert [event["deltas"] for event in result.events if event["type"] == "item_transferred"] == [
            _transfer(), _transfer(source="B", target="room")]
        assert not any(event["type"] == "item_return_fulfilled" for event in result.events)
    assert len(onward) == 1
    assert _only(return_game)["status"] == "open"


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("days,bands", [(1, 0), (0, 1), (1, 1)])
def test_fulfillment_uses_final_action_clock_after_backstage_advance(
        return_game, monkeypatch, explicit, days, bands):
    strategy = AuthorStrategy()
    _create(return_game, strategy=strategy)
    before = _snapshot(return_game, strategy)
    cid = _only(return_game)["id"]
    clock_events = []
    final_clock = {"day": 1 + days, "band": bands}

    def advance_backstage(registry, store, world, *args, **kwargs):
        event = _event("clock_advanced", day=final_clock["day"],
                       advance=True, days=days, bands=bands,
                       reason="本次行动的后台效果继续推进时间")
        clock_events.append(event["id"])
        store.append(event)
        return [event]

    monkeypatch.setattr("loop.turn.run_density", advance_backstage)
    promises = [{"op": "fulfill", "id": cid}] if explicit else None
    proposal = _proposal(items=[_transfer()], promises=promises)
    if explicit:
        # An explicit foreground completion has already acquired the earlier
        # clock. Reject the whole action rather than rewrite that event history.
        with pytest.raises(TurnRejected, match="final action clock"):
            _run(return_game, [proposal], strategy=strategy)
        _unchanged(return_game, strategy, before)
        _holder(return_game, "A")
        assert _only(return_game)["status"] == "open"
        assert "fulfilled_at" not in _only(return_game)
        assert not set(clock_events) & {event["id"] for event in return_game.store.iter_events()}
    else:
        result, _ = _run(return_game, [proposal], strategy=strategy)
        _holder(return_game, "B")
        record = _only(return_game)
        assert record["status"] == "fulfilled"
        assert record["fulfilled_at"] == final_clock
        assert {key: result.world["meta"][key] for key in final_clock} == final_clock
        fulfilled = [event for event in result.events if event["type"] == "item_return_fulfilled"]
        assert len(fulfilled) == 1
        assert result.events.index(fulfilled[0]) > next(
            index for index, event in enumerate(result.events) if event["id"] == clock_events[0])
        replay = project(return_game.registry, return_game.store.iter_events())
        assert replay["systems"]["return_commitments"]["records"][cid] == record
    assert len(clock_events) == 1


def test_overdue_is_derived_strictly_after_deadline_without_rewriting_promise(return_game):
    _create(return_game)
    original = copy.deepcopy(_only(return_game))
    _run(return_game, [_proposal(days=1, bands=1)], action="等到约定的时刻。")
    assert visible_records(return_game.world, _build_scene(return_game))[0]["overdue"] is False
    result, _ = _run(return_game, [_proposal(bands=1)], action="继续等到下午。")
    assert visible_records(return_game.world, _build_scene(return_game))[0]["overdue"] is True
    assert _only(return_game) == original
    assert "overdue" not in _only(return_game)
    assert not any(event["type"].startswith("item_return_") for event in result.events)
    rewind(return_game, result.events[0]["turn"])
    assert visible_records(return_game.world, _build_scene(return_game))[0]["overdue"] is False


def test_reopen_replay_and_rewind_restore_holder_and_commitment_together(return_game):
    created, _ = _create(return_game)
    completed, _ = _run(return_game, [_proposal(items=[_transfer()], days=1)])
    saved_record = copy.deepcopy(_only(return_game))
    reopened = build_engine(Path(return_game.store.db_path).parent, provider=FakeLLMProvider())
    try:
        assert _only(reopened) == saved_record
        _holder(reopened, "B")
        rewind(reopened, completed.events[0]["turn"])
        assert _only(reopened)["status"] == "open"
        assert "fulfilled_at" not in _only(reopened)
        assert _only(reopened)["evidence"] == saved_record["evidence"]
        _holder(reopened, "A")
        assert not {event["id"] for event in completed.events} & {
            event["id"] for event in reopened.store.iter_events()}
        rewind(reopened, created.events[0]["turn"])
        assert not _records(reopened)
        _holder(reopened, "A")
    finally:
        reopened.store.close()


@pytest.mark.parametrize("stage", ["creation", "completion"])
def test_write_fault_rolls_back_record_item_events_mirror_and_conversation(return_game, monkeypatch, stage):
    strategy = AuthorStrategy()
    if stage == "completion":
        _create(return_game, strategy=strategy)
    before = _snapshot(return_game, strategy)
    original_insert = return_game.store._insert
    writes = []

    def fail(event):
        writes.append(event["type"])
        if (stage == "creation" and len(writes) == 2
                or stage == "completion" and event["type"] == "item_return_fulfilled"):
            raise OSError("injected return write fault")
        return original_insert(event)

    monkeypatch.setattr(return_game.store, "_insert", fail)
    with pytest.raises(OSError, match="injected return write fault"):
        _run(return_game, [_proposal(items=[_transfer()])], strategy=strategy,
             promise=_promise() if stage == "creation" else None)
    assert writes
    if stage == "creation":
        assert writes[0] == "item_return_promised"
    else:
        assert writes.index("item_transferred") < writes.index("item_return_fulfilled")
    _unchanged(return_game, strategy, before)
    _holder(return_game, "A")
    replay = project(return_game.registry, return_game.store.iter_events())
    assert replay["systems"]["return_commitments"]["records"] == before["records"]


def test_public_query_is_copied_and_guessed_ids_do_not_reveal_others_records(return_game):
    _create(return_game)
    cid = _only(return_game)["id"]
    scene = _build_scene(return_game)
    tools = build_tool_registry(return_game.registry, return_game.world, scene)
    answer = json.loads(tools.execute("return_commitments_query", {"commitment_id": cid}))
    assert len(answer["commitments"]) == 1
    assert "initial_held_by_source_event" not in json.dumps(answer)
    answer["commitments"][0]["evidence"]["player_actions"][0] = "tampered"
    assert _only(return_game)["evidence"]["player_actions"] == [ACTION]
    return_game.store.append_many([
        _event("entity_created", id="C", etype="Person", tier="tracked"),
        _event("relation_added", src="C", rel="located_in", dst="room"),
    ])
    _refresh(return_game)
    outsider = {**_build_scene(return_game), "protagonist": "C", "present": ["A", "B"]}
    for dm in (False, True):
        other_tools = build_tool_registry(return_game.registry, return_game.world, outsider, dm=dm)
        for args in ({}, {"commitment_id": cid}, {"commitment_id": "guessed_unknown"}):
            result = other_tools.execute("return_commitments_query", args)
            assert json.loads(result) == {"commitments": []}
            assert ACTION not in result and cid not in result
        assert "error" in json.loads(other_tools.execute("return_commitments_query", {"pov": "A"}))
    assert cid not in assemble_context(return_game.registry, return_game.world, outsider)


def test_party_query_does_not_reveal_item_hidden_before_promise_creation(return_game):
    return_game.store.append(_event("object_created", id="umbrella", visibility="hidden"))
    _refresh(return_game)
    _create(return_game)
    cid = _only(return_game)["id"]
    assert visible_records(return_game.world, _build_scene(return_game), commitment_id=cid) == []


def test_known_at_creation_identity_survives_hiding_without_current_secret_disclosure(return_game):
    _create(return_game)
    cid = _only(return_game)["id"]
    original_view = visible_records(return_game.world, _build_scene(return_game), commitment_id=cid)
    hidden = copy.deepcopy(return_game.world)
    attrs = hidden["systems"]["ontology"].get_entity("umbrella").attrs
    attrs.update(visibility="hidden", current_secret="CANARY_CURRENT_UMBRELLA_SECRET")
    scene = _build_scene(return_game)
    retained = visible_records(hidden, scene, commitment_id=cid)
    assert retained == original_view
    assert "debtor_known_entities" not in json.dumps(retained)
    query = build_tool_registry(return_game.registry, hidden, scene).execute(
        "return_commitments_query", {"commitment_id": cid})
    assert json.loads(query)["commitments"] == retained
    assert "CANARY_CURRENT_UMBRELLA_SECRET" not in query
    assert "CANARY_CURRENT_UMBRELLA_SECRET" not in assemble_context(return_game.registry, hidden, scene)


def test_compare_ready_intent_waits_for_normal_mode_without_models_or_persistence(return_game, monkeypatch):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)

    def no_comparison(*args, **kwargs):
        pytest.fail("ready return intent must not generate comparison candidates")

    monkeypatch.setattr("app.play.run_compare", no_comparison)
    return_game.provider = FakeLLMProvider(json_responses=[_ready()])
    out = []
    play_loop(return_game, [ACTION], out=out.append, strategy=strategy, compare=True)
    _unchanged(return_game, strategy, before)
    assert return_game.pending_return_intent["player_actions"] == [ACTION]
    assert len(return_game.provider.calls) == 1  # The read-only extractor only.
    assert any("/compare off" in line for line in out)
    assert not any(line == "[DM]" for line in out)
    return_game.provider = FakeLLMProvider(json_responses=[
        _ready(quotes=[ACTION, REPLY]), _proposal()])
    play_loop(return_game, ["/compare off", REPLY], out=out.append, strategy=strategy, compare=True)
    assert return_game.pending_return_intent is None
    assert _only(return_game)["evidence"]["player_actions"] == [ACTION, REPLY]
    assert len(return_game.provider.calls) == 2
    assert len([event for event in return_game.store.iter_events()
                if event["type"] == "item_return_promised"]) == 1
    assert len([event for event in return_game.store.iter_events()
                if event["type"] == "narration_recorded"]) == 1


def test_real_extractor_clarification_then_reply_records_only_actual_player_turns(return_game):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    out = []
    return_game.provider = FakeLLMProvider(json_responses=[
        {"status": "clarify", "question": "明天哪个时段归还？"},
        _ready(quotes=[AMBIGUOUS, REPLY]),
        _proposal(),
    ])
    play_loop(return_game, [AMBIGUOUS], out=out.append, strategy=strategy)
    _unchanged(return_game, strategy, before)
    assert return_game.pending_return_intent["player_actions"] == [AMBIGUOUS]
    assert any("明天哪个时段" in line for line in out)
    assert not any(line == "[DM]" for line in out)
    play_loop(return_game, [REPLY], out=out.append, strategy=strategy)
    assert return_game.pending_return_intent is None
    record = _only(return_game)
    assert record["evidence"] == {"player_actions": [AMBIGUOUS, REPLY], "quotes": [AMBIGUOUS, REPLY]}
    assert record["due"] == {"day": 2, "band": 1}
    assert record["created_turn"] == 1
    assert len(return_game.provider.calls) == 3
    extracted_input = json.loads(return_game.provider.calls[1][1])
    assert extracted_input["player_actions"] == [AMBIGUOUS, REPLY]
    assert extracted_input["has_pending"] is True
    assert "\n".join([AMBIGUOUS, REPLY]) in return_game.provider.calls[2][1]
    assert len(strategy._thread) == 3


def test_ready_narration_failure_keeps_both_player_turns_for_retry(return_game):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    return_game.provider = FakeLLMProvider(json_responses=[
        {"status": "clarify", "question": "明天哪个时段？"},
        _ready(quotes=[AMBIGUOUS, REPLY]),
        _proposal(items=[_transfer(source="B")]),
    ])
    out = []
    play_loop(return_game, [AMBIGUOUS, REPLY], out=out.append, strategy=strategy, max_repairs=0)
    _unchanged(return_game, strategy, before)
    assert return_game.pending_return_intent["player_actions"] == [AMBIGUOUS, REPLY]
    assert any("世界保持原状" in line for line in out)
    retry = "是的，明天中午。"
    return_game.provider = FakeLLMProvider(json_responses=[
        _ready(quotes=[AMBIGUOUS, REPLY, retry]), _proposal()])
    play_loop(return_game, [retry], out=out.append, strategy=strategy)
    assert return_game.pending_return_intent is None
    assert _only(return_game)["evidence"]["player_actions"] == [AMBIGUOUS, REPLY, retry]


@pytest.mark.parametrize("failure", ["invalid_evidence", "schema_exhaustion", "provider_failure"])
def test_extractor_failure_keeps_original_and_latest_reply_without_advancing(return_game, monkeypatch, failure):
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    return_game.provider = FakeLLMProvider(json_responses=[{"status": "clarify", "question": "哪个时段？"}])
    play_loop(return_game, [AMBIGUOUS], out=lambda _: None, strategy=strategy)
    original_pending = return_game.pending_return_intent
    pending = copy.deepcopy(return_game.pending_return_intent)
    invalid = (_ready(quotes=["我从来没有说过的话"]) if failure == "invalid_evidence"
               else {"status": "ready", "due": {"day": 77, "band": 0},
                     "model_only": "MODEL_OUTPUT_MUST_NOT_ENTER_PENDING"})
    return_game.provider = FakeLLMProvider(json_responses=[invalid])
    if failure == "provider_failure":
        def fail_provider(messages, **kwargs):
            return_game.provider.calls.append((messages[0]["content"], messages[-1]["content"]))
            raise ConnectionError("injected offline classifier outage")
        monkeypatch.setattr(return_game.provider, "complete_messages", fail_provider)
    out = []
    play_loop(return_game, [REPLY], out=out.append, strategy=strategy)
    assert original_pending == pending  # The previous pending snapshot is immutable.
    assert return_game.pending_return_intent == {**pending, "player_actions": [AMBIGUOUS, REPLY]}
    assert return_game.pending_return_intent is not original_pending
    assert "MODEL_OUTPUT_MUST_NOT_ENTER_PENDING" not in json.dumps(return_game.pending_return_intent)
    assert len(return_game.provider.calls) == (1 if failure == "provider_failure" else 2)
    _unchanged(return_game, strategy, before)
    assert any("原话已保留" in line and "重试" in line for line in out)
    assert not any(line == "[DM]" for line in out)


@pytest.mark.parametrize("command", ["/undo", "/oops", "//veto", "/rewind 1", "//retcon 1"])
def test_state_changing_ooc_clears_pending_and_stale_narrator_context(return_game, command):
    strategy = AuthorStrategy()
    _run(return_game, strategy=strategy)
    assert strategy._thread
    return_game.provider = FakeLLMProvider(json_responses=[{"status": "clarify", "question": "哪个时段？"}])
    play_loop(return_game, [AMBIGUOUS], out=lambda _: None, strategy=strategy)
    assert return_game.pending_return_intent is not None
    play_loop(return_game, [command], out=lambda _: None, strategy=strategy)
    assert return_game.pending_return_intent is None
    assert not strategy._thread
    assert not _records(return_game)
    assert not any(event["type"] == "narration_recorded" for event in return_game.store.iter_events())


def test_read_only_ooc_preserves_pending_until_quit(return_game):
    strategy = AuthorStrategy()
    return_game.provider = FakeLLMProvider(json_responses=[{"status": "clarify", "question": "哪个时段？"}])
    play_loop(return_game, [AMBIGUOUS], out=lambda _: None, strategy=strategy)
    pending = copy.deepcopy(return_game.pending_return_intent)
    before = _snapshot(return_game, strategy)
    play_loop(return_game, ["/help", "/verbosity"], out=lambda _: None, strategy=strategy)
    assert return_game.pending_return_intent == pending
    _unchanged(return_game, strategy, before)
    play_loop(return_game, ["/quit"], out=lambda _: None, strategy=strategy)
    assert return_game.pending_return_intent is None
    _unchanged(return_game, strategy, before)


@pytest.mark.parametrize("action", ["我借一把伞。", "雨伞要还给谁？", "我不答应明天中午把雨伞还给阿林。",
                                    "如果雨停，我就明天中午还伞。", "阿林说：‘我明天中午还伞。’"])
def test_none_classification_creates_no_obligation_for_inquiries_or_negatives(return_game, action):
    return_game.provider = FakeLLMProvider(json_responses=[{"status": "none"}, _proposal()])
    out = []
    play_loop(return_game, [action], out=out.append)
    assert not _records(return_game)
    assert return_game.pending_return_intent is None
    assert len(return_game.provider.calls) == 2
    assert any(line == "[DM]" for line in out)
    assert not any(event["type"].startswith("item_return_") for event in return_game.store.iter_events())


def test_cancel_pending_runs_only_current_action_and_preserves_recorded_promise(return_game):
    _create(return_game)
    original = copy.deepcopy(_only(return_game))
    return_game.provider = FakeLLMProvider(json_responses=[
        {"status": "clarify", "question": "这次约定是哪个时段？"},
        {"status": "none"}, _proposal()])
    play_loop(return_game, [AMBIGUOUS, "取消刚才还没登记的提议。"], out=lambda _: None)
    assert return_game.pending_return_intent is None
    assert _only(return_game) == original
    narrator_call = return_game.provider.calls[-1][1]
    assert "取消刚才还没登记的提议。" in narrator_call
    assert AMBIGUOUS not in narrator_call


def test_play_skips_extraction_when_system_not_registered(return_game, monkeypatch):
    registry = Registry()
    for system in return_game.registry.systems:
        if system.name != "return_commitments":
            registry.register(system)
    return_game.registry = registry
    _refresh(return_game)

    def forbidden(*args, **kwargs):
        pytest.fail("extractor called without ReturnCommitmentSystem")

    monkeypatch.setattr("loop.return_intent.extract_return_intent", forbidden)
    return_game.provider = FakeLLMProvider(json_responses=[_proposal()])
    play_loop(return_game, ["环顾四周。"], out=lambda _: None)
    assert len(return_game.provider.calls) == 1
    assert any(event["type"] == "narration_recorded" for event in return_game.store.iter_events())


def test_stale_pending_revision_is_rejected_without_classification_or_story(return_game):
    first = extract_return_intent(return_game.world, _build_scene(return_game), AMBIGUOUS,
                                 FakeLLMProvider(json_responses=[{"status": "clarify", "question": "哪个时段？"}]))
    _run(return_game)
    strategy = AuthorStrategy()
    before = _snapshot(return_game, strategy)
    return_game.pending_return_intent = first["pending"]
    return_game.provider = FakeLLMProvider(json_responses=[_ready(quotes=[REPLY])])
    out = []
    play_loop(return_game, [REPLY], out=out.append, strategy=strategy)
    assert return_game.pending_return_intent is None
    assert not return_game.provider.calls
    assert any("之前的提议未登记" in line for line in out)
    _unchanged(return_game, strategy, before)
