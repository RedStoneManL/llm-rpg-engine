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


def test_hybrid_strategy_freezes_prose_and_structures_separately():
    """丙: call 1 writes prose (= narration); call 2 authors structure with an
    author framing (not 史官). On repair the prose is frozen and only the
    structure conversation continues."""
    from loop.strategy import HybridStrategy, _NARRATE_PROMPT, _SYSTEM_PROMPT_HYBRID

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()
    provider = FakeLLMProvider(responses=["丙散文"],
                               json_responses=[{"moves": []}, {"moves": []}])
    strat = HybridStrategy()
    c1 = strat.produce(registry, world, scene, "走", provider=provider)
    c2 = strat.produce(registry, world, scene, "走", provider=provider, repair="补 moves.who")

    assert c1.narration == "丙散文" and c2.narration == "丙散文"        # frozen prose
    narrate_calls = [c for c in provider.calls if c[0] == _NARRATE_PROMPT]
    assert len(narrate_calls) == 1                                      # prose generated once
    struct_calls = [c for c in provider.calls if c[0] == _SYSTEM_PROMPT_HYBRID]
    assert len(struct_calls) == 2                                       # initial + repair (agent loop)


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
# P1 Task 7: `world` section exposed in 甲 + 丙 prompts (optional)
# ---------------------------------------------------------------------------

def test_world_section_in_author_prompt():
    from loop.strategy import _SYSTEM_PROMPT
    assert "world:" in _SYSTEM_PROMPT
    assert "areas" in _SYSTEM_PROMPT
    assert "世界事件" in _SYSTEM_PROMPT


def test_world_section_in_hybrid_prompt():
    from loop.strategy import _SYSTEM_PROMPT_HYBRID
    assert "world:" in _SYSTEM_PROMPT_HYBRID
    assert "areas" in _SYSTEM_PROMPT_HYBRID
    assert "世界事件" in _SYSTEM_PROMPT_HYBRID


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


