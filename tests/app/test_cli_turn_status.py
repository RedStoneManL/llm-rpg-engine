"""Offline CLI experience: saved turns, recap pending, and private-memory state."""
from __future__ import annotations

import gc
import json

from app.__main__ import main
from app.engine import build_engine, new_game
from app.play import play_loop
from llm.provider import FakeLLMProvider


class RecapUnavailableProvider(FakeLLMProvider):
    """Valid foreground commits, but a safe simulated outage for recap only."""

    def __init__(self):
        super().__init__(json_responses=[{
            "moves": [], "places": [], "cast": [], "facts": [],
            "narration": "PUBLIC_CLI_TURN",
            "clock": [{"advance": True, "days": 1, "bands": 0,
                       "reason": "过了一天"}],
        }])
        self.summary_calls = 0

    def complete_messages(self, messages, **kwargs):
        system = next((message.get("content", "") for message in messages
                       if message.get("role") == "system"), "")
        if "剧情摘要员" in system:
            self.summary_calls += 1
            raise RuntimeError("PRIVATE_EXCEPTION_BODY must not reach the player")
        return super().complete_messages(messages, **kwargs)


def _seed_campaign(path):
    engine = build_engine(path, provider=FakeLLMProvider())
    try:
        new_game(engine)
    finally:
        engine.store.close()


def _run_main(campaign, args, inputs, provider):
    output = []
    main(["--campaign", str(campaign), *args], inputs=inputs,
         out=output.append, provider=provider)
    # ``main`` owns the Engine, and this test deliberately reopens the same
    # SQLite campaign through the public CLI seam.
    gc.collect()
    return "\n".join(output)


def test_offline_cli_reports_saved_turn_pending_recap_and_memory_lifecycle(tmp_path):
    campaign = tmp_path / "cli-status"
    _seed_campaign(campaign)

    default = _run_main(campaign, [], ["/memory", "/quit"], FakeLLMProvider())
    assert "离线私有记忆实验未启用" in default
    assert "[离线私有记忆实验已启用]" not in default

    provider = RecapUnavailableProvider()
    enabled = _run_main(
        campaign, ["--offline-private-memory"],
        ["第一天前进", "第二天前进", "第三天前进", "/memory", "/undo", "/quit"],
        provider,
    )
    # Scene progression makes the first scene age out on the third action, so
    # this is a real digest summary attempt rather than a mocked CLI branch.
    assert provider.summary_calls >= 1
    assert enabled.count("[已保存]") == 3
    assert "[摘要] 暂未生成；本回合已保存。" in enabled
    assert "PRIVATE_EXCEPTION_BODY" not in enabled
    assert "[离线私有记忆实验已启用]" in enabled
    assert enabled.count("[memory] 第 ") == 3
    assert "[倒带] 已撤销第" in enabled

    reopened = _run_main(
        campaign, ["--offline-private-memory"], ["/memory", "/quit"],
        FakeLLMProvider(),
    )
    assert "[载入存档] 已读取" in reopened
    assert "[离线私有记忆实验已启用]" in reopened
    # The undo from the previous CLI process retracted the complete third turn,
    # including its private record; reopening proves this is durable, not a
    # presentation cache.
    assert reopened.count("[memory] 第 ") == 2
    assert "[摘要] 暂未生成；本回合已保存。" not in reopened


def test_cli_does_not_echo_untrusted_exception_text(tmp_path, monkeypatch):
    engine = build_engine(tmp_path / "error-campaign", provider=FakeLLMProvider())
    try:
        def broken_turn(*args, **kwargs):
            raise RuntimeError("PRIVATE_EXCEPTION_BODY")

        monkeypatch.setattr("app.play.run_turn", broken_turn)
        output = []
        transcript = tmp_path / "transcript.jsonl"
        play_loop(engine, ["尝试行动", "/quit"], out=output.append,
                  transcript_path=transcript)
        rendered = "\n".join(output)
        assert "[错误] 本回合未完成，世界保持原状。请稍后重试。" in rendered
        assert "PRIVATE_EXCEPTION_BODY" not in rendered
        record = json.loads(transcript.read_text(encoding="utf-8"))
        assert record["error"] == "turn_failed"
        assert "PRIVATE_EXCEPTION_BODY" not in transcript.read_text(encoding="utf-8")
    finally:
        engine.store.close()
