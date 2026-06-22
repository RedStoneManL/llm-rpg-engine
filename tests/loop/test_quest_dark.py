"""T1: state-aware 暗骰 + jit_resequence.

Tests:
1. run_lore advances a 暗 line (threshold 100) but skips a 明 line and a 了结 line.
2. jit_resequence returns parsed stages from FakeLLMProvider.
3. jit_resequence falls back to line's remaining original stages on malformed response.
"""
import os
import tempfile

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import open_store, kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem
from loop.lore import create_lore_line, run_lore, jit_resequence
from llm.provider import FakeLLMProvider


def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(LoreSystem())
    return r


def _reg_full():
    """Registry with Place + Character systems, needed so dormancy (★6) can resolve
    the protagonist's location.  Tests that exercise simple/medium 暗 line advancing
    must use this + _seed_protagonist_in_anchor() to avoid the line being frozen."""
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=registry.event_types())


def _seed_protagonist_in_anchor(store, anchor_town):
    """Seed an L2 place and a tracked hero located_in it (required by dormancy ★6)."""
    store.append(kernel_event(
        "place_created", day=1, scene="s1", summary=anchor_town,
        deltas={"id": anchor_town, "level": 2, "kind": "settlement",
                "seed": anchor_town, "tier": "tracked"},
        turn=0,
    ))
    store.append(kernel_event(
        "character_created", day=1, scene="s1", summary="hero",
        deltas={"id": "hero", "tier": "tracked", "sketch": "a", "goal": "b"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_moved", day=1, scene="s1", summary="move",
        deltas={"who": "hero", "to": anchor_town},
        turn=0,
    ))


_SK_AN = {
    "id": "an_line", "complexity": "simple", "about": "暗线测试",
    "secret": "隐情", "anchor": "town",
    "description": "描述", "trigger": "触发",
    "l3_anchor": "town_market",
    "stages": [{"hint": "暗线clue-a"}, {"hint": "暗线clue-b"}],
    "threshold": 100,  # always advances
}

_SK_MING = {
    "id": "ming_line", "complexity": "simple", "about": "明线测试",
    "secret": "隐情", "anchor": "town",
    "description": "描述", "trigger": "触发",
    "l3_anchor": "town_market",
    "stages": [{"hint": "明线clue-a"}, {"hint": "明线clue-b"}],
    "threshold": 100,  # would always advance if not skipped
    "state": "明",
}

_SK_LIUJIE = {
    "id": "liujie_line", "complexity": "simple", "about": "了结线测试",
    "secret": "隐情", "anchor": "town",
    "description": "描述", "trigger": "触发",
    "l3_anchor": "town_market",
    "stages": [{"hint": "了结clue-a"}, {"hint": "了结clue-b"}],
    "threshold": 100,  # would always advance if not skipped
    "state": "了结",
}


def test_run_lore_skips_ming_and_liujie_lines():
    """run_lore advances a 暗 line but leaves 明/了结 lines untouched."""
    # Uses _reg_full + protagonist in anchor town so dormancy (★6) doesn't freeze the 暗 line.
    r = _reg_full()
    store = _store(r)
    _seed_protagonist_in_anchor(store, "town")

    # Create all three lines
    create_lore_line(store, _SK_AN, day=1, scene="s1", turn=1)
    create_lore_line(store, _SK_MING, day=1, scene="s1", turn=2)
    create_lore_line(store, _SK_LIUJIE, day=1, scene="s1", turn=3)

    w = project(r, store.iter_events())

    # Sanity check: all lines are present
    assert "an_line" in w["systems"]["lore"]["lines"]
    assert "ming_line" in w["systems"]["lore"]["lines"]
    assert "liujie_line" in w["systems"]["lore"]["lines"]

    # Verify initial states
    assert w["systems"]["lore"]["lines"]["an_line"]["state"] == "暗"
    assert w["systems"]["lore"]["lines"]["ming_line"]["state"] == "明"
    assert w["systems"]["lore"]["lines"]["liujie_line"]["state"] == "了结"

    appended = run_lore(r, store, w)

    # Only the 暗 line should have been advanced
    advanced_ids = [e["deltas"]["id"] for e in appended if e["type"] == "lore_advanced"]
    assert "an_line" in advanced_ids, "暗 line should be advanced"
    assert "ming_line" not in advanced_ids, "明 line must NOT be advanced by 暗骰"
    assert "liujie_line" not in advanced_ids, "了结 line must NOT be advanced by 暗骰"

    # Verify the 明/了结 lines' stage_idx is still -1
    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["ming_line"]["stage_idx"] == -1
    assert w2["systems"]["lore"]["lines"]["liujie_line"]["stage_idx"] == -1
    # 暗 line advanced
    assert w2["systems"]["lore"]["lines"]["an_line"]["stage_idx"] == 0


def test_run_lore_all_an_lines_still_advance():
    """Existing behavior: 暗 lines advance when protagonist is in their anchor town."""
    # Uses _reg_full + protagonist in anchor town so dormancy (★6) doesn't freeze the line.
    r = _reg_full()
    store = _store(r)
    _seed_protagonist_in_anchor(store, "town")
    create_lore_line(store, _SK_AN, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    appended = run_lore(r, store, w)
    assert len(appended) == 1
    assert appended[0]["deltas"]["id"] == "an_line"
    assert appended[0]["deltas"]["stage_idx"] == 0


# ---------------------------------------------------------------------------
# jit_resequence tests
# ---------------------------------------------------------------------------

_LINE_WITH_STAGES = {
    "id": "jit_line",
    "about": "神秘商队",
    "secret": "商队首领卷款潜逃",
    "clues_dropped": ["集市上有人打听"],
    "stages": [
        {"hint": "stage0-original"},
        {"hint": "stage1-original"},
        {"hint": "stage2-original"},
    ],
    "stage_idx": 0,  # currently at stage 0, remaining = stages[1:]
    "anchor": "town",
    "threshold": 50,
    "complexity": "medium",
}

_WORLD = {"meta": {"day": 3, "scene": "s1"}, "systems": {}}


def test_jit_resequence_returns_provider_stages():
    """jit_resequence with a FakeLLMProvider returning canned stages → returns those stages."""
    canned = {"stages": [{"hint": "new-续写-stage-1"}, {"hint": "new-续写-stage-2"}]}
    provider = FakeLLMProvider(json_responses=[canned])

    result = jit_resequence(_LINE_WITH_STAGES, _WORLD, provider)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["hint"] == "new-续写-stage-1"
    assert result[1]["hint"] == "new-续写-stage-2"


def test_jit_resequence_fallback_on_missing_stages_key():
    """Malformed response (no 'stages' key) → fallback to remaining original stages."""
    provider = FakeLLMProvider(json_responses=[{}])  # no 'stages' key

    result = jit_resequence(_LINE_WITH_STAGES, _WORLD, provider)

    # fallback = stages[stage_idx+1:] = stages[1:]
    expected_remaining = _LINE_WITH_STAGES["stages"][_LINE_WITH_STAGES["stage_idx"] + 1:]
    assert result == expected_remaining


def test_jit_resequence_fallback_on_non_list_stages():
    """Malformed response ('stages' is not a list) → fallback to remaining original stages."""
    provider = FakeLLMProvider(json_responses=[{"stages": "not-a-list"}])

    result = jit_resequence(_LINE_WITH_STAGES, _WORLD, provider)

    expected_remaining = _LINE_WITH_STAGES["stages"][_LINE_WITH_STAGES["stage_idx"] + 1:]
    assert result == expected_remaining


def test_jit_resequence_fallback_on_empty_stages():
    """Response with stages=[] → fallback to remaining original stages (empty is invalid)."""
    provider = FakeLLMProvider(json_responses=[{"stages": []}])

    result = jit_resequence(_LINE_WITH_STAGES, _WORLD, provider)

    # An empty list from provider is treated as invalid → fallback
    expected_remaining = _LINE_WITH_STAGES["stages"][_LINE_WITH_STAGES["stage_idx"] + 1:]
    assert result == expected_remaining


def test_jit_resequence_never_raises():
    """jit_resequence must never raise, even with a completely broken provider response."""
    # Provide a response that has no 'stages' at all
    provider = FakeLLMProvider(json_responses=[{"garbage": 123}])
    # Should not raise
    result = jit_resequence(_LINE_WITH_STAGES, _WORLD, provider)
    assert isinstance(result, list)


def test_jit_resequence_at_last_stage_returns_empty_fallback():
    """Line already at last stage → remaining = [], fallback = []."""
    line_at_end = {**_LINE_WITH_STAGES, "stage_idx": 2}  # last stage (2 of 3)
    provider = FakeLLMProvider(json_responses=[{}])  # malformed → fallback

    result = jit_resequence(line_at_end, _WORLD, provider)
    assert result == []  # no remaining stages


def test_jit_resequence_fallback_on_provider_exception():
    """T1 follow-up: provider.complete_json RAISES → returns remaining original stages, never raises."""
    class ExplodingProvider:
        def complete_json(self, system, user, schema, **kw):
            raise RuntimeError("模拟 provider 异常")

    result = jit_resequence(_LINE_WITH_STAGES, _WORLD, ExplodingProvider())

    # Must not raise; returns remaining original stages (stages[stage_idx+1:])
    expected = _LINE_WITH_STAGES["stages"][_LINE_WITH_STAGES["stage_idx"] + 1:]
    assert result == expected
