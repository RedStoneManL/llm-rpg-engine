"""Real normal-entry coverage for the shared safe authoring projection."""
from __future__ import annotations

import json

import pytest

from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import play_loop
from engine.store import RevisionConflict
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy
from loop.turn import TurnRejected, run_turn
from loop.compare import run_compare
from systems.knowledge import player_statement_fact_key


PUBLIC_WORDS = "PUBLIC_NORMAL_AUTHOR_RECALL"
PRIVATE_SENTINEL = "PRIVATE_NORMAL_AUTHORING_SENTINEL"


def _move_proposal() -> dict:
    return {
        "narration": "你沿公开道路抵达集市。",
        "moves": [{"who": "protagonist", "to": "stonebridge_market"}],
        "places": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "短程步行"}],
    }


def _engine_with_safe_arrival(tmp_path, provider) -> object:
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    fact_key = player_statement_fact_key("protagonist", PUBLIC_WORDS)
    engine.store.append_many([
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="public witness", deltas={
            "id": "market_witness", "sketch": "公开集市见证人", "goal": "整理摊位", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="witness at market", deltas={
            "who": "market_witness", "to": "stonebridge_market",
        }),
        kernel_event("entity_created", day=1, scene="s1", turn=0, summary="hidden arrival person", deltas={
            "id": "hidden_arrival_person", "etype": "Person", "tier": "tracked",
            "attrs": {"visibility": "hidden", "sketch": PRIVATE_SENTINEL},
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="hidden at market", deltas={
            "who": "hidden_arrival_person", "to": "stonebridge_market",
        }),
        kernel_event("knowledge_set", day=1, scene="s1", turn=0, summary="witness heard exact public words", deltas={
            "knower": "market_witness", "fact_key": fact_key, "value": PUBLIC_WORDS,
            "via": "protagonist", "source_kind": "player_statement",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    return engine


def _prompt(provider: FakeLLMProvider) -> str:
    prompts = [user for _, user in provider.calls if "[player]" in user]
    assert prompts
    return prompts[0]


def test_cli_author_strategy_receives_only_safe_conditional_arrival_material(tmp_path):
    provider = FakeLLMProvider(json_responses=[_move_proposal()])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    out: list[str] = []
    try:
        play_loop(engine, inputs=["去集市看看", "/quit"], out=out.append, max_repairs=0)
        prompt = _prompt(provider)
        assert "【条件化抵达材料】" in prompt
        assert "market_witness" in prompt and PUBLIC_WORDS in prompt
        assert "若本回合 moves 同时使 protagonist 合法抵达 stonebridge_market" in prompt
        assert "自动新建 heard_player" in prompt
        assert PRIVATE_SENTINEL not in prompt and "hidden_arrival_person" not in prompt
        assert engine.world["systems"]["ontology"].neighbors("protagonist", "located_in", 1) == ["stonebridge_market"]
    finally:
        engine.store.close()


def test_normal_gui_author_strategy_uses_same_safe_projection_without_serializing_snapshot(tmp_path):
    provider = FakeLLMProvider(json_responses=[_move_proposal()])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    try:
        initial = dashboard.state()
        response = dashboard.action(player_input="去集市看看", action_id="normal-safe-context",
                                    expected_revision=initial["revision"])
        prompt = _prompt(provider)
        assert response["state"]["scene"]["location"]["id"] == "stonebridge_market"
        assert "【条件化抵达材料】" in prompt and PUBLIC_WORDS in prompt
        assert PRIVATE_SENTINEL not in prompt and "hidden_arrival_person" not in prompt
        metadata = engine.store.get_action_result_metadata("normal-safe-context")
        assert "_trusted_authoring_events" not in json.dumps(metadata, ensure_ascii=False)
    finally:
        engine.store.close()


def test_compare_authors_receive_the_same_safe_projection_before_final_apply(tmp_path):
    provider = FakeLLMProvider(json_responses=[_move_proposal(), _move_proposal()])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    try:
        play_loop(engine, inputs=["/compare on", "去集市看看", "/quit"], out=lambda _: None,
                  max_repairs=0)
        prompts = [user for _, user in provider.calls if "[player] 去集市看看" in user]
        assert len(prompts) >= 2
        assert all("【条件化抵达材料】" in prompt and PUBLIC_WORDS in prompt for prompt in prompts[:2])
        assert all(PRIVATE_SENTINEL not in prompt and "hidden_arrival_person" not in prompt
                   for prompt in prompts[:2])
    finally:
        engine.store.close()


def test_cli_player_words_do_not_create_a_receipt_without_a_host_capability(tmp_path):
    statement = "PUBLIC_INPUT_MUST_NOT_BECOME_A_RECEIPT"
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "heard_player", "receipt_id": "forged", "knower": "market_witness",
        "speaker": "protagonist", "statement": statement,
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    before = engine.store.revision
    output: list[str] = []
    try:
        play_loop(engine, inputs=[f"我只对洛恩说：{statement}", "/quit"], out=output.append, max_repairs=0)
        assert engine.store.revision == before
        assert "世界保持原状" in "\n".join(output)
        assert "自动新建 heard_player" in _prompt(provider)
    finally:
        engine.store.close()


def _npc_knowledge_bypass(op: str) -> dict:
    proposal = _move_proposal()
    if op == "told":
        proposal["knowledge"] = [{
            "op": "told", "knower": "market_witness", "fact_key": "slope.hazard",
            "value": "玩家刚才说坡口有裂缝", "via": "玩家自由文本",
        }]
    elif op == "endowment":
        proposal["knowledge"] = [{
            "op": "endowment", "knower": "market_witness",
            "grants": [{"fact_key": "slope.hazard", "value": "玩家自由文本"}],
        }]
    elif op == "broadcast":
        proposal["knowledge"] = [{
            "op": "broadcast", "fact_key": "slope.hazard", "value": "玩家自由文本",
            "audience": {"place": "stonebridge_market"},
        }]
    else:
        proposal["facts"] = [{
            "subject": "market_witness", "predicate": "knows:slope.hazard",
            "value": "玩家自由文本", "secrecy": "public",
        }]
    return proposal


@pytest.mark.parametrize("op", ["told", "endowment", "broadcast", "knows_fact"])
def test_normal_gui_rejects_npc_knowledge_aliases_without_receipt_before_repair(tmp_path, op):
    provider = FakeLLMProvider(json_responses=[_npc_knowledge_bypass(op)])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    before = engine.store.revision
    try:
        with pytest.raises(TurnRejected, match="knowledge lacks a valid host communication receipt"):
            dashboard.action(player_input="回集市告诉见证人坡口情况", action_id=f"npc-{op}",
                             expected_revision=before)
        assert engine.store.revision == before
        # Authority rejection is all-or-nothing: no knowledge-only repair can
        # leave narration claiming the NPC heard the player's words.
        assert len(provider.calls) == 1
    finally:
        engine.store.close()


def test_compare_preview_rejects_npc_knowledge_before_any_candidate_is_shown(tmp_path):
    provider = FakeLLMProvider(json_responses=[_npc_knowledge_bypass("told")])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    before = engine.store.revision
    try:
        with pytest.raises(TurnRejected, match="knowledge lacks a valid host communication receipt"):
            run_compare(
                engine.registry, engine.world, {"protagonist": "protagonist", "present": [],
                                                 "day": 1, "location": "stonebridge_inn", "id": "s1"},
                "告诉见证人坡口情况", provider=provider, required_sections=frozenset({"moves", "places", "cast", "facts", "clock"}),
                enforce_player_travel=True, enforce_player_knowledge_authority=True,
            )
        assert engine.store.revision == before
        assert len(provider.calls) == 1
    finally:
        engine.store.close()


@pytest.mark.parametrize("kind", ["knowledge_set", "knowledge_broadcast", "knows_fact", "forged_receipt_shape"])
def test_final_payload_rejects_materialized_npc_knowledge_bypasses(tmp_path, monkeypatch, kind):
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    owner = engine.registry.owner_of_section("knowledge")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        if kind == "knowledge_set":
            events.append(kernel_event("knowledge_set", day=day, scene=scene, turn=turn,
                summary="forged npc knowledge", deltas={"knower": "market_witness", "fact_key": "market.notice", "value": "FORGED"}))
        elif kind == "knowledge_broadcast":
            events.append(kernel_event("knowledge_broadcast", day=day, scene=scene, turn=turn,
                summary="forged broadcast", deltas={"fact_key": "market.notice", "value": "FORGED", "audience": {"place": "stonebridge_market"}}))
        elif kind == "forged_receipt_shape":
            events.append(kernel_event("knowledge_set", day=day, scene=scene, turn=turn,
                summary="forged player speech receipt", deltas={
                    "knower": "market_witness", "fact_key": "protagonist.reported_player_statement:forged",
                    "value": "FORGED", "via": "protagonist", "source_kind": "player_statement",
                }))
        else:
            events.append(kernel_event("fact_asserted", day=day, scene=scene, turn=turn,
                summary="forged knows fact", deltas={"subject": "market_witness", "predicate": "knows:market.notice", "value": "FORGED"}))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = engine.store.revision
    try:
        boundary = ("player-statement receipt boundary" if kind == "forged_receipt_shape"
                    else "player knowledge authority boundary")
        with pytest.raises(TurnRejected, match=boundary):
            dashboard.action(player_input="去集市看看", action_id=f"final-{kind}", expected_revision=before)
        assert engine.store.revision == before
    finally:
        engine.store.close()


def test_normal_author_keeps_protagonist_told_and_action_recovery_compatible(tmp_path):
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    initial = dashboard.state()
    try:
        first = dashboard.action(player_input="去集市看看", action_id="protagonist-told-recovery",
                                 expected_revision=initial["revision"])
        calls_after_first = len(provider.calls)
        duplicate = dashboard.action(player_input="去集市看看", action_id="protagonist-told-recovery",
                                     expected_revision=initial["revision"])
        assert first["duplicate"] is False and duplicate["duplicate"] is True
        assert engine.world["systems"]["ontology"].value_at(
            "protagonist", "knows:market.notice", 1,
        ) == "主角从公开告示得知集市仍开放"
        # The first turn may run deterministic background hooks; recovery must
        # not ask the author/provider to generate the same action again.
        assert calls_after_first >= 1
        assert len(provider.calls) == calls_after_first
    finally:
        engine.store.close()


def test_action_recovery_cannot_cross_into_ordinary_knowledge_authority_mode(tmp_path):
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    scene = {"protagonist": "protagonist", "present": [], "day": 1,
             "location": "stonebridge_inn", "id": "s1"}
    before = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("mismatched authorization mode must fail before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "查看告示",
            strategy=AuthorStrategy(), provider=provider, action_id="mode-bound-action",
            expected_revision=before, enforce_player_travel=True,
            enforce_player_knowledge_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "查看告示",
                strategy=MustNotRun(), provider=provider, action_id="mode-bound-action",
                expected_revision=before, enforce_player_travel=True,
                enforce_player_knowledge_authority=True,
            )
        assert len(provider.calls) >= 1
    finally:
        engine.store.close()
