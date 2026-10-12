"""Safety and persistence coverage for the loopback player dashboard."""
from __future__ import annotations

import json

import pytest

from app.engine import build_engine
from app.gui import GuiRequestError, _seed_demo_campaign, build_dashboard, build_player_state
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
    dashboard.undo()
    assert dashboard.state()["scene"]["location"]["id"] == "stonebridge_inn"
    dashboard.engine.store.close()

    reopened = build_dashboard(campaign)
    assert reopened.state()["scene"]["location"]["id"] == "stonebridge_inn"
    assert reopened.state()["inventory"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
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
