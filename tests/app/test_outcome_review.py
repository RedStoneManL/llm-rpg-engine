"""An accepted schema alone must not publish contradictory ordinary prose."""
import json
from app.gui import build_dashboard
from llm.provider import FakeLLMProvider
from loop.outcome_narration import _REVIEW


def test_normal_game_freezes_empty_effects_and_rejects_false_final_draft(tmp_path):
    seed = build_dashboard(tmp_path, demo=True)
    seed.engine.store.close()
    false = "你抵达石桥集市，把旧铜灯卖给摊主。"
    proposal = {"narration":false, "moves":[], "places":[], "facts":[], "cast":[],
                "clock":[{"advance":False, "reason":"观察"}]}

    class Reviewer(FakeLLMProvider):
        def complete_messages(self, messages, **kwargs):
            if messages[0]["content"] == _REVIEW:
                payload = json.loads(messages[-1]["content"])
                assert payload["outcome"]["sections"]["moves"] == []
                return '{"supported":false,"reason":"移动及售灯均未发生"}'
            return super().complete_messages(messages, **kwargs)

    provider = Reviewer(json_responses=[proposal], responses=[false])
    dashboard = build_dashboard(tmp_path, provider=provider)
    try:
        result = dashboard.action(player_input="只观察。", expected_revision=dashboard.engine.store.revision,
                                  action_id="bad-prose")
        state = result["state"]
        assert false not in state["narration"]
        assert result["narration"] == state["narration"][-1]
        assert "客栈" in result["narration"] and "卖" not in result["narration"]
        assert state["inventory"][0]["id"] == "old_brass_lantern"
        assert state["scene"]["location"]["id"] == "stonebridge_inn"
        assert not dashboard.recall("卖给摊主").get("sources")
    finally:
        dashboard.engine.store.close()
