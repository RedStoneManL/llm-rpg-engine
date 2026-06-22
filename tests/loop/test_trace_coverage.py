"""Task-3 trace-coverage tests.

Drive a tiny offline bootstrap + a scripted turn under a DebugTracer, then
assert the JSONL contains:
  - genesis span + genesis▸gen_frame:frame path
  - produce span
  - repair span (when a repair is forced)
  - player_input event with the expected text
"""
from __future__ import annotations

import json
import os

import kernel.observability as obs


# ---------------------------------------------------------------------------
# JSONL helper
# ---------------------------------------------------------------------------

def _records(path):
    """Return all parsed JSON records from a JSONL file at path."""
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def _paths(path):
    """Return path strings from all records in the JSONL file."""
    return [r.get("path", "") for r in _records(path)]


# ---------------------------------------------------------------------------
# Bootstrap engine helper (reuses T9 machinery from test_bootstrap.py)
# ---------------------------------------------------------------------------

def _make_bootstrap_engine(tmp_path):
    """Build a fresh engine with a full-bootstrap ScriptedProvider."""
    from tests.loop.test_bootstrap import (
        ScriptedProvider,
        _canned_local_map_reply,
        _T9_N_FACTIONS,
        _T9_N_REGIONS,
        _T9_N_VENUES,
        _T9_N_NPCS,
        _T9_N_THREADS,
        _T9_N_P,
    )
    from app.engine import build_engine

    venues = [f"venue_{i}" for i in range(_T9_N_VENUES)]

    frame_reply = json.dumps({
        "world_name": "追踪测试世界",
        "central_conflict": "追踪测试冲突",
    })
    regions_reply = json.dumps({
        "regions": [
            {"name": f"地域{i}", "terrain": ["山地", "荒漠", "森林", "水乡", "平原"][i],
             "seed": f"地域{i}描述"}
            for i in range(_T9_N_REGIONS)
        ]
    })
    local_map_reply = _canned_local_map_reply(n_venues=_T9_N_VENUES, n_neighbors=1)
    factions_reply = json.dumps({
        "factions": [
            {"name": f"势力{i}", "motivation": f"势力{i}的动机"}
            for i in range(_T9_N_FACTIONS)
        ]
    })
    npcs_reply = json.dumps({
        "npcs": [
            {"sketch": f"NPC{i}外貌", "goal": f"NPC{i}目标", "secret": f"NPC{i}秘密"}
            for i in range(_T9_N_NPCS)
        ]
    })
    campaign_threads_reply = json.dumps({
        "lines": [
            {
                "about": f"暗线{i}表象",
                "description": f"暗线{i}描述",
                "trigger": f"暗线{i}触发",
                "secret": f"暗线{i}真相",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"暗线{i}阶段{j}"} for j in range(3)],
            }
            for i in range(_T9_N_THREADS)
        ]
    })
    prot_threads_reply = json.dumps({
        "lines": [
            {
                "about": f"主角线{i}表象",
                "description": f"主角线{i}描述",
                "trigger": f"主角线{i}触发",
                "secret": f"主角线{i}真相",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"主角线{i}阶段{j}"} for j in range(2)],
            }
            for i in range(_T9_N_P)
        ]
    })
    opening_reply = "你踏入了追踪测试世界的起始之地，充满可能。"

    provider = ScriptedProvider([
        frame_reply,
        regions_reply,
        local_map_reply,
        factions_reply,
        npcs_reply,
        campaign_threads_reply,
        prot_threads_reply,
        opening_reply,
    ])

    campaign_dir = tmp_path / "trace_test_campaign"
    return build_engine(campaign_dir, provider=provider)


# ---------------------------------------------------------------------------
# Test 1: bootstrap steps produce genesis + gen_frame spans
# ---------------------------------------------------------------------------

def test_bootstrap_steps_are_spanned(tmp_path, monkeypatch):
    """bootstrap_world wraps execution in a 'genesis' span with gen_frame inside."""
    trace_file = str(tmp_path / "t.jsonl")
    monkeypatch.setenv("RPG_DEBUG_TRACE", trace_file)
    obs._DEBUG_TRACER = None  # force singleton re-creation

    from loop.bootstrap import bootstrap_world

    engine = _make_bootstrap_engine(tmp_path)
    bootstrap_world(engine, "东方武侠追踪测试")

    ps = _paths(trace_file)

    # The outer genesis span must appear
    assert any("genesis" in p for p in ps), \
        f"Expected 'genesis' in paths, got: {ps[:10]}"

    # The gen_frame sub-span must appear nested under genesis
    assert any("gen_frame" in p for p in ps), \
        f"Expected 'gen_frame' in paths, got: {ps[:10]}"

    # gen_frame must be a child of genesis (path contains genesis▸gen_frame)
    assert any("genesis" in p and "gen_frame" in p for p in ps), \
        f"Expected path containing both 'genesis' and 'gen_frame', got: {ps[:10]}"


def test_bootstrap_threads_span(tmp_path, monkeypatch):
    """bootstrap_world wraps gen_threads in a 'gen_threads' span under genesis."""
    trace_file = str(tmp_path / "t.jsonl")
    monkeypatch.setenv("RPG_DEBUG_TRACE", trace_file)
    obs._DEBUG_TRACER = None

    from loop.bootstrap import bootstrap_world

    engine = _make_bootstrap_engine(tmp_path)
    bootstrap_world(engine, "线程追踪测试")

    ps = _paths(trace_file)

    assert any("gen_threads" in p for p in ps), \
        f"Expected 'gen_threads' in paths, got: {ps[:10]}"
    assert any("genesis" in p and "gen_threads" in p for p in ps), \
        f"Expected path with genesis▸gen_threads, got: {ps[:10]}"


def test_bootstrap_opening_span(tmp_path, monkeypatch):
    """bootstrap_world wraps gen_opening in a 'gen_opening' span under genesis."""
    trace_file = str(tmp_path / "t.jsonl")
    monkeypatch.setenv("RPG_DEBUG_TRACE", trace_file)
    obs._DEBUG_TRACER = None

    from loop.bootstrap import bootstrap_world

    engine = _make_bootstrap_engine(tmp_path)
    bootstrap_world(engine, "开场追踪测试")

    ps = _paths(trace_file)

    assert any("gen_opening" in p for p in ps), \
        f"Expected 'gen_opening' in paths, got: {ps[:10]}"


# ---------------------------------------------------------------------------
# Test 2: produce span appears during a normal turn
# ---------------------------------------------------------------------------

def _make_turn_engine(tmp_path):
    """Build an engine with new_game + a FakeLLMProvider for turns."""
    from llm.provider import FakeLLMProvider
    from app.engine import build_engine

    # Bootstrap provider (ScriptedProvider for new_game)
    from tests.loop.test_bootstrap import (
        ScriptedProvider,
        _canned_local_map_reply,
        _T9_N_FACTIONS,
        _T9_N_REGIONS,
        _T9_N_VENUES,
        _T9_N_NPCS,
        _T9_N_THREADS,
        _T9_N_P,
    )

    venues = [f"venue_{i}" for i in range(_T9_N_VENUES)]
    frame_reply = json.dumps({"world_name": "回合测试世界", "central_conflict": "回合冲突"})
    regions_reply = json.dumps({
        "regions": [
            {"name": f"地域{i}", "terrain": ["山地", "荒漠", "森林", "水乡", "平原"][i],
             "seed": f"地域{i}"}
            for i in range(_T9_N_REGIONS)
        ]
    })
    local_map_reply = _canned_local_map_reply(n_venues=_T9_N_VENUES, n_neighbors=1)
    factions_reply = json.dumps({
        "factions": [{"name": f"势力{i}", "motivation": f"动机{i}"} for i in range(_T9_N_FACTIONS)]
    })
    npcs_reply = json.dumps({
        "npcs": [
            {"sketch": f"NPC{i}", "goal": f"目标{i}", "secret": f"秘密{i}"}
            for i in range(_T9_N_NPCS)
        ]
    })
    campaign_threads_reply = json.dumps({
        "lines": [
            {
                "about": f"暗{i}", "description": f"描{i}", "trigger": f"触{i}",
                "secret": f"秘{i}", "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"阶{i}"}],
            }
            for i in range(_T9_N_THREADS)
        ]
    })
    prot_threads_reply = json.dumps({
        "lines": [{
            "about": "主线", "description": "主描", "trigger": "主触",
            "secret": "主秘", "l3_anchor": venues[0],
            "stages": [{"hint": "主阶"}],
        }]
    })
    opening_reply = "你踏入了回合测试世界。"

    bootstrap_provider = ScriptedProvider([
        frame_reply, regions_reply, local_map_reply, factions_reply,
        npcs_reply, campaign_threads_reply, prot_threads_reply, opening_reply,
    ])

    campaign_dir = tmp_path / "turn_trace_campaign"
    engine = build_engine(campaign_dir, provider=bootstrap_provider)

    from loop.bootstrap import bootstrap_world
    bootstrap_world(engine, "回合测试")

    # Now swap in a FakeLLMProvider for the actual turn
    valid_commit = {
        "narration": "你环顾四周，发现这是一片宁静的旷野。",
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "时间未推进"}],
    }
    engine.provider = FakeLLMProvider(json_responses=[valid_commit])
    return engine


def test_produce_span_appears(tmp_path, monkeypatch):
    """produce_turn wraps the initial LLM call in a 'produce' span."""
    trace_file = str(tmp_path / "t.jsonl")
    monkeypatch.setenv("RPG_DEBUG_TRACE", trace_file)
    obs._DEBUG_TRACER = None

    from loop.turn import run_turn, REQUIRED_SECTIONS
    from loop.strategy import AuthorStrategy

    engine = _make_turn_engine(tmp_path)

    from app.play import _build_scene
    scene = _build_scene(engine)

    run_turn(
        engine.registry, engine.store, engine.world, scene,
        "我环顾四周",
        strategy=AuthorStrategy(),
        provider=engine.provider,
        max_repairs=3,
        required_sections=REQUIRED_SECTIONS,
    )

    ps = _paths(trace_file)
    assert any("produce" in p for p in ps), \
        f"Expected 'produce' span in paths. Got paths containing: {[p for p in ps if p][:20]}"


# ---------------------------------------------------------------------------
# Test 3: repair span appears when the first commit is invalid
# ---------------------------------------------------------------------------

class _RepairFakeProvider:
    """Provider that returns an invalid commit first, then a valid one."""

    def __init__(self):
        self._calls = 0
        self._bad = json.dumps({
            "narration": "坏的回合输出",
            # Missing required 'clock' section — will fail validate_commit
        })
        self._good = json.dumps({
            "narration": "修复后的输出，你环顾四周。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "未推进"}],
        })

    def supports_tools(self):
        return False

    def complete_messages(self, messages, **kw):
        self._calls += 1
        if self._calls == 1:
            return self._bad
        return self._good

    def complete(self, system, user, **kw):
        return self._good


def test_repair_span_appears(tmp_path, monkeypatch):
    """produce_turn wraps each repair iteration in a 'repair' span."""
    trace_file = str(tmp_path / "t.jsonl")
    monkeypatch.setenv("RPG_DEBUG_TRACE", trace_file)
    obs._DEBUG_TRACER = None

    from loop.turn import run_turn, REQUIRED_SECTIONS
    from loop.strategy import AuthorStrategy

    engine = _make_turn_engine(tmp_path)
    # Replace provider with one that forces a repair
    engine.provider = _RepairFakeProvider()

    from app.play import _build_scene
    scene = _build_scene(engine)

    run_turn(
        engine.registry, engine.store, engine.world, scene,
        "我检查地图",
        strategy=AuthorStrategy(),
        provider=engine.provider,
        max_repairs=3,
        required_sections=REQUIRED_SECTIONS,
    )

    ps = _paths(trace_file)
    assert any("repair" in p for p in ps), \
        f"Expected 'repair' span in paths. Got: {[p for p in ps if p][:20]}"

    # Verify repair span has attempt attribute in the record
    recs = _records(trace_file)
    repair_recs = [r for r in recs if r.get("name") == "repair"]
    assert repair_recs, "Expected at least one repair span record"
    # The first repair attempt should be attempt=1
    first_repair = repair_recs[0]
    assert (first_repair.get("attrs") or {}).get("attempt") == 1, \
        f"Expected repair attempt=1, got attrs: {first_repair.get('attrs')}"


# ---------------------------------------------------------------------------
# Test 4: player_input event recorded via play_loop
# ---------------------------------------------------------------------------

def test_player_input_event_recorded(tmp_path, monkeypatch):
    """play_loop emits a player_input event before running each normal turn."""
    trace_file = str(tmp_path / "t.jsonl")
    monkeypatch.setenv("RPG_DEBUG_TRACE", trace_file)
    obs._DEBUG_TRACER = None

    from app.play import play_loop
    from llm.provider import FakeLLMProvider

    # Build engine with a bootstrap provider (ScriptedProvider) then a turn provider
    from tests.loop.test_bootstrap import (
        ScriptedProvider,
        _canned_local_map_reply,
        _T9_N_FACTIONS,
        _T9_N_REGIONS,
        _T9_N_VENUES,
        _T9_N_NPCS,
        _T9_N_THREADS,
        _T9_N_P,
    )
    from app.engine import build_engine
    from loop.bootstrap import bootstrap_world

    venues = [f"venue_{i}" for i in range(_T9_N_VENUES)]
    frame_reply = json.dumps({"world_name": "事件测试世界", "central_conflict": "事件冲突"})
    regions_reply = json.dumps({
        "regions": [
            {"name": f"区{i}", "terrain": ["山地", "荒漠", "森林", "水乡", "平原"][i], "seed": f"区{i}"}
            for i in range(_T9_N_REGIONS)
        ]
    })
    local_map_reply = _canned_local_map_reply(n_venues=_T9_N_VENUES, n_neighbors=1)
    factions_reply = json.dumps({
        "factions": [{"name": f"派{i}", "motivation": f"图{i}"} for i in range(_T9_N_FACTIONS)]
    })
    npcs_reply = json.dumps({
        "npcs": [{"sketch": f"甲{i}", "goal": f"标{i}", "secret": f"密{i}"} for i in range(_T9_N_NPCS)]
    })
    c_threads = json.dumps({
        "lines": [
            {
                "about": f"表{i}", "description": f"述{i}", "trigger": f"发{i}",
                "secret": f"秘{i}", "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"段{i}"}],
            }
            for i in range(_T9_N_THREADS)
        ]
    })
    p_threads = json.dumps({
        "lines": [{
            "about": "主", "description": "主", "trigger": "主",
            "secret": "密", "l3_anchor": venues[0], "stages": [{"hint": "主"}],
        }]
    })
    opening = "你踏入了事件测试世界。"

    bootstrap_prov = ScriptedProvider([
        frame_reply, regions_reply, local_map_reply, factions_reply,
        npcs_reply, c_threads, p_threads, opening,
    ])

    campaign_dir = tmp_path / "event_trace_campaign"
    engine = build_engine(campaign_dir, provider=bootstrap_prov)
    bootstrap_world(engine, "事件追踪测试")

    # Swap to a FakeLLMProvider for the actual turn
    valid_commit = {
        "narration": "你环顾四周，发现这是一片宁静的旷野。",
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "未推进"}],
    }
    engine.provider = FakeLLMProvider(json_responses=[valid_commit])

    # Run play_loop with one real input then /quit
    player_text = "我环顾四周"
    play_loop(engine, inputs=[player_text, "/quit"], out=lambda *a: None)

    # Parse the JSONL and find the player_input event
    recs = _records(trace_file)
    pin_recs = [r for r in recs if r.get("type") == "event" and r.get("name") == "player_input"]
    assert pin_recs, \
        f"Expected player_input event in trace. Events found: {[r.get('name') for r in recs if r.get('type') == 'event']}"

    pin = pin_recs[0]
    attrs = pin.get("attrs") or {}
    assert player_text in attrs.get("text", ""), \
        f"Expected player text '{player_text}' in attrs.text, got: {attrs}"
    assert attrs.get("turn") == 1, \
        f"Expected turn=1 in player_input attrs, got: {attrs}"


# ---------------------------------------------------------------------------
# Test 5: noop tracer — all additions produce zero overhead (no file written)
# ---------------------------------------------------------------------------

def test_noop_tracer_no_file(tmp_path, monkeypatch):
    """Without RPG_DEBUG_TRACE, no trace file is created and bootstrap runs fine."""
    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)
    obs._DEBUG_TRACER = None

    from loop.bootstrap import bootstrap_world

    engine = _make_bootstrap_engine(tmp_path)
    result = bootstrap_world(engine, "无追踪测试")
    # Bootstrap completed without error and no trace file exists
    assert result["summary"]["world_name"]
    trace_file = tmp_path / "t.jsonl"
    assert not trace_file.exists(), "No trace file should be written under NoopTracer"
