"""Offline typed-return domain checks; creation authorization is host-owned."""
from __future__ import annotations

import copy
import json

import pytest

from context.access import pov_world
from kernel.contextsystem import Fragment
from kernel.events import kernel_event, open_store
from kernel.projection import project
from kernel.registry import Registry
from systems.object import ObjectSystem
from systems.ontology import OntologySystem
from systems.narrative import NarrativeSystem
from systems.return_commitments import ReturnCommitmentSystem, completion_events, visible_records
from systems.time import TimeSystem


SYSTEM = ReturnCommitmentSystem()


def registry():
    return (Registry().register(OntologySystem()).register(TimeSystem())
            .register(ObjectSystem()).register(ReturnCommitmentSystem()))


def event(kind="item_return_promised", *, day=2, turn=4, data=None, event_id=None):
    return kernel_event(kind, day=day, scene="square", turn=turn, summary="an unrelated summary",
                        id=event_id, deltas=creation() if data is None else data)


def creation():
    return {"id": "return-book", "item": "book", "debtor": "alice", "recipient": "bob",
            "due": {"day": 3, "band": 2},
            "evidence": {"player_actions": ["I tell Bob: I will return the book tomorrow afternoon."],
                         "quotes": ["I will return the book tomorrow afternoon."]}}


def world(*, holder="alice", band=1, turn=1):
    result = project(registry(), [])
    result["meta"].update(day=2, band=band, scene="square")
    graph = result["systems"]["ontology"]
    for eid, etype in (("alice", "Person"), ("bob", "Person"), ("eve", "Person"),
                       ("book", "Object"), ("library", "Place"), ("guild", "Faction")):
        graph.add_entity(eid, etype, visibility="public")
    if holder is not None:
        graph.add_relation("book", "held_by", holder, day=1, turn=turn, source_event="initial-possession")
    return result


def promise(result, *, data=None, turn=4):
    SYSTEM.apply(result, event(data=data, turn=turn))
    return result["systems"]["return_commitments"]["records"]["return-book"]


def transfer(result, *, to="bob", day=2, turn=4, source="physical-return"):
    ObjectSystem().apply(result, event("item_transferred", day=day, turn=turn, event_id=source,
                                     data={"item": "book", "to": to}))


def scene(actor="alice", **kwargs):
    return {"protagonist": actor, "day": 2, "location": "square", "present": [], **kwargs}


def complete(result, *, day=2):
    SYSTEM.apply(result, event("item_return_fulfilled", day=day, data={"id": "return-book"}))


def test_contract_dependencies_and_no_legacy_event_ownership():
    assert SYSTEM.name == "return_commitments"
    assert SYSTEM.event_types() == {"item_return_promised", "item_return_fulfilled"}
    assert SYSTEM.commit_sections() == {"promises"}
    assert SYSTEM.requires() == {"ontology", "time", "object"}
    assert SYSTEM.empty_state() == {"records": {}}
    assert SYSTEM.created_ids("promises", [{"op": "fulfill", "id": "new-entity"}]) == set()
    with pytest.raises(ValueError, match="requires"):
        Registry().register(SYSTEM)
    first = SYSTEM.empty_state()
    first["records"]["one"] = {}
    assert SYSTEM.empty_state() == {"records": {}}


def test_create_stores_exact_immutable_inputs_and_provenance_without_moving_item():
    result = world()
    data = creation()
    record = promise(result, data=data)
    assert record["status"] == "open"
    assert record["created_at"] == {"day": 2, "band": 1}
    assert record["created_turn"] == 4
    assert record["initial_held_by_source_event"] == "initial-possession"
    assert all(record[key] == data[key] for key in data)
    data["due"]["day"] = 100
    data["evidence"]["quotes"].append("fabricated")
    assert record["due"] == {"day": 3, "band": 2}
    assert record["evidence"] == creation()["evidence"]
    graph = result["systems"]["ontology"]
    assert graph.neighbors("book", "held_by", 2) == ["alice"]
    assert len(graph.relations) == 1
    assert not graph.facts


def test_recipient_place_is_destination_without_claiming_ownership():
    result = world()
    data = creation()
    data["recipient"] = "library"
    promise(result, data=data)
    transfer(result, to="library")
    complete(result)
    assert result["systems"]["return_commitments"]["records"]["return-book"]["status"] == "fulfilled"
    assert {relation.rel for relation in result["systems"]["ontology"].relations} == {"held_by"}


@pytest.mark.parametrize("field", sorted(creation()))
def test_creation_missing_each_required_field_raises_value_error(field):
    data = creation()
    del data[field]
    result = world()
    with pytest.raises(ValueError):
        promise(result, data=data)
    assert result["systems"]["return_commitments"] == {"records": {}}


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", " "), ("id", True), ("id", []),
    ("item", None), ("item", {}), ("item", "missing-hidden-item"),
    ("item", "alice"), ("item", "library"),
    ("debtor", "book"), ("debtor", "library"), ("debtor", "missing-hidden-person"),
    ("recipient", "book"), ("recipient", "guild"), ("recipient", "missing-hidden-person"),
    ("recipient", "alice"), ("recipient", False),
])
def test_creation_rejects_malformed_and_wrong_type_references_without_echoing_values(field, value):
    data = creation()
    data[field] = value
    result = world()
    with pytest.raises(ValueError) as error:
        promise(result, data=data)
    assert "missing-hidden" not in str(error.value)
    assert not result["systems"]["return_commitments"]["records"]


@pytest.mark.parametrize("due", [None, [], {}, {"day": 3}, {"band": 1},
    {"day": 3, "band": 1, "extra": "secret"}, {"day": True, "band": 0},
    {"day": 0, "band": 0}, {"day": -1, "band": 0}, {"day": 2.0, "band": 1},
    {"day": "3", "band": 1}, {"day": 3, "band": False}, {"day": 3, "band": -1},
    {"day": 3, "band": 4}, {"day": 3, "band": "1"}, {"day": 3, "band": 1.0},
    {"day": 1, "band": 3}, {"day": 2, "band": 0},
])
def test_creation_rejects_bad_or_past_dates(due):
    data = creation()
    data["due"] = due
    with pytest.raises(ValueError):
        promise(world(), data=data)


def test_due_equal_to_current_clock_is_valid_and_not_overdue():
    result = world()
    data = creation()
    data["due"] = {"day": 2, "band": 1}
    promise(result, data=data)
    assert visible_records(result, scene())[0]["overdue"] is False


@pytest.mark.parametrize("evidence", [None, [], {}, {"player_actions": ["one"]},
    {"player_actions": [], "quotes": []},
    {"player_actions": ["one"], "quotes": []},
    {"player_actions": "one", "quotes": ["one"]},
    {"player_actions": ["one"], "quotes": "one"},
    {"player_actions": [None], "quotes": ["one"]},
    {"player_actions": ["one"], "quotes": [False]},
    {"player_actions": ["one"], "quotes": [""]},
    {"player_actions": ["one"], "quotes": [" "]},
    {"player_actions": ["one"], "quotes": ["ONE"]},
    {"player_actions": ["one", "two"], "quotes": ["one\ntwo"]},
    {"player_actions": ["I may return this book."], "quotes": ["I will return this book."]},
    {"player_actions": ["one"], "quotes": ["one"], "secret": "private-value"},
])
def test_evidence_is_nonempty_exact_quote_provenance(evidence):
    data = creation()
    data["evidence"] = evidence
    with pytest.raises(ValueError) as error:
        promise(world(), data=data)
    assert "private-value" not in str(error.value)


def test_multiple_quotes_must_each_match_one_actual_action():
    result = world()
    data = creation()
    data["evidence"] = {"player_actions": ["I will return the book.", "By tomorrow afternoon."],
                        "quotes": ["return the book", "tomorrow afternoon"]}
    promise(result, data=data)
    assert visible_records(result, scene())[0]["evidence"] == data["evidence"]


@pytest.mark.parametrize("extra", ["status", "op", "created_at", "initial_held_by_source_event",
                                  "debtor_known_entities", "amount", "cancel"])
def test_creation_rejects_extra_fields(extra):
    data = creation()
    data[extra] = "private-value"
    with pytest.raises(ValueError) as error:
        promise(world(), data=data)
    assert "private-value" not in str(error.value)


def test_duplicate_creation_cannot_change_due_parties_or_evidence():
    result = world()
    before = copy.deepcopy(promise(result))
    data = creation()
    data.update(recipient="eve", due={"day": 100, "band": 0})
    with pytest.raises(ValueError, match="cannot be rewritten"):
        promise(result, data=data)
    assert result["systems"]["return_commitments"]["records"]["return-book"] == before


@pytest.mark.parametrize("turn", [True, -1, "4", 1.5])
def test_creation_rejects_bad_turn(turn):
    with pytest.raises(ValueError):
        promise(world(), turn=turn)


@pytest.mark.parametrize("decl", [None, {}, "fulfill", [None], [False], [[]], [{}],
    [{"op": "fulfill"}], [{"op": "fulfill", "id": ""}], [{"op": "fulfill", "id": True}],
    [{"op": "create", "id": "return-book"}], [{"op": "promise", "id": "return-book"}],
    [{"op": "cancel", "id": "return-book"}], [{"op": "renegotiate", "id": "return-book"}],
    [{"op": "fulfill", "id": "return-book", "due": {"day": 100, "band": 0}}],
    [{"op": "fulfill", "id": "return-book", "recipient": "eve"}],
])
def test_model_can_only_request_structurally_exact_fulfillment(decl):
    result = world()
    promise(result)
    errors = SYSTEM.validate("promises", decl, result)
    assert errors
    with pytest.raises(ValueError):
        SYSTEM.to_events("promises", decl, turn=4, day=2, scene="square")
    assert SYSTEM.validate("other", decl, result) == []
    assert SYSTEM.to_events("other", decl, turn=4, day=2, scene="square") == []


def test_validate_rejects_unknown_closed_and_duplicate_ids_without_record_details():
    result = world()
    promise(result)
    unknown = SYSTEM.validate("promises", [{"op": "fulfill", "id": "unknown-secret"}], result)
    duplicate = SYSTEM.validate("promises", [{"op": "fulfill", "id": "return-book"}] * 2, result)
    transfer(result)
    complete(result)
    closed = SYSTEM.validate("promises", [{"op": "fulfill", "id": "return-book"}], result)
    assert unknown and closed and duplicate
    assert unknown[0].hint == closed[0].hint == duplicate[0].hint
    assert "unknown-secret" not in str(unknown)
    assert "bob" not in str(closed)


def test_validate_defers_physical_check_for_same_action_transfer():
    result = world()
    promise(result)
    declaration = [{"op": "fulfill", "id": "return-book"}]
    assert SYSTEM.validate("promises", declaration, result) == []
    events = SYSTEM.to_events("promises", declaration, turn=4, day=2, scene="square")
    assert events[0]["deltas"] == {"id": "return-book"}
    with pytest.raises(ValueError, match="physical return"):
        SYSTEM.apply(result, events[0])
    transfer(result, turn=4)
    SYSTEM.apply(result, events[0])
    record = result["systems"]["return_commitments"]["records"]["return-book"]
    assert record["status"] == "fulfilled"
    assert record["fulfilled_at"] == {"day": 2, "band": 1}
    assert record["due"] == creation()["due"]
    assert record["evidence"] == creation()["evidence"]


@pytest.mark.parametrize("data", [{}, {"id": None}, {"id": []}, {"id": True},
    {"id": "return-book", "due": {"day": 50, "band": 0}},
    {"id": "return-book", "op": "fulfill"}, {"id": "return-book", "recipient": "eve"},
])
def test_fulfilled_events_reject_missing_or_mutating_data(data):
    result = world()
    before = copy.deepcopy(promise(result))
    transfer(result)
    with pytest.raises(ValueError):
        SYSTEM.apply(result, event("item_return_fulfilled", data=data))
    assert result["systems"]["return_commitments"]["records"]["return-book"] == before


def test_fulfillment_requires_open_existing_record():
    result = world()
    with pytest.raises(ValueError, match="unavailable"):
        complete(result)
    promise(result)
    transfer(result)
    complete(result)
    before = copy.deepcopy(result["systems"]["return_commitments"])
    with pytest.raises(ValueError, match="unavailable"):
        complete(result)
    assert result["systems"]["return_commitments"] == before


def test_preexisting_recipient_possession_is_not_fulfillment_even_same_turn():
    result = world(holder="bob", turn=4)
    promise(result)
    assert completion_events(result, day=2, scene="square", turn=4) == []
    with pytest.raises(ValueError, match="physical return"):
        complete(result)


def test_new_return_must_have_new_source_event_and_not_predate_created_turn():
    result = world()
    promise(result)
    transfer(result, source="initial-possession", turn=4)
    assert completion_events(result, day=2, scene="square", turn=4) == []
    transfer(result, source="new-source", turn=3)
    with pytest.raises(ValueError, match="physical return"):
        complete(result)
    transfer(result, to="alice", source="actually-borrowed", turn=4)
    transfer(result, source="actual-new-source", turn=4)
    complete(result)


def test_no_initial_holder_can_be_returned_after_creation():
    result = world(holder=None)
    record = promise(result)
    assert record["initial_held_by_source_event"] is None
    transfer(result)
    complete(result)
    assert record["status"] == "fulfilled"


def test_fresh_same_holder_receipt_does_not_return_preexisting_recipient_possession():
    result = world(holder="bob")
    record = promise(result)
    # ObjectSystem replay remains permissive for legacy saves; the typed domain
    # must reject this independently of new-turn transfer validation.
    transfer(result, to="bob", source="legacy-b-to-b", turn=4)
    assert completion_events(result, day=2, scene="square", turn=4) == []
    with pytest.raises(ValueError, match="physical return"):
        complete(result)
    assert record["status"] == "open"
    assert "fulfilled_at" not in record
    transfer(result, to="alice", source="real-borrow", turn=4)
    transfer(result, to="bob", source="real-return", turn=4)
    completions = completion_events(result, day=2, scene="square", turn=4)
    assert len(completions) == 1
    SYSTEM.apply(result, completions[0])
    assert record["status"] == "fulfilled"


def test_final_receipt_must_itself_prove_holder_change():
    result = world()
    promise(result)
    transfer(result, to="bob", source="real-a-to-b", turn=4)
    assert len(completion_events(result, day=2, scene="square", turn=4)) == 1
    # Reasserting B -> B as the final receipt must not borrow proof from the
    # earlier legitimate handover; completion checks the final canonical edge.
    transfer(result, to="bob", source="later-b-to-b", turn=4)
    assert completion_events(result, day=2, scene="square", turn=4) == []
    with pytest.raises(ValueError, match="physical return"):
        complete(result)


def test_claims_in_summary_facts_or_knowledge_cannot_fulfill():
    result = world()
    promise(result)
    graph = result["systems"]["ontology"]
    graph.assert_fact("book", "returned_to", "bob", day=2, turn=4, source_event="story-claim")
    graph.assert_fact("alice", "knows:book.returned_to", "bob", day=2, turn=4, source_event="belief")
    claim = event("item_return_fulfilled", data={"id": "return-book"})
    claim["summary"] = "Alice has returned the book to Bob, who forgives her."
    with pytest.raises(ValueError, match="physical return"):
        SYSTEM.apply(result, claim)
    assert completion_events(result, day=2, scene="square", turn=4) == []


def test_completion_generation_uses_final_holder_and_does_not_mutate():
    result = world()
    record = promise(result)
    transfer(result)
    events = completion_events(result, day=2, scene="square", turn=4)
    assert len(events) == 1
    assert events[0]["type"] == "item_return_fulfilled"
    assert events[0]["deltas"] == {"id": "return-book"}
    assert record["status"] == "open"
    transfer(result, to="eve", source="passed-on")
    assert completion_events(result, day=2, scene="square", turn=4) == []
    with pytest.raises(ValueError, match="physical return"):
        SYSTEM.apply(result, events[0])
    transfer(result, source="returned-finally")
    final_events = completion_events(result, day=2, scene="square", turn=4)
    SYSTEM.apply(result, final_events[0])
    assert completion_events(result, day=2, scene="square", turn=4) == []


def test_ambiguous_or_untyped_final_holder_cannot_fulfill():
    result = world()
    promise(result)
    graph = result["systems"]["ontology"]
    graph.add_relation("book", "held_by", "bob", day=2, turn=4,
                       source_event="ambiguous", supersede=False)
    with pytest.raises(ValueError, match="physical return"):
        complete(result)
    other = world()
    promise(other)
    transfer(other)
    other["systems"]["ontology"].add_entity("bob", "Object")
    with pytest.raises(ValueError, match="physical return"):
        complete(other)


@pytest.mark.parametrize("day,band,expected", [(3, 1, False), (3, 2, False), (3, 3, True), (4, 0, True)])
def test_overdue_is_derived_strictly_after_due_boundary(day, band, expected):
    result = world()
    record = promise(result)
    result["meta"].update(day=day, band=band)
    rows = visible_records(result, scene(day=day))
    assert rows[0]["overdue"] is expected
    assert "overdue" not in record


def test_late_completion_preserves_due_and_exact_fulfilled_clock():
    result = world()
    record = promise(result)
    result["meta"].update(day=4, band=0)
    assert visible_records(result, scene(day=4))[0]["overdue"] is True
    transfer(result, day=4, turn=7)
    events = completion_events(result, day=4, scene="library", turn=7)
    SYSTEM.apply(result, events[0])
    assert record["fulfilled_at"] == {"day": 4, "band": 0}
    assert record["due"] == {"day": 3, "band": 2}
    assert visible_records(result, scene(day=4))[0]["overdue"] is False


def test_visibility_requires_party_membership_even_when_everyone_public():
    result = world()
    promise(result)
    assert len(visible_records(result, scene("alice"))) == 1
    assert len(visible_records(result, scene("bob"))) == 1
    assert visible_records(result, scene("eve")) == []
    assert visible_records(result, scene("eve"), commitment_id="return-book") == []
    assert visible_records(result, {}) == []
    assert visible_records(result, scene(), commitment_id="missing") == []
    assert visible_records(result, scene(), commitment_id=[]) == []


@pytest.mark.parametrize("entity_id", ["book", "bob"])
def test_hidden_item_or_party_blocks_records_and_injection(entity_id):
    result = world()
    result["systems"]["ontology"].get_entity(entity_id).attrs["visibility"] = "hidden"
    promise(result)
    assert visible_records(result, scene()) == []
    assert visible_records(result, scene(), commitment_id="return-book") == []
    assert SYSTEM.inject(scene(), result) is None
    result["systems"]["ontology"].get_entity(entity_id).attrs["discovered_by"] = ["alice"]
    assert len(visible_records(result, scene())) == 1


def test_hidden_debtor_is_not_visible_to_recipient_until_discovered():
    result = world()
    promise(result)
    result["systems"]["ontology"].get_entity("alice").attrs["visibility"] = "hidden"
    assert visible_records(result, scene("bob")) == []
    assert len(visible_records(result, scene("bob", present=["alice"]))) == 1


def test_visible_records_are_independent_and_omit_private_transfer_provenance():
    result = world()
    record = promise(result)
    rows = visible_records(result, scene())
    assert "initial_held_by_source_event" not in rows[0]
    assert "debtor_known_entities" not in rows[0]
    assert "initial-possession" not in json.dumps(rows)
    rows[0]["due"]["day"] = 100
    rows[0]["evidence"]["quotes"][0] = "changed"
    rows[0]["status"] = "fulfilled"
    rows.clear()
    assert record["due"] == creation()["due"]
    assert record["evidence"] == creation()["evidence"]
    assert record["status"] == "open"


def test_injection_preserves_due_and_evidence_despite_contradictory_summaries():
    result = world()
    promise(result)
    result["systems"]["narrative"] = {"super_summary": "Alice never promised anything."}
    fragment = SYSTEM.inject(scene(), result)
    assert isinstance(fragment, Fragment)
    assert fragment.system == "return_commitments" and fragment.layer == "scene"
    injected = json.loads(fragment.text.splitlines()[1])
    assert injected[0]["due"] == creation()["due"]
    assert injected[0]["evidence"] == creation()["evidence"]
    assert "initial-possession" not in fragment.text
    assert "Alice never promised" not in fragment.text
    assert SYSTEM.inject(scene("eve"), result) is None


def test_old_promise_events_replay_as_empty_without_conversion():
    events = [event("promise_made", day=1, data={"id": "old", "text": "Return my money"}),
              event("promise_kept", day=2, data={"id": "old"})]
    result = project(registry(), events)
    assert result["systems"]["return_commitments"] == {"records": {}}
    assert visible_records(result, scene()) == []


def test_replay_retraction_and_final_clock_are_event_sourced():
    events = [event("entity_created", day=1, data={"id": "alice", "etype": "Person"}),
              event("entity_created", day=1, data={"id": "bob", "etype": "Person"}),
              event("object_created", day=1, data={"id": "book"}),
              event("item_transferred", day=1, turn=1, data={"item": "book", "to": "alice"}),
              event("clock_advanced", data={"bands": 1}),
              event(),
              event("item_transferred", data={"item": "book", "to": "bob"}),
              event("clock_advanced", data={"bands": 2}),
              event("item_return_fulfilled", data={"id": "return-book"})]
    result = project(registry(), events)
    record = result["systems"]["return_commitments"]["records"]["return-book"]
    assert record["created_at"] == {"day": 2, "band": 1}
    assert record["fulfilled_at"] == {"day": 2, "band": 3}
    events[-1]["retracted"] = True
    assert project(registry(), events)["systems"]["return_commitments"]["records"]["return-book"]["status"] == "open"
    for item in events[5:]:
        item["retracted"] = True
    assert project(registry(), events)["systems"]["return_commitments"] == {"records": {}}


def test_recipient_gets_contract_but_never_debtor_action_text_or_quotes():
    result = world()
    data = creation()
    data["evidence"]["player_actions"][0] += " Privately, I am hiding a treasure behind the altar."
    record = promise(result, data=data)
    debtor = visible_records(result, scene("alice"))[0]
    recipient = visible_records(result, scene("bob"))[0]
    assert debtor["evidence"] == data["evidence"] == record["evidence"]
    assert "evidence" not in recipient
    assert {key: recipient[key] for key in ("item", "debtor", "recipient", "due", "status")} == {
        "item": "book", "debtor": "alice", "recipient": "bob",
        "due": {"day": 3, "band": 2}, "status": "open"}
    injected = SYSTEM.inject(scene("bob"), result)
    assert "treasure" not in injected.text
    assert "player_actions" not in injected.text
    assert "I will return" not in injected.text
    assert "treasure" in SYSTEM.inject(scene("alice"), result).text
    assert record["evidence"] == data["evidence"]


def _colocated_world():
    result = world()
    graph = result["systems"]["ontology"]
    for name in ("alice", "bob"):
        graph.get_entity(name).attrs.clear()  # Ordinary people are not globally public.
        graph.add_relation(name, "located_in", "library", day=1, turn=1, source_event="arrival-" + name)
    graph.add_entity("inn", "Place")
    return result


def test_departed_counterpart_identity_remains_in_debtors_own_commitment_only():
    result = _colocated_world()
    graph = result["systems"]["ontology"]
    before = copy.deepcopy(graph.__dict__)
    creation_event = event()
    event_before = copy.deepcopy(creation_event)
    SYSTEM.apply(result, creation_event)
    record = result["systems"]["return_commitments"]["records"]["return-book"]
    assert record["debtor_known_entities"] == ["book", "alice", "bob"]
    assert graph.__dict__ == before  # No global discovery, knowledge, or physical mutation.
    assert creation_event == event_before
    graph.add_relation("alice", "located_in", "inn", day=2, turn=5, source_event="departure")
    debtor_scene = scene(location="inn")
    filtered = pov_world(result, debtor_scene)
    assert filtered["systems"]["ontology"].get_entity("bob") is None
    assert len(visible_records(result, debtor_scene)) == 1
    # The assembler already filters the graph before calling inject.
    assert visible_records(filtered, debtor_scene) == visible_records(result, debtor_scene)
    assert SYSTEM.inject(debtor_scene, filtered) is not None
    assert visible_records(result, scene("bob", location="library")) == []
    assert visible_records(result, scene("eve", location="inn")) == []
    assert graph.neighbors("book", "held_by", 2) == ["alice"]


@pytest.mark.parametrize("hidden_id", ["bob", "book"])
def test_hidden_at_creation_is_never_added_to_identity_memory(hidden_id):
    result = _colocated_world()
    graph = result["systems"]["ontology"]
    if hidden_id == "bob":
        graph.add_relation("bob", "located_in", "inn", day=2, turn=3, source_event="unseen")
    graph.get_entity(hidden_id).attrs["visibility"] = "hidden"
    record = promise(result)
    assert hidden_id not in record["debtor_known_entities"]
    graph.add_relation("alice", "located_in", "square", day=2, turn=5, source_event="departure")
    assert visible_records(result, scene(location="square")) == []
    assert SYSTEM.inject(scene(location="square"), result) is None


def test_identity_retention_exposes_no_current_location_holder_or_secret_facts():
    result = _colocated_world()
    promise(result)
    graph = result["systems"]["ontology"]
    graph.add_relation("alice", "located_in", "inn", day=2, turn=5, source_event="departure")
    graph.add_entity("secret-room", "Place", visibility="hidden")
    graph.add_relation("bob", "located_in", "secret-room", day=2, turn=5, source_event="hidden-travel")
    graph.assert_fact("bob", "hidden-plan", "TOP-SECRET-PLAN", day=2, turn=5,
                      source_event="private-plan", secrecy="secret")
    transfer(result, to="eve", turn=5, source="hidden-holder-change")
    graph.relations[-1].attrs["visibility"] = "hidden"
    debtor_scene = scene(location="inn")
    record_text = json.dumps(visible_records(result, debtor_scene))
    assert '"recipient": "bob"' in record_text
    for private in ("TOP-SECRET-PLAN", "secret-room", "hidden-plan", "hidden-holder-change", "eve"):
        assert private not in record_text
        assert private not in SYSTEM.inject(debtor_scene, result).text
    filtered = pov_world(result, debtor_scene)["systems"]["ontology"]
    assert filtered.get_entity("bob") is None
    assert not filtered.relations_at("book", "held_by", 2)
    assert not filtered.current_facts("bob")


def test_debtor_memory_does_not_become_public_in_other_party_records():
    result = _colocated_world()
    first = promise(result)
    other = creation()
    other.update(id="other-return", debtor="eve", recipient="bob")
    SYSTEM.apply(result, event(data=other))
    second = result["systems"]["return_commitments"]["records"]["other-return"]
    assert "bob" in first["debtor_known_entities"]
    assert "bob" not in second["debtor_known_entities"]
    assert visible_records(result, scene("eve", location="inn")) == []
    assert visible_records(result, scene("eve", location="inn"), commitment_id="return-book") == []


def test_offscene_own_record_survives_store_reopen_and_summary_compaction(tmp_path):
    reg = registry().register(NarrativeSystem())
    events = [event("entity_created", day=1, data={"id": "alice", "etype": "Person"}),
              event("entity_created", day=1, data={"id": "bob", "etype": "Person"}),
              event("entity_created", day=1, data={"id": "library", "etype": "Place"}),
              event("entity_created", day=1, data={"id": "inn", "etype": "Place"}),
              event("object_created", day=1, data={"id": "book"}),
              event("relation_added", day=1, data={"src": "alice", "rel": "located_in", "dst": "library"}),
              event("relation_added", day=1, data={"src": "bob", "rel": "located_in", "dst": "library"}),
              event("item_transferred", day=1, data={"item": "book", "to": "alice"}),
              event(),
              event("narration_recorded", data={"scene": "square", "text": "A long promise discussion."}),
              event("relation_added", data={"src": "alice", "rel": "located_in", "dst": "inn"}),
              event("scene_summarized", data={"scene": "square", "summary": "The book never mattered."}),
              event("recap_recompressed", data={"super_summary": "Alice never promised a return.",
                                               "summarized_through_index": 1})]
    before = copy.deepcopy(events)
    database, mirror = tmp_path / "events.db", tmp_path / "events.jsonl"
    with open_store(database, mirror, reg.event_types()) as store:
        store.append_many(events)
        first = project(reg, store.iter_events())
    disk_bytes = mirror.read_bytes()
    with open_store(database, mirror, reg.event_types()) as reopened:
        restored = project(reg, reopened.iter_events())
    assert events == before
    assert mirror.read_bytes() == disk_bytes
    assert first["systems"]["return_commitments"] == restored["systems"]["return_commitments"]
    debtor_scene = scene(location="inn")
    filtered = pov_world(restored, debtor_scene)
    assert filtered["systems"]["ontology"].get_entity("bob") is None
    rows = visible_records(filtered, debtor_scene)
    assert len(rows) == 1
    assert rows[0]["evidence"] == creation()["evidence"]
    assert rows[0]["due"] == creation()["due"]
    assert rows[0]["status"] == "open"
    assert json.loads(SYSTEM.inject(debtor_scene, filtered).text.splitlines()[1])[0] == rows[0]
    assert restored["systems"]["ontology"].neighbors("book", "held_by", 2) == ["alice"]
