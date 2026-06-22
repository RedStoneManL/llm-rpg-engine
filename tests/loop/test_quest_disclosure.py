"""tests/loop/test_quest_disclosure.py — T5: unified quest disclosure (B won).

The disclosure is now always-on with no A/B mode switch:
  - 明账 (明 quests): injected via LoreSystem.inject (明 lines only)
  - 暗 ambient clues: appended by AuthorStrategy.produce via station_push_fragment

Tests:
  1. Context carries 明账 for 明 quests (from inject / assemble_context).
  2. Context carries 暗 ambient clues for 暗 quests at the current venue (station_push_fragment).
  3. A 暗 line is NOT presented as advanceable (it's only ambient clue, no 明账 entry).
  4. A 明 line IS in the 明账 (inject returns Fragment with 明账 text).
  5. No-LoreSystem path: station_push_fragment returns None → strategy runs cleanly (existing
     tests without LoreSystem are unaffected).
"""
from __future__ import annotations

import json

import pytest

from kernel.registry import Registry
from kernel.projection import project, empty_world
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem
from systems.place import PlaceSystem
from loop.lore_disclosure import station_push_fragment
from loop.strategy import AuthorStrategy
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(LoreSystem())
    return r


def _commit_data(**kw) -> dict:
    base = {
        "narration": "叙事内容",
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "无时间推进"}],
        "reasons": {"moves": "未移动", "places": "无新地点", "cast": "无新角色", "facts": "无新事实"},
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# World fixture: one 明 quest + one 暗 line at the current venue
# ---------------------------------------------------------------------------

def _build_unified_world():
    """
    World:
      qingshi_town (L2) → market (L3)
      hero at market

    Quests:
      quest_merchant (明): surfaced via quest_opened, has summary
      rumor_line     (暗): lore_created with l3_anchor=market, has a clue dropped
    """
    r = _reg()
    events = [
        kernel_event("place_created", day=1, scene="s1", summary="qingshi_town",
                     deltas={"id": "qingshi_town", "level": 2, "kind": "settlement"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="market",
                     deltas={"id": "market", "level": 3, "kind": "venue",
                             "parent": "qingshi_town"}, turn=0),
        kernel_event("entity_created", day=1, scene="s1", summary="hero",
                     deltas={"id": "hero", "etype": "Character", "tier": "tracked"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="hero→market",
                     deltas={"who": "hero", "to": "market"}, turn=0),
        # 明 quest: opened by narrator
        kernel_event("quest_opened", day=1, scene="s1", summary="明线任务",
                     deltas={"id": "quest_merchant", "summary": "调查商队失踪事件"},
                     turn=1),
        # 暗 lore line at market
        kernel_event("lore_created", day=1, scene="s1", summary="rumor",
                     deltas={
                         "id": "rumor_line",
                         "complexity": "low",
                         "about": "市场谣言",
                         "secret": "谣言背后的真相",
                         "anchor": "qingshi_town",
                         "description": "市场上有人窃窃私语",
                         "trigger": "玩家打听",
                         "l3_anchor": "market",
                         "stages": [{"hint": "有人目击可疑人物"}],
                         "threshold": 100,
                     }, turn=2),
        # Advance rumor_line so it has a clue
        kernel_event("lore_advanced", day=1, scene="s1", summary="adv",
                     deltas={"id": "rumor_line", "stage_idx": 0, "hint": "有人目击可疑人物"},
                     turn=3),
    ]
    w = project(r, events)
    scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "market"}
    return r, w, scene


# ---------------------------------------------------------------------------
# T5.1: 明账 present for 明 quests
# ---------------------------------------------------------------------------

class TestMingLedgerInjection:
    """LoreSystem.inject returns the 明账 fragment for 明 quests."""

    def test_ming_quest_in_ledger(self):
        """A 明 quest appears in the 明账 fragment from LoreSystem.inject."""
        r, w, scene = _build_unified_world()
        ls = LoreSystem()
        frag = ls.inject(scene, w)
        assert frag is not None, "inject must return 明账 fragment when 明 lines exist"
        assert "任务·明账" in frag.text
        assert "quest_merchant" in frag.text
        assert "调查商队失踪事件" in frag.text

    def test_dark_line_not_in_ledger(self):
        """A 暗 line must NOT appear in the 明账."""
        r, w, scene = _build_unified_world()
        ls = LoreSystem()
        frag = ls.inject(scene, w)
        assert frag is not None
        assert "rumor_line" not in frag.text, "暗 line must not be in 明账"
        assert "市场谣言" not in frag.text

    def test_no_ming_quests_returns_none(self):
        """When there are no 明 quests, inject returns None (no ledger to show)."""
        r = _reg()
        w = empty_world(r)
        scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "market"}
        ls = LoreSystem()
        result = ls.inject(scene, w)
        assert result is None


# ---------------------------------------------------------------------------
# T5.2: 暗 ambient clues appended via station_push_fragment
# ---------------------------------------------------------------------------

class TestAmbientDarkClues:
    """station_push_fragment provides 暗 ambient clues at the current venue."""

    def test_dark_line_clue_in_ambient_fragment(self):
        """暗 line at the current venue appears as ambient clue in station_push_fragment."""
        r, w, scene = _build_unified_world()
        frag = station_push_fragment(r, w, scene)
        assert frag is not None, "station_push_fragment must return text with nearby 暗 lines"
        assert "有人目击可疑人物" in frag, "暗 clue text must be in ambient fragment"

    def test_ming_quest_not_in_ambient_fragment(self):
        """明 quests do NOT appear in station_push_fragment (ambient is for 暗 lines only).

        Hardened canary: build a world that has a surfaced 明 line WITH anchor/l3_anchor
        set to the protagonist's current town/venue, then assert its clue text AND [id]
        are ABSENT from the ambient fragment (state==明 filter must exclude it).
        Positive control: a still-暗 line at the same venue MUST appear.
        """
        from kernel.events import open_store
        import os, tempfile
        from systems.lore import LoreSystem

        r = _reg()

        # Build world: qingshi_town (L2) → market (L3), hero at market
        events = [
            kernel_event("place_created", day=1, scene="s1", summary="qingshi_town",
                         deltas={"id": "qingshi_town", "level": 2, "kind": "settlement"}, turn=0),
            kernel_event("place_created", day=1, scene="s1", summary="market",
                         deltas={"id": "market", "level": 3, "kind": "venue",
                                 "parent": "qingshi_town"}, turn=0),
            kernel_event("entity_created", day=1, scene="s1", summary="hero",
                         deltas={"id": "hero", "etype": "Character", "tier": "tracked"}, turn=0),
            kernel_event("entity_moved", day=1, scene="s1", summary="hero→market",
                         deltas={"who": "hero", "to": "market"}, turn=0),
            # 暗 line created WITH anchor=qingshi_town + l3_anchor=market (current venue)
            # This line will be surfaced to 明 — it must NOT appear in ambient.
            kernel_event("lore_created", day=1, scene="s1", summary="surfaced line",
                         deltas={
                             "id": "surfaced_line",
                             "complexity": "complex",
                             "about": "码头浮尸案",
                             "secret": "秘密凶手",
                             "anchor": "qingshi_town",
                             "description": "码头发现浮尸",
                             "trigger": "玩家调查码头",
                             "l3_anchor": "market",
                             "stages": [{"hint": "浮尸线索A"}],
                             "threshold": 100,
                         }, turn=1),
            # Surface it to 明 (state becomes 明, status remains active)
            kernel_event("quest_surfaced", day=1, scene="s1", summary="surf",
                         deltas={"id": "surfaced_line"}, turn=2),
            # 暗 positive-control line at same venue — must still appear
            kernel_event("lore_created", day=1, scene="s1", summary="still-dark line",
                         deltas={
                             "id": "dark_control",
                             "complexity": "low",
                             "about": "市场谣言",
                             "secret": "另一个秘密",
                             "anchor": "qingshi_town",
                             "description": "市场窃窃私语",
                             "trigger": "玩家打听",
                             "l3_anchor": "market",
                             "stages": [{"hint": "暗控制线索B"}],
                             "threshold": 100,
                         }, turn=3),
            kernel_event("lore_advanced", day=1, scene="s1", summary="adv",
                         deltas={"id": "dark_control", "stage_idx": 0, "hint": "暗控制线索B"},
                         turn=4),
        ]

        w = project(r, events)
        scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "market"}

        # Verify preconditions
        lines = w["systems"]["lore"]["lines"]
        assert lines["surfaced_line"]["state"] == "明", "precondition: surfaced_line must be 明"
        assert lines["dark_control"]["state"] == "暗", "precondition: dark_control must be 暗"

        frag = station_push_fragment(r, w, scene)
        assert frag is not None, "ambient fragment must not be None (暗 control line is present)"

        # Hardened assertion: 明 line's clue text AND [id] must NOT appear in ambient
        assert "浮尸线索A" not in frag, (
            "明 line's clue text must be absent from ambient (it leaked — state filter missing)"
        )
        assert "[surfaced_line]" not in frag, (
            "明 line's [id] must be absent from ambient fragment"
        )

        # Positive control: 暗 line's clue text must appear
        assert "暗控制线索B" in frag or "[dark_control]" in frag, (
            "暗 control line must still appear in ambient fragment"
        )

    def test_no_dark_lines_at_venue_returns_none(self):
        """When no 暗 lines are at the current venue, station_push_fragment returns None."""
        r = _reg()
        events = [
            kernel_event("place_created", day=1, scene="s1", summary="lone_town",
                         deltas={"id": "lone_town", "level": 2, "kind": "settlement"}, turn=0),
            kernel_event("place_created", day=1, scene="s1", summary="lone_venue",
                         deltas={"id": "lone_venue", "level": 3, "kind": "venue",
                                 "parent": "lone_town"}, turn=0),
            kernel_event("entity_created", day=1, scene="s1", summary="hero",
                         deltas={"id": "hero", "etype": "Character", "tier": "tracked"}, turn=0),
            kernel_event("entity_moved", day=1, scene="s1", summary="hero→lone_venue",
                         deltas={"who": "hero", "to": "lone_venue"}, turn=0),
        ]
        w = project(r, events)
        scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "lone_venue"}
        result = station_push_fragment(r, w, scene)
        assert result is None


# ---------------------------------------------------------------------------
# T5.3: 暗 line is NOT advanceable (only ambient clue, no 明账 entry)
# ---------------------------------------------------------------------------

class TestDarkLineNotAdvanceable:
    """A 暗 line must not be presented as advanceable; only its ambient clue is shown."""

    def test_dark_line_not_in_quests_advance_affordance(self):
        """The 明账 fragment affordance does not mention 暗 lines as advanceable."""
        r, w, scene = _build_unified_world()
        ls = LoreSystem()
        frag = ls.inject(scene, w)
        # 明账 exists (due to quest_merchant 明 quest)
        assert frag is not None
        # rumor_line is 暗 — it should not appear in the ledger
        assert "rumor_line" not in frag.text
        # The ambient fragment (station_push_fragment) shows rumor_line as a clue only
        ambient = station_push_fragment(r, w, scene)
        assert ambient is not None
        # Clue text present as environment hint
        assert "有人目击可疑人物" in ambient
        # Not in 明账 (not advanceable via quests section)
        assert "rumor_line" not in frag.text

    def test_validate_rejects_advance_on_dark_line(self):
        """LoreSystem.validate rejects an 'advance' op on a 暗 line (state-partition guard)."""
        from systems.lore import LoreSystem
        r, w, scene = _build_unified_world()
        ls = LoreSystem()
        # Try to narrator-advance the 暗 line via quests section
        decl = [{"op": "advance", "id": "rumor_line", "summary": "推进了暗线"}]
        errs = ls.validate("quests", decl, w)
        assert len(errs) > 0, "advancing a 暗 line must produce a validation error"
        assert any("state" in e.hint.lower() or "明" in e.hint for e in errs)


# ---------------------------------------------------------------------------
# T5.4: strategy always appends station_push_fragment to context
# ---------------------------------------------------------------------------

class TestStrategyAlwaysAppendAmbient:
    """AuthorStrategy.produce always appends station_push_fragment — no mode switch."""

    def test_ambient_in_user_message_with_lore(self):
        """With 暗 lines in range, strategy user message contains ambient fragment."""
        r, w, scene = _build_unified_world()
        provider = FakeLLMProvider(json_responses=[_commit_data(narration="叙事")])
        strat = AuthorStrategy()
        commit = strat.produce(r, w, scene, "查看", provider=provider)
        assert commit.narration == "叙事"
        user_content = next(m["content"] for m in strat._messages if m["role"] == "user")
        assert "本地暗线" in user_content or "有人目击" in user_content, \
            "ambient 暗 fragment must be in user message when 暗 lines are in range"

    def test_no_ambient_when_no_lore_system(self):
        """Without LoreSystem, station_push_fragment returns None → strategy works unchanged."""
        r_bare = Registry()
        r_bare.register(OntologySystem())
        w_bare = empty_world(r_bare)
        scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "nowhere"}
        provider = FakeLLMProvider(json_responses=[_commit_data(narration="bare")])
        strat = AuthorStrategy()
        commit = strat.produce(r_bare, w_bare, scene, "行动", provider=provider)
        assert commit.narration == "bare"

    def test_no_disclosure_mode_key_needed(self):
        """scene has no disclosure_mode key → strategy runs fine (key no longer used)."""
        r, w, scene = _build_unified_world()
        # Explicitly ensure disclosure_mode is absent
        assert "disclosure_mode" not in scene
        provider = FakeLLMProvider(json_responses=[_commit_data(narration="clean")])
        strat = AuthorStrategy()
        commit = strat.produce(r, w, scene, "行动", provider=provider)
        assert commit.narration == "clean"
