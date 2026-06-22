"""Tests for memory/reflection.py (Task 5).

Uses FakeLLMProvider — no live network calls.
"""

import pytest
from engine.schema import make_event


def _event(ev_type="action", day=1, summary="something happened", importance=3):
    return {
        "type": ev_type,
        "day": day,
        "scene": "test_scene",
        "actors": ["hero"],
        "summary": summary,
        "importance": importance,
    }


class TestShouldReflect:
    def test_below_threshold_false(self):
        from memory.reflection import should_reflect
        assert should_reflect(29, threshold=30) is False

    def test_at_threshold_true(self):
        from memory.reflection import should_reflect
        assert should_reflect(30, threshold=30) is True

    def test_above_threshold_true(self):
        from memory.reflection import should_reflect
        assert should_reflect(50, threshold=30) is True

    def test_zero_false(self):
        from memory.reflection import should_reflect
        assert should_reflect(0, threshold=30) is False

    def test_default_threshold_is_30(self):
        from memory.reflection import should_reflect
        assert should_reflect(30) is True
        assert should_reflect(29) is False

    def test_custom_threshold(self):
        from memory.reflection import should_reflect
        assert should_reflect(10, threshold=10) is True
        assert should_reflect(9, threshold=10) is False


class TestReflect:
    def _make_fake_provider_with_json(self, arc_summary):
        """Return a FakeLLMProvider that returns a JSON arc fact-delta."""
        import json
        from llm.provider import FakeLLMProvider
        payload = json.dumps({"predicate": "arc", "value": arc_summary})
        return FakeLLMProvider(responses=[payload])

    def test_reflect_returns_dict(self):
        from memory.reflection import reflect
        from llm.provider import FakeLLMProvider
        import json
        fake = self._make_fake_provider_with_json("英雄走上了救赎之路")
        result = reflect("艾拉", [_event()], provider=fake)
        assert isinstance(result, dict)

    def test_reflect_has_predicate_arc(self):
        from memory.reflection import reflect
        fake = self._make_fake_provider_with_json("英雄找到了目标")
        result = reflect("艾拉", [_event()], provider=fake)
        assert result.get("predicate") == "arc"

    def test_reflect_has_value(self):
        from memory.reflection import reflect
        fake = self._make_fake_provider_with_json("人物命运转折点")
        result = reflect("艾拉", [_event()], provider=fake)
        assert "value" in result
        assert result["value"] == "人物命运转折点"

    def test_reflect_passes_subject_in_prompt(self):
        from memory.reflection import reflect
        from llm.provider import FakeLLMProvider
        import json
        fake = FakeLLMProvider(responses=[json.dumps({"predicate": "arc", "value": "x"})])
        reflect("艾拉", [_event(summary="关键事件")], provider=fake)
        # The system or user prompt must mention the subject
        assert any("艾拉" in call[0] or "艾拉" in call[1] for call in fake.calls)

    def test_reflect_passes_events_in_prompt(self):
        from memory.reflection import reflect
        from llm.provider import FakeLLMProvider
        import json
        events = [_event(summary="发现了秘密"), _event(summary="背叛了盟友")]
        fake = FakeLLMProvider(responses=[json.dumps({"predicate": "arc", "value": "x"})])
        reflect("英雄", events, provider=fake)
        # The user prompt should contain event summaries
        all_prompts = " ".join(c[0] + c[1] for c in fake.calls)
        assert "发现了秘密" in all_prompts or "背叛了盟友" in all_prompts

    def test_reflect_with_multiple_events(self):
        from memory.reflection import reflect
        import json
        events = [
            _event(day=1, summary="英雄离开家乡", importance=5),
            _event(day=5, summary="英雄找到了导师", importance=6),
            _event(day=10, summary="英雄面临第一次抉择", importance=8),
        ]
        from llm.provider import FakeLLMProvider
        summary = "英雄完成了成长第一阶段"
        fake = FakeLLMProvider(responses=[json.dumps({"predicate": "arc", "value": summary})])
        result = reflect("英雄", events, provider=fake)
        assert result["predicate"] == "arc"
        assert result["value"] == summary

    def test_reflect_fallback_on_bad_json(self):
        """If LLM returns bad JSON, reflect should still return a valid dict
        (either retry succeeds or a safe fallback is returned)."""
        from memory.reflection import reflect
        from llm.provider import FakeLLMProvider
        import json
        # First call bad, second call good
        good = json.dumps({"predicate": "arc", "value": "fallback summary"})
        fake = FakeLLMProvider(responses=["not-json", good])
        result = reflect("艾拉", [_event()], provider=fake)
        assert isinstance(result, dict)
        assert "predicate" in result

    def test_reflect_raises_or_returns_on_persistent_bad_json(self):
        """If LLM always returns bad JSON, reflect may raise ValueError or
        return a fallback dict — but must not crash with an unhandled exception."""
        from memory.reflection import reflect
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["bad", "also-bad", "still-bad"])
        try:
            result = reflect("艾拉", [_event()], provider=fake)
            # If it doesn't raise, it must return a dict
            assert isinstance(result, dict)
        except ValueError:
            pass  # also acceptable
