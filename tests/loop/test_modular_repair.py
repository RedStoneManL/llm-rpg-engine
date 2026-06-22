"""Fix #8: modular repair — re-emit ONLY failing sections on each repair attempt.

Tests:
  1. MODULAR_NARRATION_PRESERVED — narration from first pass is NEVER regenerated;
     only the failing section is re-emitted; passing sections are preserved.
  2. REPAIR_PROMPT_TARGETS_ONLY_FAILING — the repair call asks for ONLY the
     failing section(s), not narration, not passing sections.
  3. DROP_STILL_FAILING_AFTER_MAX_REPAIRS — a section that stays invalid after
     max_repairs is still dropped (existing fallback intact).
  4. HYBRID_MODULAR_REPAIR — HybridStrategy also repairs modularly (prose frozen;
     structure repair covers only failing sections).
  5. MULTIPLE_FAILING_SECTIONS — two different sections both fail initially;
     both are repaired in one repair call, passing section preserved.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from kernel.registry import Registry
from kernel.projection import empty_world
from kernel.turncommit import TurnCommit
from kernel.contextsystem import ValidationError
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    return r


def _make_scene(day=1):
    return {"protagonist": "hero", "present": [], "day": day, "location": "town", "id": "sc01"}


def _make_world(registry):
    return empty_world(registry)


class _RecordingProvider(FakeLLMProvider):
    """FakeLLMProvider that records the full messages list on each complete_messages call.

    `message_log` stores a list of (messages_snapshot, returned_content) per call,
    in call order, so tests can inspect exactly what the LLM "saw" on each round.
    """

    def __init__(self, json_seq: list[dict]):
        super().__init__(json_responses=json_seq)
        self.message_log: list[list[dict]] = []

    def complete_messages(self, messages: list[dict], **kwargs) -> str:
        # Deep-copy so later mutations to self._messages don't back-patch history
        self.message_log.append([dict(m) for m in messages])
        return super().complete_messages(messages, **kwargs)


# ---------------------------------------------------------------------------
# Test 1 — narration preserved; only the failing section is repaired
# ---------------------------------------------------------------------------

def test_modular_repair_preserves_narration():
    """First pass: valid narration + valid entities + invalid facts.
    Repair: only `facts` re-emitted (partial dict, no narration key).
    Result: narration == first-pass narration; entities preserved; facts fixed.
    """
    from loop.turn import produce_turn
    from loop.strategy import AuthorStrategy

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    first_narration = "月光洒在青石板上，英雄缓缓走出巷口。"

    # Pass 1: valid narration + valid entities, but facts has dangling ref (subject missing entity)
    first_pass = {
        "narration": first_narration,
        "entities": [{"id": "hero", "etype": "Person", "tier": "tracked"}],
        "facts": [{"subject": "ghost_npc", "predicate": "mood", "value": "angry"}],
        "reasons": {"moves": "未移动", "places": "无新地点", "cast": "无新角色"},
    }
    # Repair pass: ONLY facts, no narration key (modular!)
    repair_pass = {
        "facts": [],  # corrected: no dangling refs
    }

    provider = _RecordingProvider(json_seq=[first_pass, repair_pass])
    strategy = AuthorStrategy()

    commit, attempts, dropped = produce_turn(
        registry, world, scene, "我向前走",
        strategy=strategy, provider=provider, max_repairs=3,
    )

    # Core assertion: narration unchanged from first pass
    assert commit.narration == first_narration, (
        f"narration was regenerated! got: {commit.narration!r}"
    )

    # Repair happened exactly once
    assert attempts == 1, f"expected 1 repair attempt, got {attempts}"

    # entities from first pass are preserved
    assert "entities" in commit.sections
    assert commit.sections["entities"] == [{"id": "hero", "etype": "Person", "tier": "tracked"}]

    # facts are now the repaired version
    assert "facts" in commit.sections
    assert commit.sections["facts"] == []

    # Nothing dropped
    assert dropped == []

    # Exactly 2 LLM calls (initial + 1 repair)
    assert len(provider.message_log) == 2, (
        f"expected 2 LLM calls, got {len(provider.message_log)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — repair prompt targets ONLY failing sections
# ---------------------------------------------------------------------------

def test_repair_prompt_targets_only_failing_sections():
    """The repair call's prompt must ask for ONLY the failing sections by name,
    not for narration or passing sections.
    """
    from loop.turn import produce_turn
    from loop.strategy import AuthorStrategy

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    first_narration = "英雄踏入月夜。"

    first_pass = {
        "narration": first_narration,
        "entities": [{"id": "hero", "etype": "Person", "tier": "tracked"}],
        "facts": [{"subject": "phantom", "predicate": "mood", "value": "angry"}],  # dangling
        "reasons": {"moves": "未移动", "places": "无新地点", "cast": "无新角色"},
    }
    repair_pass = {"facts": []}

    provider = _RecordingProvider(json_seq=[first_pass, repair_pass])
    strategy = AuthorStrategy()

    produce_turn(
        registry, world, scene, "等待",
        strategy=strategy, provider=provider, max_repairs=3,
    )

    # The repair call is the second message log entry
    repair_messages = provider.message_log[1]

    # Find the last user message (the repair instruction)
    user_msgs = [m["content"] for m in repair_messages if m.get("role") == "user"]
    assert user_msgs, "no user message in repair call"
    repair_instruction = user_msgs[-1]

    # The instruction must mention the failing section
    assert "facts" in repair_instruction, (
        f"repair instruction should mention 'facts'; got: {repair_instruction!r}"
    )

    # The instruction must NOT ask to rewrite narration
    assert "narration" not in repair_instruction.lower() or (
        # acceptable if narration is mentioned only in context of "don't rewrite it"
        "不" in repair_instruction or "只" in repair_instruction
    ), (
        f"repair instruction should NOT request narration rewrite; got: {repair_instruction!r}"
    )

    # The instruction must ask for ONLY the failing section(s) — should use 只/仅/only
    # and must reference the specific section names
    assert any(kw in repair_instruction for kw in ["只", "仅", "only", "ONLY"]), (
        f"repair instruction should use 只/仅/only to limit scope; got: {repair_instruction!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — still-failing section after max_repairs is dropped
# ---------------------------------------------------------------------------

def test_still_failing_section_dropped_after_max_repairs():
    """A section that stays invalid after max_repairs is dropped (fallback intact)."""
    from loop.turn import produce_turn
    from loop.strategy import AuthorStrategy

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    first_narration = "英雄抵达荒村。"

    # Every response has a stubborn dangling ref in facts
    stubborn = {
        "narration": first_narration,
        "entities": [{"id": "real_hero", "etype": "Person", "tier": "tracked"}],
        "facts": [{"subject": "phantom", "predicate": "mood", "value": "angry"}],
        "reasons": {"moves": "未移动", "places": "无新地点", "cast": "无新角色"},
    }
    # Repair responses return ONLY the section (modular), but still invalid
    stubborn_repair = {
        "facts": [{"subject": "phantom", "predicate": "mood", "value": "angry"}],  # still bad
    }

    # initial + max_repairs repair calls
    provider = _RecordingProvider(json_seq=[stubborn, stubborn_repair, stubborn_repair, stubborn_repair])
    strategy = AuthorStrategy()

    commit, attempts, dropped = produce_turn(
        registry, world, scene, "等待",
        strategy=strategy, provider=provider, max_repairs=3,
    )

    # facts is dropped after failing all repairs
    assert "facts" in dropped, f"facts should be dropped; got dropped={dropped}"

    # narration still from the first pass
    assert commit.narration == first_narration

    # entities (valid) are preserved
    assert "entities" in commit.sections
    assert commit.sections["entities"] == [{"id": "real_hero", "etype": "Person", "tier": "tracked"}]

    assert attempts == 3


# ---------------------------------------------------------------------------
# Test 4 — HybridStrategy also repairs modularly (prose frozen)
# ---------------------------------------------------------------------------

def test_hybrid_strategy_modular_repair():
    """HybridStrategy: prose frozen across all attempts; structure repair
    re-emits only the failing section. narration must equal the first prose call.
    """
    from loop.turn import produce_turn
    from loop.strategy import HybridStrategy

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    frozen_prose = "烛火摇曳，映出英雄的身影。"

    # HybridStrategy: first complete() call is for prose, then complete_messages for structure
    # Pass 1 prose response
    first_struct = {
        "entities": [{"id": "hero", "etype": "Person", "tier": "tracked"}],
        "facts": [{"subject": "phantom", "predicate": "mood", "value": "angry"}],  # dangling
        "reasons": {"moves": "未移动", "places": "无新地点", "cast": "无新角色"},
    }
    # Repair: only facts (no narration key in response)
    repair_struct = {"facts": []}

    provider = _RecordingProvider(json_seq=[first_struct, repair_struct])
    # Prose comes via complete() — set responses for that
    provider._responses = [frozen_prose]

    strategy = HybridStrategy()

    commit, attempts, dropped = produce_turn(
        registry, world, scene, "观察环境",
        strategy=strategy, provider=provider, max_repairs=3,
    )

    # Prose is frozen — narration equals the prose call
    assert commit.narration == frozen_prose, (
        f"narration changed! got: {commit.narration!r}"
    )

    # Repair happened
    assert attempts == 1

    # entities preserved from first struct
    assert "entities" in commit.sections
    assert commit.sections["entities"] == [{"id": "hero", "etype": "Person", "tier": "tracked"}]

    # facts fixed
    assert commit.sections.get("facts") == []
    assert dropped == []


# ---------------------------------------------------------------------------
# Test 5 — multiple failing sections repaired in one call
# ---------------------------------------------------------------------------

def test_multiple_failing_sections_repaired_together():
    """Two sections fail on first pass; both are re-emitted in one repair call.
    Passing section (entities) is preserved.
    """
    from loop.turn import produce_turn
    from loop.strategy import AuthorStrategy

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()

    first_narration = "双重谜题出现了。"

    first_pass = {
        "narration": first_narration,
        "entities": [{"id": "hero", "etype": "Person", "tier": "tracked"}],
        # Two bad sections: both reference non-existent entities
        "facts": [{"subject": "phantom_a", "predicate": "role", "value": "spy"}],
        "relations": [{"src": "phantom_b", "rel": "knows", "dst": "hero"}],
        "reasons": {"moves": "未移动", "places": "无新地点", "cast": "无新角色"},
    }
    # Repair: both sections fixed (no narration key)
    repair_pass = {
        "facts": [],
        "relations": [],
    }

    provider = _RecordingProvider(json_seq=[first_pass, repair_pass])
    strategy = AuthorStrategy()

    commit, attempts, dropped = produce_turn(
        registry, world, scene, "审视谜题",
        strategy=strategy, provider=provider, max_repairs=3,
    )

    # Narration unchanged
    assert commit.narration == first_narration

    # Both sections repaired
    assert commit.sections.get("facts") == []
    assert commit.sections.get("relations") == []

    # entities from first pass preserved
    assert commit.sections.get("entities") == [{"id": "hero", "etype": "Person", "tier": "tracked"}]

    assert attempts == 1
    assert dropped == []

    # Only 2 LLM calls (initial + 1 repair for both failing sections together)
    assert len(provider.message_log) == 2
