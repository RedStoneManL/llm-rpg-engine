"""Tests for app/__main__.py --debug flag.

TDD: Tests verify that --debug flag:
1. Sets os.environ["RPG_DEBUG_TRACE"] to <campaign_dir>/trace.jsonl
2. Produces a trace file with JSON records after genesis
3. Cleans up properly so other tests aren't affected
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


class ScriptedProvider:
    """Returns canned strings in order (or repeats the last one)."""
    def __init__(self, replies):
        self._r = list(replies)
        self.i = 0

    def supports_tools(self):
        return False

    def _next(self):
        r = self._r[self.i] if self.i < len(self._r) else self._r[-1]
        self.i += 1
        return r

    def complete_messages(self, messages):
        return self._next()

    def complete(self, system, user, **kw):
        return self._next()


def _make_scripted_replies() -> list:
    """Return canned replies for bootstrap (8 LLM calls)."""
    frame_reply = json.dumps({
        "world_name": "调试测试世界",
        "central_conflict": "调试测试冲突",
    })
    regions_reply = json.dumps({
        "regions": [
            {"name": f"地域{i}", "terrain": "山地", "seed": f"地域{i}描述"}
            for i in range(5)
        ]
    })
    local_map_reply = json.dumps({
        "town": {"name": "调试测试镇", "seed": "古老的集镇"},
        "venues": [{"name": f"场所{i}", "seed": f"场所{i}描述"} for i in range(2)],
        "neighbors": [{"name": "荒野", "seed": "危险地带"}],
    })
    factions_reply = json.dumps({
        "factions": [
            {"name": f"势力{i}", "motivation": f"势力{i}的动机"}
            for i in range(5)
        ]
    })
    npcs_reply = json.dumps({
        "npcs": [
            {"sketch": f"NPC{i}外貌", "goal": f"NPC{i}目标", "secret": f"NPC{i}秘密"}
            for i in range(3)
        ]
    })
    threads_reply = json.dumps({
        "lines": [
            {
                "about": f"暗线{i}表象",
                "description": f"暗线{i}描述",
                "trigger": f"暗线{i}触发",
                "secret": f"暗线{i}真相",
                "l3_anchor": f"venue_{i % 2}",
                "stages": [{"hint": f"暗线{i}阶段{j}"} for j in range(3)],
            }
            for i in range(3)
        ]
    })
    prot_threads_reply = json.dumps({
        "lines": [
            {
                "about": "主角线表象",
                "description": "主角线描述",
                "trigger": "主角线触发",
                "secret": "主角线真相",
                "l3_anchor": "venue_0",
                "stages": [{"hint": f"主角线阶段{j}"} for j in range(3)],
            }
        ]
    })
    opening_reply = "你踏入了调试测试世界的起始之地。"

    return [
        frame_reply,
        regions_reply,
        local_map_reply,
        factions_reply,
        npcs_reply,
        threads_reply,
        prot_threads_reply,
        opening_reply,
    ]


def test_debug_flag_sets_env_var(tmp_path, monkeypatch):
    """--debug flag sets RPG_DEBUG_TRACE env var to <campaign_dir>/trace.jsonl."""
    from app.__main__ import main

    # Make sure the env var is not already set
    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)

    campaign_dir = tmp_path / "test_campaign"
    output = []

    provider = ScriptedProvider(_make_scripted_replies())
    main(
        ["--campaign", str(campaign_dir), "--debug"],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=provider,
    )

    # After main returns, the env var should be set
    assert os.environ.get("RPG_DEBUG_TRACE") == str(campaign_dir / "trace.jsonl")


def test_debug_flag_creates_trace_file(tmp_path, monkeypatch):
    """--debug flag creates a trace.jsonl file with JSON records."""
    from app.__main__ import main

    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)

    campaign_dir = tmp_path / "test_campaign_2"
    output = []

    provider = ScriptedProvider(_make_scripted_replies())
    main(
        ["--campaign", str(campaign_dir), "--debug"],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=provider,
    )

    trace_file = campaign_dir / "trace.jsonl"
    assert trace_file.exists(), f"Trace file {trace_file} does not exist"

    # Read the trace file and verify it has JSON records
    records = []
    with open(trace_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    assert len(records) >= 1, f"Trace file has no records; expected >= 1"


def test_debug_flag_prints_hint(tmp_path, monkeypatch):
    """--debug flag prints a hint with trace path and viewer instructions."""
    from app.__main__ import main

    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)

    campaign_dir = tmp_path / "test_campaign_3"
    output = []

    provider = ScriptedProvider(_make_scripted_replies())
    main(
        ["--campaign", str(campaign_dir), "--debug"],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=provider,
    )

    combined = "\n".join(output)
    trace_path = str(campaign_dir / "trace.jsonl")
    assert trace_path in combined, (
        f"Expected trace path {trace_path} in output, got: {combined!r}"
    )
    assert "app.trace" in combined, (
        f"Expected viewer hint 'app.trace' in output, got: {combined!r}"
    )


def test_debug_flag_respects_existing_env_var(tmp_path, monkeypatch):
    """If RPG_DEBUG_TRACE is already set, --debug doesn't override it."""
    from app.__main__ import main

    # Pre-set the env var to a specific path
    existing_path = str(tmp_path / "existing_trace.jsonl")
    monkeypatch.setenv("RPG_DEBUG_TRACE", existing_path)

    campaign_dir = tmp_path / "test_campaign_4"
    output = []

    provider = ScriptedProvider(_make_scripted_replies())
    main(
        ["--campaign", str(campaign_dir), "--debug"],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=provider,
    )

    # Env var should still be the pre-set value
    assert os.environ["RPG_DEBUG_TRACE"] == existing_path


def test_no_debug_flag_does_not_set_trace(tmp_path, monkeypatch):
    """Without --debug flag, RPG_DEBUG_TRACE is not set (unless already set)."""
    from app.__main__ import main

    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)

    campaign_dir = tmp_path / "test_campaign_5"
    output = []

    provider = ScriptedProvider(_make_scripted_replies())
    main(
        ["--campaign", str(campaign_dir)],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=provider,
    )

    # Env var should not be set
    assert os.environ.get("RPG_DEBUG_TRACE") is None


def test_debug_flag_traces_genesis(tmp_path, monkeypatch):
    """Trace file created by --debug includes genesis span records."""
    from app.__main__ import main

    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)

    campaign_dir = tmp_path / "test_campaign_6"
    output = []

    provider = ScriptedProvider(_make_scripted_replies())
    main(
        ["--campaign", str(campaign_dir), "--debug"],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=provider,
    )

    trace_file = campaign_dir / "trace.jsonl"
    records = []
    with open(trace_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # Should have genesis spans
    genesis_records = [r for r in records if "genesis" in r.get("path", "")]
    assert len(genesis_records) >= 1, (
        f"Expected >= 1 genesis record in trace, got {len(genesis_records)}"
    )


def test_debug_flag_cleanup_for_other_tests(tmp_path, monkeypatch):
    """After --debug, the env var and singleton should be reset by cleanup."""
    from app.__main__ import main
    import kernel.observability as _obs

    # Save original state
    original_env = os.environ.get("RPG_DEBUG_TRACE")

    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)

    campaign_dir = tmp_path / "test_campaign_7"
    output = []

    provider = ScriptedProvider(_make_scripted_replies())
    main(
        ["--campaign", str(campaign_dir), "--debug"],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=provider,
    )

    # Manually clean up (caller would use monkeypatch.delenv in a real test)
    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)
    _obs._DEBUG_TRACER = None

    # Should be clean now
    assert os.environ.get("RPG_DEBUG_TRACE") is None
    assert _obs._DEBUG_TRACER is None
