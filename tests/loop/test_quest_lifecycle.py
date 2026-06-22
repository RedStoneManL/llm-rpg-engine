"""tests/loop/test_quest_lifecycle.py — Full lifecycle integration test (T1–T5).

Drives ONE multi-turn play through ALL of the Unified Questline state machine
with a FakeLLMProvider (no network, fully deterministic).

Lifecycle steps covered:
  1. 暗 brew + ambient disclosure: 暗骰 advances a 暗 line; clue in station_push_fragment.
  2. 暗→明 surface (player-pull): quests:[{op:"surface"}] → state 暗→明; 明账 inject.
  3. 明 advance (narrator): quests:[{op:"advance", summary}] → summary updated in 明账.
  4. 明→暗 demote-on-leave + JIT: protagonist moves to different town; jit stages in; 暗.
  5. complex 暗 world-push surface: 暗骰 walks complex line to last stage → quest_surfaced{by:world}.
  6. 明→了结 resolve: quests:[{op:"resolve"}] → state 了结.

Key invariant asserts:
  - 暗骰 never touches a 明 line (state 暗 check in run_lore).
  - quests "advance"/"resolve" on a 暗 line is a ValidationError → dropped/rejected.

World:
  镇 (L2) ⊃ 市 (L3, l3_anchor for dark_line) + 寨 (L3)
  外镇 (L2) ⊃ 郊 (L3)

Protagonist arc:
  Steps 1-4a: at 市 (inside 镇)
  Steps 4b+: at 郊 (inside 外镇, triggers demote for 镇-anchored lines)

FakeLLMProvider response sequencing:
  run_turn calls provider.complete_json once per produce call (AuthorStrategy).
  The demote step (step 4) also calls jit_resequence → provider.complete_json a 2nd time.
  FakeLLMProvider cycles its json_responses list; each call consumes one response.
  Sequence for step 4: [narration_commit, jit_stages]
  For steps without demote: single narration commit suffices.

World-push (step 5) design:
  complex_line is created AFTER protagonist is at 郊 (step 5 setup), so it is never
  accidentally demoted during earlier turns.  It starts at stage 0 so one run_lore
  push reaches the last stage (stage 1) and fires quest_surfaced{by:"world"}.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import open_store, kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.lore import LoreSystem
from loop.lore import create_lore_line, run_lore
from loop.lore_disclosure import station_push_fragment
from loop.turn import run_turn
from loop.strategy import AuthorStrategy
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Registry/store helpers
# ---------------------------------------------------------------------------

def _full_reg():
    """Registry with OntologySystem, PlaceSystem, CharacterSystem, LoreSystem."""
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(
        os.path.join(d, "e.db"),
        os.path.join(d, "e.jsonl"),
        allowed_types=registry.event_types(),
    )


# ---------------------------------------------------------------------------
# Canned LLM responses
# ---------------------------------------------------------------------------

_NARRATION_ONLY = {"narration": "无事发生。"}

_NARRATION_SURFACE = {
    "narration": "玩家主动揭开了暗线。",
    "quests": [{"op": "surface", "id": "dark_line"}],
}

_NARRATION_ADVANCE = {
    "narration": "任务有所进展。",
    "quests": [{"op": "advance", "id": "dark_line", "summary": "玩家找到了关键线索"}],
}

_NARRATION_DEMOTE_MOVE = {"narration": "勇者离开了小镇。"}

_JIT_STAGES = {"stages": [{"hint": "新阶段A"}, {"hint": "新阶段B"}]}

_NARRATION_NOOP = {"narration": "又是平静的一天。"}


# ---------------------------------------------------------------------------
# World setup helper
# ---------------------------------------------------------------------------

def _seed_world(store):
    """Seed the town graph and protagonist.

    镇 (L2) → 市 (L3) + 寨 (L3)
    外镇 (L2) → 郊 (L3)
    hero starts at 市 (inside 镇).
    """
    events = [
        # L2 towns
        kernel_event("place_created", day=1, scene="s1", summary="镇",
                     deltas={"id": "镇", "level": 2, "kind": "settlement", "seed": "zhen"},
                     turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="外镇",
                     deltas={"id": "外镇", "level": 2, "kind": "settlement", "seed": "waizhen"},
                     turn=0),
        # L3 venues inside 镇
        kernel_event("place_created", day=1, scene="s1", summary="市",
                     deltas={"id": "市", "level": 3, "kind": "venue", "parent": "镇"},
                     turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="寨",
                     deltas={"id": "寨", "level": 3, "kind": "venue", "parent": "镇"},
                     turn=0),
        # L3 venue inside 外镇
        kernel_event("place_created", day=1, scene="s1", summary="郊",
                     deltas={"id": "郊", "level": 3, "kind": "venue", "parent": "外镇"},
                     turn=0),
        # adjacency links
        kernel_event("place_linked", day=1, scene="s1", summary="市-寨",
                     deltas={"a": "市", "b": "寨"}, turn=0),
        # protagonist
        kernel_event("character_created", day=1, scene="s1", summary="hero",
                     deltas={"id": "hero", "tier": "tracked", "sketch": "勇者", "goal": "冒险"},
                     turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="hero→市",
                     deltas={"who": "hero", "to": "市"},
                     turn=0),
    ]
    for ev in events:
        store.append(ev)


# ---------------------------------------------------------------------------
# Lore line skeletons
# ---------------------------------------------------------------------------

# Simple line anchored at 镇/市 — follows protagonist through steps 1–4, 6
_SK_DARK_LINE = {
    "id": "dark_line",
    "complexity": "simple",
    "about": "镇上神秘失踪事件",
    "secret": "幕后是商会阴谋",
    "anchor": "镇",
    "description": "失踪事件的传言",
    "trigger": "玩家打听居民",
    "l3_anchor": "市",
    "stages": [{"hint": "有人目击陌生人"}, {"hint": "失踪者留下血迹"}],
    "threshold": 100,   # always advances (d100 always <= 100)
}

# Complex line introduced AFTER protagonist moves to 外镇 — for step 5 world-push
# Anchored at 外镇/郊 to match the post-move protagonist location.
# Created at stage 0 so ONE additional 暗骰 push reaches last stage (stage 1).
_SK_COMPLEX_LINE = {
    "id": "complex_line",
    "complexity": "complex",
    "about": "外镇的古老诅咒",
    "secret": "诅咒来自上古遗迹",
    "anchor": "外镇",
    "description": "外镇诡异事件",
    "trigger": "玩家调查外镇",
    "l3_anchor": "郊",
    "stages": [{"hint": "夜晚有奇怪声音"}, {"hint": "诅咒即将爆发"}],
    "threshold": 100,
}


# ===========================================================================
# THE LIFECYCLE TEST
# ===========================================================================

class TestQuestFullLifecycle:
    """End-to-end lifecycle: 暗 brew → surface → advance → demote → world-push → resolve."""

    def test_full_lifecycle(self):
        """Single cohesive test driving all 6 lifecycle steps in sequence."""

        r = _full_reg()
        store = _store(r)

        # -----------------------------------------------------------------------
        # SETUP: seed world + dark_line (no complex_line yet)
        # -----------------------------------------------------------------------
        _seed_world(store)
        create_lore_line(store, _SK_DARK_LINE, day=1, scene="s1", turn=0)

        world = project(r, store.iter_events())
        scene_at_市 = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "市"}

        # Verify initial state
        assert world["systems"]["lore"]["lines"]["dark_line"]["state"] == "暗"
        assert world["systems"]["lore"]["lines"]["dark_line"]["stage_idx"] == -1

        # -----------------------------------------------------------------------
        # STEP 1: 暗 brew + ambient disclosure
        # 暗骰 advances dark_line (threshold=100 → always fires); clue appears
        # in station_push_fragment (B-mode ambient push).
        # -----------------------------------------------------------------------
        result1 = run_turn(
            r, store, world, scene_at_市, "四处打探",
            strategy=AuthorStrategy(),
            provider=FakeLLMProvider(json_responses=[_NARRATION_ONLY]),
        )
        world = result1.world

        ln = world["systems"]["lore"]["lines"]["dark_line"]
        # 暗骰 must have advanced dark_line (threshold=100 always passes)
        assert ln["stage_idx"] >= 0, (
            f"step 1: 暗骰 should have advanced dark_line; got stage_idx={ln['stage_idx']}"
        )
        assert ln["state"] == "暗", "step 1: dark_line must remain 暗 after 暗骰"
        assert len(ln["clues_dropped"]) > 0, "step 1: a clue must have been dropped"

        # Ambient disclosure: station_push_fragment includes the beat/clue
        push = station_push_fragment(r, world, scene_at_市)
        assert push is not None, "step 1: station_push_fragment must return content at 市"
        assert "〔本地暗线·环境可织入" in push, "step 1: fragment must have the 暗线 header"
        # dark_line.l3_anchor == 市 == current_l3 → L1 disclosure: beat + clue appear
        latest_clue = ln["clues_dropped"][-1]
        assert latest_clue in push, (
            f"step 1: latest clue {latest_clue!r} should appear in station_push_fragment"
        )

        # KEY INVARIANT CHECK: 明 lines are never touched by 暗骰
        # (here dark_line is still 暗 — confirmed above)

        # -----------------------------------------------------------------------
        # STEP 2: 暗→明 surface (player-pull)
        # quests:[{op:"surface", id:"dark_line"}] → state 暗→明; 明账 inject includes it.
        # -----------------------------------------------------------------------
        result2 = run_turn(
            r, store, world, scene_at_市, "玩家主动接线",
            strategy=AuthorStrategy(),
            provider=FakeLLMProvider(json_responses=[_NARRATION_SURFACE]),
        )
        world = result2.world

        ln2 = world["systems"]["lore"]["lines"]["dark_line"]
        assert ln2["state"] == "明", (
            f"step 2: after surface commit, dark_line must be 明; got {ln2['state']}"
        )
        assert ln2.get("surfaced_turn") is not None, "step 2: surfaced_turn must be set"

        # 明账 inject must include dark_line
        lore_sys = r.owner_of_event("quest_surfaced")
        fragment = lore_sys.inject({"protagonist": "hero"}, world)
        assert fragment is not None, "step 2: inject must return a Fragment for 明 lines"
        assert "dark_line" in fragment.text, (
            "step 2: dark_line id must appear in the 明账 ledger"
        )

        # KEY INVARIANT: 暗骰 must NOT advance a 明 line.
        # Verify directly: run_lore on current world yields no lore_advanced for dark_line.
        stage_before = ln2["stage_idx"]
        lore_events_check = run_lore(r, store, world)
        adv_for_dark = [e for e in lore_events_check
                        if e["type"] == "lore_advanced"
                        and e["deltas"].get("id") == "dark_line"]
        assert len(adv_for_dark) == 0, (
            "step 2 invariant: 暗骰 must NOT emit lore_advanced for a 明 line"
        )
        # Re-project after the run_lore check (it may have appended to store)
        world = project(r, store.iter_events())
        ln2_after = world["systems"]["lore"]["lines"]["dark_line"]
        assert ln2_after["stage_idx"] == stage_before, (
            "step 2 invariant: 明 line stage_idx unchanged by 暗骰"
        )
        assert ln2_after["state"] == "明", (
            "step 2 invariant: 明 line state unchanged by 暗骰"
        )

        # -----------------------------------------------------------------------
        # STEP 3: 明 advance (narrator)
        # quests:[{op:"advance", id:"dark_line", summary:"..."}] → summary updated.
        # -----------------------------------------------------------------------
        result3 = run_turn(
            r, store, world, scene_at_市, "继续推进任务",
            strategy=AuthorStrategy(),
            provider=FakeLLMProvider(json_responses=[_NARRATION_ADVANCE]),
        )
        world = result3.world

        ln3 = world["systems"]["lore"]["lines"]["dark_line"]
        assert ln3["state"] == "明", "step 3: advance must keep line 明"
        assert ln3.get("summary") == "玩家找到了关键线索", (
            f"step 3: summary should be updated; got {ln3.get('summary')!r}"
        )

        # 明账 should reflect the updated summary
        fragment3 = lore_sys.inject({"protagonist": "hero"}, world)
        assert fragment3 is not None
        assert "玩家找到了关键线索" in fragment3.text, (
            "step 3: updated summary must appear in 明账 inject"
        )

        # -----------------------------------------------------------------------
        # STEP 4: 明→暗 demote-on-leave + JIT resequence
        # Move protagonist to 郊 (inside 外镇, different from 镇).
        # dark_line.anchor == 镇 != 外镇 (current_l2) → demote fires.
        #
        # FakeLLMProvider response ordering:
        #   response 0: narration commit (consumed by produce_turn/AuthorStrategy)
        #   response 1: jit stages (consumed by jit_resequence via provider.complete_json)
        # -----------------------------------------------------------------------
        # Move hero to 郊 directly via event (pre-turn state setup)
        store.append(kernel_event(
            "entity_moved", day=1, scene="s1", summary="hero→郊",
            deltas={"who": "hero", "to": "郊"},
            turn=99,
        ))
        world = project(r, store.iter_events())
        scene_at_郊 = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "郊"}

        # Verify protagonist is at 郊 (L3 inside 外镇)
        g = world["systems"]["ontology"]
        locs = g.neighbors("hero", "located_in", 1)
        assert locs and locs[0] == "郊", f"setup: hero should be at 郊, got {locs}"

        # Provider: narration first, then jit stages for jit_resequence
        provider4 = FakeLLMProvider(json_responses=[_NARRATION_DEMOTE_MOVE, _JIT_STAGES])

        result4 = run_turn(
            r, store, world, scene_at_郊, "在外镇行动",
            strategy=AuthorStrategy(),
            provider=provider4,
        )
        world = result4.world

        ln4 = world["systems"]["lore"]["lines"]["dark_line"]
        assert ln4["state"] == "暗", (
            f"step 4: dark_line should be demoted to 暗 after leaving 镇; got {ln4['state']}"
        )
        assert ln4["stage_idx"] == -1, (
            f"step 4: stage_idx must be reset to -1 after demote; got {ln4['stage_idx']}"
        )
        # JIT stages from provider replace old stages
        assert ln4["stages"] == [{"hint": "新阶段A"}, {"hint": "新阶段B"}], (
            f"step 4: JIT stages should replace old stages; got {ln4['stages']}"
        )
        # clues_dropped preserved through demote
        assert len(ln4["clues_dropped"]) > 0, "step 4: clues_dropped must be preserved after demote"

        # Verify quest_demoted event in store
        all_events = list(store.iter_events())
        demoted_evs = [e for e in all_events if e["type"] == "quest_demoted"
                       and e["deltas"].get("id") == "dark_line"]
        assert len(demoted_evs) >= 1, "step 4: quest_demoted event must be in store"

        # -----------------------------------------------------------------------
        # STEP 5: complex 暗 world-push surface
        # Introduce complex_line NOW (protagonist already at 郊 inside 外镇).
        # complex_line.anchor == 外镇 == current_l2 → NOT demoted when at 郊.
        # Pre-seed at stage 0; 暗骰 (threshold=100) will advance to stage 1 (last)
        # → complex line world-push surfaced → quest_surfaced{by:"world"}.
        # -----------------------------------------------------------------------
        create_lore_line(store, _SK_COMPLEX_LINE, day=1, scene="s1", turn=100)
        # Pre-advance to stage 0 so the NEXT 暗骰 push hits the last stage (stage 1)
        store.append(kernel_event(
            "lore_advanced", day=1, scene="s1", summary="complex pre-advance",
            deltas={"id": "complex_line", "stage_idx": 0, "hint": "夜晚有奇怪声音"},
            turn=100,
        ))
        world = project(r, store.iter_events())

        ln_complex_pre = world["systems"]["lore"]["lines"]["complex_line"]
        assert ln_complex_pre["state"] == "暗", "step 5 setup: complex_line must be 暗"
        assert ln_complex_pre["stage_idx"] == 0, (
            f"step 5 setup: complex_line must be at stage 0; got {ln_complex_pre['stage_idx']}"
        )

        # Run a turn — 暗骰 in run_turn advances complex_line to stage 1 (last) → world-push surface
        result5 = run_turn(
            r, store, world, scene_at_郊, "探索外镇",
            strategy=AuthorStrategy(),
            provider=FakeLLMProvider(json_responses=[_NARRATION_NOOP]),
        )
        world = result5.world

        ln_complex5 = world["systems"]["lore"]["lines"]["complex_line"]
        assert ln_complex5["state"] == "明", (
            f"step 5: complex line at last stage must be world-pushed to 明; got {ln_complex5['state']}"
        )
        assert ln_complex5["stage_idx"] == 1, (
            f"step 5: complex_line must be at stage 1 (last); got {ln_complex5['stage_idx']}"
        )

        # Verify quest_surfaced{by:"world"} in store
        all_events5 = list(store.iter_events())
        surfaced_world = [
            e for e in all_events5
            if e["type"] == "quest_surfaced"
            and e["deltas"].get("id") == "complex_line"
            and e["deltas"].get("by") == "world"
        ]
        assert len(surfaced_world) >= 1, (
            "step 5: quest_surfaced{by:'world'} must be in store for complex_line"
        )

        # KEY INVARIANT: 明 lines not advanced by 暗骰 (complex_line is now 明 at last stage;
        # run_lore skips both lines-at-last-stage and 明 lines).
        lore_events_step5 = run_lore(r, store, world)
        adv_for_complex = [e for e in lore_events_step5
                           if e["type"] == "lore_advanced"
                           and e["deltas"].get("id") == "complex_line"]
        assert len(adv_for_complex) == 0, (
            "step 5 invariant: 暗骰 must NOT emit lore_advanced for complex_line (now 明)"
        )
        world = project(r, store.iter_events())

        # -----------------------------------------------------------------------
        # STEP 6: 明→了结 resolve
        # complex_line is 明 (world-pushed). Player resolves it.
        # -----------------------------------------------------------------------
        narration_resolve = {
            "narration": "诅咒被终结了。",
            "quests": [{"op": "resolve", "id": "complex_line", "summary": "玩家破解了古老诅咒"}],
        }

        result6 = run_turn(
            r, store, world, scene_at_郊, "终结诅咒",
            strategy=AuthorStrategy(),
            provider=FakeLLMProvider(json_responses=[narration_resolve]),
        )
        world = result6.world

        ln6 = world["systems"]["lore"]["lines"]["complex_line"]
        assert ln6["state"] == "了结", (
            f"step 6: after resolve, complex_line must be 了结; got {ln6['state']}"
        )
        assert ln6.get("resolved") is not None, "step 6: resolved dict must be set"
        assert ln6["resolved"]["by"] == "player", (
            f"step 6: resolved.by must be 'player'; got {ln6['resolved']['by']}"
        )
        assert ln6["resolved"]["summary"] == "玩家破解了古老诅咒", (
            f"step 6: resolved.summary mismatch: {ln6['resolved']['summary']!r}"
        )

        # Verify quest_resolved event in store
        all_events6 = list(store.iter_events())
        resolved_evs = [
            e for e in all_events6
            if e["type"] == "quest_resolved"
            and e["deltas"].get("id") == "complex_line"
        ]
        assert len(resolved_evs) >= 1, "step 6: quest_resolved must be in store"

        # 明账 must no longer include complex_line (it's 了结, not 明)
        fragment6 = lore_sys.inject({"protagonist": "hero"}, world)
        # Only 明 lines appear; 了结 must not
        if fragment6 is not None:
            assert "complex_line" not in fragment6.text, (
                "step 6: 了结 line must not appear in 明账 inject"
            )


# ---------------------------------------------------------------------------
# BONUS: KEY INVARIANT — quests section validates state partition strictly
# ---------------------------------------------------------------------------

class TestQuestInvariantValidation:
    """Validate that the state-partition invariants are enforced by LoreSystem.validate."""

    def _setup(self):
        """Return (r, world) with one 暗 line (dark_line)."""
        r = _full_reg()
        store = _store(r)
        _seed_world(store)
        create_lore_line(store, _SK_DARK_LINE, day=1, scene="s1", turn=0)
        world = project(r, store.iter_events())
        return r, store, world

    def test_advance_on_an_line_is_validation_error(self):
        """quests:[{op:"advance", id:<暗 line>}] must produce a ValidationError (wrong_state)."""
        r, store, world = self._setup()

        assert world["systems"]["lore"]["lines"]["dark_line"]["state"] == "暗"

        lore_sys = r.owner_of_event("quest_advanced")
        decl = [{"op": "advance", "id": "dark_line", "summary": "不允许推进暗线"}]
        errors = lore_sys.validate("quests", decl, world)

        assert len(errors) > 0, "advancing a 暗 line must produce a ValidationError"
        err_codes = [e.code for e in errors]
        assert "wrong_state" in err_codes, (
            f"Expected wrong_state error for advance on 暗 line; got codes: {err_codes}"
        )

    def test_resolve_on_an_line_is_validation_error(self):
        """quests:[{op:"resolve", id:<暗 line>}] must produce a ValidationError (wrong_state)."""
        r, store, world = self._setup()

        lore_sys = r.owner_of_event("quest_resolved")
        decl = [{"op": "resolve", "id": "dark_line", "summary": "不允许收束暗线"}]
        errors = lore_sys.validate("quests", decl, world)

        assert len(errors) > 0, "resolving a 暗 line must produce a ValidationError"
        err_codes = [e.code for e in errors]
        assert "wrong_state" in err_codes

    def test_surface_on_ming_line_is_validation_error(self):
        """quests:[{op:"surface", id:<明 line>}] must produce a ValidationError (wrong_state)."""
        r, store, world = self._setup()
        # Surface dark_line first
        store.append(kernel_event(
            "quest_surfaced", day=1, scene="s1", summary="surface",
            deltas={"id": "dark_line"},
            turn=1,
        ))
        world = project(r, store.iter_events())

        assert world["systems"]["lore"]["lines"]["dark_line"]["state"] == "明"

        lore_sys = r.owner_of_event("quest_surfaced")
        decl = [{"op": "surface", "id": "dark_line"}]
        errors = lore_sys.validate("quests", decl, world)

        assert len(errors) > 0, "surfacing a 明 line must produce a ValidationError"
        err_codes = [e.code for e in errors]
        assert "wrong_state" in err_codes
