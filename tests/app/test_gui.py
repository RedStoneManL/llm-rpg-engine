"""Safety and persistence coverage for the loopback player dashboard."""
from __future__ import annotations

import io
import json

import pytest

import app.engine as engine_module
import app.gui as gui
from app.engine import build_engine
from app.gui import (GuiRequestError, PlayerDashboard, _seed_demo_campaign,
                     build_dashboard, build_player_state, make_app)
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider


def test_demo_dashboard_action_duplicate_undo_and_reopen(tmp_path):
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    initial = dashboard.state()
    assert initial["scene"]["location"]["id"] == "stonebridge_inn"
    assert initial["inventory"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
    assert initial["codex"][0]["title"] == "石桥镇"

    saved = dashboard.action(player_input="去集市看看", action_id="gui-1", expected_revision=initial["revision"])
    assert saved["duplicate"] is False
    assert saved["state"]["scene"]["location"]["id"] == "stonebridge_market"
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

    reopened = build_dashboard(campaign)
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
    assert dashboard.state()["revision"] == before
    dashboard.engine.store.close()


def test_wsgi_action_display_failure_keeps_committed_turn_and_hides_exception(tmp_path, monkeypatch):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    app = make_app(dashboard)
    before = dashboard.state()["revision"]

    def fail_display(_engine):
        raise RuntimeError("PRIVATE_GUI_RESPONSE_EXCEPTION")

    monkeypatch.setattr(gui, "build_player_state", fail_display)
    status, payload = _post_wsgi(app, "/api/action", {
        "text": "保存后模拟显示失败", "action_id": "response-display-failure", "expected_revision": before,
    })
    assert status.startswith("500")
    assert payload["message"] == "请求结果未能显示；请刷新查看当前状态。"
    assert "PRIVATE_GUI_RESPONSE_EXCEPTION" not in json.dumps(payload, ensure_ascii=False)
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


def test_gui_explicitly_disables_ambient_embedder(tmp_path, monkeypatch):
    monkeypatch.setenv("RPG_EMBEDDER", "fastembed")
    monkeypatch.setattr(engine_module, "get_embedder", lambda: (_ for _ in ()).throw(AssertionError("must not construct")))
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    assert dashboard.engine.embedder is None
    dashboard.engine.store.close()
