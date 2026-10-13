"""Explicit player-to-NPC speech entry points use host-issued receipts only."""
from __future__ import annotations

import json

import pytest

from app.gui import GuiRequestError, _seed_demo_campaign, build_dashboard
from app.play import _build_scene, play_loop
from context.native_authoring import host_player_statement_receipt
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from systems.knowledge import player_statement_fact_key


def _proposal(receipt, statement: str) -> dict:
    return {
        "narration": "你把明确选定的一句话告诉了旅店掌柜。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "knowledge": [{
            "op": "heard_player", "receipt_id": receipt["id"],
            "knower": "innkeeper_mara", "speaker": "protagonist", "statement": statement,
        }],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "同场交谈"}],
    }


def _add_public_innkeeper(engine) -> None:
    engine.store.append_many([
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="public innkeeper", deltas={
            "id": "innkeeper_mara", "sketch": "公开同场的玛拉", "goal": "照看客栈", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="innkeeper at inn", deltas={
            "who": "innkeeper_mara", "to": "stonebridge_inn",
        }),
        kernel_event("entity_created", day=1, scene="s1", turn=0, summary="hidden listener", deltas={
            "id": "HIDDEN_SPEECH_RECIPIENT", "etype": "Person", "tier": "tracked",
            "attrs": {"visibility": "hidden", "sketch": "PRIVATE_SPEECH_SENTINEL"},
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="hidden listener at inn", deltas={
            "who": "HIDDEN_SPEECH_RECIPIENT", "to": "stonebridge_inn",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision


def test_cli_speak_explicitly_issues_one_current_scene_receipt(tmp_path):
    statement = "请替我记住：桥边的灯还亮着。"
    seed = build_dashboard(tmp_path / "campaign", demo=True)
    _add_public_innkeeper(seed.engine)
    scene = _build_scene(seed.engine)
    receipt = host_player_statement_receipt(seed.engine.world, scene, recipient="innkeeper_mara", statement=statement)
    provider = FakeLLMProvider(json_responses=[_proposal(receipt, statement)])
    seed.engine.provider = provider
    output: list[str] = []
    try:
        play_loop(seed.engine, inputs=[f"/speak innkeeper_mara {statement}", "/quit"], out=output.append,
                  max_repairs=0)
        graph = seed.engine.world["systems"]["ontology"]
        key = player_statement_fact_key("protagonist", statement)
        assert graph.value_at("innkeeper_mara", f"knows:{key}", 1) == statement
        prompt = next(user for _, user in provider.calls if "[player]" in user)
        assert "【本回合明确交流授权】" in prompt and receipt["id"] in prompt
        assert "PRIVATE_SPEECH_SENTINEL" not in prompt and "HIDDEN_SPEECH_RECIPIENT" not in prompt
    finally:
        seed.engine.store.close()


def test_gui_explicit_speech_is_visible_only_to_current_recipient_and_cold_recovers(tmp_path):
    campaign = tmp_path / "campaign"
    seed = build_dashboard(campaign, demo=True)
    _add_public_innkeeper(seed.engine)
    seed.engine.store.close()

    statement = "只请你记住这句公开提醒。"
    provider = FakeLLMProvider()
    dashboard = build_dashboard(campaign, provider=provider, max_repairs=0)
    initial = dashboard.state()
    assert initial["speech_recipients"] == [{"id": "innkeeper_mara", "label": "公开同场的玛拉"}]
    scene = _build_scene(dashboard.engine)
    receipt = host_player_statement_receipt(dashboard.engine.world, scene, recipient="innkeeper_mara", statement=statement)
    provider._json_responses = [_proposal(receipt, statement)]
    saved = dashboard.action(player_input="我郑重说明一件事", action_id="gui-explicit-speech",
                             expected_revision=initial["revision"], recipient="innkeeper_mara", statement=statement)
    assert saved["duplicate"] is False
    metadata = dashboard.engine.store.get_action_result_metadata("gui-explicit-speech")
    assert metadata["host_envelope"]["trusted_player_statement_receipts"] == [receipt]
    dashboard.engine.store.close()

    reopened = build_dashboard(campaign, provider=FakeLLMProvider(json_responses=[]), max_repairs=0)
    duplicate = reopened.action(player_input="我郑重说明一件事", action_id="gui-explicit-speech",
                                expected_revision=initial["revision"], recipient="innkeeper_mara", statement=statement)
    assert duplicate["duplicate"] is True
    with pytest.raises(GuiRequestError, match="绑定到另一项明确交流"):
        reopened.action(player_input="我郑重说明一件事", action_id="gui-explicit-speech",
                        expected_revision=initial["revision"], recipient="innkeeper_mara", statement="被改写的话")
    with pytest.raises(GuiRequestError, match="当前公开同场"):
        reopened.action(player_input="试图选择隐藏人物", action_id="hidden-speech",
                        expected_revision=reopened.state()["revision"], recipient="HIDDEN_SPEECH_RECIPIENT", statement="不能送达")
    rendered = json.dumps(reopened.state(), ensure_ascii=False)
    assert "PRIVATE_SPEECH_SENTINEL" not in rendered and "HIDDEN_SPEECH_RECIPIENT" not in rendered
    reopened.engine.store.close()
