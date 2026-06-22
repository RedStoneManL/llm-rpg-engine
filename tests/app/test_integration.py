"""Task 10 integration tests: real bootstrap wired into new_game + CLI first-run flow.

Covers:
- new_game(engine, pitch) calls bootstrap_world → rich world with lore lines + NPCs.
- CLI --pitch flag and RPG_BOOTSTRAP_PITCH env honored.
- First-run reroll loop dispatches: 'reroll' / 'reroll <step>' / '开始' / 'start' / ''.
- Existing placeholder entity IDs (starting_location / generic protagonist) replaced by
  bootstrap IDs (town_0, npc_0, ...).
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# ScriptedProvider — mirrors the one in tests/loop/test_bootstrap.py
# (defined locally so this file has zero inter-test-file imports)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Pre-computed parameters for the fixed campaign name used in integration tests
# (same as _T9_CAMPAIGN_NAME in test_bootstrap.py, same constants)
# Campaign name: "bootstrap_fixed_name" → seed=49346256305563
# frame:    n_factions=5, n_regions=5
# local_map: n_extra_l2=1, n_venues=2 → venues=['venue_0','venue_1']
# npcs:     n_npcs=3
# threads:  n_threads=3, n_p=1
# ---------------------------------------------------------------------------

_CAMPAIGN_NAME = "bootstrap_fixed_name"
_N_FACTIONS = 5
_N_REGIONS = 5
_N_VENUES = 2
_N_NPCS = 3
_N_THREADS = 3
_N_P = 1


def _make_scripted_replies(attempt: int = 0) -> list:
    """Return the list of canned replies for all 8 LLM calls in bootstrap_world."""
    venues = [f"venue_{i}" for i in range(_N_VENUES)]

    frame_reply = json.dumps({
        "world_name": f"集成测试世界_{attempt}",
        "central_conflict": f"集成测试冲突_{attempt}",
    })
    regions_reply = json.dumps({
        "regions": [
            {"name": f"地域{i}", "terrain": ["山地", "荒漠", "森林", "水乡", "平原"][i],
             "seed": f"地域{i}描述"}
            for i in range(_N_REGIONS)
        ]
    })
    local_map_reply = json.dumps({
        "town": {"name": "集成测试镇", "seed": "古老的集镇"},
        "venues": [{"name": f"场所{i}", "seed": f"场所{i}描述"} for i in range(_N_VENUES)],
        "neighbors": [{"name": "荒野", "seed": "危险地带"}],  # n_extra_l2=1
    })
    factions_reply = json.dumps({
        "factions": [
            {"name": f"势力{i}", "motivation": f"势力{i}的动机"}
            for i in range(_N_FACTIONS)
        ]
    })
    npcs_reply = json.dumps({
        "npcs": [
            {"sketch": f"NPC{i}外貌", "goal": f"NPC{i}目标", "secret": f"NPC{i}秘密"}
            for i in range(_N_NPCS)
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
            for i in range(_N_THREADS)
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
                "stages": [{"hint": f"主角线{i}阶段{j}"} for j in range(3)],
            }
            for i in range(_N_P)
        ]
    })
    opening_reply = f"你踏入了集成测试世界_{attempt}的起始之地，四周充满了可能性。"

    return [
        frame_reply,
        regions_reply,
        local_map_reply,
        factions_reply,
        npcs_reply,
        campaign_threads_reply,
        prot_threads_reply,
        opening_reply,
    ]


def _make_scripted_provider(attempt: int = 0) -> ScriptedProvider:
    """Build a ScriptedProvider with canned replies for all 8 LLM calls."""
    return ScriptedProvider(_make_scripted_replies(attempt))


def _make_engine(tmp_path, attempt: int = 0):
    """Build a fresh engine in a named campaign sub-dir so seeds are deterministic."""
    from app.engine import build_engine
    campaign_dir = tmp_path / _CAMPAIGN_NAME
    return build_engine(campaign_dir, provider=_make_scripted_provider(attempt=attempt))


# ---------------------------------------------------------------------------
# Step 1 / Step 2 (TDD): these tests should FAIL before implementation.
# After implementation they must pass.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A. new_game produces bootstrapped world — not old placeholder
# ---------------------------------------------------------------------------

def test_new_game_with_pitch_produces_lore_lines(tmp_path):
    """new_game(engine, pitch) → world has >= 1 lore line (bootstrap path)."""
    from app.engine import build_engine, new_game
    engine = _make_engine(tmp_path)
    new_game(engine, "东方武侠悬疑")

    lore = engine.world.get("systems", {}).get("lore", {})
    lines = lore.get("lines", {})
    assert len(lines) >= 1, (
        f"Expected >= 1 lore line after bootstrap, got {len(lines)}"
    )


def test_new_game_with_pitch_produces_npcs_with_secrets(tmp_path):
    """new_game(engine, pitch) → world has NPCs and each has a secret fact."""
    from app.engine import build_engine, new_game
    engine = _make_engine(tmp_path)
    new_game(engine, "东方武侠悬疑")

    events = list(engine.store.iter_events())
    npc_ids = {
        e["deltas"]["id"]
        for e in events
        if e["type"] == "character_created"
        and not e["deltas"]["id"].startswith("protagonist")
    }
    assert len(npc_ids) >= 2, f"Expected >= 2 NPCs, got {npc_ids}"

    secret_subjects = {
        e["deltas"]["subject"]
        for e in events
        if e["type"] == "fact_asserted" and e["deltas"].get("secrecy") == "secret"
    }
    for npc_id in npc_ids:
        assert npc_id in secret_subjects, f"NPC {npc_id} has no secret fact"


def test_new_game_with_pitch_no_starting_location_placeholder(tmp_path):
    """Bootstrap world must NOT use the old 'starting_location' placeholder."""
    from app.engine import build_engine, new_game
    engine = _make_engine(tmp_path)
    new_game(engine, "东方武侠悬疑")

    g = engine.world["systems"]["ontology"]
    assert "starting_location" not in g.entities, (
        "'starting_location' placeholder still present — bootstrap did not replace old genesis"
    )
    # town_0 must exist instead
    assert "town_0" in g.entities, "town_0 not found in world after bootstrap"


def test_new_game_default_pitch_still_bootstraps(tmp_path):
    """new_game(engine) with no pitch (default '') still runs bootstrap (no old placeholder)."""
    from app.engine import build_engine, new_game
    engine = _make_engine(tmp_path)
    new_game(engine)  # default pitch=""

    lore = engine.world.get("systems", {}).get("lore", {})
    lines = lore.get("lines", {})
    assert len(lines) >= 1, "Bootstrap did not run with default pitch"


def test_new_game_returns_result_dict(tmp_path):
    """new_game should return the bootstrap_world result dict (or at minimum not None)."""
    from app.engine import build_engine, new_game
    engine = _make_engine(tmp_path)
    result = new_game(engine, "测试")

    # bootstrap_world returns a dict with 'summary' key
    assert result is not None
    assert isinstance(result, dict)
    assert "summary" in result


# ---------------------------------------------------------------------------
# B. CLI --pitch flag honored
# ---------------------------------------------------------------------------

def test_main_pitch_flag_is_used(tmp_path, monkeypatch):
    """--pitch <text> is passed to bootstrap_world as the pitch argument."""
    from app.__main__ import main

    received_pitches = []

    # Monkey-patch bootstrap_world to capture the pitch.
    # new_game in app/engine.py does `from loop.bootstrap import bootstrap_world`
    # at call time, so we must patch `loop.bootstrap.bootstrap_world` and also
    # ensure the `app.engine` module re-imports from the patched location.
    import loop.bootstrap as _bootstrap_mod

    original = _bootstrap_mod.bootstrap_world

    def _capture(engine, pitch, **kw):
        received_pitches.append(pitch)
        return original(engine, pitch, **kw)

    monkeypatch.setattr(_bootstrap_mod, "bootstrap_world", _capture)

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake",
         "--pitch", "战国乱世"],
        inputs=["/quit"],
        out=output.append,
        provider=_make_scripted_provider(),
    )

    assert received_pitches, "bootstrap_world was never called"
    assert received_pitches[0] == "战国乱世", (
        f"Expected pitch='战国乱世', got '{received_pitches[0]}'"
    )


def test_main_env_pitch_honored(tmp_path, monkeypatch):
    """RPG_BOOTSTRAP_PITCH env var is used when --pitch is not supplied."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    received_pitches = []
    original = _bootstrap_mod.bootstrap_world

    def _capture(engine, pitch, **kw):
        received_pitches.append(pitch)
        return original(engine, pitch, **kw)

    monkeypatch.setattr(_bootstrap_mod, "bootstrap_world", _capture)
    monkeypatch.setenv("RPG_BOOTSTRAP_PITCH", "仙侠修仙")

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["/quit"],
        out=output.append,
        provider=_make_scripted_provider(),
    )

    assert received_pitches, "bootstrap_world was never called"
    assert received_pitches[0] == "仙侠修仙", (
        f"Expected pitch from env='仙侠修仙', got '{received_pitches[0]}'"
    )


def test_main_pitch_flag_takes_priority_over_env(tmp_path, monkeypatch):
    """--pitch flag takes priority over RPG_BOOTSTRAP_PITCH env."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    received_pitches = []
    original = _bootstrap_mod.bootstrap_world

    def _capture(engine, pitch, **kw):
        received_pitches.append(pitch)
        return original(engine, pitch, **kw)

    monkeypatch.setattr(_bootstrap_mod, "bootstrap_world", _capture)
    monkeypatch.setenv("RPG_BOOTSTRAP_PITCH", "被覆盖的值")

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake",
         "--pitch", "旗舰级别优先"],
        inputs=["/quit"],
        out=output.append,
        provider=_make_scripted_provider(),
    )

    assert received_pitches[0] == "旗舰级别优先", (
        f"--pitch flag did not override env: got '{received_pitches[0]}'"
    )


# ---------------------------------------------------------------------------
# C. First-run prints the summary then enters the reroll loop
# ---------------------------------------------------------------------------

def test_main_first_run_prints_summary(tmp_path):
    """After bootstrap, main prints the world summary before the reroll prompt."""
    from app.__main__ import main

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["开始"],
        out=output.append,
        provider=_make_scripted_provider(),
    )
    combined = "\n".join(output)
    # Summary must mention the world name we canned for attempt=0
    assert "集成测试世界_0" in combined, (
        f"Expected world_name in summary output, got: {combined!r}"
    )


def test_main_first_run_does_not_bootstrap_twice(tmp_path):
    """On a pre-existing store (second run), main does NOT bootstrap again."""
    from app.__main__ import main

    # First run: bootstrap
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["开始"],
        out=lambda _: None,
        provider=_make_scripted_provider(),
    )

    # Second run: store already has events — must produce [载入存档] not [新游戏]
    second_output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["/quit"],
        out=second_output.append,
        provider=_make_scripted_provider(),
    )
    combined = "\n".join(second_output)
    assert "载入存档" in combined, (
        f"Second run should show [载入存档], got: {combined!r}"
    )
    assert "新游戏" not in combined, (
        f"Second run should NOT show [新游戏], got: {combined!r}"
    )


# ---------------------------------------------------------------------------
# D. Reroll loop dispatch
# ---------------------------------------------------------------------------

def test_reroll_loop_reroll_dispatches_reroll_all(tmp_path, monkeypatch):
    """'reroll' in reroll loop calls reroll_all and reprints summary."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    reroll_all_calls = []
    original = _bootstrap_mod.reroll_all

    def _capture(engine, prev):
        reroll_all_calls.append(True)
        return original(engine, prev)

    monkeypatch.setattr(_bootstrap_mod, "reroll_all", _capture)

    # 16 replies: 8 for initial bootstrap + 8 for reroll_all
    replies = _make_scripted_replies(attempt=0) + _make_scripted_replies(attempt=1)

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["reroll", "开始", "/quit"],
        out=output.append,
        provider=ScriptedProvider(replies),
    )

    assert len(reroll_all_calls) >= 1, "reroll_all was never called"


def test_reroll_loop_reroll_step_factions_dispatches(tmp_path, monkeypatch):
    """'reroll factions' in reroll loop calls reroll_step(engine, prev, 'factions')."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    reroll_step_calls = []
    original = _bootstrap_mod.reroll_step

    def _capture(engine, prev, step):
        reroll_step_calls.append(step)
        return original(engine, prev, step)

    monkeypatch.setattr(_bootstrap_mod, "reroll_step", _capture)

    # reroll_step('factions') re-runs factions(1) + npcs(1) + threads(2) + opening(1) = 5 more
    venues = [f"venue_{i}" for i in range(_N_VENUES)]
    extra_replies = [
        json.dumps({"factions": [{"name": f"新势力{i}", "motivation": f"新动机{i}"} for i in range(_N_FACTIONS)]}),
        json.dumps({"npcs": [{"sketch": f"新NPC{i}外貌", "goal": f"新NPC{i}目标", "secret": f"新NPC{i}秘密"} for i in range(_N_NPCS)]}),
        json.dumps({"lines": [{"about": f"新暗线{i}", "description": f"描述{i}", "trigger": f"触发{i}", "secret": f"真相{i}", "l3_anchor": venues[i % len(venues)], "stages": [{"hint": "提示"}]} for i in range(_N_THREADS)]}),
        json.dumps({"lines": [{"about": "新主角线", "description": "描述", "trigger": "触发", "secret": "真相", "l3_anchor": venues[0], "stages": [{"hint": "提示"}]}]}),
        "你踏入了重掷后的世界。",
    ]
    replies = _make_scripted_replies(attempt=0) + extra_replies

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["reroll factions", "开始", "/quit"],
        out=output.append,
        provider=ScriptedProvider(replies),
    )

    assert "factions" in reroll_step_calls, (
        f"reroll_step('factions') was never called; calls={reroll_step_calls}"
    )


def test_reroll_loop_reroll_step_npcs_dispatches(tmp_path, monkeypatch):
    """'reroll npcs' in reroll loop calls reroll_step(engine, prev, 'npcs')."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    reroll_step_calls = []
    original = _bootstrap_mod.reroll_step

    def _capture(engine, prev, step):
        reroll_step_calls.append(step)
        return original(engine, prev, step)

    monkeypatch.setattr(_bootstrap_mod, "reroll_step", _capture)

    # reroll_step('npcs') re-runs npcs(1) + threads(2) + opening(1) = 4 more
    venues = [f"venue_{i}" for i in range(_N_VENUES)]
    extra_replies = [
        json.dumps({"npcs": [{"sketch": f"新NPC{i}外貌", "goal": f"新NPC{i}目标", "secret": f"新NPC{i}秘密"} for i in range(_N_NPCS)]}),
        json.dumps({"lines": [{"about": f"新暗线{i}", "description": f"描述{i}", "trigger": f"触发{i}", "secret": f"真相{i}", "l3_anchor": venues[i % len(venues)], "stages": [{"hint": "提示"}]} for i in range(_N_THREADS)]}),
        json.dumps({"lines": [{"about": "新主角线", "description": "描述", "trigger": "触发", "secret": "真相", "l3_anchor": venues[0], "stages": [{"hint": "提示"}]}]}),
        "你踏入了重掷后的世界。",
    ]
    replies = _make_scripted_replies(attempt=0) + extra_replies

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["reroll npcs", "开始", "/quit"],
        out=output.append,
        provider=ScriptedProvider(replies),
    )

    assert "npcs" in reroll_step_calls, (
        f"reroll_step('npcs') was never called; calls={reroll_step_calls}"
    )


def test_reroll_loop_reroll_step_threads_dispatches(tmp_path, monkeypatch):
    """'reroll threads' in reroll loop calls reroll_step(engine, prev, 'threads')."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    reroll_step_calls = []
    original = _bootstrap_mod.reroll_step

    def _capture(engine, prev, step):
        reroll_step_calls.append(step)
        return original(engine, prev, step)

    monkeypatch.setattr(_bootstrap_mod, "reroll_step", _capture)

    # reroll_step('threads') re-runs threads(2) + opening(1) = 3 more
    venues = [f"venue_{i}" for i in range(_N_VENUES)]
    extra_replies = [
        json.dumps({"lines": [{"about": f"新暗线{i}", "description": f"描述{i}", "trigger": f"触发{i}", "secret": f"真相{i}", "l3_anchor": venues[i % len(venues)], "stages": [{"hint": "提示"}]} for i in range(_N_THREADS)]}),
        json.dumps({"lines": [{"about": "新主角线", "description": "描述", "trigger": "触发", "secret": "真相", "l3_anchor": venues[0], "stages": [{"hint": "提示"}]}]}),
        "你踏入了重掷后的世界。",
    ]
    replies = _make_scripted_replies(attempt=0) + extra_replies

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["reroll threads", "开始", "/quit"],
        out=output.append,
        provider=ScriptedProvider(replies),
    )

    assert "threads" in reroll_step_calls, (
        f"reroll_step('threads') was never called; calls={reroll_step_calls}"
    )


def test_reroll_loop_kaishi_breaks_into_play(tmp_path, monkeypatch):
    """'开始' in the reroll loop breaks into play_loop (no reroll called)."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    reroll_all_calls = []
    original_all = _bootstrap_mod.reroll_all

    def _count_all(engine, prev):
        reroll_all_calls.append(True)
        return original_all(engine, prev)

    monkeypatch.setattr(_bootstrap_mod, "reroll_all", _count_all)

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["开始", "/quit"],
        out=output.append,
        provider=_make_scripted_provider(),
    )
    assert len(reroll_all_calls) == 0, (
        "reroll_all was called after '开始' — should break into play_loop"
    )


def test_reroll_loop_start_english_breaks_into_play(tmp_path, monkeypatch):
    """'start' in the reroll loop breaks into play_loop (no reroll called)."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    reroll_all_calls = []
    original_all = _bootstrap_mod.reroll_all

    def _count_all(engine, prev):
        reroll_all_calls.append(True)
        return original_all(engine, prev)

    monkeypatch.setattr(_bootstrap_mod, "reroll_all", _count_all)

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["start", "/quit"],
        out=output.append,
        provider=_make_scripted_provider(),
    )
    assert len(reroll_all_calls) == 0, (
        "'start' triggered reroll_all — should break into play_loop"
    )


def test_reroll_loop_empty_line_breaks_into_play(tmp_path, monkeypatch):
    """Empty line '' in the reroll loop breaks into play_loop (no reroll called)."""
    from app.__main__ import main
    import loop.bootstrap as _bootstrap_mod

    reroll_all_calls = []
    original_all = _bootstrap_mod.reroll_all

    def _count_all(engine, prev):
        reroll_all_calls.append(True)
        return original_all(engine, prev)

    monkeypatch.setattr(_bootstrap_mod, "reroll_all", _count_all)

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["", "/quit"],
        out=output.append,
        provider=_make_scripted_provider(),
    )
    assert len(reroll_all_calls) == 0, (
        "Empty line triggered reroll_all — should break into play_loop"
    )


def test_reroll_loop_reprints_summary_after_reroll(tmp_path):
    """After a reroll, the summary is printed again (so user can see the new world)."""
    from app.__main__ import main

    # 16 replies: 8 for initial bootstrap + 8 for reroll_all
    replies = _make_scripted_replies(attempt=0) + _make_scripted_replies(attempt=1)

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake"],
        inputs=["reroll", "开始", "/quit"],
        out=output.append,
        provider=ScriptedProvider(replies),
    )
    combined = "\n".join(output)
    # Summary should appear at least twice (initial + after reroll).
    # attempt=0 → "集成测试世界_0", attempt=1 → "集成测试世界_1"
    assert "集成测试世界_0" in combined, (
        f"Initial world name not found in output: {combined!r}"
    )
    assert "集成测试世界_1" in combined, (
        f"Post-reroll world name not found in output: {combined!r}"
    )


# ---------------------------------------------------------------------------
# E. Integration smoke: full first-run → reroll → play
# ---------------------------------------------------------------------------

def test_full_first_run_play_flow(tmp_path):
    """Full path: new campaign → bootstrap → '开始' → one play turn → /quit."""
    from app.__main__ import main

    turn_response = json.dumps({
        "narration": "你进入了世界，感受到了这里的氛围。",
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}],
    })
    # 8 bootstrap replies + 1 play-turn reply
    all_replies = _make_scripted_replies(attempt=0) + [turn_response]

    output = []
    main(
        ["--campaign", str(tmp_path / _CAMPAIGN_NAME), "--provider", "fake",
         "--pitch", "奇幻冒险"],
        inputs=["开始", "向前走", "/quit"],
        out=output.append,
        provider=ScriptedProvider(all_replies),
    )

    combined = "\n".join(output)
    assert len(combined) > 0, "Expected some output from full run"
    # The play turn should have run
    assert "你进入了世界" in combined, (
        f"Play turn narration not found in output: {combined!r}"
    )
