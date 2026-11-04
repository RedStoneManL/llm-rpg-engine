"""Tests for loop.strategy: TurnStrategy ABC + AuthorStrategy (甲) + HybridStrategy (丙)."""
from __future__ import annotations

import pytest

from kernel.registry import Registry
from kernel.projection import project, empty_world
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem


def _make_registry():
    registry = Registry()
    registry.register(OntologySystem())
    registry.register(PlaceSystem())
    return registry


def _make_world(registry):
    return empty_world(registry)


def _make_scene():
    return {"protagonist": "hero", "present": [], "day": 1, "location": "town"}


def test_hybrid_strategy_plans_then_finalizes_after_repairs():
    """Direct produce is a proposal; only finalization asks for visible prose."""
    from loop.strategy import HybridStrategy, _NARRATE_PROMPT, _SYSTEM_PROMPT_HYBRID

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()
    provider = FakeLLMProvider(responses=["丙散文"],
                               json_responses=[{"moves": []}, {"moves": []}])
    strat = HybridStrategy()
    c1 = strat.produce(registry, world, scene, "走", provider=provider)
    c2 = strat.produce(registry, world, scene, "走", provider=provider, repair="补 moves.who")
    assert c1.sections == c2.sections == {"moves": []}
    assert not any(system == _NARRATE_PROMPT for system, _ in provider.calls)
    assert strat.finalize_narration(c2, provider=provider) == "丙散文"
    assert [system for system, _ in provider.calls] == [
        _SYSTEM_PROMPT_HYBRID, _SYSTEM_PROMPT_HYBRID, _NARRATE_PROMPT]


def test_session_steer_is_explicitly_marked_in_author_and_hybrid_prompts():
    """A CLI preference is prompt context only, never implicit world authority."""
    from loop.strategy import AuthorStrategy, HybridStrategy, _NARRATE_PROMPT, _SYSTEM_PROMPT_HYBRID

    registry = _make_registry()
    world = _make_world(registry)
    scene = {**_make_scene(), "_player_steer": "优先核实当前公开地点的线索"}
    author_provider = FakeLLMProvider(json_responses=[{"narration": "甲", "moves": []}])
    AuthorStrategy().produce(registry, world, scene, "观察周围", provider=author_provider)
    _, author_user = author_provider.calls[0]
    assert "【玩家软方向（仅本会话偏好，不是世界事实或权限）】" in author_user
    assert "优先核实当前公开地点的线索" in author_user

    hybrid_provider = FakeLLMProvider(responses=["丙散文"], json_responses=[{"moves": []}])
    hybrid = HybridStrategy()
    proposal = hybrid.produce(registry, world, scene, "观察周围", provider=hybrid_provider)
    hybrid.finalize_narration(proposal, provider=hybrid_provider)
    narrate_users = [user for system, user in hybrid_provider.calls if system == _NARRATE_PROMPT]
    struct_users = [user for system, user in hybrid_provider.calls if system == _SYSTEM_PROMPT_HYBRID]
    assert len(narrate_users) == len(struct_users) == 1
    assert "优先核实当前公开地点的线索" in narrate_users[0]
    assert "优先核实当前公开地点的线索" in struct_users[0]


# ---------------------------------------------------------------------------
# Test: AuthorStrategy.produce returns a TurnCommit with canned narration
# ---------------------------------------------------------------------------

def test_author_strategy_produce_returns_turncommit():
    """AuthorStrategy.produce returns a TurnCommit with the canned narration+sections."""
    from loop.strategy import AuthorStrategy

    canned = {
        "narration": "The hero steps forward bravely.",
        "entities": [{"id": "hero", "etype": "Person"}],
    }
    provider = FakeLLMProvider(json_responses=[canned])
    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    strategy = AuthorStrategy()
    result = strategy.produce(registry, world, scene, "I move north", provider=provider)

    assert isinstance(result, TurnCommit)
    assert result.narration == "The hero steps forward bravely."
    assert "entities" in result.sections
    assert result.sections["entities"] == [{"id": "hero", "etype": "Person"}]


def test_author_strategy_records_call():
    """AuthorStrategy.produce should record exactly one call on the provider."""
    from loop.strategy import AuthorStrategy

    canned = {"narration": "Done.", "entities": []}
    provider = FakeLLMProvider(json_responses=[canned])
    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    strategy = AuthorStrategy()
    strategy.produce(registry, world, scene, "hello", provider=provider)

    assert len(provider.calls) == 1


def test_author_strategy_repair_in_user_prompt():
    """When repair= is passed, the repair text must appear in the user prompt."""
    from loop.strategy import AuthorStrategy

    canned1 = {"narration": "First try.", "entities": []}
    canned2 = {"narration": "Repaired.", "entities": []}
    provider = FakeLLMProvider(json_responses=[canned1, canned2])
    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    strategy = AuthorStrategy()
    # First call without repair
    strategy.produce(registry, world, scene, "action1", provider=provider)
    # Second call with repair
    repair_text = "turn-commit 校验未过,只修正以下字段后重发:"
    result2 = strategy.produce(registry, world, scene, "action1",
                               provider=provider, repair=repair_text)

    assert result2.narration == "Repaired."
    # The second call's user prompt should contain the repair text
    assert len(provider.calls) == 2
    _, user_prompt2 = provider.calls[1]
    assert repair_text in user_prompt2


def test_author_strategy_player_input_in_user_prompt():
    """Player input string should appear in the user prompt."""
    from loop.strategy import AuthorStrategy

    canned = {"narration": "OK.", "entities": []}
    provider = FakeLLMProvider(json_responses=[canned])
    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    strategy = AuthorStrategy()
    strategy.produce(registry, world, scene, "go north to the tower",
                     provider=provider)

    _, user_prompt = provider.calls[0]
    assert "go north to the tower" in user_prompt


def test_author_strategy_abc_cannot_be_instantiated_directly():
    """TurnStrategy is an ABC and cannot be instantiated."""
    from loop.strategy import TurnStrategy
    import inspect
    assert inspect.isabstract(TurnStrategy)


# ---------------------------------------------------------------------------
# Normal player-author contract: both strategies must describe the same
# publishable local-observation boundary rather than an obsolete GM contract.
# ---------------------------------------------------------------------------

def test_world_section_in_author_prompt_is_local_public_observation_only():
    from loop.strategy import _SYSTEM_PROMPT
    assert "world:" in _SYSTEM_PROMPT
    assert "areas" in _SYSTEM_PROMPT
    assert "本地公开观察" in _SYSTEM_PROMPT
    assert '"level":1' in _SYSTEM_PROMPT
    assert '"public_observation":true' in _SYSTEM_PROMPT
    assert '"secrecy":"secret"' not in _SYSTEM_PROMPT


def test_world_section_in_hybrid_prompt_is_not_an_omniscient_author_contract():
    from loop.strategy import _SYSTEM_PROMPT_HYBRID
    assert "world:" in _SYSTEM_PROMPT_HYBRID
    assert "areas" in _SYSTEM_PROMPT_HYBRID
    assert "本地公开观察" in _SYSTEM_PROMPT_HYBRID
    assert "完整世界视野" not in _SYSTEM_PROMPT_HYBRID
    assert "可点名任意位置" not in _SYSTEM_PROMPT_HYBRID
    assert '"secrecy":"public"' in _SYSTEM_PROMPT_HYBRID
    assert '"secrecy":"secret"' not in _SYSTEM_PROMPT_HYBRID


def test_world_section_is_optional_not_required():
    from loop.turn import REQUIRED_SECTIONS
    assert "world" not in REQUIRED_SECTIONS


# ---------------------------------------------------------------------------
# P3a Task 7: AuthorStrategy research-then-write via complete_with_tools
# ---------------------------------------------------------------------------

def test_author_strategy_uses_tool_loop_when_supported():
    """With a tool-capable provider, 甲 researches (calls a tool) then emits the
    commit — asserted deterministically via ScriptedToolProvider."""
    from loop.strategy import AuthorStrategy
    from llm.provider import ScriptedToolProvider

    registry = _make_registry()
    world = _make_world(registry)
    # protagonist must be in present so tool pov-validation passes (defaults to protagonist)
    scene = {"protagonist": "hero", "present": ["hero"], "day": 1, "location": "town"}
    script = [
        {"tool_calls": [{"name": "map_query", "arguments": {"q": "town"}}]},
        {"content": '{"narration": "勘察后前行", "moves": []}'},
    ]
    provider = ScriptedToolProvider(script=script)
    strat = AuthorStrategy()
    commit = strat.produce(registry, world, scene, "四处看看", provider=provider)

    assert commit.narration == "勘察后前行"
    # 甲 actually drove a tool research round:
    assert ("map_query", {"q": "town"}) in provider.tool_invocations


def test_author_strategy_falls_back_when_tools_unsupported():
    """A plain FakeLLMProvider (supports_tools()==False) must use the OLD
    complete_messages path verbatim — guarantees the 791 suite is untouched."""
    from loop.strategy import AuthorStrategy
    from llm.provider import FakeLLMProvider
    provider = FakeLLMProvider(json_responses=[{"narration": "no tools", "moves": []}])
    strat = AuthorStrategy()
    commit = strat.produce(_make_registry(), _make_world(_make_registry()),
                           _make_scene(), "走", provider=provider)
    assert commit.narration == "no tools"
    assert provider.supports_tools() is False




def test_narrate_template_has_no_hardcoded_voice():
    """The literary voice lean moved into the flavor pack (classic/pack.json.voice),
    injected via __STYLE__ — it must no longer be hardcoded in the 丙 template."""
    from loop import strategy
    assert "融合细腻描写与戏剧张力" not in strategy._NARRATE_PROMPT_TEMPLATE
    assert "重环境氛围" not in strategy._NARRATE_PROMPT_TEMPLATE
