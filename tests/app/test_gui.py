"""Safety and persistence coverage for the loopback player dashboard."""
from __future__ import annotations

import io
import json

import pytest

import app.engine as engine_module
import app.gui as gui
from app.engine import build_engine
from app.gui import (GuiRequestError, PlayerDashboard, _seed_demo_campaign,
                     GmDashboard, build_dashboard, build_gm_state,
                     build_player_state, make_app)
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy


def test_demo_dashboard_action_duplicate_undo_and_reopen(tmp_path):
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    initial = dashboard.state()
    assert initial["scene"]["location"]["id"] == "stonebridge_inn"
    assert initial["inventory"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
    assert initial["codex"][0]["title"] == "石桥镇"
    assert {node["id"] for node in initial["map"]["nodes"]} == {
        "stonebridge_inn", "stonebridge_market",
    }
    assert initial["map"]["edges"] == [{
        "from": "stonebridge_inn", "to": "stonebridge_market", "cost": "1",
    }]
    assert "north_bank_old_tower" not in json.dumps(initial, ensure_ascii=False)

    saved = dashboard.action(player_input="去集市看看", action_id="gui-1", expected_revision=initial["revision"])
    assert saved["duplicate"] is False
    assert saved["state"]["scene"]["location"]["id"] == "stonebridge_market"
    assert saved["state"]["map"] == initial["map"]
    revision = saved["revision"]

    retry = dashboard.action(player_input="去集市看看", action_id="gui-1", expected_revision=initial["revision"])
    assert retry["duplicate"] is True
    assert retry["revision"] == revision
    assert saved["narration"] in dashboard.state()["narration"]
    dashboard.undo(expected_revision=revision)
    after_undo = dashboard.state()
    assert after_undo["scene"]["location"]["id"] == "stonebridge_inn"
    assert saved["narration"] not in after_undo["narration"]
    dashboard.engine.store.close()

    reopened = build_dashboard(campaign, demo=True)
    assert reopened.state()["scene"]["location"]["id"] == "stonebridge_inn"
    assert reopened.state()["inventory"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
    assert saved["narration"] not in reopened.state()["narration"]
    reopened.engine.store.close()


def test_dashboard_does_not_serialize_secret_or_private_memory(tmp_path):
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None,
                          enable_interpretive_memory=True)
    _seed_demo_campaign(engine)
    engine.store.append_many([
        kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="private fact",
                     deltas={"subject": "protagonist", "predicate": "hidden",
                             "value": "PRIVATE_GUI_SECRET_SENTINEL", "secrecy": "secret"}),
        kernel_event("character_interpretation_recorded", day=1, scene="s1", turn=0,
                     summary="private memory", deltas={"actor": "protagonist",
                     "text": "PRIVATE_GUI_MEMORY_SENTINEL", "memory_id": "m-private"}),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    payload = json.dumps(build_player_state(engine), ensure_ascii=False)
    assert "PRIVATE_GUI_SECRET_SENTINEL" not in payload
    assert "PRIVATE_GUI_MEMORY_SENTINEL" not in payload
    assert "interpretive_memory" not in payload
    engine.store.close()


@pytest.mark.parametrize("kwargs", [
    {"player_input": "", "action_id": "x", "expected_revision": 1},
    {"player_input": "ok", "action_id": "", "expected_revision": 1},
    {"player_input": "ok", "action_id": "x", "expected_revision": -1},
])
def test_dashboard_rejects_invalid_request_before_turn(tmp_path, kwargs):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    before = dashboard.state()["revision"]
    with pytest.raises(GuiRequestError):
        dashboard.action(**kwargs)
    assert dashboard.state()["revision"] == before
    dashboard.engine.store.close()


def test_undo_is_revision_bound_and_second_identical_request_cannot_undo_twice(tmp_path):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    first = dashboard.state()
    one = dashboard.action(player_input="第一回合", action_id="one", expected_revision=first["revision"])
    two = dashboard.action(player_input="第二回合", action_id="two", expected_revision=one["revision"])
    original_revision = two["revision"]

    undone = dashboard.undo(expected_revision=original_revision)
    assert undone["state"]["turn"] == 1
    revision_after_undo = undone["state"]["revision"]
    with pytest.raises(GuiRequestError):
        dashboard.undo(expected_revision=original_revision)
    assert dashboard.state()["revision"] == revision_after_undo
    assert dashboard.state()["turn"] == 1
    dashboard.engine.store.close()


def _post_wsgi(app, path, payload, *, host="127.0.0.1:8765", origin="http://127.0.0.1:8765",
               content_type="application/json"):
    raw = json.dumps(payload).encode("utf-8") if content_type == "application/json" else b"expected_revision=1"
    captured = []
    environ = {
        "REQUEST_METHOD": "POST", "PATH_INFO": path, "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8765",
        "HTTP_HOST": host, "HTTP_ORIGIN": origin, "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw),
    }
    response = b"".join(app(environ, lambda status, headers: captured.append((status, headers))))
    return captured[0][0], json.loads(response.decode("utf-8"))


def _get_wsgi(app, path):
    captured = []
    response = b"".join(app({
        "REQUEST_METHOD": "GET", "PATH_INFO": path, "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8765",
        "HTTP_HOST": "127.0.0.1:8765", "wsgi.input": io.BytesIO(),
    }, lambda status, headers: captured.append((status, headers))))
    return captured[0][0], response


@pytest.mark.parametrize("host,origin,content_type", [
    ("127.0.0.1:8765", "http://attacker.invalid", "application/json"),
    ("attacker.invalid", "http://127.0.0.1:8765", "application/json"),
    ("127.0.0.1:8765", "http://127.0.0.1:8765", "application/x-www-form-urlencoded"),
])
def test_write_routes_reject_cross_site_or_form_requests_without_writes(tmp_path, host, origin, content_type):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    app = make_app(dashboard)
    before = dashboard.state()["revision"]
    status, payload = _post_wsgi(app, "/api/undo", {"expected_revision": before}, host=host,
                                 origin=origin, content_type=content_type)
    assert status.startswith("400")
    assert "跨站" in payload["message"]
    assert payload["confirmed_rejection"] is True
    assert dashboard.state()["revision"] == before
    dashboard.engine.store.close()


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, ValueError])
def test_wsgi_post_publish_display_failures_keep_committed_turn_and_hide_exception(tmp_path, monkeypatch, caplog, error_type):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    app = make_app(dashboard)
    before = dashboard.state()["revision"]

    def fail_display(_engine):
        raise error_type("PRIVATE_GUI_RESPONSE_EXCEPTION")

    monkeypatch.setattr(gui, "build_player_state", fail_display)
    status, payload = _post_wsgi(app, "/api/action", {
        "text": "保存后模拟显示失败", "action_id": "response-display-failure", "expected_revision": before,
    })
    assert status.startswith("500")
    assert payload["message"] == "请求结果未能显示；请刷新查看当前状态。"
    assert "PRIVATE_GUI_RESPONSE_EXCEPTION" not in json.dumps(payload, ensure_ascii=False)
    assert "confirmed_rejection" not in payload
    assert "PRIVATE_GUI_RESPONSE_EXCEPTION" not in caplog.text
    assert dashboard.engine.store.revision == before + 1
    assert dashboard.engine.store.get_action_receipt("response-display-failure") is not None
    dashboard.engine.store.close()


def _dashboard_with_ordered_exits(tmp_path, *, public_exit: bool) -> PlayerDashboard:
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(responses=["1"]), embedder=None)
    events = [
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="hero", deltas={
            "id": "protagonist", "sketch": "旅人", "goal": "探索", "tier": "tracked"}),
        kernel_event("place_created", day=1, scene="s1", turn=0, summary="start", deltas={
            "id": "start", "level": 2, "kind": "venue", "seed": "起点", "tier": "tracked"}),
        kernel_event("entity_created", day=1, scene="s1", turn=0, summary="hidden", deltas={
            "id": "PRIVATE_HIDDEN_EXIT_SENTINEL", "etype": "Place", "tier": "tracked",
            "attrs": {"visibility": "hidden"}}),
        # This relation is deliberately first in canonical insertion order.
        kernel_event("relation_added", day=1, scene="s1", turn=0, summary="hidden link", deltas={
            "src": "start", "rel": "adjacent_to", "dst": "PRIVATE_HIDDEN_EXIT_SENTINEL"}),
    ]
    if public_exit:
        events.extend([
            kernel_event("place_created", day=1, scene="s1", turn=0, summary="public", deltas={
                "id": "public_road", "level": 2, "kind": "settlement", "seed": "公开道路", "tier": "tracked"}),
            kernel_event("relation_added", day=1, scene="s1", turn=0, summary="public link", deltas={
                "src": "start", "rel": "adjacent_to", "dst": "public_road"}),
        ])
    events.append(kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="at start",
                               deltas={"who": "protagonist", "to": "start"}))
    engine.store.append_many(events)
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    return PlayerDashboard(engine)


def test_offline_demo_strategy_uses_pov_exit_not_first_canonical_hidden_exit(tmp_path):
    dashboard = _dashboard_with_ordered_exits(tmp_path, public_exit=True)
    before = dashboard.state()
    result = dashboard.action(player_input="沿路前进", action_id="pov-public", expected_revision=before["revision"])
    payload = json.dumps(result["state"], ensure_ascii=False)
    assert result["state"]["scene"]["location"]["id"] == "public_road"
    assert "PRIVATE_HIDDEN_EXIT_SENTINEL" not in result["narration"]
    assert "PRIVATE_HIDDEN_EXIT_SENTINEL" not in payload
    dashboard.engine.store.close()


def test_offline_demo_strategy_does_not_move_when_only_hidden_exit_exists(tmp_path):
    dashboard = _dashboard_with_ordered_exits(tmp_path, public_exit=False)
    before = dashboard.state()
    result = dashboard.action(player_input="寻找出口", action_id="pov-hidden-only", expected_revision=before["revision"])
    assert result["state"]["scene"]["location"]["id"] == "start"
    assert "没有已知出口" in result["narration"]
    assert "PRIVATE_HIDDEN_EXIT_SENTINEL" not in json.dumps(result, ensure_ascii=False)
    dashboard.engine.store.close()


def test_omitted_engine_embedder_keeps_ambient_default_while_gui_disables_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RPG_EMBEDDER", "fastembed")
    marker = object()
    calls = []

    def ambient_embedder():
        calls.append(True)
        return marker

    monkeypatch.setattr(engine_module, "get_embedder", ambient_embedder)
    ordinary = build_engine(tmp_path / "ordinary", provider=FakeLLMProvider())
    assert ordinary.embedder is marker
    assert calls == [True]
    ordinary.store.close()

    calls.clear()
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    assert dashboard.engine.embedder is None
    assert calls == []
    dashboard.engine.store.close()


def test_default_player_app_has_no_gm_endpoint_or_hidden_world_data(tmp_path):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    app = make_app(dashboard)
    status, raw = _get_wsgi(app, "/api/gm/state")
    assert status.startswith("404")
    assert b"north_bank_old_tower" not in raw
    status, raw = _get_wsgi(app, "/api/state")
    assert status.startswith("200")
    assert b"north_bank_old_tower" not in raw
    assert b"GUI_DEMO_PRIVATE_SENTINEL" not in raw
    status, raw = _get_wsgi(app, "/")
    assert status.startswith("200")
    assert b"north_bank_old_tower" not in raw
    dashboard.engine.store.close()


def test_gm_state_is_read_only_canonical_map_but_omits_private_interpretations(tmp_path):
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None,
                          enable_interpretive_memory=True)
    _seed_demo_campaign(engine)
    engine.store.append_many([kernel_event(
        "character_interpretation_recorded", day=1, scene="s1", turn=0, summary="private memory",
        deltas={"actor": "protagonist", "text": "GM_PRIVATE_MEMORY_SENTINEL", "memory_id": "gm-private"},
    )])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    player_payload = json.dumps(build_player_state(engine), ensure_ascii=False)
    gm = GmDashboard(engine)
    gm_state = gm.state()
    gm_payload = json.dumps(gm_state, ensure_ascii=False)

    assert "north_bank_old_tower" not in player_payload
    assert "GUI_DEMO_PRIVATE_SENTINEL" not in player_payload
    assert "north_bank_old_tower" in gm_payload
    assert "GUI_DEMO_PRIVATE_SENTINEL" in gm_payload
    assert "北岸旧塔的封印出现裂痕" in gm_payload
    assert "GM_PRIVATE_MEMORY_SENTINEL" not in gm_payload
    assert "interpretive_memory" not in gm_payload
    assert {node["id"] for node in gm_state["map"]["nodes"]} >= {"stonebridge_market", "north_bank_old_tower"}
    assert {edge["from"] + ":" + edge["to"] for edge in gm_state["map"]["edges"]} >= {
        "north_bank_old_tower:stonebridge_market",
    }

    gm_app = make_app(gm, mode="gm")
    status, raw = _get_wsgi(gm_app, "/api/gm/state")
    assert status.startswith("200") and b"north_bank_old_tower" in raw
    status, raw = _get_wsgi(gm_app, "/api/state")
    assert status.startswith("404")
    status, raw = _get_wsgi(gm_app, "/")
    assert status.startswith("200") and b"GM_PRIVATE_MEMORY_SENTINEL" not in raw
    status, raw = _post_wsgi(gm_app, "/api/action", {"text": "must not write", "action_id": "nope", "expected_revision": 1})
    assert status.startswith("404")
    engine.store.close()


def test_gm_timeline_marks_retracted_history_without_leaking_private_memory(tmp_path):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    before = dashboard.state()
    saved = dashboard.action(player_input="写入后撤回", action_id="gm-retracted", expected_revision=before["revision"])
    dashboard.undo(expected_revision=saved["revision"])
    gm_state = GmDashboard(dashboard.engine).state()
    retracted = [event for event in gm_state["timeline"] if event["retracted"]]
    assert retracted
    assert any(event["type"] == "narration_recorded" and event["category"] == "public_narrative" for event in retracted)
    assert "character_interpretation_recorded" not in {event["type"] for event in gm_state["timeline"]}
    dashboard.engine.store.close()


def test_gm_fails_closed_when_ordinary_history_references_active_or_retracted_private_memory(tmp_path, caplog):
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None,
                          enable_interpretive_memory=True)
    _seed_demo_campaign(engine)
    active_private = kernel_event(
        "character_interpretation_recorded", day=1, scene="s1", turn=0, id="private-active-ref",
        summary="private", deltas={"actor": "protagonist", "text": "PRIVATE_ACTIVE_BODY_SENTINEL", "memory_id": "active"},
    )
    retracted_private = kernel_event(
        "character_interpretation_recorded", day=1, scene="s1", turn=0, id="private-retracted-ref",
        summary="private", deltas={"actor": "protagonist", "text": "PRIVATE_RETRACTED_BODY_SENTINEL", "memory_id": "retracted"},
    )
    retracted_private["retracted"] = True
    engine.store.append_many([
        active_private,
        retracted_private,
        kernel_event("narration_recorded", day=1, scene="s1", turn=0, summary="ordinary active ref",
                     deltas={"scene": "s1", "text": "ORDINARY_ACTIVE_REFERENCE_BODY"},
                     thread_refs=["private-active-ref"]),
        kernel_event("scene_summarized", day=1, scene="s1", turn=0, summary="ordinary retracted ref",
                     deltas={"scene": "s1", "summary": "ORDINARY_RETRACTED_REFERENCE_BODY"},
                     chunk_ids=["private-retracted-ref"]),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    gm = GmDashboard(engine)
    with pytest.raises(ValueError, match="public event references private memory") as excinfo:
        gm.state()
    assert "PRIVATE_" not in str(excinfo.value)

    gm_app = make_app(gm, mode="gm")
    status, raw = _get_wsgi(gm_app, "/api/gm/state")
    payload = json.loads(raw.decode("utf-8"))
    assert status.startswith("500")
    assert payload == {"message": "请求结果未能显示；请刷新查看当前状态。"}
    rendered = json.dumps(payload, ensure_ascii=False) + caplog.text
    for sentinel in ("PRIVATE_ACTIVE_BODY_SENTINEL", "PRIVATE_RETRACTED_BODY_SENTINEL",
                     "ORDINARY_ACTIVE_REFERENCE_BODY", "ORDINARY_RETRACTED_REFERENCE_BODY"):
        assert sentinel not in rendered
    engine.store.close()


def _normal_gui_commit(narration="NORMAL_GUI_PROVIDER_STUB"):
    return {"narration": narration, "moves": [], "places": [], "cast": [], "facts": [],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试正常 GUI 接线"}]}


def test_normal_gui_uses_author_strategy_and_injected_provider_not_demo_script(tmp_path):
    campaign = tmp_path / "campaign"
    seed = build_dashboard(campaign, demo=True)
    seed.engine.store.close()
    provider = FakeLLMProvider(json_responses=[_normal_gui_commit("普通模式响应：任意行动被现有策略理解")])
    dashboard = build_dashboard(campaign, provider=provider)
    assert isinstance(dashboard.strategy, AuthorStrategy)
    before = dashboard.state()
    result = dashboard.action(player_input="不沿演示路线，先观察钟楼", action_id="normal-author", expected_revision=before["revision"])
    assert "普通模式响应" in result["narration"]
    assert provider.calls
    assert result["state"]["scene"]["location"]["id"] == "stonebridge_inn", "normal strategy output controls movement"
    dashboard.engine.store.close()

    reopened = build_dashboard(campaign, provider=FakeLLMProvider(json_responses=[]))
    duplicate = reopened.action(player_input="不沿演示路线，先观察钟楼", action_id="normal-author", expected_revision=before["revision"])
    assert duplicate["duplicate"] is True
    assert "普通模式响应" in duplicate["narration"]
    reopened.engine.store.close()


def test_normal_gui_requires_existing_campaign_and_explicit_provider(tmp_path):
    with pytest.raises(GuiRequestError, match="需要已配置的 provider"):
        build_dashboard(tmp_path / "no-provider")
    with pytest.raises(GuiRequestError, match="尚无世界"):
        build_dashboard(tmp_path / "empty", provider=FakeLLMProvider())


def test_public_codex_dto_is_searchable_read_only_and_html_safe(tmp_path):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    dashboard.engine.store.append(kernel_event("codex_entry_added", day=1, scene="s1", turn=0, summary="html codex",
        deltas={"id": "special", "kind": "秘闻", "title": "<img src=x onerror=alert(1)>", "body": "旧塔词条 <b>HTML</b>"}))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    state = dashboard.state()
    entry = next(item for item in state["codex"] if item["id"] == "special")
    assert entry["kind"] == "秘闻" and "<img" in entry["title"] and "<b>" in entry["body"]
    gm_state = GmDashboard(dashboard.engine).state()
    assert entry in gm_state["codex"]
    dashboard.engine.store.close()


def test_gui_cold_reopen_duplicate_uses_persisted_original_scene_after_move(tmp_path):
    campaign = tmp_path / "campaign"
    first = build_dashboard(campaign, demo=True)
    initial = first.state()
    saved = first.action(player_input="走向集市", action_id="cold-move", expected_revision=initial["revision"])
    assert saved["state"]["scene"]["location"]["id"] == "stonebridge_market"
    first.engine.store.close()
    reopened = build_dashboard(campaign, demo=True)
    duplicate = reopened.action(player_input="走向集市", action_id="cold-move", expected_revision=initial["revision"])
    assert duplicate["duplicate"] is True
    assert duplicate["state"]["scene"]["location"]["id"] == "stonebridge_market"
    reopened.engine.store.close()


def test_gui_old_duplicate_without_host_envelope_fails_closed(tmp_path):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    state = dashboard.state()
    dashboard.action(player_input="旧记录", action_id="legacy-gui", expected_revision=state["revision"])
    dashboard.engine.store._conn.execute("UPDATE action_result_metadata SET metadata='{}' WHERE action_id='legacy-gui'")
    dashboard.engine.store._conn.commit()
    dashboard.requests.clear()
    with pytest.raises(gui.GuiRecoveryError, match="无法安全恢复"):
        dashboard.action(player_input="旧记录", action_id="legacy-gui", expected_revision=state["revision"])
    dashboard.requests.clear()
    status, payload = _post_wsgi(make_app(dashboard), "/api/action", {
        "text": "旧记录", "action_id": "legacy-gui", "expected_revision": state["revision"],
    })
    assert status.startswith("409") and payload == {"message": "已保存行动无法安全恢复；请刷新查看当前世界状态。"}
    assert "confirmed_rejection" not in payload
    dashboard.engine.store.close()


def test_gui_passes_cli_catchup_provider(tmp_path, monkeypatch):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    marker = object(); dashboard.engine.cascade_provider = marker
    observed = {}; original = gui.run_turn
    def wrapped(*args, **kwargs):
        observed["cascade"] = kwargs.get("cascade_provider"); observed["catchup"] = kwargs.get("catchup_provider")
        return original(*args, **kwargs)
    monkeypatch.setattr(gui, "run_turn", wrapped)
    dashboard.action(player_input="接线检查", action_id="catchup", expected_revision=dashboard.state()["revision"])
    assert observed == {"cascade": marker, "catchup": marker}
    dashboard.engine.store.close()


def test_old_duplicate_does_not_rewind_previous_scene_before_third_action(tmp_path, monkeypatch):
    class CountingOffline(gui.OfflineDemoStrategy):
        def __init__(self): self.calls = 0
        def produce(self, *args, **kwargs): self.calls += 1; return super().produce(*args, **kwargs)

    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    strategy = CountingOffline(); dashboard.strategy = strategy
    first_state = dashboard.state()
    one = dashboard.action(player_input="动作一移动", action_id="cursor-one", expected_revision=first_state["revision"])
    two = dashboard.action(player_input="动作二继续移动", action_id="cursor-two", expected_revision=one["revision"])
    latest_previous = dashboard.previous_scene.copy()
    latest_world_revision = two["revision"]
    calls_before = strategy.calls
    duplicate = dashboard.action(player_input="动作一移动", action_id="cursor-one", expected_revision=first_state["revision"])
    assert duplicate["duplicate"] is True and strategy.calls == calls_before
    assert dashboard.engine.store.revision == latest_world_revision
    assert dashboard.previous_scene == latest_previous
    observed = {}; original = gui.run_turn
    def wrapped(*args, **kwargs): observed["prev_scene"] = kwargs.get("prev_scene"); return original(*args, **kwargs)
    monkeypatch.setattr(gui, "run_turn", wrapped)
    dashboard.action(player_input="动作三", action_id="cursor-three", expected_revision=latest_world_revision)
    assert observed["prev_scene"] == latest_previous
    dashboard.engine.store.close()
