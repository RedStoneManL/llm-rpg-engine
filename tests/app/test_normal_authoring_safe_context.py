"""Real normal-entry coverage for the shared safe authoring projection."""
from __future__ import annotations

import json

from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import play_loop
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy
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
