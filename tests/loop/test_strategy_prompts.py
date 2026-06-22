"""Tests for prompt content: quests section documented in 甲/丙 prompts (T3)."""
from __future__ import annotations

from loop.strategy import _SYSTEM_PROMPT, _SYSTEM_PROMPT_HYBRID


def test_quests_section_documented_in_both_prompts():
    for p in (_SYSTEM_PROMPT, _SYSTEM_PROMPT_HYBRID):
        assert "quests" in p
        assert "open" in p and "advance" in p and "resolve" in p and "surface" in p
        # still mentions knowledge (we add alongside, not replace)
        assert "knowledge" in p
