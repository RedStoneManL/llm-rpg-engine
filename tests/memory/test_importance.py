"""Tests for memory/importance.py (Task 3).

Uses FakeLLMProvider — no live network calls.
"""

import pytest

from engine.schema import make_event


def _trivial_event():
    return make_event("action", 1, "road", ["hero"], "英雄在路上闲逛")


def _thread_opening_event():
    return make_event("thread_open", 2, "village", ["elder", "hero"],
                      "村长开启了寻找失踪工匠的任务",
                      thread_refs=["quest_artisan"])


def _relationship_change_event():
    return make_event("relationship_change", 3, "tavern", ["hero", "ally"],
                      "盟友因背叛而与英雄决裂",
                      deltas={"relationship_hero_ally": "broken"})


def _character_reveal_event():
    return make_event("character_reveal", 4, "cave", ["villain"],
                      "反派揭示了自己真实身份",
                      deltas={"villain_identity": "revealed"})


class TestHeuristicFloor:
    def test_trivial_event_low_floor(self):
        from memory.importance import heuristic_floor
        ev = _trivial_event()
        score = heuristic_floor(ev)
        assert isinstance(score, int)
        assert 0 <= score <= 10
        assert score <= 3  # trivial action should be low

    def test_thread_open_higher_floor(self):
        from memory.importance import heuristic_floor
        ev_trivial = _trivial_event()
        ev_thread = _thread_opening_event()
        assert heuristic_floor(ev_thread) > heuristic_floor(ev_trivial)

    def test_relationship_change_elevated(self):
        from memory.importance import heuristic_floor
        ev_trivial = _trivial_event()
        ev_rel = _relationship_change_event()
        assert heuristic_floor(ev_rel) > heuristic_floor(ev_trivial)

    def test_character_reveal_elevated(self):
        from memory.importance import heuristic_floor
        ev_trivial = _trivial_event()
        ev_reveal = _character_reveal_event()
        assert heuristic_floor(ev_reveal) > heuristic_floor(ev_trivial)

    def test_returns_int_in_range(self):
        from memory.importance import heuristic_floor
        for ev in [_trivial_event(), _thread_opening_event(),
                   _relationship_change_event(), _character_reveal_event()]:
            s = heuristic_floor(ev)
            assert isinstance(s, int), f"expected int, got {type(s)}"
            assert 0 <= s <= 10, f"score {s} out of range"

    def test_promise_made_elevated(self):
        from memory.importance import heuristic_floor
        ev = make_event("promise_made", 5, "throne_room", ["king", "hero"],
                        "国王承诺了英雄的报酬")
        trivial = heuristic_floor(_trivial_event())
        assert heuristic_floor(ev) > trivial

    def test_combat_result_elevated(self):
        from memory.importance import heuristic_floor
        ev = make_event("combat_result", 6, "battlefield", ["hero", "enemy"],
                        "英雄在战斗中击败了首领")
        trivial = heuristic_floor(_trivial_event())
        assert heuristic_floor(ev) > trivial

    def test_event_with_deltas_elevated_over_no_deltas(self):
        from memory.importance import heuristic_floor
        with_deltas = make_event("action", 1, "town", ["hero"], "对话引发了变化",
                                 deltas={"hero_mood": "resolved"})
        without = _trivial_event()
        # Events with deltas carry more information
        assert heuristic_floor(with_deltas) >= heuristic_floor(without)


class TestScoreWithProvider:
    def test_score_uses_llm_score_when_provider_given(self):
        from memory.importance import score
        from llm.provider import FakeLLMProvider
        # LLM returns "8"
        fake = FakeLLMProvider(responses=["8"])
        ev = _trivial_event()
        result = score(ev, provider=fake)
        assert result == 8

    def test_score_clamps_high(self):
        from memory.importance import score
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["15"])  # out of range
        ev = _trivial_event()
        result = score(ev, provider=fake)
        assert result == 10

    def test_score_clamps_low(self):
        from memory.importance import score
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["0"])  # out of range
        ev = _trivial_event()
        result = score(ev, provider=fake)
        assert result == 1

    def test_score_takes_max_of_heuristic_and_llm(self):
        from memory.importance import score, heuristic_floor
        from llm.provider import FakeLLMProvider
        ev = _thread_opening_event()
        h = heuristic_floor(ev)
        # LLM returns 1 (lower than heuristic)
        fake = FakeLLMProvider(responses=["1"])
        result = score(ev, provider=fake)
        # Must take max(heuristic, clamped_llm)
        assert result == max(h, 1)

    def test_score_no_provider_returns_heuristic(self):
        from memory.importance import score, heuristic_floor
        ev = _thread_opening_event()
        result = score(ev, provider=None)
        assert result == heuristic_floor(ev)

    def test_score_returns_int(self):
        from memory.importance import score
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["7"])
        result = score(_trivial_event(), provider=fake)
        assert isinstance(result, int)

    def test_score_handles_llm_returning_text_with_number(self):
        """LLM might return 'Score: 6' — must extract the int."""
        from memory.importance import score
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["Score: 6"])
        result = score(_trivial_event(), provider=fake)
        assert result == 6

    def test_score_handles_noisy_llm_response(self):
        """LLM returns '我认为这是 7 分' — extract first integer."""
        from memory.importance import score
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["我认为这是 7 分的重要程度"])
        result = score(_trivial_event(), provider=fake)
        assert result == 7
