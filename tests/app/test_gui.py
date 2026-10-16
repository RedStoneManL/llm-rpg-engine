"""Safety and persistence coverage for the loopback player dashboard."""
from __future__ import annotations

import io
import json
import copy

import pytest

import app.engine as engine_module
import app.gui as gui
from app.engine import build_engine, rewind
from app.gui import (GuiRequestError, PlayerDashboard, _seed_demo_campaign,
                     GmDashboard, build_dashboard, build_gm_dashboard, build_gm_state,
                     build_player_state, make_app)
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy
from loop.turn import TurnRejected


def test_demo_dashboard_action_duplicate_undo_and_reopen(tmp_path):
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    initial = dashboard.state()
    assert initial["scene"]["location"]["id"] == "stonebridge_inn"
    assert initial["inventory"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
    assert initial["objectives"] == {
        "long_term_goal": "在黎明前找到旧塔的线索",
        "active_quests": [],
        "completed_quests": [],
    }
    assert initial["codex"][0]["title"] == "石桥镇"
    assert {node["id"] for node in initial["map"]["nodes"]} == {
        "stonebridge_inn", "stonebridge_market",
    }
    assert initial["map"]["edges"] == [{
        "from": "stonebridge_inn", "to": "stonebridge_market", "cost": "1",
    }]
    assert initial["scene"]["exits"] == [{
        "id": "stonebridge_market", "label": "石桥集市", "kind": "settlement",
    }]
    assert initial["scene"]["exit_costs"] == {"stonebridge_market": "1"}
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
    assert reopened.previous_scene is None  # retracted receipts are never recovery anchors
    assert reopened.state()["inventory"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
    assert saved["narration"] not in reopened.state()["narration"]
    reopened.engine.store.close()


def test_player_shell_labels_demo_and_normal_saved_campaign_without_exposing_state(tmp_path):
    """A normal GUI must not claim it is the fixed offline demo."""
    campaign = tmp_path / "campaign"
    demo = build_dashboard(campaign, demo=True)
    status, demo_html = _get_wsgi(make_app(demo), "/")
    assert status.startswith("200")
    assert b"OFFLINE DEMO" in demo_html
    assert b"SAVED CAMPAIGN" not in demo_html
    demo.engine.store.close()

    normal = build_dashboard(campaign, provider=FakeLLMProvider(responses=["unused"]))
    try:
        status, normal_html = _get_wsgi(make_app(normal), "/")
        assert status.startswith("200")
        assert b"SAVED CAMPAIGN" in normal_html
        assert b"OFFLINE DEMO" not in normal_html
        assert b"north_bank_old_tower" not in normal_html
    finally:
        normal.engine.store.close()


def test_offline_demo_never_opens_an_unmarked_saved_campaign(tmp_path, capsys):
    """``--demo`` must not run its deterministic strategy against a normal save."""
    campaign = tmp_path / "normal-campaign"
    engine = build_engine(campaign, provider=FakeLLMProvider(responses=["unused"]), embedder=None)
    engine.store.append_many([kernel_event(
        "narration_recorded", day=1, scene="s1", turn=0, summary="已有普通存档",
        deltas={"scene": "s1", "text": "NORMAL_CAMPAIGN_SENTINEL"},
    )])
    engine.store.close()
    db_before = (campaign / "events.db").read_bytes()
    jsonl_before = (campaign / "events.jsonl").read_bytes()

    with pytest.raises(GuiRequestError, match="只能初始化空 campaign"):
        build_dashboard(campaign, demo=True)
    with pytest.raises(GuiRequestError, match="只能初始化空 campaign"):
        build_gm_dashboard(campaign, demo=True)
    with pytest.raises(SystemExit) as exit_status:
        gui.main(["--campaign", str(campaign), "--demo", "--port", "0"])
    assert exit_status.value.code == 2
    assert "只能初始化空 campaign" in capsys.readouterr().err

    assert (campaign / "events.db").read_bytes() == db_before
    assert (campaign / "events.jsonl").read_bytes() == jsonl_before
    assert not (campaign / gui._OFFLINE_DEMO_MARKER).exists()

    demo_campaign = tmp_path / "offline-demo"
    first = build_dashboard(demo_campaign, demo=True)
    first.engine.store.close()
    assert json.loads((demo_campaign / gui._OFFLINE_DEMO_MARKER).read_text(encoding="utf-8")) == {
        "format": 1, "kind": "offline-demo",
    }
    reopened = build_dashboard(demo_campaign, demo=True)
    try:
        assert reopened.state()["scene"]["location"]["id"] == "stonebridge_inn"
    finally:
        reopened.engine.store.close()


def test_normal_and_gm_gui_do_not_create_a_missing_campaign(tmp_path, capsys):
    """Read-only startup guidance must not leave an empty store or directory behind."""
    missing = tmp_path / "missing-campaign"
    with pytest.raises(GuiRequestError, match="尚无已保存世界"):
        build_dashboard(missing, provider=FakeLLMProvider(responses=["unused"]))
    with pytest.raises(GuiRequestError, match="尚无已保存世界"):
        build_gm_dashboard(missing)
    with pytest.raises(SystemExit) as exit_status:
        gui.main(["--campaign", str(missing), "--gm", "--port", "0"])
    assert exit_status.value.code == 2
    assert "尚无已保存世界" in capsys.readouterr().err
    assert not missing.exists()

    empty = tmp_path / "empty-store"
    seed = build_engine(empty, provider=FakeLLMProvider(responses=["unused"]), embedder=None)
    seed.store.close()
    db_before = (empty / "events.db").read_bytes()
    with pytest.raises(GuiRequestError, match="尚无已保存世界"):
        build_dashboard(empty, provider=FakeLLMProvider(responses=["unused"]))
    with pytest.raises(GuiRequestError, match="尚无已保存世界"):
        build_gm_dashboard(empty)
    assert (empty / "events.db").read_bytes() == db_before
    assert not (empty / "events.jsonl").exists()


def test_explicit_new_game_creates_a_normal_campaign_without_overwriting(tmp_path):
    """GUI setup has one explicit, atomic opt-in instead of a silent demo fallback."""
    campaign = tmp_path / "new-normal-campaign"
    dashboard = gui.build_new_game_dashboard(
        campaign, provider=FakeLLMProvider(), pitch="一座有钟楼的海港小镇",
    )
    try:
        state = dashboard.state()
        assert dashboard.demo is False
        assert state["revision"] > 0
        assert state["scene"]["location"] is not None
        assert (campaign / "events.db").exists()
        assert (campaign / "events.jsonl").exists()
    finally:
        dashboard.engine.store.close()

    db_before = (campaign / "events.db").read_bytes()
    jsonl_before = (campaign / "events.jsonl").read_bytes()
    with pytest.raises(GuiRequestError, match="不会覆盖"):
        gui.build_new_game_dashboard(campaign, provider=FakeLLMProvider(), pitch="另一座世界")
    assert (campaign / "events.db").read_bytes() == db_before
    assert (campaign / "events.jsonl").read_bytes() == jsonl_before


def test_explicit_new_game_failure_never_publishes_partial_campaign(tmp_path, monkeypatch):
    campaign = tmp_path / "new-normal-campaign"

    def fail_genesis(*args, **kwargs):
        raise RuntimeError("test-only genesis failure")

    monkeypatch.setattr(gui, "new_game", fail_genesis)
    with pytest.raises(RuntimeError, match="test-only genesis failure"):
        gui.build_new_game_dashboard(campaign, provider=FakeLLMProvider(), pitch="会失败的世界")
    assert not campaign.exists()
    assert list(tmp_path.glob(".new-normal-campaign.new-*")) == []


def test_explicit_new_game_publish_race_never_overwrites_new_target(tmp_path, monkeypatch):
    campaign = tmp_path / "raced-campaign"

    def competing_publish(staging, target):
        target.mkdir()
        (target / "owner.txt").write_text("peer", encoding="utf-8")
        raise GuiRequestError("新 campaign 发布时目标路径已出现；不会覆盖已有目录。")

    monkeypatch.setattr(gui, "_publish_new_campaign_no_replace", competing_publish)
    with pytest.raises(GuiRequestError, match="不会覆盖"):
        gui.build_new_game_dashboard(campaign, provider=FakeLLMProvider(), pitch="竞争世界")
    assert (campaign / "owner.txt").read_text(encoding="utf-8") == "peer"
    assert not (campaign / "events.db").exists()
    assert list(tmp_path.glob(".raced-campaign.new-*")) == []


def test_new_game_cli_mode_rejects_incompatible_modes_before_creating_campaign(tmp_path, capsys):
    campaign = tmp_path / "should-not-exist"
    with pytest.raises(SystemExit) as exit_status:
        gui.main(["--campaign", str(campaign), "--new-game", "--demo", "--port", "0"])
    assert exit_status.value.code == 2
    assert "不能与 --demo 或 --gm" in capsys.readouterr().err
    assert not campaign.exists()


def test_new_game_cli_rejects_default_fake_provider_before_creating_campaign(tmp_path, capsys):
    campaign = tmp_path / "should-not-exist"
    with pytest.raises(SystemExit) as exit_status:
        gui.main(["--campaign", str(campaign), "--new-game", "--pitch", "普通世界", "--port", "0"])
    assert exit_status.value.code == 2
    assert "需要已配置的正常 provider" in capsys.readouterr().err
    assert not campaign.exists()


def test_gui_request_context_is_ephemeral_and_duplicate_uses_durable_envelope(tmp_path):
    """Completed and pre-publish-failed actions must not grow controller memory."""
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    initial = dashboard.state()

    class FailingBeforePublish:
        def produce(self, *args, **kwargs):
            raise RuntimeError("test-only pre-publish failure")

    dashboard.strategy = FailingBeforePublish()
    with pytest.raises(RuntimeError, match="pre-publish"):
        dashboard.action(player_input="先试着观察", action_id="ephemeral-action",
                         expected_revision=initial["revision"])
    assert dashboard.requests == {}
    assert dashboard.engine.store.get_action_receipt("ephemeral-action") is None
    assert dashboard.state()["revision"] == initial["revision"]

    class CountingOffline(gui.OfflineDemoStrategy):
        def __init__(self):
            self.calls = 0

        def produce(self, *args, **kwargs):
            self.calls += 1
            return super().produce(*args, **kwargs)

    published = CountingOffline()
    dashboard.strategy = published
    saved = dashboard.action(player_input="先试着观察", action_id="ephemeral-action",
                             expected_revision=initial["revision"])
    assert saved["duplicate"] is False and published.calls == 1
    assert dashboard.requests == {}

    class MustNotRun:
        def produce(self, *args, **kwargs):  # pragma: no cover - assertion is the call itself
            raise AssertionError("durable action recovery must run before strategy")

    dashboard.strategy = MustNotRun()
    recovered = dashboard.action(player_input="先试着观察", action_id="ephemeral-action",
                                 expected_revision=initial["revision"])
    assert recovered["duplicate"] is True
    assert recovered["revision"] == saved["revision"]
    assert dashboard.requests == {}
    dashboard.engine.store.close()


def test_inventory_action_suggestion_uses_only_existing_player_inventory_dto(tmp_path):
    """The browser affordance must not add an item lookup or a write endpoint."""
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    try:
        before = dashboard.state()
        assert before["inventory"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
        assert "function suggestInventoryItem" in gui._JS
        assert "inventory-suggestion" in gui._JS
        assert "使用${label}" in gui._JS
        assert "/api/inventory" not in gui._JS
        assert dashboard.state()["revision"] == before["revision"]
    finally:
        dashboard.engine.store.close()


def test_player_state_shows_only_bounded_public_local_observations(tmp_path):
    """The dashboard may show durable local observations, never event provenance."""
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    engine = dashboard.engine
    engine.store.append_many([
        kernel_event("world_change", day=1, scene="s1", turn=0, summary="门前石阶潮湿。", deltas={
            "place": "stonebridge_inn", "level": 1, "summary": "门前石阶潮湿。",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "single_observation", "observation_range": "客栈门前两步内",
        }),
        kernel_event("world_change", day=1, scene="s1", turn=0, summary="集市入口有一盏未点亮的灯。", deltas={
            "place": "stonebridge_market", "level": 1, "summary": "集市入口有一盏未点亮的灯。",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "stable_layout", "observation_range": "集市入口近处",
        }),
        kernel_event("world_change", day=1, scene="s1", turn=0, summary="PRIVATE_OBSERVATION_SENTINEL", deltas={
            "place": "north_bank_old_tower", "level": 1, "summary": "PRIVATE_OBSERVATION_SENTINEL",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "stable_layout", "observation_range": "塔门近处",
        }),
        kernel_event("world_change", day=1, scene="s1", turn=0, summary="UNMARKED_OBSERVATION_SENTINEL", deltas={
            "place": "stonebridge_inn", "level": 1, "summary": "UNMARKED_OBSERVATION_SENTINEL",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    before = engine.store.revision
    try:
        expected = [{
            "place": "这里", "is_current": True, "kind": "现场观察",
            "range": "客栈门前两步内", "summary": "门前石阶潮湿。",
        }, {
            "place": "石桥集市", "is_current": False, "kind": "稳定布局",
            "range": "集市入口近处", "summary": "集市入口有一盏未点亮的灯。",
        }]
        state = dashboard.state()
        assert state["observations"] == expected
        assert "source_event_id" not in json.dumps(state, ensure_ascii=False)
        assert "PRIVATE_OBSERVATION_SENTINEL" not in json.dumps(state, ensure_ascii=False)
        assert "UNMARKED_OBSERVATION_SENTINEL" not in json.dumps(state, ensure_ascii=False)

        status, body = _get_wsgi(make_app(dashboard), "/api/state")
        assert status.startswith("200")
        assert json.loads(body.decode("utf-8"))["observations"] == expected
        assert engine.store.revision == before
    finally:
        engine.store.close()


def test_player_observations_follow_peer_publish_rewind_and_cold_reopen(tmp_path):
    """Observation DTOs are active-history projections, not a local browser cache."""
    campaign = tmp_path / "campaign"
    reader = build_dashboard(campaign, demo=True)
    peer = build_dashboard(campaign, demo=True)
    try:
        assert reader.state()["observations"] == []
        peer.engine.store.append_many([
            kernel_event("world_change", day=1, scene="s1", turn=1, summary="门前石阶潮湿。", deltas={
                "place": "stonebridge_inn", "level": 1, "summary": "门前石阶潮湿。",
                "source": "author_world", "visibility": "public", "public_observation": True,
                "observation_kind": "single_observation", "observation_range": "客栈门前两步内",
            }),
        ], preflight=lambda history: project(peer.engine.registry, history))
        published_revision = peer.engine.store.revision

        # The reader begins with an older projection.  GET state must adopt
        # the peer's active observation without creating a player write.
        assert reader.state()["revision"] == published_revision
        assert reader.state()["observations"] == [{
            "place": "这里", "is_current": True, "kind": "现场观察",
            "range": "客栈门前两步内", "summary": "门前石阶潮湿。",
        }]

        peer.engine.store.retract_from_turn(1, expected_revision=published_revision)
        retracted_revision = peer.engine.store.revision
        assert reader.state()["revision"] == retracted_revision
        assert reader.state()["observations"] == []
    finally:
        peer.engine.store.close()
        reader.engine.store.close()

    reopened = build_dashboard(campaign, demo=True)
    try:
        assert reopened.state()["observations"] == []
    finally:
        reopened.engine.store.close()


def test_player_clock_is_public_and_replayable_across_dashboard_undo_and_reopen(tmp_path):
    """The player sees the already-public engine clock, never a new time authority."""
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)

    class AdvanceTime:
        def produce(self, *args, **kwargs):
            return TurnCommit(narration="你在公开道路上走过半日。", sections={
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": True, "days": 1, "bands": 1, "reason": "测试公开时间显示"}],
            })

        def repair_sections(self, *args, **kwargs):  # pragma: no cover - already valid
            return {}

        def commit_to_thread(self, narration):
            return None

    dashboard.strategy = AdvanceTime()
    initial = dashboard.state()
    assert initial["scene"]["time"] == {"day": 1, "band": "晨"}

    saved = dashboard.action(player_input="沿公开道路前进", action_id="clock-dashboard",
                             expected_revision=initial["revision"])
    assert saved["state"]["scene"]["time"] == {"day": 2, "band": "中午"}
    status, body = _get_wsgi(make_app(dashboard), "/api/state")
    assert status.startswith("200")
    assert json.loads(body.decode("utf-8"))["scene"]["time"] == {"day": 2, "band": "中午"}

    dashboard.undo(expected_revision=saved["revision"])
    assert dashboard.state()["scene"]["time"] == {"day": 1, "band": "晨"}
    dashboard.engine.store.close()

    reopened = build_dashboard(campaign, demo=True)
    assert reopened.state()["scene"]["time"] == {"day": 1, "band": "晨"}
    reopened.engine.store.close()


def test_dashboard_undo_resets_author_conversation_before_next_action(tmp_path):
    """A GUI rewind must not leave retracted prose in a normal author thread."""
    from engine import settings

    class RecordingProvider(FakeLLMProvider):
        def __init__(self):
            super().__init__(responses=[json.dumps({
                "narration": "公开的短暂观察。", "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "仍在眼前"}],
            }, ensure_ascii=False)])
            self.message_sets = []

        def complete_messages(self, messages, **kwargs):
            self.message_sets.append(copy.deepcopy(messages))
            return super().complete_messages(messages, **kwargs)

    class TrackingAuthor(AuthorStrategy):
        def __init__(self):
            super().__init__()
            self.resets = 0

        def reset(self):
            self.resets += 1
            super().reset()

    settings.set_conversation_mode("multiturn")
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    provider = RecordingProvider()
    strategy = TrackingAuthor()
    dashboard.engine.provider = provider
    dashboard.strategy = strategy
    try:
        first = dashboard.action(
            player_input="GUI_RETRACTED_ACTION_SENTINEL：先说一句会被撤销的话",
            action_id="gui-reset-first", expected_revision=dashboard.state()["revision"],
        )
        assert first["duplicate"] is False
        assert "GUI_RETRACTED_ACTION_SENTINEL" in json.dumps(strategy._thread, ensure_ascii=False)

        dashboard.undo(expected_revision=first["revision"])
        assert strategy.resets == 1
        assert strategy._thread is None and strategy._messages is None

        calls_before_second = len(provider.message_sets)
        second = dashboard.action(
            player_input="GUI_AFTER_UNDO_PUBLIC_ACTION：只观察当前公开地点",
            action_id="gui-reset-second", expected_revision=dashboard.state()["revision"],
        )
        assert second["duplicate"] is False
        # Background density hooks may use the same fake provider after the
        # foreground author call.  Select the request that actually contains
        # this second normal action rather than assuming it was the last call.
        requests = [json.dumps(messages, ensure_ascii=False)
                    for messages in provider.message_sets[calls_before_second:]
                    if "GUI_AFTER_UNDO_PUBLIC_ACTION" in json.dumps(messages, ensure_ascii=False)]
        assert requests
        rendered_request = requests[-1]
        assert "GUI_AFTER_UNDO_PUBLIC_ACTION" in rendered_request
        assert "GUI_RETRACTED_ACTION_SENTINEL" not in rendered_request
    finally:
        settings.set_conversation_mode("stateless")
        dashboard.engine.store.close()


def test_dashboard_reopen_keeps_catchup_scope_from_last_saved_action(tmp_path):
    """A cold GUI reopen must not turn a continuously present NPC into a new arrival.

    Catch-up deliberately happens on the *next* action after an entity enters
    player scope.  Here the innkeeper was already co-located before a saved
    no-move time advance.  A live dashboard keeps that prior scene in memory;
    reopening the same campaign used to replace it with ``None`` and wrongly
    run an extra catch-up evolution on the next ordinary action.
    """
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    dashboard.engine.store.append(kernel_event(
        "character_created", day=1, scene="s1", turn=0, summary="掌柜在客栈",
        deltas={"id": "innkeeper", "tier": "tracked", "sketch": "掌柜", "goal": "照看客栈"},
    ))
    dashboard.engine.store.append(kernel_event(
        "entity_moved", day=1, scene="s1", turn=0, summary="掌柜留在客栈",
        deltas={"who": "innkeeper", "to": "stonebridge_inn"},
    ))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision

    class AdvanceInPlace:
        def produce(self, *args, **kwargs):
            return TurnCommit(narration="客栈里安静地过去了几天。", sections={
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": True, "days": 4, "bands": 0, "reason": "客栈内等待"}],
            })

        def repair_sections(self, *args, **kwargs):  # pragma: no cover - valid fixture
            return {}

        def commit_to_thread(self, narration):
            return None

    # Model an already-live session: before the saved wait, this NPC was in
    # scope, so its later staleness must not be interpreted as a fresh entry.
    dashboard.previous_scene = gui._build_scene(dashboard.engine)
    dashboard.strategy = AdvanceInPlace()
    saved = dashboard.action(player_input="留在客栈等待", action_id="catchup-wait",
                             expected_revision=dashboard.state()["revision"])
    assert saved["duplicate"] is False
    assert dashboard.engine.world["meta"]["day"] == 5
    dashboard.engine.store.close()

    class ObserveInPlace(AdvanceInPlace):
        def produce(self, *args, **kwargs):
            return TurnCommit(narration="你仍在客栈内观察。", sections={
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处观察"}],
            })

    catchup = FakeLLMProvider(json_responses=[{
        "changed": True, "predicate": "mood", "value": "不应因重开而变化",
    }])
    reopened = build_dashboard(campaign, demo=True)
    reopened.strategy = ObserveInPlace()
    reopened.engine.cascade_provider = catchup
    try:
        assert reopened.previous_scene is not None
        follow_up = reopened.action(player_input="看看客栈", action_id="catchup-after-reopen",
                                    expected_revision=reopened.state()["revision"])
        assert follow_up["duplicate"] is False
        assert catchup.calls == []
        assert all(event["type"] != "character_evolved"
                   for event in reopened.engine.store.iter_events())
    finally:
        reopened.engine.store.close()


def test_dashboard_reopen_preserves_pending_catchup_after_actual_arrival(tmp_path):
    """Recovery must retain a real entry, not silence catch-up after a restart."""
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    dashboard.engine.store.append(kernel_event(
        "character_created", day=1, scene="s1", turn=0, summary="集市旅人在远处",
        deltas={"id": "market_traveler", "tier": "tracked", "sketch": "旅人", "goal": "赶路"},
    ))
    dashboard.engine.store.append(kernel_event(
        "entity_moved", day=1, scene="s1", turn=0, summary="旅人留在集市",
        deltas={"who": "market_traveler", "to": "stonebridge_market"},
    ))
    dashboard.engine.store.append(kernel_event(
        "time_advanced", day=5, scene="s1", turn=0, summary="数日过去",
        deltas={"to_day": 5, "reason": "fixture time"},
    ))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision
    # The demo's first visible exit moves the protagonist from inn to market.
    dashboard.previous_scene = gui._build_scene(dashboard.engine)
    arrived = dashboard.action(player_input="去集市", action_id="catchup-arrival",
                               expected_revision=dashboard.state()["revision"])
    assert arrived["state"]["scene"]["location"]["id"] == "stonebridge_market"
    assert all(event["type"] != "character_evolved"
               for event in dashboard.engine.store.iter_events())
    dashboard.engine.store.close()

    class ObserveInPlace:
        def produce(self, *args, **kwargs):
            return TurnCommit(narration="你先观察集市。", sections={
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处观察"}],
            })

        def repair_sections(self, *args, **kwargs):  # pragma: no cover - valid fixture
            return {}

        def commit_to_thread(self, narration):
            return None

    catchup = FakeLLMProvider(json_responses=[{
        "changed": True, "predicate": "mood", "value": "久候后的轻松",
    }])
    reopened = build_dashboard(campaign, demo=True)
    reopened.strategy = ObserveInPlace()
    reopened.engine.cascade_provider = catchup
    try:
        assert reopened.previous_scene is not None
        assert reopened.previous_scene["location"] == "stonebridge_inn"
        reopened.action(player_input="看看旅人", action_id="catchup-after-arrival",
                        expected_revision=reopened.state()["revision"])
        assert catchup.calls
        assert any(event["type"] == "character_evolved"
                   and event["deltas"]["id"] == "market_traveler"
                   for event in reopened.engine.store.iter_events())
    finally:
        reopened.engine.store.close()


def test_dashboard_undo_rebuilds_catchup_scope_from_surviving_action(tmp_path):
    """Undo must recover the same scope anchor as a cold reopen, not clear it."""
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    dashboard.engine.store.append(kernel_event(
        "character_created", day=1, scene="s1", turn=0, summary="掌柜在客栈",
        deltas={"id": "innkeeper", "tier": "tracked", "sketch": "掌柜", "goal": "照看客栈"},
    ))
    dashboard.engine.store.append(kernel_event(
        "entity_moved", day=1, scene="s1", turn=0, summary="掌柜留在客栈",
        deltas={"who": "innkeeper", "to": "stonebridge_inn"},
    ))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision

    class ObserveInPlace:
        def produce(self, *args, **kwargs):
            return TurnCommit(narration="你留在客栈观察。", sections={
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处观察"}],
            })

        def repair_sections(self, *args, **kwargs):  # pragma: no cover - valid fixture
            return {}

        def commit_to_thread(self, narration):
            return None

    dashboard.strategy = ObserveInPlace()
    dashboard.previous_scene = gui._build_scene(dashboard.engine)
    first = dashboard.action(player_input="第一段观察", action_id="undo-catchup-first",
                             expected_revision=dashboard.state()["revision"])
    # Model a host-side time advance between ordinary player actions.  It is
    # not part of the next undo target and leaves the same NPC stale in scope.
    dashboard.engine.store.append(kernel_event(
        "time_advanced", day=5, scene="s1", turn=0, summary="数日过去",
        deltas={"to_day": 5, "reason": "fixture host time"},
    ))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision
    catchup = FakeLLMProvider(json_responses=[{
        "changed": True, "predicate": "mood", "value": "不应因撤销而变化",
    }])
    dashboard.engine.cascade_provider = catchup
    second = dashboard.action(player_input="第二段观察", action_id="undo-catchup-second",
                              expected_revision=dashboard.state()["revision"])
    assert catchup.calls == []

    dashboard.undo(expected_revision=second["revision"])
    assert dashboard.previous_scene is not None
    dashboard.action(player_input="撤销后的观察", action_id="undo-catchup-third",
                     expected_revision=dashboard.state()["revision"])
    assert catchup.calls == []
    assert all(event["type"] != "character_evolved"
               for event in dashboard.engine.store.iter_events())
    dashboard.engine.store.close()


def test_player_gui_recall_uses_shared_pov_boundary_without_writing(tmp_path):
    """The dashboard gets the same read-only recall behavior as CLI play."""
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    app = make_app(dashboard)
    before = dashboard.state()["revision"]
    db_before = (campaign / "events.db").read_bytes()
    jsonl_before = (campaign / "events.jsonl").read_bytes()

    status, raw = _get_wsgi(app, "/api/recall", query="q=north_bank_old_tower")
    hidden = json.loads(raw.decode("utf-8"))
    assert status.startswith("200")
    assert hidden == {"hits": [], "revision": before}
    assert "north_bank_old_tower" not in raw.decode("utf-8")

    status, raw = _get_wsgi(app, "/api/recall", query="q=%E7%9F%B3%E6%A1%A5")
    public = json.loads(raw.decode("utf-8"))
    assert status.startswith("200")
    assert public["revision"] == before
    assert any("石桥" in hit for hit in public["hits"])
    assert dashboard.engine.store.revision == before
    assert (campaign / "events.db").read_bytes() == db_before
    assert (campaign / "events.jsonl").read_bytes() == jsonl_before

    status, raw = _get_wsgi(app, "/api/recall")
    malformed = json.loads(raw.decode("utf-8"))
    assert status.startswith("400")
    assert malformed["confirmed_rejection"] is True
    dashboard.engine.store.close()


def test_gui_soft_direction_is_session_only_and_action_recovery_is_steer_independent(tmp_path):
    """The GUI mirrors CLI steer without persisting a preference as world input."""
    campaign = tmp_path / "campaign"

    class CapturingDemo(gui.OfflineDemoStrategy):
        def __init__(self):
            self.scenes = []
            self.resets = 0

        def produce(self, registry, world, scene, player_input, **kwargs):
            self.scenes.append(copy.deepcopy(scene))
            return super().produce(registry, world, scene, player_input, **kwargs)

        def reset(self):
            self.resets += 1

    dashboard = build_dashboard(campaign, demo=True)
    strategy = CapturingDemo()
    dashboard.strategy = strategy
    app = make_app(dashboard)
    before = dashboard.state()["revision"]
    db_before = (campaign / "events.db").read_bytes()
    jsonl_before = (campaign / "events.jsonl").read_bytes()

    status, changed = _post_wsgi(app, "/api/steer", {"text": "优先核实眼前公开线索"})
    assert status.startswith("200")
    assert changed == {"ok": True, "soft_direction": "优先核实眼前公开线索"}
    session = dashboard.state()["session"]
    assert session["soft_direction"] == "优先核实眼前公开线索"
    assert len(session["campaign_key"]) == 24
    assert set(session["campaign_key"]) <= set("0123456789abcdef")
    assert dashboard.engine.store.revision == before
    assert (campaign / "events.db").read_bytes() == db_before
    assert (campaign / "events.jsonl").read_bytes() == jsonl_before

    saved = dashboard.action(player_input="先在眼前观察", action_id="gui-session-steer",
                             expected_revision=before)
    assert saved["duplicate"] is False
    assert strategy.scenes[0]["_player_steer"] == "优先核实眼前公开线索"
    metadata = dashboard.engine.store.get_action_result_metadata("gui-session-steer")
    assert "_player_steer" not in metadata["host_envelope"]["scene"]
    assert "优先核实眼前公开线索" not in json.dumps(metadata, ensure_ascii=False)

    status, cleared = _post_wsgi(app, "/api/steer", {"text": None})
    assert status.startswith("200")
    assert cleared == {"ok": True, "soft_direction": None}
    assert strategy.resets == 2  # set and clear both invalidate conversation cache
    assert dashboard.engine.store.revision == saved["revision"]
    dashboard.engine.store.close()

    # A cold response-loss retry needs no old steering state: it restores the
    # committed result and does not run a strategy again.
    reopened = build_dashboard(campaign, demo=True)
    assert reopened.state()["session"] == {
        "soft_direction": None,
        "campaign_key": session["campaign_key"],
    }
    duplicate = reopened.action(player_input="先在眼前观察", action_id="gui-session-steer",
                                expected_revision=before)
    assert duplicate["duplicate"] is True
    assert duplicate["revision"] == saved["revision"]
    reopened.engine.store.close()


def test_gui_campaign_key_scopes_pending_recovery_to_one_saved_world(tmp_path):
    """A same-origin browser must not offer campaign A's pending action in B."""
    campaign_a = tmp_path / "campaign-a"
    first = build_dashboard(campaign_a, demo=True)
    first_state = first.state()
    first_key = first_state["session"]["campaign_key"]
    first_event_id = first.engine.store.snapshot()[1][0]["id"]
    first.engine.store.close()

    reopened = build_dashboard(campaign_a, demo=True)
    assert reopened.state()["session"]["campaign_key"] == first_key
    reopened.engine.store.close()

    campaign_b = tmp_path / "campaign-b"
    second = build_dashboard(campaign_b, demo=True)
    second_state = second.state()
    assert second_state["session"]["campaign_key"] != first_key
    rendered = json.dumps(second_state, ensure_ascii=False)
    assert str(campaign_a.resolve()) not in rendered
    assert str(campaign_b.resolve()) not in rendered
    assert first_event_id not in rendered
    assert "pending-action.v2" not in rendered
    assert "pending-action.v2" in gui._JS
    assert "campaign_key" in gui._JS
    assert "pending-action.v1" not in gui._JS
    assert "期间不可提交或撤销" in gui._JS
    assert "const blocked=!!p" in gui._JS
    assert "已结束恢复；已刷新当前保存的世界状态。" in gui._JS
    assert "render(await api('/api/state'))" in gui._JS
    assert "当前状态暂时无法读取；请稍后刷新页面。" in gui._JS
    second.engine.store.close()


def test_gui_soft_direction_wsgi_validation_has_no_campaign_write(tmp_path):
    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    app = make_app(dashboard)
    before = dashboard.state()["revision"]
    status, payload = _post_wsgi(app, "/api/steer", {"text": "x" * 501})
    assert status.startswith("400")
    assert "500" in payload["message"]
    assert payload["confirmed_rejection"] is True
    status, payload = _post_wsgi(app, "/api/steer", {"text": "不能跨站"}, origin="http://attacker.invalid")
    assert status.startswith("400")
    assert "跨站" in payload["message"]
    assert dashboard.engine.store.revision == before
    dashboard.engine.store.close()


def test_dashboard_does_not_serialize_secret_or_private_memory(tmp_path):
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None,
                          enable_interpretive_memory=True)
    _seed_demo_campaign(engine)
    engine.store.append_many([
        kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="private fact",
                     deltas={"subject": "protagonist", "predicate": "hidden",
                             "value": "PRIVATE_GUI_SECRET_SENTINEL", "secrecy": "secret"}),
        kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="restricted fact",
                     deltas={"subject": "protagonist", "predicate": "director_note",
                             "value": "PRIVATE_GUI_RESTRICTED_SENTINEL", "secrecy": "restricted"}),
        kernel_event("character_interpretation_recorded", day=1, scene="s1", turn=0,
                     summary="private memory", deltas={"actor": "protagonist",
                     "text": "PRIVATE_GUI_MEMORY_SENTINEL", "memory_id": "m-private"}),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    payload = json.dumps(build_player_state(engine), ensure_ascii=False)
    assert "PRIVATE_GUI_SECRET_SENTINEL" not in payload
    assert "PRIVATE_GUI_RESTRICTED_SENTINEL" not in payload
    assert "PRIVATE_GUI_MEMORY_SENTINEL" not in payload
    assert "interpretive_memory" not in payload
    engine.store.close()


def test_player_talkable_people_reuse_public_current_scene_recipients_and_refresh_after_move(tmp_path):
    """The display is the existing receipt-safe recipient projection, not a new view."""
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    engine = dashboard.engine
    assert dashboard.state()["speech_recipients"] == []

    engine.store.append_many([
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="public a", deltas={
            "id": "a_public_listener", "sketch": "公开同场的艾", "goal": "交谈", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="public a at inn", deltas={
            "who": "a_public_listener", "to": "stonebridge_inn",
        }),
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="public b", deltas={
            "id": "b_public_listener", "sketch": "公开同场的贝", "goal": "交谈", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="public b at inn", deltas={
            "who": "b_public_listener", "to": "stonebridge_inn",
        }),
        kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="public a mood", deltas={
            "subject": "a_public_listener", "predicate": "mood", "value": "平静", "secrecy": "public",
        }),
        kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="public b state", deltas={
            "subject": "b_public_listener", "predicate": "state", "value": "警觉", "secrecy": "public",
        }),
        kernel_event("entity_created", day=1, scene="s1", turn=0, summary="hidden listener", deltas={
            "id": "HIDDEN_GUI_TALKABLE_SENTINEL", "etype": "Person", "tier": "tracked",
            "attrs": {"visibility": "hidden", "sketch": "PRIVATE_GUI_TALKABLE_SENTINEL"},
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="hidden listener at inn", deltas={
            "who": "HIDDEN_GUI_TALKABLE_SENTINEL", "to": "stonebridge_inn",
        }),
        kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="hidden mood", deltas={
            "subject": "HIDDEN_GUI_TALKABLE_SENTINEL", "predicate": "mood",
            "value": "PRIVATE_GUI_TALKABLE_MOOD_SENTINEL", "secrecy": "public",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision

    populated = dashboard.state()
    assert populated["speech_recipients"] == [
        {"id": "a_public_listener", "label": "公开同场的艾"},
        {"id": "b_public_listener", "label": "公开同场的贝"},
    ]
    assert populated["present_people"] == [
        {"id": "a_public_listener", "label": "公开同场的艾",
         "observables": [{"name": "心情", "value": "平静"}]},
        {"id": "b_public_listener", "label": "公开同场的贝",
         "observables": [{"name": "状态", "value": "警觉"}]},
    ]
    status, body = _get_wsgi(make_app(dashboard), "/api/state")
    assert status.startswith("200")
    assert json.loads(body.decode("utf-8"))["present_people"] == populated["present_people"]
    # The panel is a local affordance for this same recipient list, not a
    # second character lookup or a request-writing route.
    assert "function chooseSpeechRecipient" in gui._JS
    assert "recipient-pick" in gui._JS
    assert "speech-statement" in gui._JS
    rendered = json.dumps(populated, ensure_ascii=False)
    assert "HIDDEN_GUI_TALKABLE_SENTINEL" not in rendered
    assert "PRIVATE_GUI_TALKABLE_SENTINEL" not in rendered
    assert "PRIVATE_GUI_TALKABLE_MOOD_SENTINEL" not in rendered

    # The offline demo takes the existing public exit.  Its resulting state is
    # also the refresh source for both the recipient select and the new panel.
    moved = dashboard.action(player_input="去集市看看", action_id="talkable-people-move",
                             expected_revision=populated["revision"])
    assert moved["state"]["scene"]["location"]["id"] == "stonebridge_market"
    assert moved["state"]["speech_recipients"] == []
    assert moved["state"]["present_people"] == []
    dashboard.engine.store.close()

    reopened = build_dashboard(campaign, demo=True)
    reopened_state = reopened.state()
    assert reopened_state["scene"]["location"]["id"] == "stonebridge_market"
    assert reopened_state["speech_recipients"] == []
    assert reopened_state["present_people"] == []
    assert "HIDDEN_GUI_TALKABLE_SENTINEL" not in json.dumps(reopened_state, ensure_ascii=False)
    reopened.engine.store.close()


def test_player_objectives_are_public_replayable_and_do_not_expose_dark_lore(tmp_path):
    """The player DTO is a read model, not a second task-writing path."""
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    engine = dashboard.engine
    engine.store.append_many([
        kernel_event("fact_asserted", day=1, scene="s1", turn=1, summary="新的长期目标",
                     deltas={"subject": "protagonist", "predicate": "目标",
                             "value": "在集市确认渡口的消息来源", "secrecy": "public"}),
        kernel_event("quest_opened", day=1, scene="s1", turn=1, summary="公开任务",
                     deltas={"id": "verify_ferry_report", "summary": "向可信目击者核实渡口消息",
                             "secret": "GUI_OBJECTIVE_PRIVATE_SENTINEL"}),
        kernel_event("lore_created", day=1, scene="s1", turn=1, summary="暗线",
                     deltas={"id": "hidden_tower_thread", "about": "GUI_OBJECTIVE_DARK_SENTINEL",
                             "secret": "GUI_OBJECTIVE_PRIVATE_SENTINEL", "state": "暗"}),
        # A backstage line can also conclude.  It must not be reclassified as
        # a player-visible completion merely because it now has state 了结.
        kernel_event("quest_expired", day=2, scene="s1", turn=1, summary="暗线过期",
                     deltas={"id": "hidden_tower_thread"}),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision

    opened = dashboard.state()
    assert opened["objectives"] == {
        "long_term_goal": "在集市确认渡口的消息来源",
        "active_quests": [{"id": "verify_ferry_report", "summary": "向可信目击者核实渡口消息"}],
        "completed_quests": [],
    }
    assert "GUI_OBJECTIVE_PRIVATE_SENTINEL" not in json.dumps(opened, ensure_ascii=False)
    assert "GUI_OBJECTIVE_DARK_SENTINEL" not in json.dumps(opened, ensure_ascii=False)
    status, refreshed = _get_wsgi(make_app(dashboard), "/api/state")
    assert status.startswith("200")
    assert json.loads(refreshed.decode("utf-8"))["objectives"] == opened["objectives"]
    # Active public task summaries are a local action-writing aid only.  They
    # do not create a task API or turn a click into an advancement/write.
    assert "function suggestQuest" in gui._JS
    assert "quest-suggestion" in gui._JS
    assert "尝试推进任务：${summary}" in gui._JS
    assert "/api/quest" not in gui._JS

    engine.store.append_many([
        kernel_event("quest_advanced", day=1, scene="s1", turn=2, summary="推进任务",
                     deltas={"id": "verify_ferry_report", "summary": "已找到可核实的目击者"}),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    assert dashboard.state()["objectives"]["active_quests"] == [
        {"id": "verify_ferry_report", "summary": "已找到可核实的目击者"},
    ]

    engine.store.append_many([
        kernel_event("quest_resolved", day=1, scene="s1", turn=3, summary="任务完成",
                     deltas={"id": "verify_ferry_report", "by": "player"}),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    assert dashboard.state()["objectives"]["active_quests"] == []
    assert dashboard.state()["objectives"]["completed_quests"] == [
        {"id": "verify_ferry_report", "summary": "已找到可核实的目击者"},
    ]

    rewind(engine, 3)
    assert dashboard.state()["objectives"]["active_quests"] == [
        {"id": "verify_ferry_report", "summary": "已找到可核实的目击者"},
    ]
    assert dashboard.state()["objectives"]["completed_quests"] == []
    rewind(engine, 2)
    assert dashboard.state()["objectives"]["active_quests"] == [
        {"id": "verify_ferry_report", "summary": "向可信目击者核实渡口消息"},
    ]
    engine.store.close()

    reopened = build_dashboard(campaign, demo=True)
    assert reopened.state()["objectives"] == opened["objectives"]
    assert "目标与任务" in gui._INDEX
    reopened.engine.store.close()


def test_player_gui_utf8_byte_preflight_matches_server_action_limit_without_writing(tmp_path):
    """Multibyte browser input needs the same preflight limit as the controller."""
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    before = dashboard.state()["revision"]
    db_before = (campaign / "events.db").read_bytes()
    jsonl_before = (campaign / "events.jsonl").read_bytes()
    too_many_bytes = "你" * 667  # 2,001 UTF-8 bytes, below maxlength=2,000 chars.

    with pytest.raises(GuiRequestError, match="行动过长"):
        dashboard.action(player_input=too_many_bytes, action_id="multibyte-over-limit",
                         expected_revision=before)
    assert dashboard.engine.store.revision == before
    assert (campaign / "events.db").read_bytes() == db_before
    assert (campaign / "events.jsonl").read_bytes() == jsonl_before
    assert "function utf8Bytes" in gui._JS
    assert "action-byte-count" in gui._INDEX
    assert "speech-byte-count" in gui._INDEX
    assert "行动超过 2000 字节" in gui._JS
    assert "逐字原话超过 2000 字节" in gui._JS
    dashboard.engine.store.close()


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


def test_gui_stale_undo_refreshes_authoritative_world_before_confirmed_rejection(tmp_path):
    """A revision-bound undo rejection must not leave `/api/state` stale.

    A second dashboard publishes a real durable action after the first page was
    rendered.  The first dashboard's undo is correctly rejected, but its next
    state read must already reflect the peer action and recover the peer
    action-start scope rather than only telling the browser to refresh an
    unchanged in-memory projection.
    """
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    peer = build_dashboard(campaign, demo=True)
    resets = [0]

    class ResetOnly:
        def reset(self):
            resets[0] += 1

    dashboard.strategy = ResetOnly()
    initial = dashboard.state()
    try:
        peer_saved = peer.action(
            player_input="另一页面先去集市", action_id="peer-before-undo",
            expected_revision=peer.state()["revision"],
        )
        status, payload = _post_wsgi(make_app(dashboard), "/api/undo", {
            "expected_revision": initial["revision"],
        })
        assert status.startswith("400")
        assert payload["confirmed_rejection"] is True
        assert "世界已变化" in payload["message"]

        state_status, state_body = _get_wsgi(make_app(dashboard), "/api/state")
        refreshed = json.loads(state_body.decode("utf-8"))
        assert state_status.startswith("200")
        assert refreshed["revision"] == peer_saved["revision"]
        assert refreshed["scene"]["location"]["id"] == "stonebridge_market"
        assert dashboard.previous_scene is not None
        assert dashboard.previous_scene["location"] == "stonebridge_inn"
        assert resets == [1]
    finally:
        peer.engine.store.close()
        dashboard.engine.store.close()


def test_gui_undo_rejects_peer_publish_inside_retraction_window(tmp_path, monkeypatch):
    """The GUI keeps its version guard through the store retraction transaction."""
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    saved = dashboard.action(
        player_input="先去集市", action_id="gui-race-turn",
        expected_revision=dashboard.state()["revision"],
    )
    peer = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    original_rewind = gui.rewind
    snapshots = {}

    def peer_wins_rewind(engine, turn, *, expected_revision=None):
        peer.store.append_many([kernel_event(
            "narration_recorded", day=1, scene="s1", turn=2,
            summary="peer wins gui undo race", deltas={"text": "GUI_PEER_UNDO_RACE"},
        )])
        snapshots["before"] = (
            engine.store.db_path.read_bytes(),
            engine.store.jsonl_path.read_bytes(),
            engine.store.revision,
        )
        return original_rewind(engine, turn, expected_revision=expected_revision)

    monkeypatch.setattr(gui, "rewind", peer_wins_rewind)
    try:
        with pytest.raises(GuiRequestError, match="世界已变化"):
            dashboard.undo(expected_revision=saved["revision"])
        snapshots["after"] = (
            dashboard.engine.store.db_path.read_bytes(),
            dashboard.engine.store.jsonl_path.read_bytes(),
            dashboard.engine.store.revision,
        )
        assert snapshots["after"] == snapshots["before"]
        assert any(event.get("turn") == 1 for event in dashboard.engine.store.iter_events())
        assert dashboard.state()["revision"] == peer.store.revision
    finally:
        peer.store.close()
        dashboard.engine.store.close()


def test_gui_read_endpoints_reproject_after_peer_publish_without_a_write_request(tmp_path):
    """Passive state/recall reads must not need a rejected mutation to catch up.

    A separate dashboard saves the ordinary action.  The first dashboard then
    performs only GET state reads: no action, undo, or receipt retry is used to
    force the synchronization.  The second read is intentionally checked too,
    so an already-current world does not repeatedly reset the author cache.
    """
    campaign = tmp_path / "campaign"
    dashboard = build_dashboard(campaign, demo=True)
    peer = build_dashboard(campaign, demo=True)
    resets = [0]

    class ResetOnly:
        def reset(self):
            resets[0] += 1

    dashboard.strategy = ResetOnly()
    initial = dashboard.state()
    try:
        peer_saved = peer.action(
            player_input="另一页面先去集市", action_id="peer-before-read",
            expected_revision=peer.state()["revision"],
        )
        recall = dashboard.recall("石桥")
        assert recall["revision"] == peer_saved["revision"]
        assert resets == [1]

        status, body = _get_wsgi(make_app(dashboard), "/api/state")
        refreshed = json.loads(body.decode("utf-8"))
        assert status.startswith("200")
        assert refreshed["revision"] == peer_saved["revision"]
        assert refreshed["revision"] != initial["revision"]
        assert refreshed["scene"]["location"]["id"] == "stonebridge_market"
        assert dashboard.previous_scene is not None
        assert dashboard.previous_scene["location"] == "stonebridge_inn"
        assert resets == [1]

        dashboard.state()
        assert resets == [1]
    finally:
        peer.engine.store.close()
        dashboard.engine.store.close()


def test_gm_state_reprojects_after_peer_player_publish_without_writing(tmp_path):
    """The separately opened read-only GM page must not stay at its boot revision.

    A GM dashboard owns no action or rejection path that could incidentally
    refresh it.  Its ordinary GET therefore has to observe a player dashboard
    publish and rebuild from the durable event history without adding another
    event, receipt, or transcript row.
    """
    campaign = tmp_path / "campaign"
    player = build_dashboard(campaign, demo=True)
    gm = build_gm_dashboard(campaign)
    try:
        initial = gm.state()
        before_events = list(gm.engine.store.iter_events())
        saved = player.action(
            player_input="另一页面先去集市", action_id="gm-peer-publish",
            expected_revision=player.state()["revision"],
        )
        assert initial["revision"] != saved["revision"]
        after_peer_publish = list(gm.engine.store.iter_events())
        assert len(after_peer_publish) > len(before_events)

        status, body = _get_wsgi(make_app(gm, mode="gm"), "/api/gm/state")
        refreshed = json.loads(body.decode("utf-8"))
        assert status.startswith("200")
        assert refreshed["revision"] == saved["revision"]
        assert gm.engine.world["_revision"] == saved["revision"]
        protagonist = next(person for person in refreshed["people"] if person["id"] == "protagonist")
        assert protagonist["location"] == "stonebridge_market"
        assert list(gm.engine.store.iter_events()) == after_peer_publish
    finally:
        gm.engine.store.close()
        player.engine.store.close()


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


def _get_wsgi(app, path, *, query=""):
    captured = []
    response = b"".join(app({
        "REQUEST_METHOD": "GET", "PATH_INFO": path, "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8765",
        "HTTP_HOST": "127.0.0.1:8765", "QUERY_STRING": query, "wsgi.input": io.BytesIO(),
    }, lambda status, headers: captured.append((status, headers))))
    return captured[0][0], response


def test_gui_revision_conflict_refreshes_world_and_rebuilds_catchup_scope(tmp_path, monkeypatch):
    """A known GUI optimistic conflict is a safe 400, not an ambiguous 500.

    The first dashboard action loses a real publish race to an independent
    durable player action.  The controller must then project the winner's
    world and recover that winner's action-start scope before a fresh browser
    action is allowed to continue.  A prior receipt using the *same* id is
    deliberately not covered here: that remains the conservative 409 recovery
    path rather than a discardable rejection.
    """
    from loop.turn import run_turn as real_run_turn

    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    observed_previous_scenes = []
    peer_published = [False]
    peer_revision = [None]
    resets = [0]

    def recording_run_turn(*args, **kwargs):
        observed_previous_scenes.append(copy.deepcopy(kwargs.get("prev_scene")))
        return real_run_turn(*args, **kwargs)

    monkeypatch.setattr(gui, "run_turn", recording_run_turn)

    class ConflictThenObserve:
        def produce(self, registry, world, scene, player_input, *, provider, embedder=None, repair=None):
            if not peer_published[0]:
                peer_published[0] = True
                peer = real_run_turn(
                    registry, dashboard.engine.store, dashboard.engine.world,
                    gui._build_scene(dashboard.engine), "另一位玩家先去集市",
                    strategy=gui.OfflineDemoStrategy(), provider=dashboard.engine.provider,
                    embedder=dashboard.engine.embedder, max_repairs=0,
                    action_id="gui-concurrent-peer-action",
                )
                dashboard.engine.world = peer.world
                peer_revision[0] = peer.receipt["revision"]
            return TurnCommit(narration="你在公开地点观察。", sections={
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处观察"}],
            })

        def repair_sections(self, *args, **kwargs):  # pragma: no cover - valid fixture
            return {}

        def commit_to_thread(self, narration):
            return None

        def reset(self):
            resets[0] += 1

    dashboard.strategy = ConflictThenObserve()
    initial = dashboard.state()
    try:
        status, payload = _post_wsgi(make_app(dashboard), "/api/action", {
            "text": "先尝试观察", "action_id": "gui-stale-action",
            "expected_revision": initial["revision"],
        })
        assert status.startswith("400")
        assert payload == {
            "message": "世界状态已更新；此行动未提交。请刷新后重试。",
            "confirmed_rejection": True,
        }
        assert dashboard.engine.store.get_action_receipt("gui-stale-action") is None
        assert dashboard.state()["scene"]["location"]["id"] == "stonebridge_market"
        assert dashboard.previous_scene is not None
        assert dashboard.previous_scene["location"] == "stonebridge_inn"
        assert "gui-stale-action" not in dashboard.requests
        assert resets == [1]

        continued = dashboard.action(
            player_input="依据刷新后的世界继续观察", action_id="gui-after-refresh",
            expected_revision=dashboard.state()["revision"],
        )
        assert continued["duplicate"] is False
        assert dashboard.engine.store.revision > peer_revision[0]
        assert len(observed_previous_scenes) == 2
        assert observed_previous_scenes[0] is None
        assert observed_previous_scenes[1]["location"] == "stonebridge_inn"
    finally:
        dashboard.engine.store.close()


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


def test_gm_keeps_canonical_panels_but_withholds_history_when_ordinary_rows_reference_private_memory(tmp_path, caplog):
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
    before = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
              engine.store.jsonl_path.read_bytes())
    state = gm.state()
    assert state["history_unavailable"] is True
    assert state["timeline"] == [] and state["recaps"] == []
    assert state["places"] and state["map"]["nodes"]
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes()) == before

    gm_app = make_app(gm, mode="gm")
    status, raw = _get_wsgi(gm_app, "/api/gm/state")
    payload = json.loads(raw.decode("utf-8"))
    assert status.startswith("200")
    assert payload["history_unavailable"] is True
    assert payload["timeline"] == [] and payload["recaps"] == []
    assert payload["places"] and payload["map"]["nodes"]
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes()) == before
    rendered = json.dumps(payload, ensure_ascii=False) + caplog.text
    for sentinel in ("PRIVATE_ACTIVE_BODY_SENTINEL", "PRIVATE_RETRACTED_BODY_SENTINEL",
                     "ORDINARY_ACTIVE_REFERENCE_BODY", "ORDINARY_RETRACTED_REFERENCE_BODY"):
        assert sentinel not in rendered
    engine.store.close()


def test_player_state_keeps_safe_panels_when_public_history_references_private_memory(tmp_path):
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None,
                          enable_interpretive_memory=True)
    _seed_demo_campaign(engine)
    engine.store.append_many([
        kernel_event("character_interpretation_recorded", day=1, scene="s1", turn=0,
                     id="private-player-state-ref", summary="private", deltas={
            "actor": "protagonist", "text": "PRIVATE_PLAYER_STATE_SENTINEL", "memory_id": "state-ref",
        }),
        kernel_event("narration_recorded", day=1, scene="s1", turn=0, summary="ordinary bad ref", deltas={
            "text": "ORDINARY_PLAYER_STATE_SENTINEL",
        }, thread_refs=["private-player-state-ref"]),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    dashboard = PlayerDashboard(engine, demo=True)
    before = (engine.store.revision, list(engine.store.iter_events(include_retracted=True)))
    state = dashboard.state()
    assert state["narration"] == []
    assert state["narration_unavailable"] is True
    assert state["scene"]["location"]["id"] == "stonebridge_inn"
    status, raw = _get_wsgi(make_app(dashboard), "/api/state")
    assert status.startswith("200")
    payload = json.loads(raw.decode("utf-8"))
    assert payload["narration"] == [] and payload["narration_unavailable"] is True
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "PRIVATE_PLAYER_STATE_SENTINEL" not in rendered
    assert "ORDINARY_PLAYER_STATE_SENTINEL" not in rendered
    assert (engine.store.revision, list(engine.store.iter_events(include_retracted=True))) == before
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


def test_normal_gui_cannot_persist_model_inferred_player_statement_without_host_receipt(tmp_path):
    """GUI uses the same run_turn boundary as CLI and cannot mint NPC memory."""
    campaign = tmp_path / "campaign"
    seed = build_dashboard(campaign, demo=True)
    seed.engine.store.close()
    proposal = _normal_gui_commit("模型声称旁人听到了主角的私下话。")
    proposal["knowledge"] = [{
        "op": "heard_player", "receipt_id": "forged", "knower": "protagonist",
        "speaker": "protagonist", "statement": "GUI_PRIVATE_WORDS_SENTINEL",
    }]
    dashboard = build_dashboard(campaign, provider=FakeLLMProvider(json_responses=[proposal]), max_repairs=0)
    before = dashboard.state()["revision"]
    with pytest.raises(TurnRejected, match="host communication receipt"):
        dashboard.action(player_input="我心里想一想", action_id="forged-heard", expected_revision=before)
    assert dashboard.state()["revision"] == before
    assert "GUI_PRIVATE_WORDS_SENTINEL" not in json.dumps(dashboard.state(), ensure_ascii=False)
    dashboard.engine.store.close()


def test_normal_gui_requires_existing_campaign_and_explicit_provider(tmp_path):
    with pytest.raises(GuiRequestError, match="需要已配置的 provider"):
        build_dashboard(tmp_path / "no-provider")
    with pytest.raises(GuiRequestError, match="尚无.*世界"):
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
    assert status.startswith("409")
    assert payload["message"] == "已保存行动无法安全恢复；请刷新查看当前世界状态。"
    # The original result remains unavailable, but the response supplies only
    # the ordinary player DTO so the browser does not keep displaying the
    # pre-commit world while recovery is pending.
    current = payload["state"]
    assert current["revision"] == dashboard.engine.store.revision
    assert current["scene"]["location"]["id"] == "stonebridge_market"
    assert "GUI_DEMO_PRIVATE_SENTINEL" not in json.dumps(current, ensure_ascii=False)
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
