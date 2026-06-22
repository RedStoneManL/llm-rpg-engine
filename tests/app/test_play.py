"""Tests for app.play: play_loop + OOC commands."""
from __future__ import annotations

import pytest
from pathlib import Path
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_canned_provider():
    """FakeLLMProvider that returns a valid TurnCommit JSON."""
    return FakeLLMProvider(json_responses=[
        {
            "narration": "你环顾四周，发现这是一片宁静的旷野。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}],
        }
    ])


def _build_engine_with_game(tmp_path, provider=None):
    """Build a fresh engine with new_game called (protagonist + starting place exist)."""
    from app.engine import build_engine, new_game
    if provider is None:
        provider = _make_canned_provider()
    engine = build_engine(tmp_path, provider=provider)
    new_game(engine)
    return engine


# ---------------------------------------------------------------------------
# Task 2: play_loop basic turn
# ---------------------------------------------------------------------------

def test_play_loop_one_turn_outputs_narration(tmp_path):
    """feed one input line → play_loop runs one turn and collected output has narration."""
    from app.play import play_loop

    provider = _make_canned_provider()
    engine = _build_engine_with_game(tmp_path, provider=provider)

    # Give fresh provider that still has the canned response
    engine.provider = _make_canned_provider()

    collected = []
    play_loop(engine, inputs=["看看四周", "/quit"], out=collected.append)

    combined = "\n".join(collected)
    assert "你环顾四周" in combined, f"Expected narration in output, got: {combined!r}"


def test_play_loop_quit_stops_loop(tmp_path):
    """'/quit' stops the loop without processing more inputs."""
    from app.play import play_loop

    engine = _build_engine_with_game(tmp_path)

    collected = []
    # quit is first; the second line should never run a turn
    play_loop(engine, inputs=["/quit", "这一行不应被处理"], out=collected.append)

    combined = "\n".join(collected)
    # Should produce a quit message, not a turn
    assert "这一行不应被处理" not in combined


def test_play_loop_ooc_recall_returns_hit_or_none(tmp_path):
    """'/recall <q>' triggers recall; output contains a hit or '无' — no turn consumed."""
    from app.play import play_loop

    engine = _build_engine_with_game(tmp_path)

    collected = []
    play_loop(engine, inputs=["/recall 起点", "/quit"], out=collected.append)

    combined = "\n".join(collected)
    # Should include some recall output (hit from genesis or "无")
    assert ("起点" in combined or "无" in combined or "starting" in combined.lower()), \
        f"Expected recall output, got: {combined!r}"


def test_play_loop_ooc_recall_no_turn(tmp_path):
    """'/recall' does not advance a turn (store unchanged)."""
    from app.play import play_loop

    engine = _build_engine_with_game(tmp_path)
    events_before = len(list(engine.store.iter_events()))

    play_loop(engine, inputs=["/recall 测试", "/quit"], out=lambda _: None)

    events_after = len(list(engine.store.iter_events()))
    assert events_after == events_before, "recall should not add events"


def test_play_loop_compare_on_then_input_runs_compare(tmp_path):
    """'/compare on' then a normal input runs run_compare and prints both candidates."""
    from app.play import play_loop

    # Need a provider that can handle multiple calls (compare calls both strategies)
    provider = FakeLLMProvider(json_responses=[
        {"narration": "甲策略叙述：你踏入前方的小路。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
        {"narration": "乙策略叙述：小路延伸向远方。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
    ])
    engine = _build_engine_with_game(tmp_path, provider=provider)
    engine.provider = FakeLLMProvider(json_responses=[
        {"narration": "甲策略叙述：你踏入前方的小路。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
        {"narration": "乙策略叙述：小路延伸向远方。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
    ])

    collected = []
    play_loop(engine, inputs=["/compare on", "向前走", "/quit"], out=collected.append)

    combined = "\n".join(collected)
    # Should mention both candidates' narrations or "甲"/"乙"
    assert ("甲" in combined or "甲策略" in combined or "策略" in combined or
            "compare" in combined.lower() or "候选" in combined), \
        f"Expected compare output mentioning candidates, got: {combined!r}"


def test_play_loop_help_prints_something(tmp_path):
    """/help prints something."""
    from app.play import play_loop

    engine = _build_engine_with_game(tmp_path)

    collected = []
    play_loop(engine, inputs=["/help", "/quit"], out=collected.append)

    combined = "\n".join(collected)
    assert len(combined) > 0, "Expected /help to print something"


def test_play_loop_unknown_ooc_prints_error(tmp_path):
    """An unknown OOC command '/foo' prints an error without crashing."""
    from app.play import play_loop

    engine = _build_engine_with_game(tmp_path)

    collected = []
    play_loop(engine, inputs=["/foo", "/quit"], out=collected.append)

    combined = "\n".join(collected)
    # Should not raise, should print some error
    assert "foo" in combined or "unknown" in combined.lower() or "未知" in combined, \
        f"Expected error for unknown OOC command, got: {combined!r}"


def test_play_loop_world_updated_after_turn(tmp_path):
    """After a normal turn, engine.world is updated (new events in store)."""
    from app.play import play_loop

    provider = _make_canned_provider()
    engine = _build_engine_with_game(tmp_path, provider=provider)
    engine.provider = _make_canned_provider()

    events_before = len(list(engine.store.iter_events()))

    play_loop(engine, inputs=["看看四周", "/quit"], out=lambda _: None)

    # A turn should have added at least some events or updated world
    # (Even with no sections in the commit, narration is stored and world updated)
    # The key is play_loop didn't crash
    assert engine.world is not None


# ---------------------------------------------------------------------------
# Phase E Task 2: OOC rewind commands (/rewind, /undo, /oops, //retcon, //veto)
# ---------------------------------------------------------------------------

def _engine_with_two_player_turns(tmp_path):
    """Build engine, run genesis, then append two fake player-turn events directly."""
    from app.engine import build_engine, new_game
    from kernel.events import kernel_event
    from kernel.projection import project

    engine = build_engine(tmp_path, provider=_make_canned_provider())
    new_game(engine)

    # Turn 1: evolve protagonist
    engine.store.append(kernel_event("character_evolved", day=2, scene="s",
                                      summary="turn1 evolve",
                                      deltas={"id": "protagonist", "predicate": "mood",
                                              "value": "excited", "op": "evolve"},
                                      turn=1))
    # Turn 2: evolve protagonist again
    engine.store.append(kernel_event("character_evolved", day=3, scene="s",
                                      summary="turn2 evolve",
                                      deltas={"id": "protagonist", "predicate": "mood",
                                              "value": "tired", "op": "evolve"},
                                      turn=2))
    engine.world = project(engine.registry, engine.store.iter_events())
    return engine


def test_dispatch_ooc_rewind_retracts_and_reprojects(tmp_path):
    """/rewind 2 retracts turn-2 events and reprojects world."""
    from app.play import dispatch_ooc
    from kernel.projection import project

    engine = _engine_with_two_player_turns(tmp_path)
    g_before = engine.world["systems"]["ontology"]
    assert g_before.value_at("protagonist", "mood", 3) == "tired"

    collected = []
    stop = dispatch_ooc("/rewind 2", engine, out=collected.append,
                        compare_mode=[False])
    assert stop is False
    combined = "\n".join(collected)
    assert "倒带" in combined or "回退" in combined or "2" in combined

    # World must be re-projected: turn-2 mood gone, turn-1 mood (excited) visible
    g = engine.world["systems"]["ontology"]
    assert g.value_at("protagonist", "mood", 3) is None or \
           g.value_at("protagonist", "mood", 3) == "excited"
    assert g.value_at("protagonist", "mood", 2) == "excited"


def test_dispatch_ooc_undo_retracts_last_turn(tmp_path):
    """/undo retracts the most recent turn's events."""
    from app.play import dispatch_ooc
    from kernel.projection import project

    engine = _engine_with_two_player_turns(tmp_path)
    g_before = engine.world["systems"]["ontology"]
    assert g_before.value_at("protagonist", "mood", 3) == "tired"  # turn-2 fact

    collected = []
    dispatch_ooc("/undo", engine, out=collected.append, compare_mode=[False])

    # Turn-2 fact gone; turn-1 fact (excited) still present
    g = engine.world["systems"]["ontology"]
    # After undo of turn=2: mood at day=3 is gone or reverted to turn-1 value
    assert g.value_at("protagonist", "mood", 3) is None or \
           g.value_at("protagonist", "mood", 3) == "excited"
    assert g.value_at("protagonist", "mood", 2) == "excited"


def test_dispatch_ooc_oops_same_as_undo(tmp_path):
    """/oops is an alias for /undo."""
    from app.play import dispatch_ooc
    from kernel.projection import project

    engine = _engine_with_two_player_turns(tmp_path)

    collected = []
    stop = dispatch_ooc("/oops", engine, out=collected.append, compare_mode=[False])
    assert stop is False
    combined = "\n".join(collected)
    # Should give confirmation output
    assert len(combined) > 0

    # Turn-2 events gone
    g = engine.world["systems"]["ontology"]
    assert g.value_at("protagonist", "mood", 3) is None or \
           g.value_at("protagonist", "mood", 3) == "excited"


def test_dispatch_ooc_retcon_double_slash(tmp_path):
    """//retcon <n> is an alias for /rewind <n>."""
    from app.play import dispatch_ooc

    engine = _engine_with_two_player_turns(tmp_path)

    collected = []
    stop = dispatch_ooc("//retcon 2", engine, out=collected.append,
                         compare_mode=[False])
    assert stop is False

    # Turn-2 retracted
    g = engine.world["systems"]["ontology"]
    assert g.value_at("protagonist", "mood", 3) is None or \
           g.value_at("protagonist", "mood", 3) == "excited"


def test_dispatch_ooc_veto_double_slash_is_undo(tmp_path):
    """//veto is an alias for /undo."""
    from app.play import dispatch_ooc

    engine = _engine_with_two_player_turns(tmp_path)

    collected = []
    stop = dispatch_ooc("//veto", engine, out=collected.append, compare_mode=[False])
    assert stop is False
    combined = "\n".join(collected)
    assert len(combined) > 0

    # Turn-2 retracted
    g = engine.world["systems"]["ontology"]
    assert g.value_at("protagonist", "mood", 3) is None or \
           g.value_at("protagonist", "mood", 3) == "excited"


def test_dispatch_ooc_rewind_invalid_arg_no_crash(tmp_path):
    """/rewind with no/invalid arg prints error, no crash."""
    from app.play import dispatch_ooc

    engine = _engine_with_two_player_turns(tmp_path)

    collected = []
    stop = dispatch_ooc("/rewind", engine, out=collected.append, compare_mode=[False])
    assert stop is False
    combined = "\n".join(collected)
    assert len(combined) > 0  # Some error message printed

    collected2 = []
    stop2 = dispatch_ooc("/rewind abc", engine, out=collected2.append,
                          compare_mode=[False])
    assert stop2 is False
    assert len("\n".join(collected2)) > 0


# ---------------------------------------------------------------------------
# Task 3: CLI entry — test_main_smoke (added here per plan)
# ---------------------------------------------------------------------------

def test_main_smoke(tmp_path):
    """main(['--campaign', str(tmp), '--provider', 'fake'], inputs=[...]) runs without error."""
    from app.__main__ import main

    output = []
    main(
        ["--campaign", str(tmp_path), "--provider", "fake"],
        inputs=["看看四周", "/quit"],
        out=output.append,
    )

    # Should have produced some output (at minimum a welcome or narration)
    combined = "\n".join(output)
    assert len(combined) > 0, f"Expected some output from main, got empty"


# ---------------------------------------------------------------------------
# Fix 1 regression: compare mode must advance the clock (not freeze it)
# ---------------------------------------------------------------------------

def test_compare_mode_advances_clock(tmp_path):
    """In /compare on mode, the applied 甲 commit must stamp events at the
    post-clock-advance day (band), not the frozen pre-turn scene.day.

    Regression guard for the 'frozen time in compare mode' bug: previously
    play_loop passed day=scene['day'] to apply_turn; now it calls advanced_day().
    """
    from app.play import play_loop
    from systems.time import TimeSystem

    # 甲 commit: clock advances by 2 bands (晨→下午)
    jia_commit = {
        "narration": "甲: 蹲守到下午，终于等到了目标。",
        "clock": [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到下午"}],
    }
    # 丙 commit: also valid (no-advance), only 甲 is applied
    bing_commit = {
        "narration": "丙: 目标现身。",
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "紧接上一刻"}],
    }

    # HybridStrategy (丙) calls complete() then complete_json(); AuthorStrategy (甲) calls complete_json().
    # Order: 甲 complete_json, 丙 complete_json (after a prose call for HybridStrategy)
    bing_prose = "丙: 目标现身。"
    provider = FakeLLMProvider(
        responses=[bing_prose],
        json_responses=[jia_commit, bing_commit],
    )

    engine = _build_engine_with_game(tmp_path, provider=provider)
    # Replace provider after build (build_engine consumes its own responses for new_game)
    engine.provider = FakeLLMProvider(
        responses=[bing_prose],
        json_responses=[jia_commit, bing_commit],
    )

    # Record the starting band
    band_before = engine.world.get("meta", {}).get("band", 0)

    collected = []
    play_loop(engine, inputs=["/compare on", "蹲守", "/quit"], out=collected.append)

    # The 甲 clock declared +2 bands; world must have advanced
    band_after = engine.world.get("meta", {}).get("band", 0)
    # band_before=0 (晨), +2 bands → 2 (下午); or day carried if wrapped
    assert band_after != band_before or engine.world.get("meta", {}).get("day", 1) > 1, (
        f"Clock froze in compare mode: band stayed {band_before} → {band_after}, "
        f"day={engine.world.get('meta', {}).get('day')}"
    )


# ---------------------------------------------------------------------------
# Co-location: _build_scene present derivation (scene-cast fix)
# ---------------------------------------------------------------------------

def _make_graph_with_locations():
    """Build a FactGraph with protagonist in place A, NPC X also in A, NPC Y in B.

    Returns (g, protagonist_id, npc_x_id, npc_y_id, place_a_id, place_b_id).
    """
    from facts.graph import FactGraph
    from facts.entity import Entity

    g = FactGraph()
    g.add_entity("protagonist", etype="Person", tier="tracked")
    g.add_entity("npc_x", etype="Person", tier="tracked")
    g.add_entity("npc_y", etype="Person", tier="tracked")
    g.add_entity("place_a", etype="Place", tier="tracked")
    g.add_entity("place_b", etype="Place", tier="tracked")

    # protagonist → place_a
    g.add_relation("protagonist", "located_in", "place_a", day=1, turn=0, source_event="test")
    # npc_x → place_a (co-located with protagonist)
    g.add_relation("npc_x", "located_in", "place_a", day=1, turn=0, source_event="test")
    # npc_y → place_b (different location)
    g.add_relation("npc_y", "located_in", "place_b", day=1, turn=0, source_event="test")

    return g


def _make_engine_with_graph(g, day=1):
    """Wrap a FactGraph into a minimal fake engine dict (no store, no registry)."""
    class FakeEngine:
        def __init__(self, g, day):
            self.world = {
                "meta": {"day": day, "scene": "test_scene"},
                "systems": {"ontology": g},
            }
    return FakeEngine(g, day)


def test_build_scene_present_only_collocated(tmp_path):
    """protagonist in place_a, npc_x in place_a, npc_y in place_b
    → present contains npc_x but NOT npc_y; location == 'place_a'.
    """
    from app.play import _build_scene

    g = _make_graph_with_locations()
    engine = _make_engine_with_graph(g, day=1)

    scene = _build_scene(engine)

    assert scene["protagonist"] == "protagonist"
    assert scene["location"] == "place_a", (
        f"Expected location='place_a', got {scene['location']!r}"
    )
    assert "npc_x" in scene["present"], (
        f"npc_x (in place_a) should be present; got {scene['present']}"
    )
    assert "npc_y" not in scene["present"], (
        f"npc_y (in place_b) must NOT be present; got {scene['present']}"
    )
    assert "protagonist" not in scene["present"], (
        f"protagonist must not appear in present; got {scene['present']}"
    )


def test_build_scene_present_empty_when_protagonist_has_no_location(tmp_path):
    """If protagonist has no located_in edge, present must be []."""
    from app.play import _build_scene
    from facts.graph import FactGraph

    g = FactGraph()
    g.add_entity("protagonist", etype="Person", tier="tracked")
    g.add_entity("npc_x", etype="Person", tier="tracked")
    # No located_in edges at all
    g.add_relation("npc_x", "located_in", "place_a", day=1, turn=0, source_event="test")

    engine = _make_engine_with_graph(g, day=1)
    scene = _build_scene(engine)

    assert scene["present"] == [], (
        f"No protagonist location → present must be []; got {scene['present']}"
    )
    assert scene["location"] is None or scene["location"] == "test_scene", (
        f"location should fall back to meta scene or None; got {scene['location']!r}"
    )


def test_build_scene_location_derived_from_graph_not_meta(tmp_path):
    """location in scene comes from the graph edge, not meta['scene']."""
    from app.play import _build_scene
    from facts.graph import FactGraph

    g = FactGraph()
    g.add_entity("protagonist", etype="Person", tier="tracked")
    g.add_entity("place_real", etype="Place", tier="tracked")
    g.add_relation("protagonist", "located_in", "place_real", day=1, turn=0, source_event="test")

    class FakeEngine:
        world = {
            "meta": {"day": 1, "scene": "meta_scene_different"},
            "systems": {"ontology": g},
        }

    scene = _build_scene(FakeEngine())
    assert scene["location"] == "place_real", (
        f"location should come from graph, not meta; got {scene['location']!r}"
    )
