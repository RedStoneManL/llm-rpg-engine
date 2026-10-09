"""Offline integration of conserved item ordering with the production write gate.

Only author/extractor responses are canned. Registry validation, ObjectSystem
preview, deterministic auditing, correction, approval and store writes are real.
These tests make no external API calls and are not native-model play evidence.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
import urllib.request

import pytest

from kernel.events import kernel_event, open_store
from kernel.projection import project
from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from loop import semantic_gate, turn
from loop.semantic_commit import VERSION, build_semantic_packet
from systems.object import ObjectSystem
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.time import TimeSystem


HANDOFF = "helper takes tool_a and tool_b from her tools and hands them to actor."
DROP = "actor sets tool_a and tool_b down in room."
SCENERY = "A quiet light fills the room."
PROSE = "\n\n".join((HANDOFF, DROP, SCENERY))
PLAYER_INPUT = "Take both tools from helper, then put both down in room."
INTERLEAVE = [0, 1, 4, 5, 2, 6, 3, 7, 8]
REQUIRED = frozenset({"items", "facts", "clock"})


def _proposal():
    rows = []
    for item in ("tool_a", "tool_b"):
        rows.extend([
            {"op": "create", "id": item},
            {"op": "transfer", "item": item, "to": "helper"},
            {"op": "transfer", "item": item, "from": "helper", "to": "actor"},
            {"op": "transfer", "item": item, "from": "actor", "to": "room"},
        ])
    # This legal but unrelated, non-minimal row must stay opaque and fixed.
    rows.append({"op": "create", "id": "spare",
                 "attrs": {"description": "OPAQUE_TEST_VALUE"}})
    return TurnCommit(PROSE, {
        "items": rows,
        "facts": [{"subject": "actor", "predicate": "mood", "value": "focused",
                   "secrecy": "public"}],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "Brief handoffs"}],
    })


def _extraction(packet):
    """Offline complete interpretation; the host, not this mock, decides truth."""
    claims = []
    for item in ("tool_a", "tool_b"):
        index = next(row["index"] for row in packet["transitions"]
                     if row.get("item") == item and row.get("from") == "helper")
        claims.append({
            "id": "source_" + item, "span_id": "s0", "quote": HANDOFF,
            "occurrence": 0, "kind": "possession", "scope": "canonical_transition",
            "binding_reason": "Taking the named tools from her tools asserts helper's source custody.",
            "mode": "completed", "moment": "transition_before", "transition_index": index,
            "refs": {"item": item, "holder": "helper"}, "present": True,
        })
    for span_id, quote, source, target in (
        ("s0", HANDOFF, "helper", "actor"),
        ("s1", DROP, "actor", "room"),
    ):
        for item in ("tool_a", "tool_b"):
            claims.append({
                "id": f"{span_id}_{item}", "span_id": span_id, "quote": quote,
                "occurrence": 0, "kind": "transfer", "scope": "canonical_transition",
                "binding_reason": "The exact quote names this item and its physical handoff.",
                "mode": "completed", "moment": "unknown", "transition_index": None,
                "refs": {"item": item, "from": source, "to": target},
            })
    result = {"version": VERSION, "claims": claims, "scene_state_support": [],
              "coverage": {"complete": True, "spans": []}}
    for span in packet["narration_spans"]:
        ids = [claim["id"] for claim in claims if claim["span_id"] == span["id"]]
        result["coverage"]["spans"].append({
            "span_id": span["id"], "claim_ids": ids, "status": "checked",
            "no_critical_claims": not ids, "context_span_ids": [], "context_complete": True,
        })
    if "reference_bindings" in packet:
        result["reference_bindings"] = []
    return result


class OfflineSemanticProvider:
    """Only the two real semantic extraction calls and one correction are allowed."""

    def __init__(self, correction=None, *, final_failure=None):
        self.correction = copy.deepcopy(correction if correction is not None else
                                        {"patches": [], "item_order": INTERLEAVE})
        self.final_failure = final_failure
        self.requests = []
        self.audit_count = 0

    def complete_messages(self, messages, **kwargs):
        payload = json.loads(messages[-1]["content"])
        self.requests.append(copy.deepcopy(payload))
        if "pov_packet" not in payload:
            assert len(self.requests) == 2, "Unexpected extra correction/provider call"
            assert "editable_spans" in payload and "issues" in payload
            return json.dumps(self.correction)
        self.audit_count += 1
        assert self.audit_count <= 2, "The gate must not obtain an unbounded extra audit"
        # A fresh full request includes even the untouched no-claims paragraph.
        assert payload["candidate_prose"] == PROSE
        assert [s["id"] for s in payload["pov_packet"]["narration_spans"]] == ["s0", "s1", "s2"]
        result = _extraction(payload["pov_packet"])
        if self.audit_count == 2:
            if self.final_failure == "contradiction":
                next(claim for claim in result["claims"] if claim["kind"] == "transfer")["refs"]["to"] = "room"
            elif self.final_failure == "incomplete":
                result["coverage"]["complete"] = False
            elif self.final_failure == "missing_span":
                result["coverage"]["spans"].pop()
            elif self.final_failure == "malformed":
                return "not JSON"
        return json.dumps(result)


class OfflineCandidateStrategy:
    def __init__(self, proposal):
        self.proposal = proposal

    def produce(self, *args, **kwargs):
        candidate = copy.deepcopy(self.proposal)
        candidate.semantic_audit_required = True
        return candidate


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This integration test is offline; network is forbidden")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)


@pytest.fixture
def game(tmp_path):
    registry = Registry()
    for system in (OntologySystem(), PlaceSystem(), ObjectSystem(), TimeSystem()):
        registry.register(system)
    store = open_store(str(tmp_path / "events.db"), str(tmp_path / "events.jsonl"),
                       allowed_types=registry.event_types())
    declarations = [
        ("entity_created", {"id": "actor", "etype": "Person"}),
        ("entity_created", {"id": "helper", "etype": "Person"}),
        ("entity_created", {"id": "room", "etype": "Place"}),
        ("entity_moved", {"who": "actor", "to": "room"}),
        ("entity_moved", {"who": "helper", "to": "room"}),
    ]
    store.append_many([
        kernel_event(kind, id=f"offline_seed_{index}", day=1, scene="room", turn=0,
                     summary="offline item-order fixture", deltas=deltas)
        for index, (kind, deltas) in enumerate(declarations)
    ])
    world = project(registry, store.iter_events())
    world["_action_turn"] = 1
    scene = {"id": "room", "location": "room", "protagonist": "actor", "day": 1,
             "present": ["actor", "helper"],
             "_semantic_resource_scope": {"actor": "actor", "turn": 1,
                                           "status": "none", "no_resources": True}}
    yield registry, store, world, scene
    store.close()


def _world_snapshot(world):
    systems = {**world["systems"], "ontology": vars(world["systems"]["ontology"])}
    return copy.deepcopy({**world, "systems": systems})


def _snapshot(game):
    _, store, world, _ = game
    return (_world_snapshot(world), store.revision, list(store.iter_events()),
            Path(store.jsonl_path).read_bytes())


def _produce(game, provider, proposal=None):
    registry, _, world, scene = game
    candidate, attempts, dropped = turn.produce_turn(
        registry, world, scene, PLAYER_INPUT,
        strategy=OfflineCandidateStrategy(proposal or _proposal()), provider=provider,
        max_repairs=0, required_sections=REQUIRED)
    assert attempts == 0 and dropped == []
    assert candidate.semantic_audit_required and candidate._semantic_approval is None
    assert not candidate.narration_rewrite_required
    return candidate


def _finalize(game, provider, candidate):
    registry, store, world, scene = game
    return semantic_gate.finalize_candidate(
        registry, world, scene, PLAYER_INPUT, candidate, provider=provider,
        revision=store.revision, required_sections=REQUIRED)


def _assert_no_publication(game, candidate, before):
    registry, store, _, scene = game
    assert candidate._semantic_approval is None
    assert _snapshot(game) == before
    with pytest.raises(turn.TurnRejected, match="host semantic approval"):
        turn.apply_turn(registry, store, candidate, day=1, scene=scene["id"])
    assert _snapshot(game) == before


@pytest.mark.parametrize("order", [INTERLEAVE, [4, 5, 0, 1, 2, 6, 3, 7, 8]])
def test_offline_real_gate_conserves_rows_endpoints_and_reaudits_every_span(game, monkeypatch, order):
    registry, store, world, scene = game
    before = _snapshot(game)
    provider = OfflineSemanticProvider({"patches": [], "item_order": order})
    calls = []
    real_produce = turn.produce_turn

    def observed_produce(*args, **kwargs):
        result = real_produce(*args, **kwargs)
        calls.append(copy.deepcopy(result[0].sections))
        return result

    def no_reuse(*args, **kwargs):
        pytest.fail("A declaration-order repair must never reuse prior model interpretations")

    monkeypatch.setattr(turn, "produce_turn", observed_produce)
    monkeypatch.setattr("loop.semantic_reuse.plan_reuse", no_reuse)
    candidate = _produce(game, provider)
    original = copy.deepcopy(candidate.sections)
    first_packet = build_semantic_packet(registry, world, scene, candidate, PLAYER_INPUT)
    approved = _finalize(game, provider, candidate)
    last_packet = build_semantic_packet(registry, world, scene, approved, PLAYER_INPUT)

    assert _snapshot(game) == before  # Neither initial validation nor the repair wrote anything.
    assert type(registry.owner_of_section("items")) is ObjectSystem
    assert len(calls) == 2 and calls[0] == original and calls[1] == approved.sections
    assert candidate.sections == original and candidate._semantic_approval is None
    assert approved.narration == PROSE and approved._semantic_approval is not None
    assert approved.sections["items"] == [original["items"][index] for index in order]
    assert Counter(map(json.dumps, approved.sections["items"])) == Counter(map(json.dumps, original["items"]))
    assert list(approved.sections) == list(original)
    for section in original.keys() - {"items"}:
        assert approved.sections[section] == original[section]
    for item in ("tool_a", "tool_b", "spare"):
        own = lambda rows: [row for row in rows if row.get("item", row.get("id")) == item]
        assert own(approved.sections["items"]) == own(original["items"])
    for key in ("actor_id", "entities", "before", "after", "continuous_custody",
                "scene_fact_context", "scene_fact_sources"):
        assert first_packet[key] == last_packet[key]
    # Real candidate references carry row provenance. Conserving their meaning
    # requires remapping source.index, and may change their list order as well.
    refs = {row["id"]: row for row in last_packet["candidate_refs"]}
    for row in first_packet["candidate_refs"]:
        expected = copy.deepcopy(row)
        expected["source"]["index"] = order.index(row["source"]["index"])
        assert refs[row["id"]] == expected
    assert first_packet["candidate_refs"] != last_packet["candidate_refs"]
    assert [row["item"] for row in first_packet["transitions"]] == ["tool_a", "tool_a", "tool_b", "tool_b"]
    assert [row["item"] for row in last_packet["transitions"]] == ["tool_a", "tool_b", "tool_a", "tool_b"]

    assert len(provider.requests) == 3 and provider.audit_count == 2
    offered = provider.requests[1]["item_order_options"]
    assert offered["rows"][-1] == {"index": 8, "movable": False}
    assert "OPAQUE_TEST_VALUE" not in json.dumps(provider.requests)
    log = approved.semantic_audit_log
    assert [entry["kind"] for entry in log] == ["semantic_audit", "semantic_correction", "semantic_audit"]
    assert log[0]["passed"] is False and log[-1]["passed"] is True and not log[-1]["issues"]
    assert log[1]["item_order"] == order
    source_before = next(claim for claim in log[0]["claims"] if claim["id"] == "source_tool_b")
    source_after = next(claim for claim in log[-1]["claims"] if claim["id"] == "source_tool_b")
    assert source_before["transition_index"] == 2 and source_after["transition_index"] == 1
    assert source_after["co_claimed_transfer_precondition"] is not None
    assert log[1]["before_sections_digest"] != log[1]["after_sections_digest"]
    for audit in (log[0], log[-1]):
        assert audit["interpretation_reuse"] == {
            "mode": "full", "reused_span_ids": [], "reextracted_span_ids": ["s0", "s1", "s2"]}
    assert semantic_gate.approval_matches(approved, world, scene, PLAYER_INPUT, store.revision)

    result = turn.apply_turn(registry, store, approved, day=1, scene="room")
    events = list(store.iter_events())[len(before[2]):]
    item_events = [event for event in events if event["type"] in {"object_created", "item_transferred"}]
    assert [event["deltas"] for event in item_events] == approved.sections["items"]
    assert all(result["systems"]["ontology"].neighbors(item, "held_by", 1) == ["room"]
               for item in ("tool_a", "tool_b"))
    assert _world_snapshot(world) == before[0]


@pytest.mark.parametrize("correction, reason", [
    ({"patches": [], "items": [{"op": "transfer", "item": "tool_a", "to": "helper"}]}, "shape"),
    ({"patches": [], "item_order": INTERLEAVE, "facts": []}, "shape"),
    ({"patches": [], "item_order": INTERLEAVE, "moves": []}, "other effect"),
    ({"patches": [], "item_order": INTERLEAVE, "links": []}, "other effect"),
    ({"patches": [], "item_order": [0, 2, 1, 4, 5, 6, 3, 7, 8]}, "individual item chain"),
    ({"patches": [], "item_order": [8, 0, 1, 4, 5, 2, 6, 3, 7]}, "opaque declarations"),
    ({"patches": [], "item_order": INTERLEAVE[:-1]}, "complete eligible index permutation"),
    ({"patches": [], "item_order": [0, 1, 4, 5, 2, 6, 3, 7, 7]}, "complete eligible index permutation"),
    ({"patches": [], "item_order": [False, 1, 4, 5, 2, 6, 3, 7, 8]}, "complete eligible index permutation"),
    ({"patches": [], "item_order": [{"index": i} for i in INTERLEAVE]}, "complete eligible index permutation"),
])
def test_offline_gate_rejects_row_edits_reverse_chains_and_invalid_orders_without_writes(game, correction, reason):
    before = _snapshot(game)
    provider = OfflineSemanticProvider(correction)
    candidate = _produce(game, provider)
    original = copy.deepcopy(candidate.sections)
    with pytest.raises(turn.TurnRejected, match=reason):
        _finalize(game, provider, candidate)
    assert "item_order_options" in provider.requests[1]
    assert len(provider.requests) == 2 and provider.audit_count == 1
    assert candidate.sections == original
    _assert_no_publication(game, candidate, before)


@pytest.mark.parametrize("scope", ["resource_unknown", "return_intent", "open_return",
                                   "time_advance", "movement", "extended_item"])
def test_offline_out_of_scope_candidates_cannot_opt_in_to_ordering(game, scope):
    _, _, world, scene = game
    proposal = _proposal()
    if scope == "resource_unknown":
        del scene["_semantic_resource_scope"]
    elif scope == "return_intent":
        scene["_semantic_return_commitment"] = True
    elif scope == "open_return":
        world["systems"]["return_commitments"] = {"records": {
            "open_tool_return": {"status": "open", "item": "tool_b"}}}
    elif scope == "time_advance":
        proposal.sections["clock"] = [{"advance": True, "bands": 1, "days": 0, "reason": "Time passes"}]
    elif scope == "movement":
        proposal.sections["moves"] = [{"who": "helper", "to": "room"}]
    elif scope == "extended_item":
        proposal.sections["items"][4]["attrs"] = {"quantity": 2}
    before = _snapshot(game)
    provider = OfflineSemanticProvider()
    candidate = _produce(game, provider, proposal)
    with pytest.raises(turn.TurnRejected, match="complete eligible index permutation"):
        _finalize(game, provider, candidate)
    assert len(provider.requests) == 2 and "item_order_options" not in provider.requests[1]
    _assert_no_publication(game, candidate, before)


@pytest.mark.parametrize("failure", ["contradiction", "incomplete", "missing_span", "malformed"])
def test_offline_successful_permutation_still_requires_a_passing_fresh_full_audit(game, failure, monkeypatch):
    corrected = []
    real_correct = semantic_gate._correct

    def observed_correct(*args, **kwargs):
        result = real_correct(*args, **kwargs)
        corrected.append(result[0])
        return result

    monkeypatch.setattr(semantic_gate, "_correct", observed_correct)
    before = _snapshot(game)
    provider = OfflineSemanticProvider(final_failure=failure)
    candidate = _produce(game, provider)
    with pytest.raises(turn.TurnRejected, match="Semantic audit|unsupported or contradictory"):
        _finalize(game, provider, candidate)
    assert len(provider.requests) == 3 and provider.audit_count == 2
    assert [row["item"] for row in provider.requests[-1]["pov_packet"]["transitions"]] == [
        "tool_a", "tool_b", "tool_a", "tool_b"]
    assert len(corrected) == 1
    _assert_no_publication(game, corrected[0], before)
    _assert_no_publication(game, candidate, before)


def test_offline_noop_permutation_does_not_bypass_final_chronology_audit(game):
    before = _snapshot(game)
    provider = OfflineSemanticProvider({"patches": [], "item_order": list(range(9))})
    candidate = _produce(game, provider)
    with pytest.raises(turn.TurnRejected, match="unsupported or contradictory"):
        _finalize(game, provider, candidate)
    assert len(provider.requests) == 3 and provider.audit_count == 2
    _assert_no_publication(game, candidate, before)


def test_offline_approved_order_is_bound_at_the_write_edge(game):
    registry, store, _, scene = game
    before = _snapshot(game)
    provider = OfflineSemanticProvider()
    approved = _finalize(game, provider, _produce(game, provider))
    approved.sections["items"][-2]["to"] = "helper"
    with pytest.raises(turn.TurnRejected, match="host semantic approval"):
        turn.apply_turn(registry, store, approved, day=1, scene=scene["id"])
    assert _snapshot(game) == before


def test_offline_nonbuiltin_item_owner_cannot_offer_reordering(game):
    class CustomObjectSystem(ObjectSystem):
        pass

    registry = Registry()
    for system in (OntologySystem(), PlaceSystem(), CustomObjectSystem(), TimeSystem()):
        registry.register(system)
    custom_game = (registry, *game[1:])
    before = _snapshot(custom_game)
    provider = OfflineSemanticProvider()
    candidate = _produce(custom_game, provider)
    with pytest.raises(turn.TurnRejected, match="complete eligible index permutation"):
        _finalize(custom_game, provider, candidate)
    assert len(provider.requests) == 2 and "item_order_options" not in provider.requests[1]
    _assert_no_publication(custom_game, candidate, before)


def test_offline_opening_context_cannot_smuggle_item_effects_into_the_gate(game):
    before = _snapshot(game)
    proposal = _proposal()
    proposal._semantic_context = {
        "policy": "opening_text_only", "immutable_prose": False, "bindings": []}
    provider = OfflineSemanticProvider()
    candidate = _produce(game, provider, proposal)
    with pytest.raises(turn.TurnRejected, match="Cannot build the bounded semantic POV packet"):
        _finalize(game, provider, candidate)
    assert provider.requests == []
    _assert_no_publication(game, candidate, before)
