"""Offline CLI experience: saved turns, recap pending, and private-memory state."""
from __future__ import annotations

import gc
import json
import logging

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
    assert "[摘要] 更新尚未完成；本回合已保存。" in enabled
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
    assert "[摘要] 更新尚未完成；本回合已保存。" not in reopened


def test_cli_does_not_echo_untrusted_exception_text(tmp_path, monkeypatch, caplog):
    engine = build_engine(tmp_path / "error-campaign", provider=FakeLLMProvider())
    try:
        caplog.set_level(logging.ERROR)

        def broken_turn(*args, **kwargs):
            raise RuntimeError("PRIVATE_EXCEPTION_BODY")

        monkeypatch.setattr("app.play.run_turn", broken_turn)
        output = []
        transcript = tmp_path / "transcript.jsonl"
        play_loop(engine, ["尝试行动", "/quit"], out=output.append,
                  transcript_path=transcript)
        rendered = "\n".join(output)
        assert "[错误] 本回合处理遇到问题；请检查当前世界状态。]" in rendered
        assert "PRIVATE_EXCEPTION_BODY" not in rendered
        record = json.loads(transcript.read_text(encoding="utf-8"))
        assert record["error"] == "turn_failed"
        assert "PRIVATE_EXCEPTION_BODY" not in transcript.read_text(encoding="utf-8")
        assert caplog.records
        assert "PRIVATE_EXCEPTION_BODY" not in caplog.text
    finally:
        engine.store.close()


def test_cli_transcript_turns_continue_across_cold_reopen(tmp_path):
    """The optional player transcript must use canonical turns, not REPL-local counters."""
    campaign = tmp_path / "transcript-resume"
    transcript = tmp_path / "transcript.jsonl"

    def turn_provider(narration):
        return FakeLLMProvider(json_responses=[{
            "moves": [], "places": [], "cast": [], "facts": [],
            "narration": narration,
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "转录编号测试"}],
        }])

    first = build_engine(campaign, provider=FakeLLMProvider())
    try:
        new_game(first)
        first.provider = turn_provider("第一段已保存叙事")
        play_loop(first, ["记录第一回合", "/quit"], out=lambda _: None, transcript_path=transcript)
    finally:
        first.store.close()

    reopened = build_engine(campaign, provider=turn_provider("第二段已保存叙事"))
    try:
        play_loop(reopened, ["记录续玩回合", "/quit"], out=lambda _: None, transcript_path=transcript)
        records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        assert [record["turn"] for record in records] == [1, 2]
        assert [record["input"] for record in records] == ["记录第一回合", "记录续玩回合"]
    finally:
        reopened.store.close()


def test_cli_transcript_revision_distinguishes_replayed_turn_after_undo(tmp_path):
    """A turn number can be reused after rewind, so transcript rows need a revision."""
    campaign = tmp_path / "transcript-undo"
    transcript = tmp_path / "transcript.jsonl"
    provider = FakeLLMProvider(json_responses=[
        {"moves": [], "places": [], "cast": [], "facts": [], "narration": "第一版",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "首次"}]},
        {"moves": [], "places": [], "cast": [], "facts": [], "narration": "重写版",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "重写"}]},
    ])
    engine = build_engine(campaign, provider=FakeLLMProvider())
    try:
        new_game(engine)
        engine.provider = provider
        play_loop(engine, ["先行动", "/undo", "改写行动", "/quit"],
                  out=lambda _: None, transcript_path=transcript)
        records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        assert [record["turn"] for record in records] == [1, 1]
        assert all(type(record["revision"]) is int for record in records)
        assert records[0]["revision"] != records[1]["revision"]
        assert all(isinstance(record["action_id"], str) and record["action_id"] for record in records)
        assert records[0]["action_id"] != records[1]["action_id"]
        assert [record["input"] for record in records] == ["先行动", "改写行动"]
    finally:
        engine.store.close()


def test_cli_output_error_after_publish_never_claims_a_rollback(tmp_path):
    engine = build_engine(tmp_path / "post-publish-output", provider=FakeLLMProvider())
    try:
        new_game(engine)
        engine.provider = FakeLLMProvider(json_responses=[{
            "moves": [], "places": [], "cast": [], "facts": [],
            "narration": "PUBLIC_POST_PUBLISH_NARRATION",
            "clock": [{"advance": False, "days": 0, "bands": 0,
                       "reason": "片刻"}],
        }])
        before_revision = engine.store.revision
        output = []

        def fail_only_on_saved_status(line):
            if line.startswith("[已保存]"):
                raise RuntimeError("POST_PUBLISH_OUTPUT_EXCEPTION_BODY")
            output.append(line)

        play_loop(engine, ["尝试行动", "/quit"], out=fail_only_on_saved_status)

        rendered = "\n".join(output)
        assert engine.store.revision > before_revision
        assert any(event["type"] == "narration_recorded" for event in engine.store.iter_events())
        assert "[提示] 本回合已保存，但结果显示未完成；请检查当前世界状态。]" in rendered
        assert "世界保持原状" not in rendered
        assert "POST_PUBLISH_OUTPUT_EXCEPTION_BODY" not in rendered
    finally:
        engine.store.close()
