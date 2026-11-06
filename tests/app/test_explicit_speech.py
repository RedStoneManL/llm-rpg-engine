"""Explicit player-to-NPC speech entry points use host-issued receipts only."""
from __future__ import annotations

import json
import io

import pytest

from app.gui import (GuiRecoveryError, GuiRequestError, _seed_demo_campaign,
                     build_dashboard, make_app)
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


def _post_wsgi(app, payload: dict) -> tuple[str, dict]:
    raw = json.dumps(payload).encode("utf-8")
    captured = []
    response = b"".join(app({
        "REQUEST_METHOD": "POST", "PATH_INFO": "/api/action",
        "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8765",
        "HTTP_HOST": "127.0.0.1:8765", "HTTP_ORIGIN": "http://127.0.0.1:8765",
        "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }, lambda status, headers: captured.append((status, headers))))
    return captured[0][0], json.loads(response.decode("utf-8"))


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


def test_cli_speak_accepts_the_unique_public_display_name(tmp_path):
    """CLI status exposes labels, so a unique public label must be usable safely."""
    statement = "请替我记住：桥边的灯还亮着。"
    seed = build_dashboard(tmp_path / "campaign", demo=True)
    _add_public_innkeeper(seed.engine)
    scene = _build_scene(seed.engine)
    receipt = host_player_statement_receipt(
        seed.engine.world, scene, recipient="innkeeper_mara", statement=statement,
    )
    provider = FakeLLMProvider(json_responses=[_proposal(receipt, statement)])
    seed.engine.provider = provider
    output: list[str] = []
    try:
        play_loop(
            seed.engine,
            inputs=["/status", f'/speak "公开同场的玛拉" {statement}', "/quit"],
            out=output.append,
            max_repairs=0,
        )
        graph = seed.engine.world["systems"]["ontology"]
        key = player_statement_fact_key("protagonist", statement)
        assert graph.value_at("innkeeper_mara", f"knows:{key}", 1) == statement
        assert "[状态] 同场角色：公开同场的玛拉" in output
        prompt = next(user for _, user in provider.calls if "[player]" in user)
        assert receipt["id"] in prompt
        assert "PRIVATE_SPEECH_SENTINEL" not in prompt
    finally:
        seed.engine.store.close()


def test_cli_speak_rejects_an_ambiguous_public_display_name_without_writing(tmp_path):
    seed = build_dashboard(tmp_path / "campaign", demo=True)
    _add_public_innkeeper(seed.engine)
    seed.engine.store.append_many([
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="same public label", deltas={
            "id": "another_mara", "sketch": "公开同场的玛拉", "goal": "同名公开角色", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="same public label at inn", deltas={
            "who": "another_mara", "to": "stonebridge_inn",
        }),
    ], preflight=lambda history: project(seed.engine.registry, history))
    seed.engine.world = project(seed.engine.registry, seed.engine.store.iter_events())
    seed.engine.world["_revision"] = seed.engine.store.revision
    before = (seed.engine.store.revision, list(seed.engine.store.iter_events()))
    output: list[str] = []
    try:
        play_loop(
            seed.engine,
            inputs=['/speak "公开同场的玛拉" 请记住这句。', "/quit"],
            out=output.append,
            max_repairs=0,
        )
        assert any("唯一的当前公开同场角色显示名或 ID" in line for line in output)
        assert (seed.engine.store.revision, list(seed.engine.store.iter_events())) == before
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
    with pytest.raises(GuiRecoveryError, match="无法安全恢复"):
        reopened.action(player_input="我郑重说明一件事", action_id="gui-explicit-speech",
                        expected_revision=initial["revision"], recipient="innkeeper_mara", statement="被改写的话")
    # This is a committed action, not a confirmed client-side rejection.  The
    # browser's 409 branch keeps the pending action instead of clearing it.
    status, payload = _post_wsgi(make_app(reopened), {
        "text": "我郑重说明一件事", "action_id": "gui-explicit-speech",
        "expected_revision": initial["revision"], "recipient": "innkeeper_mara",
        "statement": "被改写的话",
    })
    assert status.startswith("409")
    assert payload["message"] == "已保存行动无法安全恢复；请刷新查看当前世界状态。"
    assert payload["state"] == reopened.state()
    assert "PRIVATE_SPEECH_SENTINEL" not in json.dumps(payload["state"], ensure_ascii=False)
    assert "confirmed_rejection" not in payload
    with pytest.raises(GuiRequestError, match="当前公开同场"):
        reopened.action(player_input="试图选择隐藏人物", action_id="hidden-speech",
                        expected_revision=reopened.state()["revision"], recipient="HIDDEN_SPEECH_RECIPIENT", statement="不能送达")
    rendered = json.dumps(reopened.state(), ensure_ascii=False)
    assert "PRIVATE_SPEECH_SENTINEL" not in rendered and "HIDDEN_SPEECH_RECIPIENT" not in rendered
    reopened.engine.store.close()


def test_named_historical_questions_use_receipts_without_advancing_play():
    from docs.verification.rcv130.probe import game, seed, INN, MARKET, HERO

    with game() as (engine, dash):
        for who, name in (("watch", "阿岚"), ("scribe", "白禾")):
            seed(engine, "character_created", id=who, op="create", name=name, sketch="值班人", goal="照看名册", tier="tracked")
            seed(engine, "entity_moved", who=who, to=INN)
        for n, (who, quote) in enumerate([("watch", f"第{i}枚蓝印") for i in range(6)] + [("scribe","只交给白禾的红印")]):
            receipt = host_player_statement_receipt(engine.world, _build_scene(engine), recipient=who, statement=quote)
            proposal = _proposal(receipt, quote)
            proposal["knowledge"][0]["knower"] = who
            engine.provider = FakeLLMProvider(json_responses=[proposal])
            dash.action(player_input="交代一句话", action_id=f"speech-{n}", expected_revision=engine.store.revision,
                        recipient=who, statement=quote)
        # The player can still use the name they heard in this scene after
        # the NPC changes names and leaves. Later names cannot overwrite it.
        seed(engine, "fact_asserted", subject="watch", predicate="name", value="新区名", secrecy="public")
        seed(engine, "entity_moved", who="watch", to=MARKET)
        path = engine.store.db_path.parent
        provider = FakeLLMProvider()
        reopened = build_dashboard(path, provider=provider)
        try:
            db = reopened.engine.store
            revision, history = db.snapshot()
            before_bytes = db.db_path.read_bytes()
            query = "第一天我对阿岚说了什么？"
            lookup = reopened.action(player_input=query, action_id="historical-question", expected_revision=revision)
            assert lookup["read_only"]
            page = lookup["recall"]
            assert len(page["opened"]) == 5 and page["next_before"]
            assert all(row["source"]["recipient"] == "watch" for row in page["opened"])
            assert all("红印" not in text for text in page["hits"])
            tail = reopened.recall(query + " 续页=" + page["next_before"])
            assert len(tail["opened"]) == 1
            assert tail["opened"][0]["source"]["text"] == "第0枚蓝印" and tail["next_before"] is None
            dated = reopened.recall("我哪天对阿岚说过“第2枚蓝印”？")
            assert [(r["source"]["day"], r["source"]["text"]) for r in dated["opened"]] == [(1,"第2枚蓝印")]
            assert reopened.recall("回忆：收件人=watch 未说过的暗号")["opened"] == []
            assert db.snapshot() == (revision, history) and db.db_path.read_bytes() == before_bytes
            assert not provider.calls and db.get_action_receipt("historical-question") is None
            reopened.undo(expected_revision=revision)
            with pytest.raises(GuiRequestError, match="重新检索"):
                reopened.recall(query, before=page["next_before"])
        finally:
            reopened.engine.store.close()


def test_read_only_cli_question_does_not_consume_a_turn(tmp_path):
    seed = build_dashboard(tmp_path, demo=True)
    engine = seed.engine
    revision = engine.store.revision
    output = []
    try:
        play_loop(engine, inputs=["第一天我对无人说过什么？","/quit"], out=output.append)
        assert any("找不到可核验的原文" in line for line in output)
        assert engine.store.revision == revision
    finally:
        engine.store.close()
