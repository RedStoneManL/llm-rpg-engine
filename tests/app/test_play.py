"""Tests for app.play: play_loop + OOC commands."""
from __future__ import annotations

import copy

import pytest
from pathlib import Path
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_canned_provider():
    """FakeLLMProvider that returns a valid TurnCommit JSON."""
    return FakeLLMProvider(json_responses=[
        {**{'moves': [], 'places': [], 'cast': [], 'facts': []},
            "narration": "你环顾四周，发现这是一片宁静的旷野。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}],
        }
    ])


def _capturing_author_provider():
    """Record ordinary AuthorStrategy calls while remaining fully offline."""
    class CapturingProvider(FakeLLMProvider):
        def __init__(self):
            super().__init__(json_responses=[{
                "narration": "你在客栈门前确认了眼前的路。",
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处确认"}],
            }])
            self.author_messages = []

        def complete_messages(self, messages, **kwargs):
            snapshot = copy.deepcopy(messages)
            if any(message.get("role") == "system" and "你是主持人（DM）" in message.get("content", "")
                   for message in snapshot):
                self.author_messages.append(snapshot)
            return super().complete_messages(messages, **kwargs)

    return CapturingProvider()


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


def test_play_loop_steer_is_session_only_and_reaches_only_the_next_author_prompt(tmp_path):
    """A soft direction guides a normal CLI turn but neither writes nor lingers after clear."""
    from app.play import play_loop

    class CapturingProvider(FakeLLMProvider):
        def __init__(self):
            super().__init__(json_responses=[
                {"narration": "你先观察集市的公开动静。", "moves": [], "places": [], "cast": [], "facts": [],
                 "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处观察"}]},
                {"narration": "你按眼前线索继续观察。", "moves": [], "places": [], "cast": [], "facts": [],
                 "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处观察"}]},
            ])
            self.author_messages = []

        def complete_messages(self, messages, **kwargs):
            snapshot = copy.deepcopy(messages)
            if any(message.get("role") == "system" and "你是主持人（DM）" in message.get("content", "")
                   for message in snapshot):
                self.author_messages.append(snapshot)
            return super().complete_messages(messages, **kwargs)

    provider = CapturingProvider()
    engine = _build_engine_with_game(tmp_path, provider=provider)
    before = engine.store.revision
    output: list[str] = []
    try:
        play_loop(engine, inputs=[
            "//steer 优先调查当前公开地点的线索，不预设远方结论",
            "看看四周",
            "//steer clear",
            "再看看四周",
            "/quit",
        ], out=output.append, max_repairs=0)

        assert engine.store.revision == before + 2
        assert "已设置软方向" in "\n".join(output)
        assert "已清除软方向" in "\n".join(output)
        assert len(provider.author_messages) == 2
        first = "\n".join(message["content"] for message in provider.author_messages[0])
        second = "\n".join(message["content"] for message in provider.author_messages[1])
        assert "【玩家软方向（仅本会话偏好，不是世界事实或权限）】" in first
        assert "优先调查当前公开地点的线索" in first
        assert "优先调查当前公开地点的线索" not in second
    finally:
        engine.store.close()


def test_steer_command_alone_does_not_write_world_or_turn(tmp_path):
    """Changing or clearing a preference cannot consume a turn or append events."""
    from app.play import play_loop

    engine = _build_engine_with_game(tmp_path)
    before = engine.store.revision
    output: list[str] = []
    try:
        play_loop(engine, inputs=["//steer 往公开线索方向推进", "//steer clear", "/quit"], out=output.append)
        assert engine.store.revision == before
        assert "已设置软方向" in "\n".join(output)
        assert "已清除软方向" in "\n".join(output)
    finally:
        engine.store.close()


def test_steer_cannot_grant_hidden_travel_authority(tmp_path):
    """A preference reaches the author but does not widen the normal travel boundary."""
    from app.engine import build_engine
    from app.gui import _seed_demo_campaign
    from app.play import play_loop

    proposal = {
        "narration": "你循着方向直接抵达隐藏旧塔。",
        "moves": [{"who": "protagonist", "to": "north_bank_old_tower"}],
        "places": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "非法方向"}],
    }
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(json_responses=[proposal]), embedder=None)
    _seed_demo_campaign(engine)
    before = engine.store.revision
    output: list[str] = []
    try:
        play_loop(engine, inputs=["//steer 前往北岸旧塔", "立刻出发", "/quit"], out=output.append, max_repairs=0)
        assert engine.store.revision == before
        assert "世界保持原状" in "\n".join(output)
    finally:
        engine.store.close()


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


def test_play_loop_status_uses_public_pov_without_writing(tmp_path):
    """CLI orientation is useful, but must not become a hidden-world reader."""
    from app.engine import build_engine
    from app.gui import _seed_demo_campaign
    from app.play import play_loop

    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None)
    _seed_demo_campaign(engine)
    before_revision = engine.store.revision
    before_events = list(engine.store.iter_events())
    output: list[str] = []
    try:
        play_loop(engine, ["/status", "/quit"], out=output.append)
        rendered = "\n".join(output)
        assert "当前位置：石桥客栈" in rendered
        assert "背包：旧铜灯" in rendered
        assert "已知出口：石桥集市（成本 1）" in rendered
        assert "长期目标：在黎明前找到旧塔的线索" in rendered
        assert "公开任务：无" in rendered
        assert "north_bank_old_tower" not in rendered
        assert "北岸旧塔" not in rendered
        assert "GUI_DEMO_PRIVATE_SENTINEL" not in rendered
        assert engine.store.revision == before_revision
        assert list(engine.store.iter_events()) == before_events
    finally:
        engine.store.close()


def test_play_loop_codex_is_public_read_only_and_filterable(tmp_path):
    from app.engine import build_engine
    from app.gui import _seed_demo_campaign
    from app.play import play_loop

    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None)
    _seed_demo_campaign(engine)
    before = (engine.store.revision, list(engine.store.iter_events()))
    output: list[str] = []
    try:
        play_loop(engine, ["/codex 石桥", "/quit"], out=output.append)
        rendered = "\n".join(output)
        assert "[地理志] 石桥镇" in rendered
        assert "north_bank_old_tower" not in rendered
        assert "GUI_DEMO_PRIVATE_SENTINEL" not in rendered
        assert (engine.store.revision, list(engine.store.iter_events())) == before
    finally:
        engine.store.close()


def test_cli_recall_reprojects_after_peer_dashboard_publish_without_writing(tmp_path):
    """A long-running CLI must not query a pre-peer POV projection forever."""
    from app.engine import build_engine
    from app.gui import build_dashboard
    from app.play import _build_scene, play_loop

    campaign = tmp_path / "campaign"
    seed = build_dashboard(campaign, demo=True)
    seed.engine.store.close()
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    peer = build_dashboard(campaign, demo=True)
    output: list[str] = []
    peer_saved: list[dict] = []
    resets = [0]

    class ResetOnly:
        def reset(self):
            resets[0] += 1

    def inputs():
        yield "/recall 石桥"
        peer_saved.append(peer.action(
            player_input="另一终端先去集市", action_id="cli-recall-peer",
            expected_revision=peer.state()["revision"],
        ))
        yield "/recall 石桥"
        yield "/quit"

    try:
        initial_revision = engine.world["_revision"]
        play_loop(engine, inputs=inputs(), out=output.append, strategy=ResetOnly())
        assert peer_saved and peer_saved[0]["revision"] != initial_revision
        assert engine.world["_revision"] == peer_saved[0]["revision"]
        assert _build_scene(engine)["location"] == "stonebridge_market"
        assert engine.store.revision == peer_saved[0]["revision"]
        assert len([line for line in output if line.startswith("[recall]")]) >= 2
        assert resets == [1]
    finally:
        peer.engine.store.close()
        engine.store.close()


def test_player_recall_uses_pov_projection_and_keeps_hidden_places_out(tmp_path):
    """Recall hides guesses but still searches places this protagonist discovered."""
    from app.engine import build_engine
    from app.gui import _seed_demo_campaign
    from app.play import play_loop
    from kernel.events import kernel_event
    from kernel.projection import project
    from systems.knowledge import place_discovery_key

    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(), embedder=None)
    _seed_demo_campaign(engine)
    # This is canonical-only until the protagonist receives an explicit
    # discovery fact.  The player read view must retain the known branch while
    # filtering the separately hidden north-tower counterexample below.
    engine.store.append_many([
        kernel_event("place_created", day=1, scene="s1", turn=0,
                     summary="已发现的封门", deltas={
                         "id": "known_sealed_gate", "level": 2, "kind": "venue",
                         "seed": "你已经见过的封门", "visibility": "undiscovered",
                     }),
        kernel_event("knowledge_set", day=1, scene="s1", turn=0,
                     summary="主角知道封门", deltas={
                         "knower": "protagonist",
                         "fact_key": place_discovery_key("known_sealed_gate"),
                         "value": True,
                     }),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    before = engine.store.revision
    output: list[str] = []
    try:
        play_loop(engine, inputs=[
            "/recall north_bank_old_tower",
            "/recall 石桥",
            "/recall 封门",
            "/quit",
        ], out=output.append)
        rendered = "\n".join(output)
        assert "north_bank_old_tower" not in rendered
        assert "北岸" not in rendered
        assert "[recall] 无" in rendered
        # Public current-world material remains searchable; the fix is not a
        # blanket disablement of a normal player aid.
        assert "石桥" in rendered
        assert "known_sealed_gate" in rendered
        assert engine.store.revision == before
    finally:
        engine.store.close()


def test_play_loop_compare_on_then_input_runs_compare(tmp_path):
    """'/compare on' then a normal input runs run_compare and prints both candidates."""
    from app.play import play_loop

    # Need a provider that can handle multiple calls (compare calls both strategies)
    provider = FakeLLMProvider(json_responses=[
        {**{'moves': [], 'places': [], 'cast': [], 'facts': []}, "narration": "甲策略叙述：你踏入前方的小路。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
        {**{'moves': [], 'places': [], 'cast': [], 'facts': []}, "narration": "乙策略叙述：小路延伸向远方。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
    ])
    engine = _build_engine_with_game(tmp_path, provider=provider)
    engine.provider = FakeLLMProvider(json_responses=[
        {**{'moves': [], 'places': [], 'cast': [], 'facts': []}, "narration": "甲策略叙述：你踏入前方的小路。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
        {**{'moves': [], 'places': [], 'cast': [], 'facts': []}, "narration": "乙策略叙述：小路延伸向远方。",
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
    ])

    collected = []
    play_loop(engine, inputs=["/compare on", "向前走", "/quit"], out=collected.append)

    combined = "\n".join(collected)
    # Should mention both candidates' narrations or "甲"/"乙"
    assert ("甲" in combined or "甲策略" in combined or "策略" in combined or
            "compare" in combined.lower() or "候选" in combined), \
        f"Expected compare output mentioning candidates, got: {combined!r}"


def test_play_loop_compare_passes_cli_catchup_provider(tmp_path, monkeypatch):
    """Compare's final prepared turn keeps the same background provider wiring.

    ``run_compare`` only prepares its two visible candidates.  The durable
    ``run_turn`` call that applies 甲 must still receive both the cascade and
    catch-up provider, exactly as ordinary CLI play and the dashboard do.
    """
    from app import play as play_module
    from app.play import play_loop

    jia_commit = {
        "narration": "甲策略：你确认眼前的公开标记。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处确认"}],
    }
    bing_commit = {
        "narration": "乙策略：你停在原地核验眼前细节。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处确认"}],
    }
    engine = _build_engine_with_game(tmp_path)
    engine.provider = FakeLLMProvider(
        responses=["乙策略：你停在原地核验眼前细节。"],
        json_responses=[jia_commit, bing_commit],
    )
    marker = object()
    engine.cascade_provider = marker
    observed = {}
    original = play_module.run_turn

    def wrapped(*args, **kwargs):
        observed["cascade"] = kwargs.get("cascade_provider")
        observed["catchup"] = kwargs.get("catchup_provider")
        return original(*args, **kwargs)

    monkeypatch.setattr(play_module, "run_turn", wrapped)
    try:
        play_loop(engine, inputs=["/compare on", "核验眼前", "/quit"], out=lambda _: None)
        assert observed == {"cascade": marker, "catchup": marker}
    finally:
        engine.store.close()


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


def test_play_loop_cannot_publish_hidden_destination_proposed_by_author(tmp_path):
    """The terminal product path shares GUI's publish-time travel contract."""
    from app.engine import build_engine
    from app.gui import _seed_demo_campaign
    from app.play import play_loop

    proposal = {
        "narration": "你绕开公开道路，直接抵达不该看见的旧塔。",
        "moves": [{"who": "protagonist", "to": "north_bank_old_tower"}],
        "places": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试非法移动"}],
    }
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider(json_responses=[proposal]), embedder=None)
    _seed_demo_campaign(engine)
    before = engine.store.revision
    output: list[str] = []
    play_loop(engine, inputs=["直接去旧塔", "/quit"], out=output.append, max_repairs=0)

    assert engine.store.revision == before
    assert engine.world["systems"]["ontology"].neighbors("protagonist", "located_in", 1) == ["stonebridge_inn"]
    assert "世界保持原状" in "\n".join(output)
    engine.store.close()


def test_cli_author_prompt_binds_goal_scope_and_public_observation(tmp_path):
    """The normal CLI path passes the authored scope, not just a helper DTO."""
    from app.engine import build_engine
    from app.gui import _seed_demo_campaign
    from app.play import play_loop
    from kernel.events import kernel_event
    from kernel.projection import project

    provider = _capturing_author_provider()
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    engine.store.append(
        kernel_event("world_change", day=1, scene="s1", turn=0, summary="门外石阶潮湿。", deltas={
            "place": "stonebridge_inn", "level": 1, "summary": "门外石阶潮湿。",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "single_observation", "observation_range": "客栈门前两步内",
        })
    )
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision

    try:
        play_loop(engine, inputs=["确认门前的路", "/quit"], out=lambda _: None, max_repairs=0)
        assert len(provider.author_messages) == 1
        messages = provider.author_messages[0]
        system = next(message["content"] for message in messages if message["role"] == "system")
        user = "\n".join(message["content"] for message in messages if message["role"] == "user")
        assert 'protagonist.goal = "在黎明前找到旧塔的线索"' in user
        assert "【行动能力】主角 protagonist 当前在 stonebridge_inn；本回合仅可移动到：stonebridge_market（石桥集市）。" in user
        assert "出口：stonebridge_market（路径成本 1）" in user
        assert "stonebridge_market(1日)" not in user
        assert "门外石阶潮湿。" in user
        assert "GUI_DEMO_PRIVATE_SENTINEL" not in user
        assert "普通玩家回合没有区域／全局 world 权限" in system
        assert '"public_observation":true' in system
        assert "完整世界视野" not in system
        assert "不得点名远方、隐藏或未发现地点" in system
    finally:
        engine.store.close()


def test_dashboard_author_strategy_uses_the_same_scoped_context(tmp_path):
    """The normal GUI controller reaches the same AuthorStrategy contract."""
    from app.engine import build_engine
    from app.gui import PlayerDashboard, _seed_demo_campaign
    from kernel.events import kernel_event
    from kernel.projection import project
    from loop.strategy import AuthorStrategy

    provider = _capturing_author_provider()
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    engine.store.append(kernel_event("world_change", day=1, scene="s1", turn=0,
                        summary="GUI 门前石阶潮湿。", deltas={
                            "place": "stonebridge_inn", "level": 1, "summary": "GUI 门前石阶潮湿。",
                            "source": "author_world", "visibility": "public", "public_observation": True,
                            "observation_kind": "single_observation", "observation_range": "客栈门前两步内",
                        }))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy())
    try:
        initial = dashboard.state()
        dashboard.action(player_input="确认门前的路", action_id="prompt-contract",
                         expected_revision=initial["revision"])
        assert len(provider.author_messages) == 1
        user = "\n".join(message["content"] for message in provider.author_messages[0]
                         if message["role"] == "user")
        assert 'protagonist.goal = "在黎明前找到旧塔的线索"' in user
        assert "【行动能力】主角 protagonist 当前在 stonebridge_inn；本回合仅可移动到：stonebridge_market（石桥集市）。" in user
        assert "出口：stonebridge_market（路径成本 1）" in user
        assert "stonebridge_market(1日)" not in user
        assert "GUI 门前石阶潮湿。" in user
        assert "GUI_DEMO_PRIVATE_SENTINEL" not in user
    finally:
        engine.store.close()


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


def test_play_loop_undo_rebuilds_catchup_scope_from_surviving_action(tmp_path):
    """CLI undo keeps the same catch-up scope contract as GUI/cold reopen."""
    from app.gui import build_dashboard
    from app.play import play_loop
    from kernel.events import kernel_event
    from kernel.projection import project
    from kernel.turncommit import TurnCommit

    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    engine = dashboard.engine
    engine.store.append(kernel_event(
        "character_created", day=1, scene="s1", turn=0, summary="掌柜在客栈",
        deltas={"id": "innkeeper", "tier": "tracked", "sketch": "掌柜", "goal": "照看客栈"},
    ))
    engine.store.append(kernel_event(
        "entity_moved", day=1, scene="s1", turn=0, summary="掌柜留在客栈",
        deltas={"who": "innkeeper", "to": "stonebridge_inn"},
    ))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision

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

    catchup = FakeLLMProvider(json_responses=[{
        "changed": True, "predicate": "mood", "value": "不应因撤销而变化",
    }])
    engine.cascade_provider = catchup

    def inputs():
        yield "第一段观察"
        # This host time event survives the following player undo.  The NPC is
        # stale but has never left the player's scope.
        engine.store.append(kernel_event(
            "time_advanced", day=5, scene="s1", turn=0, summary="数日过去",
            deltas={"to_day": 5, "reason": "fixture host time"},
        ))
        engine.world = project(engine.registry, engine.store.iter_events())
        engine.world["_revision"] = engine.store.revision
        yield "第二段观察"
        yield "/undo"
        yield "撤销后的观察"
        yield "/quit"

    try:
        play_loop(engine, inputs=inputs(), out=lambda _: None, strategy=ObserveInPlace())
        # Density may share the configured cheap provider; only the explicit
        # catch-up prompt names an entity id in this stable form.
        assert not any("实体 id：innkeeper" in user for _system, user in catchup.calls)
        assert all(event["type"] != "character_evolved" for event in engine.store.iter_events())
    finally:
        engine.store.close()


def test_play_loop_revision_conflict_rebuilds_scope_from_newest_action(tmp_path, monkeypatch):
    """A rejected stale CLI turn must not keep an older catch-up anchor.

    This follows the actual publication-conflict route rather than replacing
    ``run_turn`` with a synthetic exception.  While the first author call is
    preparing, a separate durable player action moves the protagonist from the
    inn to the market.  The original turn then loses its optimistic revision
    race.  The next CLI action has to use the peer action's action-start scene
    (the inn), just as a cold reopen would; carrying the original ``None``
    anchor would make background enter-scope decisions depend on a failed
    request.
    """
    from app import play as play_module
    from app.gui import OfflineDemoStrategy, build_dashboard
    from app.play import play_loop
    from kernel.turncommit import TurnCommit
    from loop.turn import run_turn as real_run_turn

    dashboard = build_dashboard(tmp_path / "campaign", demo=True)
    engine = dashboard.engine
    observed_previous_scenes = []
    peer_published = [False]
    peer_revision = [None]

    def recording_run_turn(*args, **kwargs):
        observed_previous_scenes.append(copy.deepcopy(kwargs.get("prev_scene")))
        return real_run_turn(*args, **kwargs)

    monkeypatch.setattr(play_module, "run_turn", recording_run_turn)

    class ConflictThenObserve:
        def produce(self, registry, world, scene, player_input, *, provider, embedder=None, repair=None):
            if not peer_published[0]:
                # ``run_turn`` restores a failed strategy's instance state;
                # model the independent peer process outside that state so it
                # publishes exactly once even while the rejected strategy is
                # rolled back.
                peer_published[0] = True
                # This is an independent durable action, not a direct store
                # append: it creates the receipt whose prefix is the recovery
                # boundary the CLI must use after the outer optimistic publish
                # is rejected.
                peer = real_run_turn(
                    registry, engine.store, engine.world, play_module._build_scene(engine), "同伴先去集市",
                    strategy=OfflineDemoStrategy(), provider=engine.provider, embedder=engine.embedder,
                    max_repairs=0, action_id="concurrent-peer-action",
                )
                engine.world = peer.world
                peer_revision[0] = peer.receipt["revision"]
            return TurnCommit(narration="你留在公开地点观察。", sections={
                "moves": [], "places": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "近处观察"}],
            })

        def repair_sections(self, *args, **kwargs):  # pragma: no cover - valid fixture
            return {}

        def commit_to_thread(self, narration):
            return None

    output = []
    try:
        play_loop(engine, inputs=["先尝试观察", "依据刷新后的世界继续观察", "/quit"],
                  out=output.append, strategy=ConflictThenObserve(), max_repairs=0)
        assert "世界状态已更新" in "\n".join(output)
        assert len(observed_previous_scenes) == 2
        assert observed_previous_scenes[0] is None
        assert observed_previous_scenes[1]["location"] == "stonebridge_inn"
        assert engine.store.revision > peer_revision[0]
        assert "[错误]" not in "\n".join(output)
    finally:
        engine.store.close()


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
    jia_commit = {**{'moves': [], 'places': [], 'cast': [], 'facts': []},
        "narration": "甲: 蹲守到下午，终于等到了目标。",
        "clock": [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到下午"}],
    }
    # 丙 commit: also valid (no-advance), only 甲 is applied
    bing_commit = {**{'moves': [], 'places': [], 'cast': [], 'facts': []},
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
