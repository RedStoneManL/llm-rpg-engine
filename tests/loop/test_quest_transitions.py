"""T4: autonomous quest transitions — world-push surfacing + demote-on-leave.

Tests:
1. World-push: a complex 暗 line 暗骰'd to its LAST stage → quest_surfaced(by:"world") emitted → state 明.
2. World-push: a NON-complex (simple/medium) 暗 line at its last stage → NOT surfaced (stays 暗).
3. Demote-on-leave: a 明 line anchored at town A, protagonist now at town B → quest_demoted emitted.
4. Demote-on-leave: a 明 line anchored at current town → NOT demoted.
5. Demote uses jit_resequence (FakeLLMProvider canned stages → demoted line.stages == canned).
6. quest_demoted apply: 明→暗 + new_stages + stage_idx -1 + clues_dropped kept.
7. run_turn integration: protagonist leaves a 明 line's town → line ends 暗.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import open_store, kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem
from loop.lore import create_lore_line, run_lore
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(LoreSystem())
    return r


def _reg_full():
    """Registry with Place + Character systems needed for dormancy (★6).

    Tests that exercise simple/medium 暗 line advancing must use this so
    the store accepts place_created / character_created / entity_moved events,
    allowing the dormancy gate to resolve the protagonist's L2 town.
    """
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
    return open_store(
        os.path.join(d, "e.db"),
        os.path.join(d, "e.jsonl"),
        allowed_types=registry.event_types(),
    )


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


# A complex line with 2 stages (so stage_idx 1 == last stage)
_SK_COMPLEX = {
    "id": "complex_line",
    "complexity": "complex",
    "about": "复杂暗线",
    "secret": "隐情",
    "anchor": "town_a",
    "description": "描述",
    "trigger": "触发",
    "l3_anchor": "town_a_market",
    "stages": [{"hint": "complex-clue-0"}, {"hint": "complex-clue-1"}],
    "threshold": 100,  # always advances
}

# A simple line with 2 stages — should NOT world-surface
_SK_SIMPLE = {
    "id": "simple_line",
    "complexity": "simple",
    "about": "简单暗线",
    "secret": "隐情",
    "anchor": "town_a",
    "description": "描述",
    "trigger": "触发",
    "l3_anchor": "town_a_market",
    "stages": [{"hint": "simple-clue-0"}, {"hint": "simple-clue-1"}],
    "threshold": 100,
}

# A medium line — also should NOT world-surface
_SK_MEDIUM = {
    "id": "medium_line",
    "complexity": "medium",
    "about": "中等暗线",
    "secret": "隐情",
    "anchor": "town_a",
    "description": "描述",
    "trigger": "触发",
    "l3_anchor": "town_a_market",
    "stages": [{"hint": "medium-clue-0"}, {"hint": "medium-clue-1"}],
    "threshold": 100,
}

_WORLD_BASE = {"meta": {"day": 1, "scene": "s1", "campaign_seed": 0}, "systems": {}}


# ---------------------------------------------------------------------------
# 1. World-push surfacing: complex line at last stage → quest_surfaced(by:"world")
# ---------------------------------------------------------------------------

def test_world_push_surfaces_complex_line_at_last_stage():
    """A complex 暗 line rolled to its last stage → quest_surfaced{by:"world"} emitted → state 明."""
    r = _reg()
    store = _store(r)

    # First, create the line and advance it to stage 0 (one step before last=1)
    create_lore_line(store, _SK_COMPLEX, day=1, scene="s1", turn=1)
    # Manually append a lore_advanced to bring it to stage_idx=0
    store.append(kernel_event(
        "lore_advanced", day=1, scene="s1", summary="adv",
        deltas={"id": "complex_line", "stage_idx": 0, "hint": "complex-clue-0"},
        turn=2,
    ))
    w = project(r, store.iter_events())
    # Sanity: line is at stage 0, still 暗
    ln = w["systems"]["lore"]["lines"]["complex_line"]
    assert ln["stage_idx"] == 0
    assert ln["state"] == "暗"
    # last stage is index 1 (len=2); now run_lore should advance to 1 AND emit quest_surfaced

    appended = run_lore(r, store, w)

    # Should have both lore_advanced AND quest_surfaced
    types = [e["type"] for e in appended]
    assert "lore_advanced" in types, "暗骰 should still emit lore_advanced"
    assert "quest_surfaced" in types, "complex line at last stage must emit quest_surfaced"

    surfaced_ev = next(e for e in appended if e["type"] == "quest_surfaced")
    assert surfaced_ev["deltas"]["id"] == "complex_line"
    assert surfaced_ev["deltas"]["by"] == "world"

    # After projection: state should be 明
    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["complex_line"]["state"] == "明"


# ---------------------------------------------------------------------------
# 2. World-push: simple / medium lines at last stage → NOT surfaced
# ---------------------------------------------------------------------------

def test_world_push_does_not_surface_simple_line():
    """A simple 暗 line advancing to its last stage stays 暗 (no world-push)."""
    # Uses _reg_full + protagonist in anchor town so dormancy (★6) doesn't freeze the line.
    r = _reg_full()
    store = _store(r)
    _seed_protagonist_in_anchor(store, "town_a")

    create_lore_line(store, _SK_SIMPLE, day=1, scene="s1", turn=1)
    # Advance to stage 0 so next run goes to last (stage 1)
    store.append(kernel_event(
        "lore_advanced", day=1, scene="s1", summary="adv",
        deltas={"id": "simple_line", "stage_idx": 0, "hint": "simple-clue-0"},
        turn=2,
    ))
    w = project(r, store.iter_events())

    appended = run_lore(r, store, w)

    # Must advance (lore_advanced) but must NOT emit quest_surfaced
    types = [e["type"] for e in appended]
    assert "lore_advanced" in types
    assert "quest_surfaced" not in types, "simple line must NOT be world-surfaced"

    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["simple_line"]["state"] == "暗"


def test_world_push_does_not_surface_medium_line():
    """A medium 暗 line at its last stage stays 暗 (no world-push)."""
    r = _reg()
    store = _store(r)

    create_lore_line(store, _SK_MEDIUM, day=1, scene="s1", turn=1)
    store.append(kernel_event(
        "lore_advanced", day=1, scene="s1", summary="adv",
        deltas={"id": "medium_line", "stage_idx": 0, "hint": "medium-clue-0"},
        turn=2,
    ))
    w = project(r, store.iter_events())

    appended = run_lore(r, store, w)

    types = [e["type"] for e in appended]
    assert "quest_surfaced" not in types, "medium line must NOT be world-surfaced"
    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["medium_line"]["state"] == "暗"


# ---------------------------------------------------------------------------
# 3. Demote-on-leave: protagonist left anchor town → quest_demoted emitted
# ---------------------------------------------------------------------------

def _setup_world_with_protagonist_and_line(r, store, line_anchor, protagonist_town):
    """Set up a world: one 明 line anchored at line_anchor, protagonist at protagonist_town."""
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    # Register extra systems if not already
    # (r already has OntologySystem + LoreSystem from _reg())

    # Seed town_a and town_b as L2 places
    for tid, eid in [("town_a", "town_a"), ("town_b", "town_b")]:
        store.append(kernel_event(
            "place_created", day=1, scene="s1", summary=f"place {tid}",
            deltas={"id": tid, "level": 2, "kind": "settlement", "seed": tid},
            turn=0,
        ))

    # Create protagonist character
    store.append(kernel_event(
        "character_created", day=1, scene="s1", summary="hero",
        deltas={"id": "hero", "tier": "tracked", "sketch": "勇者", "goal": "冒险"},
        turn=0,
    ))

    # Move protagonist to current town
    store.append(kernel_event(
        "entity_moved", day=1, scene="s1", summary="move",
        deltas={"who": "hero", "to": protagonist_town},
        turn=0,
    ))

    # Create a 暗 line anchored at line_anchor
    sk = {**_SK_COMPLEX, "id": "quest_line", "anchor": line_anchor}
    create_lore_line(store, sk, day=1, scene="s1", turn=0)

    # Emit quest_surfaced to flip it to 明 state
    store.append(kernel_event(
        "quest_surfaced", day=1, scene="s1", summary="surface",
        deltas={"id": "quest_line"},
        turn=1,
    ))

    return project(r, store.iter_events())


def test_demote_on_leave_emits_quest_demoted():
    """Protagonist moved away from 明 line's anchor town → quest_demoted emitted → state 暗."""
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)
    # line anchored at town_a, protagonist now in town_b
    w = _setup_world_with_protagonist_and_line(r, store, "town_a", "town_b")

    # Verify preconditions
    ln = w["systems"]["lore"]["lines"]["quest_line"]
    assert ln["state"] == "明", f"Expected 明, got {ln['state']}"

    # Run the demote-on-leave check via run_turn
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    # canned commit: simple narration with no unowned sections (no clock, no reasons)
    # so that validate_commit produces no errors → no repair loops → single narration call.
    canned = {"narration": "勇者离开了小镇。"}
    provider_canned = {"stages": [{"hint": "新续写stage-1"}, {"hint": "新续写stage-2"}]}
    # FakeLLMProvider cycles json_responses; with no repair loop: call 0=canned,
    # call 1 (jit_resequence) = provider_canned.
    provider = FakeLLMProvider(json_responses=[canned, provider_canned])

    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "town_b"}
    result = run_turn(
        r, store, w, scene, "向前走",
        strategy=AuthorStrategy(),
        provider=provider,
    )

    # Check the demoted event was appended to the store
    all_events = list(store.iter_events())
    demoted_events = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted_events) >= 1, "quest_demoted must be emitted when protagonist leaves anchor town"

    demotion = demoted_events[0]
    assert demotion["deltas"]["id"] == "quest_line"

    # After projection: state should be 暗
    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["quest_line"]["state"] == "暗"


def test_demote_on_leave_does_not_demote_same_town():
    """明 line anchored at the protagonist's CURRENT town → NOT demoted."""
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)
    # line anchored at town_a, protagonist also in town_a
    w = _setup_world_with_protagonist_and_line(r, store, "town_a", "town_a")

    ln = w["systems"]["lore"]["lines"]["quest_line"]
    assert ln["state"] == "明"

    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    canned = {"narration": "勇者在镇上活动。"}
    provider = FakeLLMProvider(json_responses=[canned])
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "town_a"}
    result = run_turn(
        r, store, w, scene, "在镇上逛",
        strategy=AuthorStrategy(),
        provider=provider,
    )

    all_events = list(store.iter_events())
    demoted_events = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted_events) == 0, "quest at same town must NOT be demoted"

    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["quest_line"]["state"] == "明"


# ---------------------------------------------------------------------------
# 5. Demote uses jit_resequence: demoted line.stages == canned stages
# ---------------------------------------------------------------------------

def test_demote_uses_jit_resequence_stages():
    """After demote-on-leave, the line's stages are replaced with jit_resequence output."""
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)
    w = _setup_world_with_protagonist_and_line(r, store, "town_a", "town_b")

    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    canned_narration = {"narration": "勇者离开了。"}
    canned_stages = {"stages": [{"hint": "jit-stage-A"}, {"hint": "jit-stage-B"}]}
    provider = FakeLLMProvider(json_responses=[canned_narration, canned_stages])

    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "town_b"}
    result = run_turn(
        r, store, w, scene, "离开",
        strategy=AuthorStrategy(),
        provider=provider,
    )

    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["quest_line"]
    # Line should be 暗 with the new jit stages
    assert ln2["state"] == "暗"
    assert ln2["stages"] == [{"hint": "jit-stage-A"}, {"hint": "jit-stage-B"}], (
        f"Expected jit stages, got {ln2['stages']}"
    )
    assert ln2["stage_idx"] == -1, "stage_idx must be reset to -1 after demote"


# ---------------------------------------------------------------------------
# 6. quest_demoted apply: 明→暗 + new_stages + stage_idx -1 + clues_dropped kept
# ---------------------------------------------------------------------------

def test_quest_demoted_apply():
    """quest_demoted: state 明→暗, new stages, stage_idx=-1, clues_dropped preserved."""
    r = _reg()
    store = _store(r)

    # Create a line as 暗, then surface it to 明
    sk = {**_SK_COMPLEX, "id": "dl", "stages": [{"hint": "s0"}, {"hint": "s1"}]}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    # Give it some history: advance to stage 0
    store.append(kernel_event(
        "lore_advanced", day=1, scene="s1", summary="adv",
        deltas={"id": "dl", "stage_idx": 0, "hint": "clue-drop-x"},
        turn=2,
    ))
    # Surface it
    store.append(kernel_event(
        "quest_surfaced", day=1, scene="s1", summary="surf",
        deltas={"id": "dl"},
        turn=3,
    ))

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["dl"]
    assert ln["state"] == "明"
    assert ln["clues_dropped"] == ["clue-drop-x"]

    # Emit quest_demoted with new_stages
    new_stages = [{"hint": "new-a"}, {"hint": "new-b"}, {"hint": "new-c"}]
    store.append(kernel_event(
        "quest_demoted", day=1, scene="s1", summary="demote",
        deltas={"id": "dl", "new_stages": new_stages},
        turn=4,
    ))

    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["dl"]

    assert ln2["state"] == "暗", f"Expected 暗, got {ln2['state']}"
    assert ln2["stages"] == new_stages, f"Expected new stages, got {ln2['stages']}"
    assert ln2["stage_idx"] == -1, f"Expected -1, got {ln2['stage_idx']}"
    # clues_dropped must be preserved
    assert ln2["clues_dropped"] == ["clue-drop-x"], (
        f"clues_dropped should be kept, got {ln2['clues_dropped']}"
    )


def test_quest_demoted_apply_skips_non_ming_line():
    """quest_demoted on a 暗 line is silently skipped (wrong state guard)."""
    r = _reg()
    store = _store(r)

    sk = {**_SK_COMPLEX, "id": "dl2"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    assert w["systems"]["lore"]["lines"]["dl2"]["state"] == "暗"

    # Try to demote a line that's still 暗 (should be a no-op/warn)
    store.append(kernel_event(
        "quest_demoted", day=1, scene="s1", summary="demote",
        deltas={"id": "dl2", "new_stages": [{"hint": "x"}]},
        turn=2,
    ))

    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["dl2"]
    # State should stay 暗 (not changed — the apply should require state==明)
    assert ln2["state"] == "暗"
    # Stages should not be replaced
    assert ln2["stages"] == sk["stages"]


# ---------------------------------------------------------------------------
# T1 follow-up: jit_resequence on provider EXCEPTION → fallback (never raises)
# ---------------------------------------------------------------------------

def test_jit_resequence_fallback_on_provider_exception():
    """jit_resequence when provider.complete_json RAISES → returns remaining original stages."""
    from loop.lore import jit_resequence

    class ExplodingProvider:
        def complete_json(self, system, user, schema):
            raise RuntimeError("模拟网络异常")

    line = {
        "id": "ex_line",
        "about": "炸掉的provider测试",
        "secret": "secret",
        "clues_dropped": [],
        "stages": [{"hint": "s0"}, {"hint": "s1"}, {"hint": "s2"}],
        "stage_idx": 0,  # remaining = stages[1:]
        "anchor": "town",
    }
    world = {"meta": {"day": 1}, "systems": {}}

    result = jit_resequence(line, world, ExplodingProvider())

    # Must NOT raise, must return remaining original stages
    expected = line["stages"][1:]  # [s1, s2]
    assert result == expected, f"Expected fallback {expected}, got {result}"


# ---------------------------------------------------------------------------
# Critical Fix: unresolvable protagonist town → no mass-demote
# ---------------------------------------------------------------------------

def test_unresolvable_town_does_not_mass_demote():
    """Protagonist at L3 with no L2 ancestor → unresolvable town → 明 line NOT demoted.

    This test locks the fix for the critical bug: when current_l2 is None
    (protagonist unplaced or at L3 with no L2 ancestor), the demote hook must
    return early and NOT demote any 明 lines.
    """
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)

    # Create two L2 towns (town_a and town_b)
    for tid in ["town_a", "town_b"]:
        store.append(kernel_event(
            "place_created", day=1, scene="s1", summary=f"place {tid}",
            deltas={"id": tid, "level": 2, "kind": "settlement", "seed": tid},
            turn=0,
        ))

    # Create an L3 place (deep_cave) at NO L2 ancestor
    # (it's a standalone L3 with no located_in link to any L2)
    store.append(kernel_event(
        "place_created", day=1, scene="s1", summary="place deep_cave",
        deltas={"id": "deep_cave", "level": 3, "kind": "location", "seed": "deep_cave"},
        turn=0,
    ))

    # Create protagonist character
    store.append(kernel_event(
        "character_created", day=1, scene="s1", summary="hero",
        deltas={"id": "hero", "tier": "tracked", "sketch": "勇者", "goal": "冒险"},
        turn=0,
    ))

    # Move protagonist to the L3 cave (NOT placed in any L2)
    store.append(kernel_event(
        "entity_moved", day=1, scene="s1", summary="move",
        deltas={"who": "hero", "to": "deep_cave"},
        turn=0,
    ))

    # Create a 暗 line anchored at town_a
    sk = {**_SK_COMPLEX, "id": "town_a_quest", "anchor": "town_a"}
    create_lore_line(store, sk, day=1, scene="s1", turn=0)

    # Surface it to 明 state
    store.append(kernel_event(
        "quest_surfaced", day=1, scene="s1", summary="surface",
        deltas={"id": "town_a_quest"},
        turn=1,
    ))

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["town_a_quest"]
    assert ln["state"] == "明", f"Expected 明, got {ln['state']}"

    # Run a turn with protagonist at deep_cave
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    canned = {"narration": "勇者在洞窟里。"}
    provider = FakeLLMProvider(json_responses=[canned])
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "deep_cave"}
    result = run_turn(
        r, store, w, scene, "在洞窟中行动",
        strategy=AuthorStrategy(),
        provider=provider,
    )

    # Check: quest_demoted should NOT be emitted (the critical fix)
    all_events = list(store.iter_events())
    demoted_events = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted_events) == 0, (
        f"CRITICAL: unresolvable town should NOT trigger mass-demote, "
        f"but {len(demoted_events)} quest_demoted event(s) were emitted"
    )

    # Verify the quest is still 明 (unchanged)
    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["town_a_quest"]
    assert ln2["state"] == "明", (
        f"Line state must stay 明 when protagonist's town is unresolvable, "
        f"but got {ln2['state']}"
    )


# ---------------------------------------------------------------------------
# Gap 1: station_push_fragment renders [id] for 暗 lines
# ---------------------------------------------------------------------------

def test_station_push_fragment_renders_ids():
    """station_push_fragment includes [lid] prefix for both L1 (venue) and L0 (town) 暗 lines."""
    from systems.place import PlaceSystem
    from loop.lore_disclosure import station_push_fragment

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(LoreSystem())

    store = _store(r)

    # Build a minimal world: L2 town + L3 venue + L3 other_venue
    store.append(kernel_event("place_created", day=1, scene="s1", summary="t",
                              deltas={"id": "test_town", "level": 2, "kind": "settlement"}, turn=0))
    store.append(kernel_event("place_created", day=1, scene="s1", summary="v",
                              deltas={"id": "test_venue", "level": 3, "kind": "venue",
                                      "parent": "test_town"}, turn=0))
    store.append(kernel_event("place_created", day=1, scene="s1", summary="ov",
                              deltas={"id": "other_venue", "level": 3, "kind": "venue",
                                      "parent": "test_town"}, turn=0))
    store.append(kernel_event("entity_created", day=1, scene="s1", summary="h",
                              deltas={"id": "hero", "etype": "Character", "tier": "tracked"}, turn=0))
    store.append(kernel_event("entity_moved", day=1, scene="s1", summary="hero→venue",
                              deltas={"who": "hero", "to": "test_venue"}, turn=0))

    # L1 line: anchored at test_venue (current L3)
    store.append(kernel_event("lore_created", day=1, scene="s1", summary="l1 line",
                              deltas={"id": "l1_quest",
                                      "complexity": "medium",
                                      "about": "L1事件",
                                      "secret": "秘密",
                                      "anchor": "test_town",
                                      "description": "L1描述",
                                      "trigger": "触发",
                                      "l3_anchor": "test_venue",
                                      "stages": [{"hint": "l1_beat"}],
                                      "threshold": 100}, turn=1))
    store.append(kernel_event("lore_advanced", day=1, scene="s1", summary="adv l1",
                              deltas={"id": "l1_quest", "stage_idx": 0, "hint": "l1_clue"}, turn=2))

    # L0 line: anchored at test_town but different venue (other_venue)
    store.append(kernel_event("lore_created", day=1, scene="s1", summary="l0 line",
                              deltas={"id": "l0_quest",
                                      "complexity": "medium",
                                      "about": "L0事件",
                                      "secret": "秘密",
                                      "anchor": "test_town",
                                      "description": "L0描述",
                                      "trigger": "触发",
                                      "l3_anchor": "other_venue",
                                      "stages": [{"hint": "l0_beat"}],
                                      "threshold": 100}, turn=3))

    w = project(r, store.iter_events())
    scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "test_venue"}

    result = station_push_fragment(r, w, scene)
    assert result is not None, "expected a fragment"
    # Both 暗 lines must expose their [id] in the fragment
    assert "[l1_quest]" in result, f"L1 line id missing from fragment:\n{result}"
    assert "[l0_quest]" in result, f"L0 line id missing from fragment:\n{result}"


# ---------------------------------------------------------------------------
# Gap 2: idle-based demote
# ---------------------------------------------------------------------------

def _setup_ming_line_world(r, store, *, surface_turn: int, current_turn: int,
                           advanced_turn: int | None = None):
    """Build world: protagonist at town_a, 明 line anchored at town_a.

    surface_turn: the turn quest_surfaced is emitted (sets last_advanced_day via day=1).
    advanced_turn: if set, emit quest_advanced at this turn (updates last_advanced_day via day=1).
    current_turn: the turn number the 'current turn' runs at (for idle calculation).
    """
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    store.append(kernel_event("place_created", day=1, scene="s1", summary="ta",
                              deltas={"id": "town_a", "level": 2, "kind": "settlement"}, turn=0))
    store.append(kernel_event("character_created", day=1, scene="s1", summary="hero",
                              deltas={"id": "hero", "tier": "tracked",
                                      "sketch": "勇者", "goal": "冒险"}, turn=0))
    store.append(kernel_event("entity_moved", day=1, scene="s1", summary="move",
                              deltas={"who": "hero", "to": "town_a"}, turn=0))

    sk = {**_SK_COMPLEX, "id": "idle_quest", "anchor": "town_a"}
    create_lore_line(store, sk, day=1, scene="s1", turn=0)

    store.append(kernel_event("quest_surfaced", day=1, scene="s1", summary="surface",
                              deltas={"id": "idle_quest"}, turn=surface_turn))

    if advanced_turn is not None:
        store.append(kernel_event("quest_advanced", day=1, scene="s1", summary="advance",
                                  deltas={"id": "idle_quest", "summary": "进展"},
                                  turn=advanced_turn))

    return project(r, store.iter_events())


def _make_idle_registry():
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())
    return r


def test_idle_demote_fires():
    """明 line idle >= IDLE_DEMOTE_DAYS game-days + protagonist in same town → idle demote fires."""
    from loop.turn import _run_demote_on_leave, IDLE_DEMOTE_DAYS

    r = _make_idle_registry()
    store = _store(r)
    # surface at day=1; now_day = 1 + IDLE_DEMOTE_DAYS → idle == IDLE_DEMOTE_DAYS → fires
    w = _setup_ming_line_world(r, store, surface_turn=1, current_turn=5)
    # Override meta.day for idle calculation: surfaced at day=1, now day=1+IDLE_DEMOTE_DAYS
    now_day = 1 + IDLE_DEMOTE_DAYS
    w["meta"]["day"] = now_day

    ln = w["systems"]["lore"]["lines"]["idle_quest"]
    assert ln["state"] == "明"
    assert ln.get("last_advanced_day") == 1  # surface event emitted at day=1

    canned_stages = {"stages": [{"hint": "idle-new-stage"}]}
    provider = FakeLLMProvider(json_responses=[canned_stages])

    _run_demote_on_leave(r, store, w, "hero", provider,
                         turn_num=5, day=now_day, scene="s1")

    all_events = list(store.iter_events())
    demoted = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted) >= 1, f"idle demote must fire after >= IDLE_DEMOTE_DAYS idle days, got {len(demoted)}"
    assert demoted[0]["deltas"]["id"] == "idle_quest"

    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["idle_quest"]["state"] == "暗"


def test_idle_demote_no_fire_recent():
    """明 line advanced recently (idle < IDLE_DEMOTE_DAYS) + same town → NOT demoted."""
    from loop.turn import _run_demote_on_leave, IDLE_DEMOTE_DAYS

    r = _make_idle_registry()
    store = _store(r)
    # surface at turn 1 day=1, advance at turn 3 day=1 (helper uses day=1 throughout),
    # now_day=2 → idle = 2-1=1 < IDLE_DEMOTE_DAYS=2 → no fire
    w = _setup_ming_line_world(r, store, surface_turn=1, current_turn=4,
                               advanced_turn=3)
    # last_advanced_day=1 (all events in helper use day=1); now_day=2 → idle=1 < 2
    w["meta"]["day"] = 2

    ln = w["systems"]["lore"]["lines"]["idle_quest"]
    assert ln["state"] == "明"
    assert ln.get("last_advanced_day") == 1  # advance event at day=1

    provider = FakeLLMProvider(json_responses=[])

    _run_demote_on_leave(r, store, w, "hero", provider,
                         turn_num=4, day=2, scene="s1")

    all_events = list(store.iter_events())
    demoted = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted) == 0, (
        f"idle < {IDLE_DEMOTE_DAYS} days must NOT trigger demote, got {len(demoted)}"
    )

    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["idle_quest"]["state"] == "明"


def test_last_advanced_turn_on_surface():
    """quest_surfaced sets last_advanced_day == event day and surfaced_turn == event turn."""
    r = _reg()
    store = _store(r)

    sk = {**_SK_COMPLEX, "id": "surf_turn_q"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    store.append(kernel_event("quest_surfaced", day=3, scene="s1", summary="surf",
                              deltas={"id": "surf_turn_q"}, turn=7))

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["surf_turn_q"]
    assert ln["state"] == "明"
    assert ln.get("last_advanced_day") == 3, (
        f"last_advanced_day should be 3 (event day), got {ln.get('last_advanced_day')}"
    )
    assert ln.get("surfaced_turn") == 7


def test_last_advanced_turn_on_advance():
    """quest_advanced sets last_advanced_day to the advance event's day."""
    r = _reg()
    store = _store(r)

    sk = {**_SK_COMPLEX, "id": "adv_turn_q"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    store.append(kernel_event("quest_surfaced", day=2, scene="s1", summary="surf",
                              deltas={"id": "adv_turn_q"}, turn=2))
    store.append(kernel_event("quest_advanced", day=5, scene="s1", summary="adv",
                              deltas={"id": "adv_turn_q", "summary": "新进展"},
                              turn=5))

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["adv_turn_q"]
    assert ln["state"] == "明"
    assert ln.get("last_advanced_day") == 5, (
        f"last_advanced_day should be 5 (advance event day), got {ln.get('last_advanced_day')}"
    )
    # surfaced_turn stays at original surface turn
    assert ln.get("surfaced_turn") == 2


# ---------------------------------------------------------------------------
# Fix 2: same-turn surface→demote thrash guard
# ---------------------------------------------------------------------------

def test_same_turn_surface_not_demoted_when_protagonist_away():
    """明 line with surfaced_turn == current turn_num, protagonist in different town → NOT demoted.

    A complex 暗 line that world-push-surfaces to 明 this exact turn must be protected
    from immediate demotion on the left_town path. The 爆点 must reach the player first.
    """
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    from loop.turn import _run_demote_on_leave

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)

    # Build world: town_a (anchor), town_b (where protagonist is)
    for tid in ["town_a", "town_b"]:
        store.append(kernel_event(
            "place_created", day=1, scene="s1", summary=f"place {tid}",
            deltas={"id": tid, "level": 2, "kind": "settlement"},
            turn=0,
        ))

    store.append(kernel_event(
        "character_created", day=1, scene="s1", summary="hero",
        deltas={"id": "hero", "tier": "tracked", "sketch": "勇者", "goal": "冒险"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_moved", day=1, scene="s1", summary="hero→town_b",
        deltas={"who": "hero", "to": "town_b"},
        turn=0,
    ))

    # Create a complex 暗 line anchored at town_a
    sk = {**_SK_COMPLEX, "id": "just_surfaced_quest", "anchor": "town_a"}
    create_lore_line(store, sk, day=1, scene="s1", turn=0)

    # Surface it to 明 at THIS turn (turn_num = 5)
    THIS_TURN = 5
    store.append(kernel_event(
        "quest_surfaced", day=1, scene="s1", summary="just surfaced this turn",
        deltas={"id": "just_surfaced_quest"},
        turn=THIS_TURN,
    ))

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["just_surfaced_quest"]

    # Verify preconditions
    assert ln["state"] == "明", f"Expected 明, got {ln['state']}"
    assert ln.get("surfaced_turn") == THIS_TURN, (
        f"surfaced_turn should be {THIS_TURN}, got {ln.get('surfaced_turn')}"
    )

    # Run demote-on-leave at the SAME turn it surfaced
    provider = FakeLLMProvider(json_responses=[])
    _run_demote_on_leave(r, store, w, "hero", provider,
                         turn_num=THIS_TURN, day=1, scene="s1")

    # Must NOT be demoted — same-turn thrash guard must fire
    all_events = list(store.iter_events())
    demoted_events = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted_events) == 0, (
        f"Same-turn surface→demote thrash must be blocked, "
        f"but {len(demoted_events)} quest_demoted event(s) were emitted"
    )

    # Verify the line is still 明
    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["just_surfaced_quest"]["state"] == "明", (
        "Line must stay 明 when demote was blocked by same-turn guard"
    )


def test_next_turn_after_surface_can_demote_when_away():
    """明 line surfaced at turn N-1, protagonist still away at turn N → CAN be demoted.

    The same-turn guard only protects surfaced_turn == turn_num; the NEXT turn
    it's eligible for demotion normally.
    """
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    from loop.turn import _run_demote_on_leave

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)

    for tid in ["town_a", "town_b"]:
        store.append(kernel_event(
            "place_created", day=1, scene="s1", summary=f"place {tid}",
            deltas={"id": tid, "level": 2, "kind": "settlement"},
            turn=0,
        ))

    store.append(kernel_event(
        "character_created", day=1, scene="s1", summary="hero",
        deltas={"id": "hero", "tier": "tracked", "sketch": "勇者", "goal": "冒险"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_moved", day=1, scene="s1", summary="hero→town_b",
        deltas={"who": "hero", "to": "town_b"},
        turn=0,
    ))

    sk = {**_SK_COMPLEX, "id": "prev_turn_quest", "anchor": "town_a"}
    create_lore_line(store, sk, day=1, scene="s1", turn=0)

    SURFACE_TURN = 4
    CURRENT_TURN = SURFACE_TURN + 1  # next turn — no longer protected

    store.append(kernel_event(
        "quest_surfaced", day=1, scene="s1", summary="surfaced last turn",
        deltas={"id": "prev_turn_quest"},
        turn=SURFACE_TURN,
    ))

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["prev_turn_quest"]
    assert ln["state"] == "明"
    assert ln.get("surfaced_turn") == SURFACE_TURN

    canned_stages = {"stages": [{"hint": "new-stage-after-demote"}]}
    provider = FakeLLMProvider(json_responses=[canned_stages])

    _run_demote_on_leave(r, store, w, "hero", provider,
                         turn_num=CURRENT_TURN, day=1, scene="s1")

    all_events = list(store.iter_events())
    demoted_events = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted_events) >= 1, (
        "Line surfaced last turn, protagonist still away → should be demoted at turn N+1"
    )
    assert demoted_events[0]["deltas"]["id"] == "prev_turn_quest"


# ---------------------------------------------------------------------------
# Lifespan / expiry / idle-by-day (added 2026-06-21)
# ---------------------------------------------------------------------------

# Skeletons for lifespan tests — threshold=0 so 暗骰 does NOT advance them
_SK_SIMPLE_NOROLL = {
    **_SK_SIMPLE,
    "id": "simple_exp",
    "threshold": 0,
    "stages": [{"hint": "s0"}, {"hint": "s1"}],
}
_SK_MEDIUM_NOROLL = {
    **_SK_MEDIUM,
    "id": "medium_exp",
    "threshold": 0,
    "stages": [{"hint": "m0"}, {"hint": "m1"}],
}
_SK_COMPLEX_NOROLL = {
    **_SK_COMPLEX,
    "id": "complex_exp",
    "threshold": 0,
    "stages": [{"hint": "c0"}],   # only 1 stage so world-push never fires
}


# (g) born_day / last_advanced_day folded from event day
def test_born_day_set_on_create():
    """lore_created event folds born_day from event's day field."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_SIMPLE_NOROLL, "id": "bd_test"}
    create_lore_line(store, sk, day=5, scene="s1", turn=1)
    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["bd_test"]
    assert ln.get("born_day") == 5, f"born_day should be 5, got {ln.get('born_day')}"


def test_lifespan_days_default_simple():
    """simple line gets lifespan_days=3 by default."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_SIMPLE_NOROLL, "id": "ld_simple"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["ld_simple"]
    assert ln.get("lifespan_days") == 3, f"simple default lifespan_days should be 3, got {ln.get('lifespan_days')}"


def test_lifespan_days_default_medium():
    """medium line gets lifespan_days=7 by default."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_MEDIUM_NOROLL, "id": "ld_medium"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["ld_medium"]
    assert ln.get("lifespan_days") == 7, f"medium default lifespan_days should be 7, got {ln.get('lifespan_days')}"


def test_lifespan_days_default_complex():
    """complex line gets lifespan_days=20 by default."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_COMPLEX_NOROLL, "id": "ld_complex"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["ld_complex"]
    assert ln.get("lifespan_days") == 20, f"complex default lifespan_days should be 20, got {ln.get('lifespan_days')}"


def test_last_advanced_day_set_on_surface():
    """quest_surfaced folds last_advanced_day from event's day field."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_SIMPLE_NOROLL, "id": "lad_surf"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    store.append(kernel_event("quest_surfaced", day=4, scene="s1", summary="surf",
                              deltas={"id": "lad_surf"}, turn=2))
    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["lad_surf"]
    assert ln.get("last_advanced_day") == 4, (
        f"last_advanced_day should be 4 (event day), got {ln.get('last_advanced_day')}"
    )


def test_last_advanced_day_set_on_advance():
    """quest_advanced folds last_advanced_day from event's day field."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_SIMPLE_NOROLL, "id": "lad_adv"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    store.append(kernel_event("quest_surfaced", day=2, scene="s1", summary="surf",
                              deltas={"id": "lad_adv"}, turn=2))
    store.append(kernel_event("quest_advanced", day=7, scene="s1", summary="adv",
                              deltas={"id": "lad_adv", "summary": "进展"},
                              turn=3))
    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["lad_adv"]
    assert ln.get("last_advanced_day") == 7, (
        f"last_advanced_day should be 7 (advance event day), got {ln.get('last_advanced_day')}"
    )


def test_last_advanced_day_set_on_lore_advanced():
    """lore_advanced folds last_advanced_day from the event's day field (暗 line branch)."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_SIMPLE_NOROLL, "id": "lad_lore_adv"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    # Emit a lore_advanced event (暗 line advancement via 暗骰) with a specific day
    store.append(kernel_event("lore_advanced", day=9, scene="s1", summary="adv",
                              deltas={"id": "lad_lore_adv", "stage_idx": 0, "hint": "clue-x"},
                              turn=2))
    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["lad_lore_adv"]
    assert ln.get("state") == "暗", "line should stay 暗 after lore_advanced"
    assert ln.get("last_advanced_day") == 9, (
        f"last_advanced_day should be 9 (lore_advanced event day), got {ln.get('last_advanced_day')}"
    )


# (a) 暗 simple line past lifespan → quest_expired → state 了结 by:expiry
def test_simple_line_expires_when_past_lifespan():
    """暗 simple line born_day=1 lifespan=3; now_day=4 → quest_expired → 了结 by:expiry."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_SIMPLE_NOROLL, "id": "exp_simple"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["exp_simple"]
    assert ln.get("born_day") == 1
    assert ln.get("lifespan_days") == 3
    assert ln.get("state") == "暗"

    # world meta day = 4 → (4-1)=3 >= 3 → expired
    w["meta"]["day"] = 4
    appended = run_lore(r, store, w)

    types = [e["type"] for e in appended]
    assert "quest_expired" in types, f"Expected quest_expired, got {types}"
    exp_ev = next(e for e in appended if e["type"] == "quest_expired")
    assert exp_ev["deltas"]["id"] == "exp_simple"

    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["exp_simple"]
    assert ln2["state"] == "了结", f"Expected 了结, got {ln2['state']}"
    assert ln2.get("resolved", {}).get("by") == "expiry"


# (b) 暗 medium line past lifespan → quest_expired
def test_medium_line_expires_when_past_lifespan():
    """暗 medium line born_day=1 lifespan=7; now_day=8 → quest_expired → 了结 by:expiry."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_MEDIUM_NOROLL, "id": "exp_medium"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)

    w = project(r, store.iter_events())
    w["meta"]["day"] = 8  # (8-1)=7 >= 7 → expired

    appended = run_lore(r, store, w)
    types = [e["type"] for e in appended]
    assert "quest_expired" in types, f"Expected quest_expired for medium, got {types}"
    exp_ev = next(e for e in appended if e["type"] == "quest_expired")
    assert exp_ev["deltas"]["id"] == "exp_medium"

    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["exp_medium"]
    assert ln2["state"] == "了结"
    assert ln2.get("resolved", {}).get("by") == "expiry"


# (c) 暗 complex past lifespan → quest_finale_due → pending_finale=True, state still 暗
def test_complex_line_finale_due_when_past_lifespan():
    """暗 complex line born_day=1 lifespan=20; now_day=21 → quest_finale_due → pending_finale=True, state stays 暗."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_COMPLEX_NOROLL, "id": "finale_complex"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)

    w = project(r, store.iter_events())
    w["meta"]["day"] = 21  # (21-1)=20 >= 20 → expired

    appended = run_lore(r, store, w)
    types = [e["type"] for e in appended]
    assert "quest_finale_due" in types, f"Expected quest_finale_due for complex, got {types}"
    finale_ev = next(e for e in appended if e["type"] == "quest_finale_due")
    assert finale_ev["deltas"]["id"] == "finale_complex"

    # State must still be 暗 (NOT 了结)
    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["finale_complex"]
    assert ln2["state"] == "暗", f"complex line should stay 暗 after finale_due, got {ln2['state']}"
    assert ln2.get("pending_finale") is True, f"pending_finale should be True, got {ln2.get('pending_finale')}"


# (d) complex finale fires at most once (idempotent on replay)
def test_complex_finale_fires_at_most_once():
    """quest_finale_due is not re-emitted if pending_finale is already True."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_COMPLEX_NOROLL, "id": "finale_once"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)

    w = project(r, store.iter_events())
    w["meta"]["day"] = 21

    # First run: should emit quest_finale_due
    appended1 = run_lore(r, store, w)
    types1 = [e["type"] for e in appended1]
    assert "quest_finale_due" in types1

    # Project and run again at same day — pending_finale already True → must not emit again
    w2 = project(r, store.iter_events())
    w2["meta"]["day"] = 21
    appended2 = run_lore(r, store, w2)
    types2 = [e["type"] for e in appended2]
    assert "quest_finale_due" not in types2, (
        "quest_finale_due must not be re-emitted if pending_finale is already True"
    )


# (e) line NOT yet past lifespan → survives, still 暗
def test_line_not_yet_expired_survives():
    """暗 simple line born_day=1 lifespan=3; now_day=3 → NOT yet expired (3-1=2 < 3)."""
    r = _reg()
    store = _store(r)
    sk = {**_SK_SIMPLE_NOROLL, "id": "survives_simple"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)

    w = project(r, store.iter_events())
    w["meta"]["day"] = 3  # (3-1)=2 < 3 → not expired

    appended = run_lore(r, store, w)
    types = [e["type"] for e in appended]
    assert "quest_expired" not in types, "line not yet past lifespan must NOT emit quest_expired"
    assert "quest_finale_due" not in types

    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["survives_simple"]
    assert ln2["state"] == "暗"


# (f) legacy line missing born_day → NOT expired (defensive)
def test_legacy_line_missing_born_day_not_expired():
    """A line without born_day (legacy) must never be expired defensively."""
    r = _reg()
    store = _store(r)
    # Inject a quest_created event whose deltas don't include born_day/lifespan_days
    from kernel.events import kernel_event as ke
    store.append(ke("lore_created", day=1, scene="s1", summary="legacy",
                    deltas={"id": "legacy_line", "complexity": "simple",
                            "about": "old line", "secret": "s", "anchor": "town_a",
                            "description": "d", "trigger": "t", "l3_anchor": "l3",
                            "stages": [{"hint": "h0"}], "threshold": 0,
                            "state": "暗"},
                    turn=1))
    w = project(r, store.iter_events())
    # Manually strip born_day from the projected line (simulating legacy state)
    w["systems"]["lore"]["lines"]["legacy_line"].pop("born_day", None)
    w["systems"]["lore"]["lines"]["legacy_line"].pop("lifespan_days", None)
    w["meta"]["day"] = 999  # far future

    appended = run_lore(r, store, w)
    types = [e["type"] for e in appended]
    assert "quest_expired" not in types, "legacy line without born_day must NOT be expired"
    assert "quest_finale_due" not in types


# ---------------------------------------------------------------------------
# Idle-demote by game-day (replaces turn-based idle demote tests)
# ---------------------------------------------------------------------------

def _setup_ming_line_world_day(r, store, *, surface_day: int, current_day: int,
                                advanced_day: int | None = None):
    """Build world: protagonist at town_a, 明 line anchored at town_a.

    surface_day: the day quest_surfaced is emitted (sets last_advanced_day).
    advanced_day: if set, emit quest_advanced at this day (updates last_advanced_day).
    current_day: the day 'now' for idle calculation.
    """
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    store.append(kernel_event("place_created", day=1, scene="s1", summary="ta",
                              deltas={"id": "town_a", "level": 2, "kind": "settlement"}, turn=0))
    store.append(kernel_event("character_created", day=1, scene="s1", summary="hero",
                              deltas={"id": "hero", "tier": "tracked",
                                      "sketch": "勇者", "goal": "冒险"}, turn=0))
    store.append(kernel_event("entity_moved", day=1, scene="s1", summary="move",
                              deltas={"who": "hero", "to": "town_a"}, turn=0))

    sk = {**_SK_COMPLEX, "id": "idle_quest_day", "anchor": "town_a"}
    create_lore_line(store, sk, day=1, scene="s1", turn=0)

    store.append(kernel_event("quest_surfaced", day=surface_day, scene="s1", summary="surface",
                              deltas={"id": "idle_quest_day"}, turn=2))

    if advanced_day is not None:
        store.append(kernel_event("quest_advanced", day=advanced_day, scene="s1", summary="advance",
                                  deltas={"id": "idle_quest_day", "summary": "进展"},
                                  turn=3))

    w = project(r, store.iter_events())
    # Override meta.day to current_day for the test
    w["meta"]["day"] = current_day
    return w


# (h) idle-demote fires when now_day - last_advanced_day >= 2
def test_idle_demote_fires_by_day():
    """明 line last_advanced_day=1; now_day=3 → idle 2 days >= IDLE_DEMOTE_DAYS=2 → demote fires."""
    from loop.turn import _run_demote_on_leave, IDLE_DEMOTE_DAYS
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)
    w = _setup_ming_line_world_day(r, store, surface_day=1, current_day=1 + IDLE_DEMOTE_DAYS)

    ln = w["systems"]["lore"]["lines"]["idle_quest_day"]
    assert ln["state"] == "明"
    assert ln.get("last_advanced_day") == 1

    canned_stages = {"stages": [{"hint": "idle-day-new-stage"}]}
    provider = FakeLLMProvider(json_responses=[canned_stages])

    _run_demote_on_leave(r, store, w, "hero", provider,
                         turn_num=5, day=1 + IDLE_DEMOTE_DAYS, scene="s1")

    all_events = list(store.iter_events())
    demoted = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted) >= 1, f"idle-demote must fire after >= IDLE_DEMOTE_DAYS idle days, got {len(demoted)}"
    assert demoted[0]["deltas"]["id"] == "idle_quest_day"

    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["idle_quest_day"]["state"] == "暗"


# (h) idle-demote does NOT fire when now_day - last_advanced_day < 2
def test_idle_demote_no_fire_recent_day():
    """明 line last_advanced_day=2; now_day=3 → idle 1 day < IDLE_DEMOTE_DAYS=2 → NOT demoted."""
    from loop.turn import _run_demote_on_leave, IDLE_DEMOTE_DAYS
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())

    store = _store(r)
    # surface_day=1, advanced_day=2, current_day=3 → idle = 3-2=1 < 2
    w = _setup_ming_line_world_day(r, store, surface_day=1, current_day=3, advanced_day=2)

    ln = w["systems"]["lore"]["lines"]["idle_quest_day"]
    assert ln["state"] == "明"
    assert ln.get("last_advanced_day") == 2

    provider = FakeLLMProvider(json_responses=[])
    _run_demote_on_leave(r, store, w, "hero", provider,
                         turn_num=5, day=3, scene="s1")

    all_events = list(store.iter_events())
    demoted = [e for e in all_events if e["type"] == "quest_demoted"]
    assert len(demoted) == 0, (
        f"idle < IDLE_DEMOTE_DAYS days must NOT trigger demote, got {len(demoted)}"
    )

    w2 = project(r, store.iter_events())
    assert w2["systems"]["lore"]["lines"]["idle_quest_day"]["state"] == "明"


# ---------------------------------------------------------------------------
# SOT regression: no `status` field after quest_expired / quest_resolved
# ---------------------------------------------------------------------------

def test_no_status_field_after_quest_expired():
    """After quest_expired the line has NO `status` key — state is the single lifecycle truth."""
    r = _reg()
    store = _store(r)

    sk = {**_SK_SIMPLE_NOROLL, "id": "sot_expired"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)

    w = project(r, store.iter_events())
    # Expire it by advancing meta.day past lifespan
    w["meta"]["day"] = 1 + (w["systems"]["lore"]["lines"]["sot_expired"]["lifespan_days"] or 3)

    appended = run_lore(r, store, w)
    types = [e["type"] for e in appended]
    assert "quest_expired" in types, f"Expected quest_expired; got {types}"

    w2 = project(r, store.iter_events())
    ln = w2["systems"]["lore"]["lines"]["sot_expired"]
    assert ln["state"] == "了结", f"state must be 了结 after quest_expired; got {ln['state']}"
    assert "status" not in ln, (
        f"status field must be absent after quest_expired (single-SOT); got {ln.get('status')!r}"
    )


def test_no_status_field_after_quest_resolved():
    """After quest_resolved the line has NO `status` key — state is the single lifecycle truth."""
    r = _reg()
    store = _store(r)

    # Create a 暗 complex line, surface it to 明, then resolve it
    sk = {**_SK_COMPLEX, "id": "sot_resolved"}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    store.append(kernel_event("quest_surfaced", day=1, scene="s1", summary="surface",
                              deltas={"id": "sot_resolved"}, turn=2))
    store.append(kernel_event("quest_resolved", day=2, scene="s1", summary="resolve",
                              deltas={"id": "sot_resolved", "by": "player",
                                      "summary": "勇者破解了谜团"},
                              turn=3))

    w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["sot_resolved"]
    assert ln["state"] == "了结", f"state must be 了结 after quest_resolved; got {ln['state']}"
    assert "status" not in ln, (
        f"status field must be absent after quest_resolved (single-SOT); got {ln.get('status')!r}"
    )


# ---------------------------------------------------------------------------
# SOT regression: lore_advanced stamped with meta.day (bug 2 — day unification)
# ---------------------------------------------------------------------------

def test_lore_advanced_stamped_with_meta_day():
    """run_lore must stamp lore_advanced.day with meta.day, not the store-tail day.

    Set meta.day (world clock) != store-tail day; run_lore; assert:
      - emitted lore_advanced.day == meta.day
      - projected line.last_advanced_day == meta.day

    Use a complex line (lifespan_days=20) with born_day=1 and meta.day=5 so
    it is not yet expired (5-1=4 < 20) but meta.day differs from store-tail day (1).
    threshold=100 guarantees the 暗骰 roll passes every time.
    """
    r = _reg()
    store = _store(r)

    # complex line: lifespan=20, threshold=100 → always advances, not expired at day 5
    sk = {**_SK_COMPLEX, "id": "meta_day_line",
          "threshold": 100,
          "stages": [{"hint": "h0"}, {"hint": "h1"}, {"hint": "h2"}]}
    # Store-tail day = 1 (from create event)
    create_lore_line(store, sk, day=1, scene="s1", turn=1)

    w = project(r, store.iter_events())
    # Set meta.day to a DIFFERENT value than the store tail day (1)
    # Must be < born_day + lifespan_days = 1 + 20 = 21 so the line does not expire
    META_DAY = 5
    w["meta"]["day"] = META_DAY
    w["meta"]["scene"] = "s1"
    w["meta"]["campaign_seed"] = 0

    appended = run_lore(r, store, w)
    lore_adv_events = [e for e in appended if e["type"] == "lore_advanced"]
    assert lore_adv_events, f"run_lore must emit lore_advanced; got {[e['type'] for e in appended]}"

    ev = lore_adv_events[0]
    assert ev["day"] == META_DAY, (
        f"lore_advanced.day must equal meta.day ({META_DAY}); got {ev['day']}"
    )

    # Verify last_advanced_day is also folded from meta.day
    w2 = project(r, store.iter_events())
    ln2 = w2["systems"]["lore"]["lines"]["meta_day_line"]
    assert ln2.get("last_advanced_day") == META_DAY, (
        f"last_advanced_day must equal meta.day ({META_DAY}); got {ln2.get('last_advanced_day')}"
    )
